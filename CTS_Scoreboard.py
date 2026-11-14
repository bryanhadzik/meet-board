#! /usr/bin/python3
import warnings
# hytek-parser's old attrs warns on Python 3.13; harmless, keep the console clean
warnings.filterwarnings('ignore', message='Running interpreter doesn')
import flask
import flask_login
import flask_socketio
import datetime
import traceback
import copy
import ctypes
import serial
import serial.tools.list_ports
import re
import urllib.parse
import time
import json
import os.path
import glob
from hytek_event_loader import HytekEventLoader
from hytek_parser.hy3.enums import GenderAge
from hytek_st2_parser import parse_st2_file
from hytek_rec_parser import parse_rec_file, format_record_date
from race_state_machine import RaceStateMachine
import ap
import argparse
import threading
import app_paths
import scb_loader
import https_server
import startlist_json
import announce
import meet_board
from race_book import RaceBook, secs as race_book_secs
from serial_monitor import monitor as serial_mon
import cts_signal
import team_logos
import updater
import obs_client
import music
import music_mute
import blackout as blackout_mod
import yt_chapters
from _version import __version__

DEBUG = False
#DEBUG = True
settings_file = app_paths.data_path('settings.json')

settings = {
    'meet_title': '',
    'serial_port': 'COM1',
    'serial_format': '9600-8N1',   # baud-databits/parity/stopbits; Debug -> Serial input -> Find the right format
    'update_interval': 1.0,        # seconds between board updates from the console
    'music_obs_mute': False,       # mute chosen OBS audio sources while music plays
    'music_obs_inputs': [],
    'music_unmute_delay': 2.0,
    'blackout_auto': True,
    'blackout_after_hours': 4.0,
    'blackout_manual': False,
    'warmup_slide_on': False,     # warm-up slide on the meet boards and stream
    'warmup_title': '',           # blank = meet title, else the meet name in the .hy3
    'warmup_subtitle': '',        # blank = the pool name in the .hy3
    'warmup_start': '',           # meet start time, HH:MM (24 h)
    'state_cuts': meet_board.default_state_cuts(),   # last season's state cuts per class (meet board)
    'warmup_speed': 30,           # TV scroll speed, px per second at 1080p
    'warmup_hold': 7,             # stream band: seconds per heat
    'username': 'admin',
    'password': 'password',
    'ad_url': '',
    'results_url': '',            # public phone results page (MeetDeck publishes it), shown as a QR code on the meet boards
    'results_qr': 'slide',        # off | corner | slide (corner + a full-screen slide every few minutes between races)
    'results_every': '4',         # minutes between full-screen slides
    'results_secs': '15',         # seconds each slide stays up
    'youtube_channel': '',        # @handle or channel URL; youtube.com/<it>/live opens whatever is streaming now
    'num_lanes': 8,           # Tooele: 8-lane pool
    'pool_course': 'SCY',
    'show_pr_tags': True,
    'show_confetti': True,
    'show_time_decorations': False,
    'seed_time_label': 'Seed Time',
    'blank_message': '',
    'blank_message_visible': False,
    'blank_message_align': 'left',
    'team_home': '',
    'team_home_tag': '',
    'team_guest1': '',
    'team_guest1_tag': '',
    'team_guest2': '',
    'team_guest2_tag': '',
    'team_guest3': '',
    'team_guest3_tag': '',
    'std_desc_overrides': {},
    # meet-board additions
    'http_port': 5000,
    'scb_folder': '',            # watch folder for Meet Manager CTS start lists
    'scb_name_style': 'first_last',
    'team_colors': {},           # {code: {name, color, alt}} overrides
    'board_home_team': '',
    'board_style': 'classic',
    'obs_url': 'ws://127.0.0.1:4455',  # OBS on this PC; obs-websocket v5
    'obs_password': '',    # classic | lanes | pool | light       # team that keeps its primary color on the board
    'github_token': '',          # only needed if the repo is private
    }
in_file = None
out_file = None
in_speed = 1.0
debug_console = False

# Event Settings
event_info = HytekEventLoader()

# Time Standards
time_standards = None

# Swim Records (list of dicts: {rec_file, filename, team_tag, set_id})
swim_record_sets = []
_next_rec_set_id = 0

app = flask.Flask(__name__,
                  template_folder=app_paths.bundle_path('templates'),
                  static_folder=app_paths.bundle_path('static'))
# config
app.config.update(
    DEBUG = False,
    # Replaced at startup by a per-install random key kept in settings.json
    # (see ensure_secret_key). Never a fixed value: the source is public.
    SECRET_KEY = os.urandom(32).hex(),
)
socketio = flask_socketio.SocketIO(app)
# Changes on every start: pages that reconnect to a different boot (restart,
# update) reload themselves, so TVs pick up new code with nobody touching them.
BOOT_ID = '%x' % int(time.time() * 1000)

# Remember the live race fields (clock, lane times/places) so a page that
# connects mid-race or during results - a TV rebooting, OBS reloading its
# browser source - shows them at once instead of waiting for the next change.
_live_race = {}
# Race on / over / results held until the next start (see race_book.py): one
# book on the server so every screen agrees and a refresh keeps the results.
race_book = RaceBook()
_race_book_last = None
_LIVE_PREFIXES = ('lane_time', 'lane_place', 'lane_running', 'running_time')
_socketio_emit = socketio.emit
# CTS blanks the tens-of-seconds digit: " 1: 5.23" -> " 1:05.23" (same width)
_ZERO_FILL = re.compile(r'(:)\s(?=\d)')


def _emit_and_remember(event, *args, **kwargs):
    if event == 'update_scoreboard' and kwargs.get('namespace') == '/scoreboard' and args and isinstance(args[0], dict):
        for k, v in list(args[0].items()):
            if k.startswith(_LIVE_PREFIXES):
                if isinstance(v, str) and ':' in v:
                    v = args[0][k] = _ZERO_FILL.sub(r'\g<1>0', v)
                _live_race[k] = v
        try:
            woke = screens_black.watch_update(args[0]) if screens_black else False
        except Exception:
            woke = False
        res = _socketio_emit(event, *args, **kwargs)
        _race_book_feed(args[0])
        if woke:
            _blackout_broadcast()
        # The first race of the meet takes the warm-up slide down by itself
        if args[0].get('race_state') == 'Running' and settings.get('warmup_slide_on'):
            settings['warmup_slide_on'] = False
            try:
                save_settings()
            except Exception:
                pass
            _warmup_broadcast()
        return res
    return _socketio_emit(event, *args, **kwargs)


socketio.emit = _emit_and_remember


def _race_book_payload():
    d = race_book.payload()
    if d['hold']:
        d['expires_in'] = max(0, int(race_book.HOLD_S - (time.time() - race_book.hold_at)))
    return d


def _race_book_feed(update):
    global _race_book_last
    try:
        race_book.feed(update)
        d = race_book.payload()
        key = json.dumps(d, sort_keys=True, default=str)
        if key != _race_book_last:
            _race_book_last = key
            _socketio_emit('board_race', _race_book_payload(), namespace='/scoreboard')
    except Exception:
        traceback.print_exc()

# Go black (see blackout.py); created once settings are loaded.
screens_black = None
_blackout_last = None


def _blackout_broadcast(force=False):
    """Tell every TV page whether to be black; only when it changes unless forced."""
    global _blackout_last
    if not screens_black:
        return
    st = screens_black.status()
    if force or st['black'] != _blackout_last:
        _blackout_last = st['black']
        _socketio_emit('blackout', st, namespace='/scoreboard')
        print('[screens] %s' % ('black (%s)' % st['reason'] if st['black'] else 'awake'))

main_thread = None
event_heat_info = [' ',' ',' ',' ',' ',' ',' ',' ']
lane_info = [[],
            [0,0,0,0,0,0,0,0],
            [0,0,0,0,0,0,0,0],
            [0,0,0,0,0,0,0,0],
            [0,0,0,0,0,0,0,0],
            [0,0,0,0,0,0,0,0],
            [0,0,0,0,0,0,0,0],
            [0,0,0,0,0,0,0,0],
            [0,0,0,0,0,0,0,0],
            [0,0,0,0,0,0,0,0],
            [0,0,0,0,0,0,0,0]]
time_info = [0,0,0,0,0,0,0,0]
running_time = '        '
channel_running = [False for i in range(10)]
score_info = {0x14: [' ',' ',' ',' ',' ',' ',' ',' '],
              0x15: [' ',' ',' ',' ',' ',' ',' ',' ']}
team_scores = {'score_home': '', 'score_guest1': '', 'score_guest2': '', 'score_guest3': ''}
race_fsm = RaceStateMachine()

def load_settings():
    global settings, time_standards, swim_record_sets, _next_rec_set_id
    try:
        with open(settings_file, "rt") as f:
            settings.update(json.load(f))
        # Go-black default moved from 2 to 4 hours: carry the old default over once
        if not settings.get('blackout_hours_v4'):
            if float(settings.get('blackout_after_hours', 4.0) or 0) == 2.0:
                settings['blackout_after_hours'] = 4.0
            settings['blackout_hours_v4'] = True
        # State cuts became per-class tables filled from the 2026 state meet
        settings['state_cuts'] = meet_board.migrate_state_cuts(settings.get('state_cuts'))
        # State cuts back on the meet board, header only (Nov 2026): switch on once
        if not settings.get('state_cuts_header_only'):
            settings['state_cuts']['on'] = True
            settings['state_cuts_header_only'] = True
        if 'event_info' in settings:
            event_info.from_object(settings['event_info'])
        if 'time_standards' in settings:
            import pickle, base64
            time_standards = pickle.loads(base64.b64decode(settings['time_standards']))
        if 'swim_record_sets' in settings:
            import pickle, base64
            swim_record_sets = pickle.loads(base64.b64decode(settings['swim_record_sets']))
            if swim_record_sets:
                _next_rec_set_id = max(s['set_id'] for s in swim_record_sets) + 1
        elif 'swim_records' in settings:
            # Backward compat: migrate single record file to list format
            import pickle, base64
            old_rec = pickle.loads(base64.b64decode(settings['swim_records']))
            swim_record_sets = [{'rec_file': old_rec, 'filename': 'migrated.rec', 'team_tag': 'ALL', 'set_id': 0}]
            _next_rec_set_id = 1
            settings['swim_record_sets'] = base64.b64encode(pickle.dumps(swim_record_sets)).decode('ascii')
            settings.pop('swim_records', None)
            with open(settings_file, "wt") as f:
                json.dump(settings, f, sort_keys=True, indent=4)
    except: pass

_settings_lock = threading.Lock()

# OBS link: runs in the background, never blocks or breaks the boards.
obs = obs_client.OBSClient(lambda: (settings.get('obs_url'), settings.get('obs_password')))

def enrich_from_saved_hy3(loader):
    """After a .scb load, put back seed times / ages / relay swimmers from the
    last .hy3 Meet Entries upload (kept in settings['hy3_source'])."""
    src = settings.get('hy3_source')
    if not src:
        return 0
    try:
        hy3 = HytekEventLoader()
        hy3.from_object(src)
        return scb_loader.enrich_from_hy3(loader, hy3)
    except Exception:
        traceback.print_exc()
        return 0

def backup_schedule():
    """Keep the schedule we're about to replace, so Undo can restore it."""
    if event_info.event_names:
        settings['event_info_prev'] = event_info.to_object()
        settings['schedule_filename_prev'] = settings.get('schedule_filename', '')

def save_settings():
    with _settings_lock:
        tmp = settings_file + '.tmp'
        with open(tmp, "wt") as f:
            json.dump(settings, f, sort_keys=True, indent=4)
        os.replace(tmp, settings_file)

# ---------------------------------------------------------------------------
# .scb watch folder: Meet Manager re-exports start lists after deck changes;
# the board picks them up within a few seconds.
# ---------------------------------------------------------------------------
_scb_watch = {'signature': None, 'message': ''}

def scb_watch_worker():
    while True:
        folder = settings.get('scb_folder', '')
        if folder:
            sig = scb_loader.folder_signature(folder)
            if sig is None:
                _scb_watch['message'] = 'Watch folder not reachable: %s' % folder
            elif sig and sig != _scb_watch['signature']:
                socketio.sleep(1.0)  # let Meet Manager finish writing the batch
                sig = scb_loader.folder_signature(folder)
                try:
                    texts = scb_loader.read_scb_folder(folder)
                    staged = copy.deepcopy(event_info)  # load into a copy; keep the board on failure
                    n_events, n_heats, errs = scb_loader.load_scb_into(
                        staged, texts, settings.get('scb_name_style', 'first_last'))
                    n_rich = enrich_from_saved_hy3(staged)
                    backup_schedule()
                    event_info.from_object(staged.to_object())
                except scb_loader.StaleExportError as e:
                    _scb_watch['message'] = 'Watch folder NOT loaded: %s' % e
                except Exception as e:
                    _scb_watch['message'] = 'Watch folder load failed: %s' % e
                else:
                    settings['event_info'] = event_info.to_object()
                    settings['schedule_filename'] = '%s (%d events, %d heats, watching%s)' % (
                        folder, n_events, n_heats,
                        '; .hy3 seeds for %d lanes' % n_rich if n_rich else '')
                    _scb_watch['message'] = 'Loaded %d events / %d heats at %s%s' % (
                        n_events, n_heats, time.strftime('%H:%M:%S'),
                        (' - skipped: ' + '; '.join(errs)) if errs else '')
                    print(_scb_watch['message'])
                    try:
                        save_settings()
                    except Exception:
                        traceback.print_exc()
                    send_event_info()
                _scb_watch['signature'] = sig
            elif not sig:
                _scb_watch['message'] = 'No .scb files in %s yet' % folder
        socketio.sleep(3.0)

SETTINGS_VERSION = 2

def migrate_settings():
    """One-time fixes for settings.json files written by older versions."""
    v = int(settings.get('settings_version', 0) or 0)
    if v < 1:
        # v0 defaulted to 6 lanes and saved it; the pool has 8.
        if int(settings.get('num_lanes', 6) or 6) == 6:
            settings['num_lanes'] = 8
    # Fixed for this pool: 8 lanes, short course yards; ad display removed;
    # the warm-up slide uses the meet name from the Meet tab (one place)
    fixed = {'num_lanes': 8, 'pool_course': 'SCY', 'ad_url': '', 'warmup_title': ''}
    changed = any(settings.get(k) != val for k, val in fixed.items())
    settings.update(fixed)
    if v < SETTINGS_VERSION or changed:
        settings['settings_version'] = SETTINGS_VERSION
        try:
            save_settings()
        except Exception:
            traceback.print_exc()

def ensure_secret_key():
    """Session cookies are signed with this. Generate one per install and
    keep it in settings.json so logins survive restarts."""
    import secrets as _secrets
    key = settings.get('secret_key')
    if not key or len(key) < 32:
        settings['secret_key'] = key = _secrets.token_hex(32)
        try:
            save_settings()
        except Exception:
            traceback.print_exc()
    app.config['SECRET_KEY'] = key

## Stuff to move the cursor
def print_at(r, c, s):
    if debug_console:
        ap.output(c, r, s)   
            
def hex_to_digit(c):
    c = c & 0x0F
    c ^= 0x0F # Invert lower nybble
    if (c > 9):
        return ' '
    return ("%i" % c)

update={}
_last_emit = 0.0
next_update = datetime.datetime.now()
last_event_sent = (1,1)

def parse_line(l, out = None):
    global event_heat_info, lane_info, time_info, running_time, update, next_update, last_event_sent, team_scores
    
    s = ""
    if out:
        k = "[%f] "% time.time() + " ".join(["%02X" % int(c) for c in l])
        out.write(k)
    try:
        # Byte 0 - Channel
        c = l.pop(0)
        running_finish = True if (c & 0x40) else False
        format_display = True if (c & 0x01) else False
        channel = ((c & 0x3E) >> 1) ^ 0x1F
        
        if (1 <= channel <= 10) and not format_display:
            channel_running[channel-1] = running_finish
            # This is a lane display of interest
            while len(l):
                c = l.pop(0)
                lane_info[channel][(c >> 4) & 0x0F] = c
            
            lane = hex_to_digit(lane_info[channel][0])
            place = hex_to_digit(lane_info[channel][1])

            update["lane_place%i"%channel] = place
            update["lane_running%i"%channel] = running_finish
            
            if running_finish:
                lane_time = running_time # '        '
                s = "%4s: running" % (channel)
            else:
                lane_time = hex_to_digit(lane_info[channel][2]) + hex_to_digit(lane_info[channel][3])
                lane_time += ':' if lane_time.strip() else ' '
                lane_time += hex_to_digit(lane_info[channel][4]) + hex_to_digit(lane_info[channel][5])
                lane_time += '.' if lane_time.strip() else ' '
                lane_time += hex_to_digit(lane_info[channel][6]) + hex_to_digit(lane_info[channel][7])
                s = "%4s: %s %s %s" % (channel, lane, place, lane_time)
                update["lane_time%i"%channel] = lane_time
                
            print_at(channel+1, 0, " " * 20)
            print_at(channel+1, 0, "%4s: %s %s %s" % (channel, lane, place, lane_time))
            
        if (channel == 0) and not format_display:
            # Running time
            while len(l):
                c = l.pop(0)
                time_info[(c >> 4) & 0x0F] = c
            running_time = hex_to_digit(time_info[2]) + hex_to_digit(time_info[3])
            running_time += ':' if running_time.strip() else ' '
            running_time += hex_to_digit(time_info[4]) + hex_to_digit(time_info[5])
            running_time += '.' if running_time.strip() else ' '
            running_time += hex_to_digit(time_info[6]) + hex_to_digit(time_info[7])
            update["running_time"] = running_time
            
            s = "Running Time: " + running_time

        if (channel == 12) and not format_display:
            # Event / Heat
            while len(l):
                c = l.pop(0)
                event_heat_info[(c >> 4) & 0x0F] = hex_to_digit(c)
                
            update["current_event"] = ''.join(event_heat_info[:3])
            update["current_heat"] = ''.join(event_heat_info[-3:])
            try:
                event_tuple = (int(update["current_event"]), int(update["current_heat"]))
            except: return

            print_at(0, 0, " Event:" +  update["current_event"] + " Heat:" + update["current_heat"] + "    ")
            
            s = " Event:" +  update["current_event"] + " Heat:" + update["current_heat"] + "    "
            
            if last_event_sent != event_tuple:
                last_event_sent = event_tuple
                send_event_info()

        if channel in (0x14, 0x15) and not format_display:
            # Team scores: 0x14 = Home + Guest 1, 0x15 = Guest 2 + Guest 3
            while len(l):
                c = l.pop(0)
                score_info[channel][(c >> 4) & 0x0F] = hex_to_digit(c)

            score_a = ''.join(score_info[channel][:4])
            score_b = ''.join(score_info[channel][-4:])

            if channel == 0x14:
                new_scores = {'score_home': score_a, 'score_guest1': score_b}
            else:
                new_scores = {'score_guest2': score_a, 'score_guest3': score_b}

            sendScores = False
            for key, val in new_scores.items():
                if team_scores[key] != val:
                    team_scores[key] = val
                    update[key] = val
                    sendScores = True

            s = "Scores ch%02X: %s / %s" % (channel, score_a.strip(), score_b.strip())

            if sendScores:
                send_scores_info()

        if out:
            if s:
                out.write(' '*max(0, 50-len(k)) + " # " + s)
            out.write("\n")
    except IndexError:
        traceback.print_exc()
        
    finally:
        #Output anything we got
        # Batch the console's ~100 records/s into one board update per
        # update_interval (default 1 s); values just accumulate in `update`.
        global _last_emit
        if ("current_event" in update or "running_time" in update) and \
                time.time() - _last_emit >= float(settings.get('update_interval', 1.0) or 0):
            _last_emit = time.time()
            race_fsm.evaluate_update(channel_running, update)
            update["race_state"] = race_fsm.state_name
            socketio.emit('update_scoreboard', update, namespace='/scoreboard')
            update.clear()
            
        if (datetime.datetime.now() > next_update) and debug_console:
            next_update = datetime.datetime.now() + datetime.timedelta(seconds=0.2)
            ap.render()


SERIAL_FORMATS = ['9600-8N1', '9600-8E1', '9600-8O1', '9600-8N2',
                  '9600-8E1-INV', '9600-8N1-INV', '9600-8O1-INV',   # inverted signal (see cts_signal.py)
                  '9600-7E1', '9600-7O1', '19200-8N1', '4800-8N1', '38400-8N1']


def serial_params(fmt):
    """'9600-8E1' (or '9600-8E1-INV') -> pyserial kwargs."""
    m = re.match(r'^(\d+)-([78])([NEO])([12])$', cts_signal.base_format(fmt or ''))
    if not m:
        m = re.match(r'^(\d+)-([78])([NEO])([12])$', '9600-8N1')
    baud, bits, par, stop = m.groups()
    return {'baudrate': int(baud),
            'bytesize': {'7': serial.SEVENBITS, '8': serial.EIGHTBITS}[bits],
            'parity': {'N': serial.PARITY_NONE, 'E': serial.PARITY_EVEN, 'O': serial.PARITY_ODD}[par],
            'stopbits': {'1': serial.STOPBITS_ONE, '2': serial.STOPBITS_TWO}[stop]}


def main_thread_worker():
    j = None
    if in_file and in_file.lower().endswith('.bin'):
        # Raw bytes captured from the serial port: replay at 9600 baud x speed
        with open(in_file, 'rb') as f:
            data = f.read()
        serial_mon.replay(os.path.basename(in_file))
        rf = cts_signal.Reframer(cts_signal.is_inverted(settings.get('serial_format')) or '-INV' in in_file.upper())
        serial_mon.inverted = rf.inverted
        confirm = cts_signal.Confirmer() if rf.inverted else None
        while True:
            l = []
            for n, raw in enumerate(data):
                serial_mon.byte(raw)
                for c in rf.feed(raw):
                    if not c:
                        continue
                    if (c & 0x80) or (len(l) > 8):
                        if len(l):
                            serial_mon.record(l)
                            if confirm is None or confirm.accept(l):
                                parse_line(l)
                        l = []
                    l.append(c)
                if n % 96 == 0:
                    socketio.sleep(0.1 / max(in_speed, 0.01))   # 96 bytes = 0.1 s at 9600 baud (8N1 ~ 960 B/s)
            socketio.sleep(2.0)  # loop the recording
    elif in_file:
        delay = 0.0
        start_time = None
        serial_mon.replay(os.path.basename(in_file))
        with open(in_file, 'rt') as f:
            if out_file:
                j = open(out_file, "at")
            l = []
            for d in re.finditer(r"\[([0-9.]+)\]\s*|([0-9a-fA-F]{2})\s+", f.read()):
                if d.group(1):
                    if start_time:
                        delay = float(d.group(1)) - in_speed*time.time() - start_time
                        if delay > 0:
                            socketio.sleep(delay)
                        print_at(13, 0, " " + d.group(1) + "   ")
                    else:
                        start_time = float(d.group(1)) - in_speed*time.time()
                    continue
                c = int(d.group(2), 16)
                serial_mon.byte(c)
                if c:
                    if (c & 0x80) or (len(l) > 8):
                        if len(l):
                            serial_mon.record(l)
                            parse_line(l, j)
                        l=[]
                    l.append(c)
                if delay > (0.1):
                    delay = 0
                    socketio.sleep(0.1) # 9600 = about 1ms per character
                else:
                    delay += 1/9600.0
    else:
        if out_file:
            j = open(out_file, "at")
        # Keep retrying: the console may be off or the USB adapter unplugged
        # when the app starts. The meet board's up-next panel keeps working.
        last_error = None
        while True:
            port = settings['serial_port']
            fmt = settings.get('serial_format') or '9600-8N1'
            if port != serial_mon.port:
                serial_mon.reset(port)          # new port: start the counters over
            serial_mon.opening(port)
            try:
                if not port:
                    raise serial.SerialException('no serial port selected')
                with serial.Serial(port, timeout=0, **serial_params(fmt)) as f:
                    print("Reading CTS data from %s (%s)" % (port, fmt))
                    serial_mon.opened(port, dict(serial_params(fmt), format=fmt))
                    rf = cts_signal.Reframer(cts_signal.is_inverted(fmt))
                    serial_mon.inverted = rf.inverted
                    confirm = cts_signal.Confirmer() if rf.inverted else None
                    last_error = None
                    l = []
                    while port == settings['serial_port'] and fmt == (settings.get('serial_format') or '9600-8N1'):
                        serial_mon.poll_capture()
                        chunk = f.read(256)
                        if not chunk:
                            socketio.sleep(0.01)
                            continue
                        serial_mon.chunk(len(chunk))
                        for raw in chunk:
                            serial_mon.byte(raw)
                            for c in rf.feed(raw):
                                if (c & 0x80) or (len(l) > 8):
                                    if len(l):
                                        serial_mon.record(l)
                                        if confirm is None or confirm.accept(l):
                                            parse_line(l, j)
                                    l = []
                                l.append(c)
                    serial_mon.closed(port, 'port changed in settings' if port != settings['serial_port']
                                      else 'serial format changed to %s' % (settings.get('serial_format') or '9600-8N1'))
            except Exception as e:
                serial_mon.failed(port, e)
                if str(e) != last_error:
                    print("Serial port %s unavailable (%s); retrying every 5 s" % (port, e))
                    last_error = str(e)
                socketio.sleep(0.5 if _autodetect.get('running') else 5.0)

# flask-login
login_manager = flask_login.LoginManager()
login_manager.init_app(app)
login_manager.login_view = "route_login"
# No authentication: the admin pages are open to anyone on the network
# (Bryan's call - the timing PC sits on the pool's private network).
# Flask-Login's LOGIN_DISABLED turns every @login_required into a no-op.
app.config['LOGIN_DISABLED'] = True


# simple user model
class User(flask_login.UserMixin):

    def __init__(self, id):
        self.id = id
        self.name = settings['username']
        self.password = settings['password']
        
    def __repr__(self):
        return "%d/%s" % (self.id, self.name)


# create the user       
user = User(0)

def _get_qualifying_times(event_number):
    """Look up qualifying times from time_standards for a given event.
    
    Returns (list_of_dicts, show_age_codes) where list_of_dicts has
    [{time, tag, description, qualifiers}, ...] and show_age_codes is True
    when multiple standards match for age or gender reasons.
    """
    if time_standards is None:
        return [], False
    
    meta = event_info.event_meta.get(event_number)
    if not meta:
        return [], False
    
    pool_course = settings.get('pool_course', 'SCY')
    
    # Determine which st2 sex codes to search for
    sex_codes = meta.get('sex_codes', [])
    if not sex_codes:
        return [], False
    
    stroke_code = meta.get('stroke_code')
    distance = meta.get('distance')
    is_relay = meta.get('relay', False)
    age_min = meta.get('age_min')
    age_max = meta.get('age_max')
    is_mixed = meta.get('is_mixed', False)
    
    # Determine expected event_type
    event_type_match = "Relay" if is_relay else "Individual"
    
    # For combined events, collect all source event age ranges
    age_ranges = []
    combined = event_info.combined
    source_events = set()
    for src, dst in combined.items():
        if dst == (event_number, 1) or src == (event_number, 1):
            src_event_num = src[0]
            src_meta = event_info.event_meta.get(src_event_num)
            if src_meta:
                source_events.add(src_event_num)
                age_ranges.append((src_meta.get('age_min'), src_meta.get('age_max')))
    
    if not age_ranges:
        age_ranges.append((age_min, age_max))
    
    # Find matching st2 events
    # Use age-appropriate gender names: Boys/Girls for youth, Men/Women for adults
    gender_age = meta.get('gender_age')
    if gender_age in (GenderAge.MEN_S, GenderAge.WOMEN_S):
        sex_display = {1: 'Men', 2: 'Women'}
    else:
        sex_display = {1: 'Boys', 2: 'Girls'}
    matches = []
    
    for sex_code in sex_codes:
        for ar_min, ar_max in age_ranges:
            for st2_event in time_standards.events:
                if st2_event.stroke_code != stroke_code:
                    continue
                if st2_event.distance != distance:
                    continue
                if st2_event.event_type != event_type_match:
                    continue
                if st2_event.sex_code != sex_code:
                    continue
                
                # Age range overlap check
                st2_min = st2_event.age_group_min
                st2_max = st2_event.age_group_max
                ev_min = ar_min if ar_min else 0
                ev_max = ar_max if ar_max else 999
                s_min = st2_min if st2_min else 0
                s_max = st2_max if st2_max else 999
                
                if ev_min > s_max or s_min > ev_max:
                    continue
                
                # Found a match - get times for the pool course
                for cs in st2_event.courses:
                    if cs.course == pool_course:
                        for qt in cs.times:
                            matches.append({
                                'sex_code': sex_code,
                                'age_min': st2_min,
                                'age_max': st2_max,
                                'tag': qt.standard.tag,
                                'description': qt.standard.description,
                                'time': qt.time_formatted,
                                'time_seconds': qt.time_seconds,
                            })
    
    if not matches:
        return [], False

    # Sort: girls/women (sex_code 2) before boys/men (1), youngest age first,
    # then fastest first. Keeps standards-qualifier group ordering consistent
    # with the records display.
    def _std_sort_key(m):
        sex_order = 0 if m['sex_code'] == 2 else 1
        age_lo = m['age_min'] if m['age_min'] else 0
        return (sex_order, age_lo, m['time_seconds'])
    matches.sort(key=_std_sort_key)
    
    # Determine which qualifiers need to be shown
    unique_sex = len(set(m['sex_code'] for m in matches)) > 1
    unique_age = len(set((m['age_min'], m['age_max']) for m in matches)) > 1
    
    # Build results grouped by qualifier string
    groups = []         # [{qualifiers: str, items: [...]}, ...]
    group_map = {}      # qualifiers_str -> index in groups
    color_idx = 0
    desc_overrides = settings.get('std_desc_overrides', {})
    for m in matches:
        qualifiers = []
        if unique_age:
            a_min = m['age_min']
            a_max = m['age_max']
            if a_min and a_max:
                qualifiers.append("%d-%d" % (a_min, a_max))
            elif a_max:
                qualifiers.append("%d & Under" % a_max)
            elif a_min:
                qualifiers.append("%d & Over" % a_min)
            else:
                qualifiers.append("Open")
        if unique_sex:
            qualifiers.append(sex_display.get(m['sex_code'], ''))
        
        qual_str = ' '.join(qualifiers)
        item = {
            'time': m['time'],
            'time_seconds': m['time_seconds'],
            'tag': m['tag'],
            'description': desc_overrides.get(m['tag'], m['description']),
            'color_class': 'qt-color-%d' % (color_idx % 12),
            'sex_code': m['sex_code'],
            'age_min': m['age_min'],
            'age_max': m['age_max'],
        }
        if qual_str not in group_map:
            group_map[qual_str] = len(groups)
            groups.append({'qualifiers': qual_str, 'items': []})
        groups[group_map[qual_str]]['items'].append(item)
        color_idx += 1
    
    return groups, (unique_sex or unique_age)

def _get_matching_records(event_number):
    """Look up swim records for a given event across all loaded record sets.
    
    Returns (list_of_set_results, show_age_codes) where list_of_set_results is:
    [{set_name, set_team_tag, records: [...]}, ...] in upload order.
    Records use strict less-than for breaking (tying does not break a record).
    """
    if not swim_record_sets:
        return [], False
    
    meta = event_info.event_meta.get(event_number)
    if not meta:
        return [], False
    
    pool_course = settings.get('pool_course', 'SCY')
    
    sex_codes = meta.get('sex_codes', [])
    if not sex_codes:
        return [], False
    
    stroke_code = meta.get('stroke_code')
    distance = meta.get('distance')
    is_relay = meta.get('relay', False)
    age_min = meta.get('age_min')
    age_max = meta.get('age_max')
    
    event_type_match = "Relay" if is_relay else "Individual"
    
    # For combined events, collect all source event age ranges
    age_ranges = []
    combined = event_info.combined
    for src, dst in combined.items():
        if dst == (event_number, 1) or src == (event_number, 1):
            src_meta = event_info.event_meta.get(src[0])
            if src_meta:
                age_ranges.append((src_meta.get('age_min'), src_meta.get('age_max')))
    if not age_ranges:
        age_ranges.append((age_min, age_max))
    
    gender_age = meta.get('gender_age')
    if gender_age in (GenderAge.MEN_S, GenderAge.WOMEN_S):
        sex_display = {1: 'Men', 2: 'Women'}
    else:
        sex_display = {1: 'Boys', 2: 'Girls'}
    
    all_set_results = []
    any_show_age = False
    color_idx = 0

    # First pass: collect matches for every set so we can compute qualifier
    # visibility (unique_sex / unique_age) globally across sets. This way a
    # set that only has one sex still gets the sex qualifier shown whenever
    # any other set needs the sex distinction.
    per_set_matches = []
    for rec_set in swim_record_sets:
        rec_file = rec_set['rec_file']

        if rec_file.header.course != pool_course:
            continue

        matches = []
        for sex_code in sex_codes:
            for ar_min, ar_max in age_ranges:
                for rec in rec_file.records:
                    if rec.stroke_code != stroke_code:
                        continue
                    if rec.distance != distance:
                        continue
                    if rec.event_type != event_type_match:
                        continue
                    if rec.sex_code != sex_code:
                        continue

                    rec_min = rec.age_group_min
                    rec_max = rec.age_group_max
                    ev_min = ar_min if ar_min else 0
                    ev_max = ar_max if ar_max else 999
                    r_min = rec_min if rec_min else 0
                    r_max = rec_max if rec_max else 999

                    if ev_min > r_max or r_min > ev_max:
                        continue

                    from hytek_rec_parser import EPOCH
                    rec_year = rec.record_date.year if rec.record_date != EPOCH else None

                    matches.append({
                        'sex_code': sex_code,
                        'age_min': rec_min,
                        'age_max': rec_max,
                        'time': rec.time_formatted,
                        'time_seconds': rec.time_seconds,
                        'swimmer_name': rec.swimmer_name or '',
                        'record_team': rec.record_team or '',
                        'record_year': str(rec_year) if rec_year else '',
                        'relay_names': rec.relay_names or '',
                    })

        if matches:
            per_set_matches.append((rec_set, rec_file, matches))

    # Global qualifier flags: if any set would differentiate by sex/age, then
    # every set should display that qualifier for visual consistency.
    all_matches = [m for _, _, ms in per_set_matches for m in ms]
    unique_sex = len(set(m['sex_code'] for m in all_matches)) > 1
    unique_age = len(set((m['age_min'], m['age_max']) for m in all_matches)) > 1
    if unique_sex or unique_age:
        any_show_age = True

    for rec_set, rec_file, matches in per_set_matches:
        # Sort: girls (2) before boys (1), youngest first, then fastest first
        def sort_key(m):
            sex_order = 0 if m['sex_code'] == 2 else 1
            age_lo = m['age_min'] if m['age_min'] else 0
            return (sex_order, age_lo, m['time_seconds'])
        matches.sort(key=sort_key)
        
        records = []
        for m in matches:
            qualifiers = []
            if unique_age:
                a_min = m['age_min']
                a_max = m['age_max']
                if a_min and a_max:
                    qualifiers.append("%d-%d" % (a_min, a_max))
                elif a_max:
                    qualifiers.append("%d & Under" % a_max)
                elif a_min:
                    qualifiers.append("%d & Over" % a_min)
                else:
                    qualifiers.append("Open")
            if unique_sex:
                qualifiers.append(sex_display.get(m['sex_code'], ''))
            
            records.append({
                'time': m['time'],
                'time_seconds': m['time_seconds'],
                'swimmer_name': m['swimmer_name'],
                'record_team': m['record_team'],
                'record_year': m['record_year'],
                'relay_names': m['relay_names'],
                'color_class': 'rec-color-%d' % (color_idx % 12),
                'qualifiers': ' '.join(qualifiers),
                'sex_code': m['sex_code'],
                'age_min': m['age_min'],
                'age_max': m['age_max'],
            })
            color_idx += 1
        
        all_set_results.append({
            'set_name': rec_file.header.record_set_name or '',
            'set_team_tag': rec_set['team_tag'],
            'records': records,
        })
    
    return all_set_results, any_show_age

def _relay_legs(e, h, lane):
    getter = getattr(event_info, 'get_relay_legs', None)
    return getter(e, h, lane) if getter else []


def send_event_info():            
    update={}
    update["current_event"] = str(last_event_sent[0])
    update["current_heat"] = str(last_event_sent[1])
    update["event_name"] = event_info.get_event_name(last_event_sent[0])
    update["schedule_has_names"] = event_info.has_names
    # Lets the overlays treat "Stansbury B" as a relay even when the meet file
    # carries no relay swimmers (e.g. an .scb start list)
    update["event_is_relay"] = bool((getattr(event_info, 'event_meta', {}).get(last_event_sent[0]) or {}).get('relay'))
    qt_results, qt_show_age = _get_qualifying_times(last_event_sent[0])
    rec_set_results, rec_show_age = _get_matching_records(last_event_sent[0])
    show_age_codes = qt_show_age or rec_show_age
    update["qualifying_times"] = qt_results
    update["record_sets"] = rec_set_results
    
    for i in range(1,11):
        update["lane_name%i" % i] = event_info.get_display_string(last_event_sent[0], last_event_sent[1], i)
        update["lane_team%i" % i] = event_info.get_team_code(last_event_sent[0], last_event_sent[1], i)
        update["lane_legs%i" % i] = _relay_legs(last_event_sent[0], last_event_sent[1], i)
        update["lane_age_code%i" % i] = event_info.get_age_code(last_event_sent[0], last_event_sent[1], i) if show_age_codes else ""
        seed = event_info.get_seed_time(last_event_sent[0], last_event_sent[1], i)
        update["lane_seed_time%i" % i] = seed if seed is not None else ""
        update["lane_usc%i" % i] = _usc_lane(last_event_sent[0], update["lane_team%i" % i], update["lane_name%i" % i])

    update["show_pr_tags"] = settings.get('show_pr_tags', True)
    update["show_confetti"] = settings.get('show_confetti', True)
    update["show_time_decorations"] = settings.get('show_time_decorations', False)
    update["seed_time_label"] = settings.get('seed_time_label', 'Seed Time')
    update["blank_message"] = settings.get('blank_message', '')
    update["blank_message_visible"] = settings.get('blank_message_visible', False)
    update["blank_message_align"] = settings.get('blank_message_align', 'left')
    update["race_state"] = race_fsm.state_name
    update["num_lanes"] = settings.get('num_lanes', 8)
    update.update(meet_board.board_payload(event_info, settings, last_event_sent))
    meet_teams = meet_board.schedule_teams(event_info)
    update["meet_teams"] = meet_teams
    update["team_colors"] = meet_board.team_colors(settings, meet_teams, _team_names())
    update["team_logos"] = _meet_logos(meet_teams)
    update["color_palette"] = meet_board.FALLBACK_PALETTE
    update["resolved_colors"] = meet_board.resolved_colors(
        update["team_colors"], meet_teams, update.get("home_team") or settings.get('board_home_team') or settings.get('team_home_tag', ''))
    update["home_team"] = settings.get('board_home_team') or settings.get('team_home_tag', '')
    update["meet_title"] = settings.get('meet_title', '')
    update["board_style"] = settings.get('board_style', 'classic')
    update["state_cut"] = _state_cut_payload(last_event_sent[0])
    update["state_now"] = _usc_event(last_event_sent[0])
    # lets the board tell a split from a finish time
    update["event_distance"] = ((getattr(event_info, 'event_meta', {}) or {}).get(last_event_sent[0]) or {}).get('distance') or 0

    socketio.emit('update_scoreboard', update, namespace='/scoreboard')
    _warmup_broadcast()


def _store_usc(doc):
    """This season's standings (utahswimcoach.com) sent with a MeetDeck start list:
    per event the state line / #1 / record per class, per swimmer class rank + season best.
    Keyed by event + team + last name (not lane) so an .scb reload with new heats still matches."""
    lanes, events = {}, {}
    for ev in (doc or {}).get('events') or []:
        num = ev.get('number')
        if ev.get('state_now'):
            events[str(num)] = ev['state_now']
        for ht in ev.get('heats') or []:
            for ln in ht.get('lanes') or []:
                st = ln.get('state') if isinstance(ln, dict) else None
                if st and st.get('rank'):
                    item = {'cls': st.get('cls', ''), 'rank': st['rank'], 'sb': st.get('sb')}
                    if ev.get('relay'):
                        item['relay'] = ln.get('letter') or 'A'
                    else:
                        item['last'] = str(ln.get('last') or '')
                    lanes.setdefault('%s|%s' % (num, ln.get('team', '')), []).append(item)
    settings['usc_lanes'] = lanes
    settings['usc_events'] = events
    settings['usc_src'] = (doc or {}).get('state_src') or {}


def _usc_lane(ev, team, name):
    if settings.get('usc_board') is False:
        return None
    name = name or ''
    low = name.lower()
    for x in (settings.get('usc_lanes') or {}).get('%s|%s' % (ev, team or ''), []):
        if x.get('last') and re.search(r'\b%s\b' % re.escape(x['last'].lower()), low):
            return x
        if x.get('relay') and re.search(r'(^|[\s"(])%s($|[\s")])' % re.escape(x['relay']), name):
            return x
    return None


def _usc_event(ev):
    if settings.get('usc_board') is False:
        return None
    return (settings.get('usc_events') or {}).get(str(ev)) or None


def _state_cuts():
    cuts = meet_board.migrate_state_cuts(settings.get('state_cuts'))
    settings['state_cuts'] = cuts
    return cuts


def _state_cut_classes(cuts, meet_teams, heat_teams):
    """Which classifications' cuts to show: those of the teams in the water,
    else of every team in the meet, else the home team's. Home class first."""
    codes = list(dict.fromkeys([c for c in heat_teams if c] + list(meet_teams)))
    canon = meet_board.canonical_codes(settings, codes + [_home_code()], _team_names())
    tc = meet_board.team_class_map(cuts, codes + [_home_code()], canon)
    home_cls = tc.get(_home_code(), '')
    fallback = meet_board.resolve_fallback(cuts, home_cls)
    picked = [tc[c] for c in heat_teams if c in tc]
    # a team with no classification in the water races the fallback class
    if fallback and any(c and c not in tc for c in heat_teams):
        picked.append(fallback)
    picked = picked or [tc[c] for c in meet_teams if c in tc] or ([home_cls] if home_cls else []) \
        or ([fallback] if fallback else [])
    order = {c: i for i, c in enumerate(meet_board.CLASSES)}
    classes = sorted(set(picked), key=lambda c: (c != home_cls, order.get(c, 99)))
    team_class = {c: tc[c] for c in codes if c in tc}
    if fallback:
        for c in codes:
            team_class.setdefault(c, fallback)
    return classes, team_class


def _home_code():
    return settings.get('board_home_team') or settings.get('team_home_tag', '') or ''


def _state_cut_payload(event_number, heat=None):
    cuts = _state_cuts()
    if not cuts.get('on'):
        return None
    meta = (getattr(event_info, 'event_meta', {}) or {}).get(event_number)
    heat = last_event_sent[1] if heat is None else heat
    heat_teams = []
    for i in range(1, 11):
        try:
            heat_teams.append(event_info.get_team_code(event_number, heat, i) or '')
        except Exception:
            pass
    classes, team_class = _state_cut_classes(cuts, meet_board.schedule_teams(event_info), heat_teams)
    items = meet_board.state_cut_for(meta, cuts, classes)
    if not items:
        return None
    return {'label': (cuts.get('label') or '').strip() or meet_board.STATE_CUTS_YEAR + ' State',
            'items': items, 'team_class': team_class}


def _state_cuts_from_st2(tag):
    """Pull one standard (e.g. the state cut) out of the loaded .st2 for the
    high school events. Returns {key: seconds}."""
    out = {}
    if time_standards is None:
        return out
    course = settings.get('pool_course', 'SCY')
    for stroke, dist, relay, _name in meet_board.HS_EVENTS:
        for sex in (2, 1):
            best = None
            for ev in time_standards.events:
                if (ev.stroke_code, ev.distance, ev.sex_code) != (stroke, dist, sex):
                    continue
                if ev.event_type != ('Relay' if relay else 'Individual'):
                    continue
                for cs in ev.courses:
                    if cs.course != course:
                        continue
                    for qt in cs.times:
                        if qt.standard.tag == tag and qt.time_seconds:
                            # several age groups: the open / widest one wins
                            span = (ev.age_group_max or 999) - (ev.age_group_min or 0)
                            if best is None or span > best[0]:
                                best = (span, round(qt.time_seconds, 2))
            if best:
                out[meet_board.cut_key(sex, stroke, dist, relay)] = best[1]
    return out


def _state_cut_now(cuts):
    """What the meet board shows for the event in the water right now, or why
    it shows nothing - so a missing cut can be sorted out from Meet Files."""
    ev, heat = last_event_sent
    name = event_info.get_event_name(ev) if ev else ''
    head = ('Event %s %s' % (ev, name)).strip() if ev else 'No event'
    if not cuts.get('on'):
        return {'ok': False, 'text': 'Off - check "Show on the meet board".'}
    meta = (getattr(event_info, 'event_meta', {}) or {}).get(ev)
    if not ev or not meta:
        return {'ok': False, 'text': head + ': not in the loaded start lists - load the meet (.hy3 or .scb) above.'}
    if not any((meta.get('stroke_code'), meta.get('distance'), bool(meta.get('relay'))) == e[:3] for e in meet_board.HS_EVENTS) \
            or not meta.get('sex_codes'):
        return {'ok': False, 'text': head + ': not one of the 11 high school events, so there is no state cut.'}
    p = _state_cut_payload(ev, heat)
    if p:
        return {'ok': True, 'text': head + ': showing ' + ', '.join(
            '%s %s%s' % (x['cls'], (x['sex'] + ' ') if len({i['sex'] for i in p['items']}) > 1 else '', x['time'])
            for x in p['items'])}
    teams = list(dict.fromkeys([event_info.get_team_code(ev, heat, i) or '' for i in range(1, 11)]
                               + meet_board.schedule_teams(event_info)))
    teams = [t for t in teams if t]
    classes, _tc = _state_cut_classes(cuts, meet_board.schedule_teams(event_info),
                                      [event_info.get_team_code(ev, heat, i) or '' for i in range(1, 11)])
    if not classes:
        return {'ok': False, 'text': head + ': none of the teams (' + ', '.join(teams[:8]) +
                ') has a classification - set them under Team classifications, or pick a class for '
                'teams with no classification.'}
    return {'ok': False, 'text': head + ': no ' + '/'.join(classes) + ' time entered for this event.'}


def _host_class(cuts):
    try:
        canon = meet_board.canonical_codes(settings, [_home_code()], _team_names())
        return meet_board.team_class_map(cuts, [_home_code()], canon).get(_home_code(), '')
    except Exception:
        return ''


def _state_cuts_view():
    cuts = _state_cuts()
    tables = cuts.get('classes') or {}
    builtin = meet_board.builtin_state_cuts()
    classes = [c for c in meet_board.CLASSES if c in tables or c in builtin]
    rows = []
    for stroke, dist, relay, name in meet_board.HS_EVENTS:
        row = {'name': name, 'cells': {}}
        for cls in classes:
            for sex, col in ((2, 'girls'), (1, 'boys')):
                k = meet_board.cut_key(sex, stroke, dist, relay)
                v = (tables.get(cls) or {}).get(k)
                row['cells'].setdefault(cls, {})[col] = {'key': k, 'time': meet_board.format_swim_time(v),
                                                          'builtin': v is not None and v == builtin.get(cls, {}).get(k)}
        rows.append(row)
    # every team we know of: the four county schools first, then the meet's
    county = ['TOOEL', 'STAN', 'GHS', 'DPEAK']
    meet = meet_board.schedule_teams(event_info)
    canon = meet_board.canonical_codes(settings, meet, _team_names())
    homes = list(dict.fromkeys(county + [canon.get(c, c) for c in meet]))
    table = meet_board.team_colors(settings, homes, _team_names())
    over = cuts.get('team_class') or {}
    teams = [{'code': c, 'name': (table.get(c) or {}).get('name') or c,
              'cls': over.get(c, meet_board.DEFAULT_TEAM_CLASS.get(c, '')),
              'default': meet_board.DEFAULT_TEAM_CLASS.get(c, ''), 'in_meet': c in canon.values()}
             for c in homes]
    src = settings.get('usc_src') or {}
    return {'on': bool(cuts.get('on')), 'label': cuts.get('label', ''), 'now': _state_cut_now(cuts),
            'usc_board': settings.get('usc_board', True), 'usc_src': src,
            'usc_swimmers': sum(len(v) for v in (settings.get('usc_lanes') or {}).values()),
            'fallback_class': cuts.get('fallback_class', meet_board.FALLBACK_HOST),
            'host_class': _host_class(cuts),
            'default_label': meet_board.STATE_CUTS_YEAR + ' State', 'year': meet_board.STATE_CUTS_YEAR,
            'classes': classes, 'builtin_classes': sorted(builtin, key=meet_board.CLASSES.index),
            'rows': rows, 'teams': teams, 'all_classes': meet_board.CLASSES,
            'st2_tags': [{'tag': st.tag, 'desc': settings.get('std_desc_overrides', {}).get(st.tag, st.description)}
                         for st in time_standards.header.standards] if time_standards else []}

def send_scores_info():
    update = {}
    update["score_home"] = team_scores['score_home']
    update["score_guest1"] = team_scores['score_guest1']
    update["score_guest2"] = team_scores['score_guest2']
    update["score_guest3"] = team_scores['score_guest3']
    update["race_state"] = race_fsm.state_name
    socketio.emit('update_scoreboard', update, namespace='/scoreboard')

def send_blank_message():
    """Broadcast just the blank-state message fields to scoreboard clients."""
    update = {
        'blank_message': settings.get('blank_message', ''),
        'blank_message_visible': settings.get('blank_message_visible', False),
        'blank_message_align': settings.get('blank_message_align', 'left'),
    }
    socketio.emit('update_scoreboard', update, namespace='/scoreboard')
            
@socketio.on('connect', namespace='/scoreboard')
def ws_scoreboard():
    print("Client connected to scoreboard namespace")
    global main_thread
    if(main_thread is None):
        main_thread = socketio.start_background_task(target=main_thread_worker)
        
    send_event_info()
    send_scores_info()
    if _live_race:
        flask_socketio.emit('update_scoreboard', dict(_live_race))   # just this client
    flask_socketio.emit('board_race', _race_book_payload())        # held results survive a refresh
    flask_socketio.emit('server_info', {'version': __version__, 'boot': BOOT_ID})
    if screens_black:
        flask_socketio.emit('blackout', screens_black.status())
    flask_socketio.emit('warmup_slide', _warmup_slide())
    flask_socketio.emit('notice', _notice())
    flask_socketio.emit('break_slide', _break_slide())

@socketio.on('next_heat', namespace='/scoreboard')
def ws_next_heat(d):
    global last_event_sent
    
    update={}
    
    event_list = list(event_info.events.keys())
    event_list.sort()

    try:
        event_tuple = event_list[event_list.index(last_event_sent)+1]
    except:
        event_tuple = event_list[0]
    
    last_event_sent = event_tuple
    race_fsm.notify_event_change()
    send_event_info()

@socketio.on('set_event_heat', namespace='/scoreboard')
def ws_set_event_heat(d):
    global last_event_sent
    event = int(d.get('event', last_event_sent[0]))
    heat = int(d.get('heat', last_event_sent[1]))
    last_event_sent = (event, heat)
    race_fsm.notify_event_change()
    send_event_info()

# ---------------------------------------------------------------------------
# Test simulation handlers
# ---------------------------------------------------------------------------
_sim_running = False  # Whether the sim clock is ticking

SIM_LANES = {
    1: {'name': 'Timmy Splash',   'team': 'HOME', 'age_code': '8B', 'seed': 17.50, 'time': 15.23, 'place': '1'},
    2: {'name': 'Sally Wave',     'team': 'AWAY', 'age_code': '8G', 'seed': 18.50, 'time': 16.89, 'place': '4'},
    3: {'name': 'James Pullbuoy', 'team': 'HOME', 'age_code': '7B', 'seed': 17.22, 'time': 17.54, 'place': '5'},
    4: {'name': 'Bobby Kick',     'team': 'HOME', 'age_code': '8B', 'seed': 19.00, 'time': 17.55, 'place': '5'},
    5: {'name': 'Lily Laneline',  'team': 'AWAY', 'age_code': '8G', 'seed': 17.20, 'time': 15.87, 'place': '2'},
    6: {'name': 'Max Dive',       'team': 'HOME', 'age_code': '8B', 'seed': 16.50, 'time': 18.50, 'place': '6'},
}

def _format_lane_time(seconds, final=True):
    """Format seconds CTS-style (8 chars, right-justified).

    CTS behaviors reproduced here:
    - Minutes are never zero-padded (shown as space when <10).
    - The tens-of-seconds digit is not zero-padded (space when seconds<10).
    - The ones-of-seconds digit is always shown.
    - While the race is running CTS emits only one decimal digit (tenths);
      the hundredths digit appears only when the time is final.
    """
    m = int(seconds) // 60
    s = seconds - m * 60
    if final:
        secs_str = '%5.2f' % s   # e.g. '15.87' or ' 5.23'
    else:
        secs_str = '%4.1f' % s   # e.g. '15.8' or ' 5.2'
    if m > 0:
        out = '%d:%s' % (m, secs_str)
    else:
        out = secs_str
    return out.rjust(8)

@socketio.on('sim_load_event', namespace='/scoreboard')
def ws_sim_load_event(d=None):
    """Populate event_info, time_standards, and records with test data."""
    global last_event_sent, time_standards, swim_record_sets

    from hytek_st2_parser import St2File, St2Header, St2Event, CourseStandards, QualifyingTime, TimeStandard
    from hytek_rec_parser import RecFile, RecHeader, SwimRecord
    from datetime import date

    ev_num = 99
    heat_num = 1

    # --- Populate event_info ---
    event_info.event_names[ev_num] = "Mixed 8 & Under 25 Yard Freestyle"
    event_info.events[(ev_num, heat_num)] = {}
    event_info.teams[(ev_num, heat_num)] = {}
    event_info.age_codes[(ev_num, heat_num)] = {}
    event_info.seed_times[(ev_num, heat_num)] = {}

    for lane, data in SIM_LANES.items():
        event_info.events[(ev_num, heat_num)][lane] = data['name']
        event_info.teams[(ev_num, heat_num)][lane] = data['team']
        event_info.age_codes[(ev_num, heat_num)][lane] = data['age_code']
        event_info.seed_times[(ev_num, heat_num)][lane] = data['seed']

    # Lane 3 is clock channel — no swimmer
    #for d_key in (event_info.events, event_info.teams, event_info.age_codes):
    #    d_key.setdefault((ev_num, heat_num), {})[3] = ""
    #event_info.seed_times.setdefault((ev_num, heat_num), {})[3] = None

    event_info.event_meta[ev_num] = {
        'stroke_code': 1,
        'distance': 25,
        'relay': False,
        'age_min': None,
        'age_max': 8,
        'sex_codes': [1, 2],
        'is_mixed': True,
        'gender_age': GenderAge.BOY_S,
    }
    event_info.has_names = True

    # --- Fake time standards: Boys A=16.00 B=18.00, Girls A=17.00 B=19.00 ---
    std_a = TimeStandard(tag='A', description='A Time')
    std_b = TimeStandard(tag='B', description='B Time')

    def _make_st2_event(sex_code, a_secs, b_secs):
        return St2Event(
            event_number=0, sex='Male' if sex_code == 1 else 'Female',
            sex_code=sex_code, stroke='Freestyle', stroke_code=1,
            distance=25, age_group_min=None, age_group_max=8,
            event_type='Individual',
            courses=[CourseStandards(course='SCY', times=[
                QualifyingTime(standard=std_a, time_seconds=a_secs, time_formatted=_format_lane_time(a_secs).strip()),
                QualifyingTime(standard=std_b, time_seconds=b_secs, time_formatted=_format_lane_time(b_secs).strip()),
            ])]
        )

    time_standards = St2File(
        header=St2Header(record_count=2, export_date=date.today(), standards=[std_a, std_b]),
        events=[_make_st2_event(1, 16.00, 18.00), _make_st2_event(2, 17.00, 19.00)]
    )

    # --- Fake records: Boys 15.50, Girls 16.00 ---
    def _make_record(sex_code, secs, swimmer, team, year):
        return SwimRecord(
            sex='Male' if sex_code == 1 else 'Female', sex_code=sex_code,
            stroke='Freestyle', stroke_code=1, distance=25,
            age_group_min=None, age_group_max=8, event_type='Individual',
            swimmer_name=swimmer, team=team, relay_names=None,
            record_date=date(year, 1, 1), time_seconds=secs,
            time_formatted=_format_lane_time(secs).strip(),
            record_team=team, entry_type='A20'
        )

    swim_record_sets = [
        {
            'rec_file': RecFile(
                header=RecHeader(course='SCY', course_code='Y', record_set_name='Midlakes Records',
                                 software_version='SIM', record_count=2, export_date=date.today()),
                records=[
                    _make_record(1, 15.50, 'Jimmy Fast', 'MIDL', 2024),
                    _make_record(2, 16.00, 'Sally Swift', 'MIDL', 2023),
                ]
            ),
            'filename': 'sim_midlakes_records.rec',
            'team_tag': 'ALL',
            'set_id': 999,
        },
        {
            'rec_file': RecFile(
                header=RecHeader(course='SCY', course_code='Y', record_set_name='Home Team',
                                 software_version='SIM', record_count=2, export_date=date.today()),
                records=[
                    _make_record(1, 15.75, 'Henry Home', 'HOME', 2022),
                ]
            ),
            'filename': 'sim_home_records.rec',
            'team_tag': 'HOME',
            'set_id': 1000,
        },
        {
            'rec_file': RecFile(
                header=RecHeader(course='SCY', course_code='Y', record_set_name='Away Team',
                                 software_version='SIM', record_count=2, export_date=date.today()),
                records=[
                    _make_record(1, 14.75, 'Andrew Away', 'AWAY', 2023),
                    _make_record(2, 16.40, 'Amy Away', 'AWAY', 2024),
                ]
            ),
            'filename': 'sim_away_records.rec',
            'team_tag': 'AWAY',
            'set_id': 1001,
        },
    ]

    # --- Set scores ---
    team_scores['score_home'] = ' 142'
    team_scores['score_guest1'] = ' 138'
    team_scores['score_guest2'] = ''
    team_scores['score_guest3'] = ''

    # --- Trigger PreRace ---
    last_event_sent = (ev_num, heat_num)
    race_fsm.notify_event_change()
    # Transition out of blank states since we now have lane data
    race_fsm.trigger('show_lanes')
    send_event_info()
    send_scores_info()


@socketio.on('sim_step', namespace='/scoreboard')
def ws_sim_step(d):
    """Advance the simulation through race phases."""
    global channel_running, running_time, _sim_running

    step = d.get('step', '') if d else ''
    update = {}
    num_lanes = settings.get('num_lanes', 8)

    if step == 'start':
        _sim_running = True
        running_time = _format_lane_time(0.0, final=False)
        update['running_time'] = running_time
        update['current_event'] = str(last_event_sent[0])
        update['current_heat'] = str(last_event_sent[1])
        for i in range(1, num_lanes + 1):
            channel_running[i - 1] = True
            update['lane_running%d' % i] = True
            update['lane_time%d' % i] = running_time
            update['lane_place%d' % i] = ' '

        race_fsm.evaluate_update(channel_running, update)
        update['race_state'] = race_fsm.state_name
        socketio.emit('update_scoreboard', update, namespace='/scoreboard')
        # Re-send scores in case we were previously in a blank state
        send_scores_info()

        # Start background clock ticker
        socketio.start_background_task(_sim_clock_tick)

    elif step == 'finish':
        _sim_running = False
        update['current_event'] = str(last_event_sent[0])
        update['current_heat'] = str(last_event_sent[1])
        for i in range(1, num_lanes + 1):
            channel_running[i - 1] = False
            update['lane_running%d' % i] = False
            if i in SIM_LANES:
                update['lane_time%d' % i] = _format_lane_time(SIM_LANES[i]['time'])
                update['lane_place%d' % i] = SIM_LANES[i]['place']
            else:
                update['lane_time%d' % i] = '        '
                update['lane_place%d' % i] = ' '

        race_fsm.evaluate_update(channel_running, update)
        update['race_state'] = race_fsm.state_name
        socketio.emit('update_scoreboard', update, namespace='/scoreboard')
        # Re-send scores in case we were previously in a blank state
        send_scores_info()

    elif step == 'clear':
        update['current_event'] = str(last_event_sent[0])
        update['current_heat'] = str(last_event_sent[1])
        for i in range(1, num_lanes + 1):
            update['lane_time%d' % i] = '        '
            update['lane_place%d' % i] = ' '

        race_fsm.evaluate_update(channel_running, update)
        update['race_state'] = race_fsm.state_name
        socketio.emit('update_scoreboard', update, namespace='/scoreboard')
        # Re-send event info so names/teams reappear on scoreboard
        send_event_info()
        send_scores_info()

    elif step == 'blank':
        _sim_running = False
        update['current_event'] = '   '
        update['current_heat'] = '   '
        for i in range(1, num_lanes + 1):
            channel_running[i - 1] = False
            update['lane_running%d' % i] = False
        # Lane 3 still shows clock
        for i in range(1, num_lanes + 1):
            if i == 3:
                update['lane_time%d' % i] = '    5:22'
            else:
                update['lane_time%d' % i] = '        '
            update['lane_place%d' % i] = ' '

        # CTS blanks team scores when going Blank; emit empty values but keep
        # the server-side team_scores cache intact so we can restore on exit.
        update['score_home'] = ''
        update['score_guest1'] = ''
        update['score_guest2'] = ''
        update['score_guest3'] = ''

        race_fsm.evaluate_update(channel_running, update)
        update['race_state'] = race_fsm.state_name
        socketio.emit('update_scoreboard', update, namespace='/scoreboard')

    elif step == 'total_blank':
        _sim_running = False
        update['current_event'] = '   '
        update['current_heat'] = '   '
        for i in range(1, num_lanes + 1):
            channel_running[i - 1] = False
            update['lane_running%d' % i] = False
            update['lane_time%d' % i] = '        '
            update['lane_place%d' % i] = ' '

        # CTS blanks team scores when going TotalBlank; emit empty values but
        # keep the server-side team_scores cache intact for restoration.
        update['score_home'] = ''
        update['score_guest1'] = ''
        update['score_guest2'] = ''
        update['score_guest3'] = ''

        race_fsm.evaluate_update(channel_running, update)
        update['race_state'] = race_fsm.state_name
        socketio.emit('update_scoreboard', update, namespace='/scoreboard')


def _sim_clock_tick():
    """Background task: increment running_time and emit to running lanes."""
    global running_time
    t = 0.0
    while _sim_running:
        socketio.sleep(0.1)
        if not _sim_running:
            break
        t += 0.1
        running_time = _format_lane_time(t, final=False)
        tick_update = {'running_time': running_time}
        num_lanes = settings.get('num_lanes', 8)
        for i in range(1, num_lanes + 1):
            if channel_running[i - 1]:
                tick_update['lane_time%d' % i] = running_time
        socketio.emit('update_scoreboard', tick_update, namespace='/scoreboard')

        

# ---------------------------------------------------------------------------
# Debug simulator (Settings > Debug): drive the boards and the OBS overlay
# from the loaded start lists without a console. Real console data, if any
# arrives, still wins - unplug the console or expect the two to fight.
# ---------------------------------------------------------------------------
import random as _random

_dbg = {'token': 0, 'auto': False, 'speed': 4.0, 'status': 'idle',
        'seeds': 'mixed'}   # mixed | beat (everyone drops time) | miss (nobody does)
DBG_SEED_MODES = ('mixed', 'beat', 'miss')

_TYPICAL_SECONDS = {25: 15, 50: 30, 100: 66, 200: 140, 400: 300, 500: 370,
                    800: 610, 1000: 760, 1500: 1150, 1650: 1260}

def _dbg_emit(update):
    update['current_event'] = str(last_event_sent[0])
    update['current_heat'] = str(last_event_sent[1])
    update['race_state'] = race_fsm.state_name
    socketio.emit('update_scoreboard', update, namespace='/scoreboard')

def _dbg_blank_lanes(update):
    global running_time
    running_time = '        '
    update['running_time'] = ''
    for i in range(1, 11):
        channel_running[i - 1] = False
        update['lane_running%d' % i] = False
        update['lane_time%d' % i] = '        '
        update['lane_place%d' % i] = ' '

def _dbg_occupied_lanes():
    e, h = last_event_sent
    return [l for l in range(1, 11)
            if event_info.get_display_string(e, h, l) or event_info.get_team_code(e, h, l)]

def debug_goto(event, heat):
    """Put a heat on the blocks: start list showing, lanes blank."""
    global last_event_sent
    _dbg['token'] += 1                      # cancels a race in progress
    last_event_sent = (int(event), int(heat))
    race_fsm.to_PreRace()
    update = {}
    _dbg_blank_lanes(update)
    send_event_info()
    _dbg_emit(update)
    _dbg['status'] = 'Event %d heat %d on the blocks' % last_event_sent

def debug_step(delta):
    order = meet_board.heat_order(event_info)
    if not order:
        return False
    cur = last_event_sent
    if cur in order:
        i = order.index(cur) + delta
    else:
        i = next((n for n, k in enumerate(order) if k > cur), len(order)) + (delta - 1 if delta > 0 else delta)
    i = max(0, min(len(order) - 1, i))
    debug_goto(*order[i])
    return True

def _dbg_finish_times():
    """A plausible time per swimmer: seed time if we have one, otherwise a
    typical time for the distance, spread +/- 8%."""
    e, h = last_event_sent
    meta = event_info.event_meta.get(e, {}) or {}
    dist = meta.get('distance') or 100
    base = _TYPICAL_SECONDS.get(dist, dist * 0.7)
    if meta.get('relay'):
        base *= 0.92
    out = {}
    for lane in _dbg_occupied_lanes():
        seed = event_info.get_seed_time(e, h, lane)
        mode = _dbg.get('seeds', 'mixed') if seed else 'mixed'
        lo, hi = {'beat': (0.95, 0.995), 'miss': (1.005, 1.06)}.get(mode, (0.94, 1.08))
        t = (seed or base) * _random.uniform(lo, hi)
        out[lane] = round(t, 2)
    return out

def _dbg_race(token):
    global running_time
    finishes = _dbg_finish_times()
    order = sorted(finishes, key=lambda l: finishes[l])
    place = {l: i + 1 for i, l in enumerate(order)}
    done = set()
    t = 0.0
    step = 0.1
    while _dbg['token'] == token:
        socketio.sleep(step)
        if _dbg['token'] != token:
            return
        t += step * _dbg['speed']
        if _dbg.pop('finish_now', False) and finishes:
            t = max(t, max(finishes.values()))
        update = {}
        running_time = _format_lane_time(t, final=False)
        update['running_time'] = running_time
        for lane in range(1, 11):
            if lane in finishes and lane not in done and t >= finishes[lane]:
                done.add(lane)
                channel_running[lane - 1] = False
                update['lane_running%d' % lane] = False
                update['lane_time%d' % lane] = _format_lane_time(finishes[lane])
                update['lane_place%d' % lane] = str(place[lane])
            elif lane in finishes and lane not in done:
                update['lane_time%d' % lane] = running_time
        if len(done) == len(finishes):
            race_fsm.to_Finished()
            update['running_time'] = _format_lane_time(max(finishes.values())) if finishes else ''
            _dbg_emit(update)
            _dbg['status'] = 'Event %d heat %d finished' % last_event_sent
            return
        _dbg_emit(update)

def debug_start():
    _dbg['token'] += 1
    token = _dbg['token']
    race_fsm.to_Running()
    update = {}
    for lane in _dbg_occupied_lanes():
        channel_running[lane - 1] = True
        update['lane_running%d' % lane] = True
        update['lane_place%d' % lane] = ' '
    _dbg_emit(update)
    _dbg['status'] = 'Event %d heat %d racing' % last_event_sent
    socketio.start_background_task(_dbg_race, token)

def debug_finish_now():
    """Everyone touches now."""
    if race_fsm.state_name != 'Running':
        debug_start()
    _dbg['finish_now'] = True

def debug_clear():
    _dbg['token'] += 1
    race_fsm.to_Clear()
    update = {}
    _dbg_blank_lanes(update)
    _dbg_emit(update)
    _dbg['status'] = 'Lanes cleared'

def debug_blank():
    _dbg['token'] += 1
    _dbg['auto'] = False
    race_fsm.to_TotalBlank()
    update = {}
    _dbg_blank_lanes(update)
    update['current_event'] = '   '
    update['current_heat'] = '   '
    update['race_state'] = race_fsm.state_name
    socketio.emit('update_scoreboard', update, namespace='/scoreboard')
    _dbg['status'] = 'Board blank'

def _dbg_auto_loop():
    while _dbg['auto']:
        if not debug_step(+1) and not event_info.events:
            _dbg['status'] = 'Auto-run needs start lists'
            _dbg['auto'] = False
            return
        for _ in range(60):                  # 6 s on the blocks
            socketio.sleep(0.1)
            if not _dbg['auto']:
                return
        debug_start()
        while _dbg['auto'] and race_fsm.state_name == 'Running':
            socketio.sleep(0.2)
        for _ in range(80):                  # 8 s of results
            socketio.sleep(0.1)
            if not _dbg['auto']:
                return
        order = meet_board.heat_order(event_info)
        if order and last_event_sent == order[-1]:
            _dbg['status'] = 'Auto-run reached the last heat'
            _dbg['auto'] = False
            return

@app.route('/debug')
@flask_login.login_required
def route_debug_page():
    """Troubleshooting: serial diagnostics and the console-free meet simulator."""
    return flask.render_template('debug.html')


@app.route('/debug/<action>', methods=['POST'])
@flask_login.login_required
def route_debug(action):
    data = flask.request.get_json(silent=True) or flask.request.form or {}
    if 'speed' in data:
        try:
            _dbg['speed'] = max(0.5, min(50.0, float(data['speed'])))
        except (TypeError, ValueError):
            pass
    if data.get('seeds') in DBG_SEED_MODES:
        _dbg['seeds'] = data['seeds']
    if action == 'goto':
        try:
            debug_goto(int(data.get('event')), int(data.get('heat', 1)))
        except (TypeError, ValueError):
            return flask.jsonify({'ok': False, 'message': 'event and heat must be numbers'}), 400
    elif action == 'next':
        debug_step(+1)
    elif action == 'prev':
        debug_step(-1)
    elif action == 'startlist':
        debug_goto(*last_event_sent)
    elif action == 'start':
        debug_start()
    elif action == 'finish':
        debug_finish_now()
    elif action == 'clear':
        debug_clear()
    elif action == 'blank':
        debug_blank()
    elif action == 'auto':
        on = str(data.get('on', '1')).lower() in ('1', 'true', 'on', 'yes')
        if on and not _dbg['auto']:
            _dbg['auto'] = True
            _dbg['status'] = 'Auto-run started'
            socketio.start_background_task(_dbg_auto_loop)
        elif not on:
            _dbg['auto'] = False
            _dbg['status'] = 'Auto-run stopped'
    elif action != 'status':
        return flask.jsonify({'ok': False, 'message': 'unknown action'}), 404
    return flask.jsonify(debug_status())

def debug_status():
    e, h = last_event_sent
    return {'ok': True, 'event': e, 'heat': h, 'event_name': event_info.get_event_name(e),
            'race_state': race_fsm.state_name, 'auto': _dbg['auto'], 'speed': _dbg['speed'],
            'seeds': _dbg.get('seeds', 'mixed'),
            'status': _dbg['status'], 'heats': [list(k) for k in meet_board.heat_order(event_info)],
            'replay': os.path.basename(in_file) if in_file else ''}


# ---------------------------------------------------------------------------
# Music tab: playlist in DATA_DIR/music, played by /music?speaker=1 on the
# streaming PC, controlled from any /music page.
# ---------------------------------------------------------------------------
music_state = music.MusicState()

def _music_folder():
    return music.music_dir(app_paths.data_path)

def _music_songs():
    return music.list_songs(_music_folder())

def _warmup_folder():
    return music.warmup_dir(app_paths.data_path)

def _warmup_songs():
    # Not anthem-pinned and a much larger cap: this list is shuffled, and a
    # warm-up runs far longer than the meet playlist.
    return music.list_songs(_warmup_folder(), limit=music.MAX_WARMUP, anthem_first=False)

def _warmup_files():
    return [x['file'] for x in _warmup_songs()]

def _playing_songs():
    """The list the currently playing track belongs to."""
    return _warmup_songs() if music_state.mode == 'warmup' else _music_songs()

stream_mute = music_mute.StreamMute(obs, settings, lambda: save_settings())
screens_black = blackout_mod.Blackout(settings, lambda: save_settings())
_music_last_status = [None]


def _music_state_dict(songs=None, warmup=None):
    d = music_state.as_dict(songs, warmup)
    d['stream_mute'] = stream_mute.status()
    d['mic'] = music.mic_merge(settings.get('mic'), {})
    d['mic_devices'] = _mic_runtime['devices']
    d['mic_status'] = _mic_runtime['status']
    return d


# what the speaker tab reports about its mic (not saved)
_mic_runtime = {'devices': [], 'status': ''}


def _music_mute_status():
    """Music status as the stream mute sees it: a song marked 'OK on stream'
    (e.g. a public-domain anthem recording) counts as no music playing."""
    if music_state.status != 'playing':
        return music_state.status
    # Look in whichever list is playing. A warm-up track that isn't in the meet
    # playlist must not fall through as "unknown" - it would still mute, but a
    # warm-up song you marked OK on stream has to be honoured too.
    song = next((x for x in _playing_songs() if x['file'] == music_state.track), None)
    return 'stopped' if song and song.get('stream_ok') else 'playing'


def _music_broadcast(include_songs=False):
    # Tell OBS first (muting must not lag the song), off the socket handler thread
    key = (music_state.status, music_state.track)
    if key != _music_last_status[0]:
        _music_last_status[0] = key
        socketio.start_background_task(_stream_mute_then_broadcast, _music_mute_status())
    socketio.emit('music_state',
                  _music_state_dict(_music_songs() if include_songs else None,
                                    _warmup_songs() if include_songs else None),
                  namespace='/music')


def _stream_mute_then_broadcast(status):
    stream_mute.music_changed(status)
    socketio.emit('music_state', _music_state_dict(), namespace='/music')   # badge shows the real OBS state


def blackout_worker():
    """Turns the TVs black once the meet has been idle long enough."""
    while True:
        socketio.sleep(5.0)
        try:
            _blackout_broadcast()
        except Exception:
            traceback.print_exc()
        try:
            warmup_auto_off()
        except Exception:
            traceback.print_exc()


def warmup_auto_off(now=None):
    """At the meet's start time the warm-up slide comes down and the boards show the first heat.
    Once per meet day: turning the slide back on afterwards keeps it up. Returns True when it fired."""
    global last_event_sent
    if not settings.get('warmup_slide_on'):
        return False
    m = _HHMM.match(settings.get('warmup_start') or '')
    if not m:
        return False
    now = now or datetime.datetime.now()
    day = now.strftime('%Y-%m-%d')
    meet_day = str((settings.get('schedule_meet') or {}).get('date') or '')[:10]
    if meet_day and meet_day != day:
        return False
    start = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    key = '%s %s' % (day, settings['warmup_start'])
    if now < start or settings.get('warmup_auto_done') == key:
        return False
    settings['warmup_slide_on'] = False
    settings['warmup_auto_done'] = key
    save_settings()
    order = meet_board.heat_order(event_info)
    if order and not race_book.racing:
        last_event_sent = order[0]           # the console takes over as soon as it sends a heat
        send_event_info()
    _warmup_broadcast()
    print('Meet start time %s: warm-up slide off, showing event %s heat %s' % (settings['warmup_start'], *(order[0] if order else ('-', '-'))))
    return True


def _warmup_slide():
    """Payload for the warm-up slide (meet, teams, start time)."""
    meet_teams = meet_board.schedule_teams(event_info)
    teams = meet_board.slide_teams(settings, meet_teams, _team_names(), _meet_logos(meet_teams))
    out = meet_board.slide_payload(settings, getattr(event_info, 'meet_info', None) or {}, teams)
    # The full start list rides along only while the slide is up: no point
    # sending every swimmer to every TV on each heat change during the meet.
    if out['on']:
        out['events'] = meet_board.slide_events(event_info, int(settings.get('num_lanes', 8) or 8))
        # STAN and SHS are one school: the swimmers-by-school pages group them together
        out['school_of'] = meet_board.canonical_codes(settings, meet_teams, _team_names())
        out['colors'] = {c: (v.get('color') or '') for c, v in
                         meet_board.team_colors(settings, meet_teams, _team_names()).items()}
        # phone results QR (the page also has the YouTube live link to pass on)
        if settings.get('results_qr') != 'off':
            out['results_url'] = (settings.get('results_url') or '').strip()
            out['stream'] = bool((settings.get('youtube_channel') or '').strip())
    return out


def _warmup_broadcast():
    try:
        _socketio_emit('warmup_slide', _warmup_slide(), namespace='/scoreboard')
    except Exception:
        traceback.print_exc()


_HHMM = re.compile(r'^([01]?\d|2[0-3]):([0-5]\d)$')


@app.route('/api/warmup_slide', methods=['GET', 'POST'])
def route_api_warmup_slide():
    """GET: what the warm-up slide shows. POST any of {on, title, subtitle, start, speed, hold}."""
    if flask.request.method == 'POST':
        if not app.config.get('LOGIN_DISABLED') and not flask_login.current_user.is_authenticated:
            return flask.jsonify({'error': 'login required'}), 401
        d = flask.request.get_json(silent=True) or {}
        if 'start' in d:
            start = str(d.get('start') or '').strip()
            m = _HHMM.match(start)
            if start and not m:
                return flask.jsonify({'error': 'start time must be HH:MM'}), 400
            settings['warmup_start'] = '%02d:%s' % (int(m.group(1)), m.group(2)) if m else ''
        for k in ('title', 'subtitle'):
            if k in d:
                settings['warmup_' + k] = str(d.get(k) or '').strip()[:120]
        for k, fn in (('speed', meet_board.slide_speed), ('hold', meet_board.slide_hold)):
            if k in d:
                try:
                    float(d[k])
                except (TypeError, ValueError):
                    return flask.jsonify({'error': k + ' must be a number'}), 400
                settings['warmup_' + k] = fn({'warmup_' + k: d[k]})
        if 'on' in d:
            settings['warmup_slide_on'] = bool(d['on'])
        save_settings()
        _warmup_broadcast()
    out = _warmup_slide()
    meet_info = getattr(event_info, 'meet_info', None) or {}
    out['defaults'] = {'title': settings.get('meet_title') or meet_info.get('name') or '',
                       'subtitle': meet_info.get('facility') or ''}
    out['saved'] = {'title': settings.get('warmup_title', ''), 'subtitle': settings.get('warmup_subtitle', ''),
                    'start': settings.get('warmup_start', '')}
    out['limits'] = {'speed': [meet_board.SPEED_MIN, meet_board.SPEED_MAX, meet_board.SPEED_DEFAULT],
                     'hold': [meet_board.HOLD_MIN, meet_board.HOLD_MAX, meet_board.HOLD_DEFAULT]}
    return flask.jsonify(out)


@app.route('/state_times')
@flask_login.login_required
def route_state_times():
    return flask.render_template('state_times.html')


@app.route('/api/state_cuts', methods=['GET', 'POST'])
def route_api_state_cuts():
    """GET: last season's state cuts per classification and each team's class.
    POST any of {on, label, cls + times: {key: '1:52.34'}, cls + from_tag,
    cls + reset (back to the built-in state meet times), team_class: {code: cls}}."""
    if flask.request.method == 'POST':
        if not app.config.get('LOGIN_DISABLED') and not flask_login.current_user.is_authenticated:
            return flask.jsonify({'error': 'login required'}), 401
        d = flask.request.get_json(silent=True) or {}
        cuts = json.loads(json.dumps(_state_cuts()))   # deep copy
        tables = cuts.setdefault('classes', {})
        cls = str(d.get('cls') or '')
        if ('times' in d or 'from_tag' in d or 'reset' in d) and cls not in meet_board.CLASSES:
            return flask.jsonify({'error': 'Pick a classification'}), 400
        if 'times' in d:
            valid = {meet_board.cut_key(sex, *e[:3]) for e in meet_board.HS_EVENTS for sex in (1, 2)}
            times, bad = dict(tables.get(cls) or {}), []
            for k, v in (d.get('times') or {}).items():
                if k not in valid:
                    continue
                try:
                    secs = meet_board.parse_swim_time(v)
                except ValueError:
                    bad.append(str(v))
                    continue
                if secs is None:
                    times.pop(k, None)
                else:
                    times[k] = secs
            if bad:
                return flask.jsonify({'error': 'Not a time: ' + ', '.join(bad[:3]) + ' (use 1:52.34 or 23.45)'}), 400
            tables[cls] = times
        if d.get('from_tag'):
            got = _state_cuts_from_st2(str(d['from_tag']))
            if not got:
                return flask.jsonify({'error': 'No high school event times for that standard in the loaded .st2'}), 400
            tables[cls] = dict(tables.get(cls) or {}, **got)
        if d.get('reset'):
            builtin = meet_board.builtin_state_cuts()
            if cls in builtin:
                tables[cls] = builtin[cls]
            else:
                tables.pop(cls, None)
        if isinstance(d.get('team_class'), dict):
            over = cuts.setdefault('team_class', {})
            for code, c in d['team_class'].items():
                code = str(code).strip().upper()[:12]
                c = str(c or '')
                if not code or (c and c not in meet_board.CLASSES and c != 'none'):
                    continue
                if c == meet_board.DEFAULT_TEAM_CLASS.get(code, '') or (not c and code not in meet_board.DEFAULT_TEAM_CLASS):
                    over.pop(code, None)
                else:
                    over[code] = c if c != 'none' else ''
        if 'fallback_class' in d:
            fb = str(d.get('fallback_class') or '')
            if fb and fb not in meet_board.CLASSES and fb != meet_board.FALLBACK_HOST:
                return flask.jsonify({'error': 'Unknown classification'}), 400
            cuts['fallback_class'] = fb
        if 'label' in d:
            cuts['label'] = str(d.get('label') or '').strip()[:30]
        if 'on' in d:
            cuts['on'] = bool(d['on'])
        if 'usc_board' in d:
            settings['usc_board'] = bool(d['usc_board'])
        settings['state_cuts'] = cuts
        save_settings()
        send_event_info()
    return flask.jsonify(_state_cuts_view())


# ---------------------------------------------------------------- notice slide ("We need timers")
NOTICE_DEFAULT = {'title': 'We need timers!', 'text': 'Can you help? Please come to the timing table. Thank you!'}


def _notice():
    n = settings.get('notice') or {}
    return {'on': bool(n.get('on')), 'title': (n.get('title') or NOTICE_DEFAULT['title'])[:80],
            'text': (n.get('text') if n.get('text') is not None else NOTICE_DEFAULT['text'])[:240],
            'defaults': NOTICE_DEFAULT}


@app.route('/api/notice', methods=['GET', 'POST'])
def route_api_notice():
    """GET: the notice slide. POST any of {on, title, text} (admin only). Full screen on the meet board TVs."""
    if flask.request.method == 'POST':
        if not app.config.get('LOGIN_DISABLED') and not flask_login.current_user.is_authenticated:
            return flask.jsonify({'error': 'login required'}), 401
        d = flask.request.get_json(silent=True) or {}
        n = settings.setdefault('notice', {})
        if 'on' in d:
            n['on'] = bool(d['on'])
        for k in ('title', 'text'):
            if k in d:
                n[k] = str(d.get(k) or '').strip()
        if 'title' in d and not n['title']:
            n.pop('title')                     # blank title: back to "We need timers!"
        save_settings()
        _socketio_emit('notice', _notice(), namespace='/scoreboard')
    return flask.jsonify(_notice())


# ---------------------------------------------------------------- break slide (between sessions)
# "Break: results so far" on the meet board TVs - team scores, the top three of every event swum, and the events
# still to come. Shown by hand (tab bar / Settings > Screens), or automatically a couple of minutes after the
# last heat of the session's last event (Settings: after event N). It comes down by itself when a race starts.
BREAK = {'on': False, 'timer': None, 'fired_for': None, 'final': False}
_BREAK_RES = {'at': 0.0, 'url': '', 'data': None}


def _break_cfg():
    b = settings.setdefault('break_slide', {})
    b.setdefault('after', 8)
    b.setdefault('auto', False)
    b.setdefault('delay', 120)
    return b


def _break_results():
    """The public results page's results.json (team scores, every event's results), cached 30 s."""
    url = (settings.get('results_url') or '').strip()
    if not url:
        return None
    now = time.time()
    if _BREAK_RES['data'] is not None and _BREAK_RES['url'] == url and now - _BREAK_RES['at'] < 30:
        return _BREAK_RES['data']
    try:
        import urllib.request as _ur
        req = _ur.Request(url.rstrip('/') + '/results.json?t=%d' % int(now), headers={'Cache-Control': 'no-cache', 'User-Agent': 'meet-board'})
        with _ur.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode('utf-8'))
        _BREAK_RES.update(at=now, url=url, data=data)
    except Exception as e:
        print('break slide: results.json:', e)
        _BREAK_RES['at'] = now - 20            # try again in 10 s
    return _BREAK_RES['data']


def _break_slide():
    b = _break_cfg()
    out = {'on': BREAK['on'], 'final': BREAK['final'], 'after': b['after'], 'auto': bool(b['auto']), 'delay': b['delay'],
           'final_auto': bool(b.get('final_auto', True)), 'pending': bool(BREAK['timer'])}
    if not BREAK['on']:
        return out
    res = _break_results() or {}
    out['meet'] = (res.get('meet') or {}).get('name') if isinstance(res.get('meet'), dict) else (res.get('meet') or '')
    out['scores'] = [{'name': x.get('name', ''), 'code': x.get('code') or x.get('team') or '', 'total': x.get('total', 0)}
                     for x in (res.get('scores') or [])]
    done, coming = [], []
    for ev in res.get('events') or []:
        rows = [r for r in ev.get('rows') or [] if r.get('place') and not r.get('exh')]
        if rows:
            top = sorted(rows, key=lambda r: int(r.get('place') or 99))[:3]
            done.append({'no': ev.get('no'), 'label': ev.get('label', ''),
                         'top': [{'place': r.get('place'), 'name': r.get('name', ''), 'team': r.get('team', ''), 'time': r.get('result', '')} for r in top]})
        elif not ev.get('rows'):
            coming.append({'no': ev.get('no'), 'label': ev.get('label', '')})
    if not res:                                # no results page: at least list what's left from the start lists
        try:
            seen = set()
            for e, h in meet_board.heat_order(event_info):
                if e > int(b['after']) and e not in seen:
                    seen.add(e)
                    meta = (getattr(event_info, 'event_meta', {}) or {}).get(e) or {}
                    coming.append({'no': e, 'label': announce.event_label(meta) if meta else event_info.get_event_name(e)})
        except Exception:
            pass
    out.update(done=done, coming=coming)
    return out


def _break_emit():
    _socketio_emit('break_slide', _break_slide(), namespace='/scoreboard')


def _break_set(on, final=False):
    if BREAK['timer']:
        try:
            BREAK['timer'].cancel()
        except Exception:
            pass
        BREAK['timer'] = None
    BREAK['on'] = bool(on)
    BREAK['final'] = bool(on and final)
    _break_emit()


def _final_race_over(e, h):
    """After the last heat of the meet's last event: final results scrolling on the meet boards."""
    b = _break_cfg()
    if not b.get('final_auto', True) or BREAK['on'] or BREAK['fired_for'] == ('final', e, h):
        return False
    order = meet_board.heat_order(event_info)
    if not order or (e, h) != tuple(order[-1]):
        return False
    BREAK['fired_for'] = ('final', e, h)

    def fire():
        BREAK['timer'] = None
        BREAK['on'], BREAK['final'] = True, True
        _BREAK_RES['at'] = 0                     # fresh results
        print('Final results slide: on (after event %d heat %d)' % (e, h))
        _break_emit()
    BREAK['timer'] = threading.Timer(30, fire)  # give MeetDeck a moment to score and publish the last event
    BREAK['timer'].daemon = True
    BREAK['timer'].start()
    _break_emit()
    return True


def _break_race_over(event, heat):
    """Arm the automatic break slide after the last heat of the 'after' event."""
    b = _break_cfg()
    try:
        e, h = int(event), int(heat)
    except (TypeError, ValueError):
        return
    try:
        if _final_race_over(e, h):
            return
    except Exception as x:
        print('final results slide:', x)
    if not b.get('auto') or e != int(b.get('after') or 0) or BREAK['on'] or BREAK['fired_for'] == (e, h):
        return
    try:
        last = meet_board.heat_count(event_info, e) or h
    except Exception:
        last = h
    if h < last:
        return
    BREAK['fired_for'] = (e, h)

    def fire():
        BREAK['timer'] = None
        BREAK['on'] = True
        print('Break slide: on (after event %d heat %d)' % (e, h))
        _break_emit()
    BREAK['timer'] = threading.Timer(max(0, int(b['delay'] if b.get('delay') is not None else 120)), fire)
    BREAK['timer'].daemon = True
    BREAK['timer'].start()
    _break_emit()


@app.route('/api/break_slide', methods=['GET', 'POST'])
def route_api_break_slide():
    """GET: the break slide. POST any of {on, after, auto, delay} (admin only)."""
    if flask.request.method == 'POST':
        if not app.config.get('LOGIN_DISABLED') and not flask_login.current_user.is_authenticated:
            return flask.jsonify({'error': 'login required'}), 401
        d = flask.request.get_json(silent=True) or {}
        b = _break_cfg()
        if 'after' in d:
            try:
                b['after'] = max(1, min(999, int(d['after'])))
            except (TypeError, ValueError):
                return flask.jsonify({'error': 'after must be an event number'}), 400
            BREAK['fired_for'] = None
        if 'auto' in d:
            b['auto'] = bool(d['auto'])
            if not b['auto'] and BREAK['timer']:
                BREAK['timer'].cancel()
                BREAK['timer'] = None
        if 'delay' in d:
            try:
                b['delay'] = max(0, min(1800, int(d['delay'])))
            except (TypeError, ValueError):
                return flask.jsonify({'error': 'delay must be seconds'}), 400
        if 'final_auto' in d:
            b['final_auto'] = bool(d['final_auto'])
        save_settings()
        if 'on' in d:
            _BREAK_RES['at'] = 0
            _break_set(d['on'], final=bool(d.get('final')))
        else:
            _break_emit()
    return flask.jsonify(_break_slide())


@app.route('/api/blackout', methods=['GET', 'POST'])
def route_api_blackout():
    """GET: go-black state. POST {black} or {auto, hours} (admin only)."""
    if flask.request.method == 'POST':
        if not app.config.get('LOGIN_DISABLED') and not flask_login.current_user.is_authenticated:
            return flask.jsonify({'error': 'login required'}), 401
        d = flask.request.get_json(silent=True) or {}
        try:
            if 'black' in d:
                screens_black.set_manual(bool(d['black']))
            if 'auto' in d or 'hours' in d:
                screens_black.configure(auto=d.get('auto'), hours=d.get('hours'))
        except (TypeError, ValueError):
            return flask.jsonify({'error': 'bad value'}), 400
        _blackout_broadcast(force=True)
    return flask.jsonify(screens_black.status())


def stream_mute_worker():
    """Delayed unmute after the music stops, and recovery after a restart."""
    while True:
        socketio.sleep(1.0)
        before = stream_mute.muted_by_us
        try:
            stream_mute.tick(_music_mute_status())
        except Exception:
            traceback.print_exc()
        if stream_mute.muted_by_us != before:
            socketio.emit('music_state', _music_state_dict(), namespace='/music')


@app.route('/api/music/stream_mute', methods=['GET', 'POST'])
@flask_login.login_required
def route_music_stream_mute():
    """GET: OBS audio sources + settings. POST {enabled, inputs, delay}: save."""
    if flask.request.method == 'POST':
        d = flask.request.get_json(silent=True) or {}
        settings['music_obs_mute'] = bool(d.get('enabled'))
        settings['music_obs_inputs'] = [str(x) for x in (d.get('inputs') or [])][:20]
        try:
            settings['music_unmute_delay'] = max(0.0, min(30.0, float(d.get('delay', 2))))
        except (TypeError, ValueError):
            pass
        save_settings()
        if music_state.status == 'playing':
            socketio.start_background_task(_stream_mute_then_broadcast, _music_mute_status())
    try:
        inputs, err = stream_mute.audio_inputs(), ''
    except Exception as e:
        inputs, err = [], str(e)
    return flask.jsonify(dict(stream_mute.status(), inputs=inputs, obs_error=err,
                              delay=float(settings.get('music_unmute_delay', 2.0) or 0)))


# ---------------------------------------------------------------- heat announcements
# "Event five. Women's 200 freestyle. Heat one. Please report to the blocks." stitched from
# recorded clips (announce.py). The Music page has one Announce button that always offers the
# next heat; optionally it fires by itself when a race is over.
_ann = {'announced': set(), 'swum': set(), 'override': None, 'last': None, 'last_text': '', 'seq': 0, 'timer': None,
        'now': None}     # what was said last (heat or one-off): {'title', 'text'}


def _ann_settings():
    a = settings.setdefault('announce', {})
    a.setdefault('auto', False)
    a.setdefault('delay', 5)
    a.setdefault('volume', 100)          # % - announcements have their own level, apart from the music
    a.setdefault('when', 'end')          # end: when the race is over | near: the last swimmer ~5 yards out (from splits)
    race_book.near_yards = 5 if (a.get('auto') and a.get('when') == 'near') else 0
    return a


def _ann_order():
    try:
        return meet_board.heat_order(event_info)
    except Exception:
        return []


def _ann_target():
    cur = (int(last_event_sent[0]), int(last_event_sent[1])) if last_event_sent else None
    return announce.pick_target(_ann_order(), cur, bool(race_book.racing), _ann['swum'], _ann['announced'], _ann['override'])


def _ann_pieces(key):
    e, h = key
    meta = (getattr(event_info, 'event_meta', {}) or {}).get(e) or {}
    last = _ann['last']
    # every heat says the event number and name, then the heat ("Event three. Girls 200 free. Heat two. ...")
    pieces = announce.build(e, h, meta_heats(e), meta, new_event=True)
    return announce.resolve(pieces, announce.clips_on_disk(_ann_folder()), meta), meta


def meta_heats(e):
    try:
        return meet_board.heat_count(event_info, e)
    except Exception:
        return 0


def _ann_folder():
    return announce.clip_folder(app_paths.data_path)


def _ann_view():
    a = _ann_settings()
    key = _ann_target()
    out = {'auto': bool(a.get('auto')), 'delay': a.get('delay', 5), 'when': a.get('when', 'end'), 'volume': a.get('volume', 100), 'target': None, 'last': None,
           'speaker': bool(music_state.speaker_sid), 'clips': len(announce.clips_on_disk(_ann_folder())),
           'clips_total': len(announce.clip_script()), 'pending': bool(_ann['timer'])}
    if key:
        pieces, meta = _ann_pieces(key)
        have = announce.clips_on_disk(_ann_folder())
        out['target'] = {'event': key[0], 'heat': key[1], 'heats': meta_heats(key[0]),
                         'name': announce.event_label(meta) if meta else event_info.get_event_name(key[0]),
                         'text': announce.text_of(pieces), 'missing': [p['clip'] for p in pieces if p['clip'] not in have],
                         'override': _ann['override'] == key}
    have = announce.clips_on_disk(_ann_folder())
    out['extras'] = [{'clip': k, 'label': lab, 'text': t, 'have': k in have} for k, lab, t in announce.EXTRAS]
    out['now'] = _ann['now']
    out['ago'] = int(time.time() - _ann['at']) if _ann.get('at') else None
    if _ann['last']:
        e, h = _ann['last']
        meta = (getattr(event_info, 'event_meta', {}) or {}).get(e) or {}
        try:
            name = announce.event_label(meta) if meta else event_info.get_event_name(e)
        except Exception:
            name = ''
        out['last'] = {'event': e, 'heat': h, 'heats': meta_heats(e), 'name': name or '', 'text': _ann.get('last_text', '')}
    return out


def _ann_broadcast():
    socketio.emit('announce_state', _ann_view(), namespace='/music')


def _ann_play_extra(clip):
    x = next((x for x in announce.EXTRAS if x[0] == clip), None)
    if not x:
        return 'unknown announcement'
    if not music_state.speaker_sid:
        return 'no speaker tab open (/music?speaker=1 on the pool PC)'
    k, label, text = x
    _ann['seq'] += 1
    item = {'url': '/announce/clip/%s.mp3' % k, 'say': text} if k in announce.clips_on_disk(_ann_folder()) else {'say': text}
    socketio.emit('announce_play', {'id': _ann['seq'], 'items': [item], 'text': text},
                  namespace='/music', to=music_state.speaker_sid)
    _ann['now'] = {'title': label, 'text': text}
    _ann['at'] = time.time()
    print('Announced %s: %s' % (label, text))
    _ann_broadcast()
    return None


def _ann_play(key, why='button'):
    if not key:
        return 'nothing left to announce'
    if not music_state.speaker_sid:
        return 'no speaker tab open (/music?speaker=1 on the pool PC)'
    pieces, meta = _ann_pieces(key)
    _ann['seq'] += 1
    have = announce.clips_on_disk(_ann_folder())
    items = [{'url': '/announce/clip/%s.mp3' % p['clip'], 'say': p['say']} if p['clip'] in have else {'say': p['say']}
             for p in pieces]
    socketio.emit('announce_play', {'id': _ann['seq'], 'items': items, 'text': announce.text_of(pieces)},
                  namespace='/music', to=music_state.speaker_sid)
    _ann['announced'].add(key)
    _ann['last'] = key
    _ann['last_text'] = announce.text_of(pieces)
    _ann['now'] = None
    _ann['at'] = time.time()
    _ann['override'] = None
    print('Announced %s/%s (%s): %s' % (key[0], key[1], why, announce.text_of(pieces)))
    _ann_broadcast()
    return None


def _ann_break_after(event, heat):
    """True after the last heat of the break event (Break slide > after event N, automatic on):
    the next heat is after the break, so it isn't announced by itself."""
    b = _break_cfg()
    try:
        e, h = int(event), int(heat)
        if not b.get('auto') or e != int(b.get('after') or 0):
            return False
        return h >= (meet_board.heat_count(event_info, e) or h)
    except Exception:
        return False


def _ann_near_end(event, heat):
    """The last swimmer is about 5 yards out (estimated from splits): announce the next heat now."""
    a = _ann_settings()
    try:
        k = (int(event), int(heat))
    except (TypeError, ValueError):
        return
    if not a.get('auto') or a.get('when') != 'near' or k in _ann['swum']:
        return
    if _ann_break_after(*k):
        print('Auto announce: break after event %d - not announcing the next heat' % k[0])
        return
    _ann['near_done'] = k
    err = _ann_play(_ann_target(), 'near the finish')
    if err:
        print('Auto announce skipped:', err)
        _ann_broadcast()


def _ann_race_over(event, heat):
    try:
        k = (int(event), int(heat))
    except (TypeError, ValueError):
        return
    if k in _ann['swum']:          # already handled when everyone touched
        return
    _ann['swum'].add(k)
    if _ann.get('near_done') == k:  # already announced as the last swimmer came in
        _ann_broadcast()
        return
    a = _ann_settings()
    if a.get('auto') and _ann_break_after(*k):
        print('Auto announce: break after event %d - not announcing the next heat' % k[0])
    elif a.get('auto'):
        if _ann['timer']:
            try:
                _ann['timer'].cancel()
            except Exception:
                pass

        def fire():
            _ann['timer'] = None
            err = _ann_play(_ann_target(), 'auto')
            if err:
                print('Auto announce skipped:', err)
                _ann_broadcast()
        _ann['timer'] = threading.Timer(max(0.0, float(a.get('delay', 5) or 0)), fire)
        _ann['timer'].daemon = True
        _ann['timer'].start()
    _ann_broadcast()


def _race_over(event, heat):
    _stream_activity()
    _ann_race_over(event, heat)
    try:
        _break_race_over(event, heat)
    except Exception as e:
        print('break slide:', e)


def _race_started():
    _stream_activity()
    try:                                # for "Copy YouTube chapters"
        st = race_book.s
        STREAM_LOG.race(st.get('current_event'), st.get('current_heat'), race_book_secs(st.get('running_time')))
    except Exception as e:
        print('stream log:', e)
    try:
        _ann_settings()                 # near-end announcing: on/off from the saved choice
    except Exception:
        pass
    if BREAK['on']:                     # the next session is under way: take the break slide down
        BREAK['on'] = False
        _break_emit()


race_book.on_end = _race_over
race_book.on_all_touched = _race_over          # auto-announce as soon as the last swimmer touches
race_book.on_start = _race_started
race_book.on_near_end = _ann_near_end
_ann_settings()                                 # sets race_book.near_yards from the saved choice


@app.route('/api/announce', methods=['GET', 'POST'])
def route_api_announce():
    if flask.request.method == 'POST':
        d = flask.request.get_json(silent=True, force=True) or {}
        act = d.get('action')
        err = None
        if act == 'play':
            err = _ann_play(_ann_target())
        elif act == 'extra':
            err = _ann_play_extra(str(d.get('clip') or ''))
        elif act in ('next', 'prev'):
            order, key = _ann_order(), _ann_target()
            if key in order:
                i = order.index(key) + (1 if act == 'next' else -1)
                if 0 <= i < len(order):
                    _ann['override'] = order[i]
        elif act == 'reset':                 # start over: forget what was announced / swum
            _ann.update(announced=set(), swum=set(), override=None, last=None)
        elif act == 'settings':
            a = _ann_settings()
            if 'auto' in d:
                a['auto'] = bool(d['auto'])
                if not a['auto'] and _ann['timer']:
                    _ann['timer'].cancel()
                    _ann['timer'] = None
            if 'delay' in d:
                try:
                    a['delay'] = max(0, min(60, int(d['delay'])))
                except (TypeError, ValueError):
                    pass
            if d.get('when') in ('end', 'near'):
                a['when'] = d['when']
            _ann_settings()                  # near-end estimate on/off follows auto + when
            if 'volume' in d:
                try:
                    a['volume'] = max(0, min(150, int(round(float(d['volume'])))))
                except (TypeError, ValueError):
                    pass
            save_settings()
        elif act == 'cancel' and _ann['timer']:
            _ann['timer'].cancel()
            _ann['timer'] = None
        _ann_broadcast()
        if err:
            return _cors(flask.make_response(flask.jsonify({'ok': False, 'error': err, **_ann_view()}), 400))
    return _cors(flask.jsonify({'ok': True, **_ann_view()}))


@app.route('/announce/clip/<name>.mp3')
def route_announce_clip(name):
    if not announce.SAFE.match(name):
        return flask.abort(404)
    return flask.send_from_directory(_ann_folder(), name + '.mp3', conditional=True)


@app.route('/api/announce/clips', methods=['GET', 'POST', 'OPTIONS'])
def route_api_announce_clips():
    """GET: the clip list (recorded or not). POST: upload clips (files named <clip>.mp3, or a .zip of them)."""
    if flask.request.method == 'OPTIONS':
        return _cors(flask.make_response('', 204))
    folder = _ann_folder()
    saved = []
    known = {c for c, _ in announce.clip_script()}
    if flask.request.method == 'POST':
        import zipfile as _zf
        for f in flask.request.files.getlist('clips'):
            fn = os.path.basename(f.filename or '')
            if fn.lower().endswith('.zip'):
                with _zf.ZipFile(f.stream) as z:
                    for n in z.namelist():
                        b = os.path.basename(n)
                        if b.lower().endswith('.mp3') and b[:-4] in known:
                            with open(os.path.join(folder, b), 'wb') as out:
                                out.write(z.read(n))
                            saved.append(b[:-4])
            elif fn.lower().endswith('.mp3') and fn[:-4] in known:
                f.save(os.path.join(folder, fn))
                saved.append(fn[:-4])
        _ann_broadcast()
    have = announce.clips_on_disk(folder)
    return _cors(flask.jsonify({'saved': saved, 'clips': [{'clip': c, 'text': t, 'have': c in have}
                                                           for c, t in announce.clip_script()]}))

@app.route('/music')
def route_music():
    return flask.render_template('music.html', speaker='speaker' in flask.request.args,
                                 listen_last=dict(_listen_last),
                                 songs=_music_songs(), music_folder=_music_folder(),
                                 warmup=_warmup_songs(), warmup_folder=_warmup_folder(),
                                 mic_defaults=music.mic_defaults())

@app.route('/music/file/<path:name>')
def route_music_file(name):
    safe = music.safe_filename(name)
    if not safe or safe != name:
        return flask.abort(404)
    return flask.send_from_directory(_music_folder(), safe, conditional=True)

@app.route('/music/upload', methods=['POST'])
@flask_login.login_required
def route_music_upload():
    saved, skipped = [], []
    folder = _music_folder()
    for f in flask.request.files.getlist('songs'):
        name = music.safe_filename(f.filename)
        if not name:
            skipped.append(f.filename)
            continue
        f.save(os.path.join(folder, name))
        saved.append(name)
    _music_broadcast(include_songs=True)
    return flask.jsonify({'saved': saved, 'skipped': skipped, 'songs': _music_songs()})

@app.route('/music/meta', methods=['POST'])
@flask_login.login_required
def route_music_meta():
    """Edit a song: {file, title, artist, stream_ok}."""
    d = flask.request.get_json(silent=True) or {}
    name = d.get('file', '')
    safe = music.safe_filename(name)
    folder = _warmup_folder() if d.get('mode') == 'warmup' else _music_folder()
    if not safe or safe != name or not os.path.isfile(os.path.join(folder, safe)):
        return flask.jsonify({'error': 'not found'}), 404
    kw = {}
    if 'level_db' in d:
        lv = d.get('level_db')
        kw['level_db'] = None if lv in (None, '', 'auto') else float(lv)
    if d.get('auto_db') is not None:
        kw['auto_db'] = float(d['auto_db'])
    music.update_meta(folder, safe, title=d.get('title'), artist=d.get('artist'),
                      stream_ok=d.get('stream_ok'), **kw)
    _music_last_status[0] = None                 # re-evaluate the stream mute for the playing song
    _music_broadcast(include_songs=True)
    return flask.jsonify({'ok': True, 'songs': _music_songs()})


@app.route('/music/delete', methods=['POST'])
@flask_login.login_required
def route_music_delete():
    name = (flask.request.get_json(silent=True) or {}).get('file', '')
    safe = music.safe_filename(name)
    if not safe or safe != name or not os.path.isfile(os.path.join(_music_folder(), safe)):
        return flask.jsonify({'error': 'not found'}), 404
    if music_state.track == safe:
        music_state.command('stop')
    os.remove(os.path.join(_music_folder(), safe))
    music.forget_meta(_music_folder(), safe)
    _music_broadcast(include_songs=True)
    return flask.jsonify({'songs': _music_songs()})

# --- Warm-up list: a separate folder, played on shuffle by one button -----
@app.route('/warmup/file/<path:name>')
def route_warmup_file(name):
    safe = music.safe_filename(name)
    if not safe or safe != name:
        return flask.abort(404)
    return flask.send_from_directory(_warmup_folder(), safe, conditional=True)

@app.route('/warmup/upload', methods=['POST'])
@flask_login.login_required
def route_warmup_upload():
    saved, skipped = [], []
    folder = _warmup_folder()
    for f in flask.request.files.getlist('songs'):
        name = music.safe_filename(f.filename)
        if not name:
            skipped.append(f.filename)
            continue
        f.save(os.path.join(folder, name))
        saved.append(name)
    _music_broadcast(include_songs=True)
    return flask.jsonify({'saved': saved, 'skipped': skipped, 'warmup': _warmup_songs()})

@app.route('/warmup/meta', methods=['POST'])
@flask_login.login_required
def route_warmup_meta():
    """Edit a warm-up song: {file, title, artist, stream_ok}."""
    d = flask.request.get_json(silent=True) or {}
    name = d.get('file', '')
    safe = music.safe_filename(name)
    if not safe or safe != name or not os.path.isfile(os.path.join(_warmup_folder(), safe)):
        return flask.jsonify({'error': 'not found'}), 404
    music.update_meta(_warmup_folder(), safe, title=d.get('title'), artist=d.get('artist'),
                      stream_ok=d.get('stream_ok'))
    _music_last_status[0] = None
    _music_broadcast(include_songs=True)
    return flask.jsonify({'ok': True, 'warmup': _warmup_songs()})

@app.route('/warmup/delete', methods=['POST'])
@flask_login.login_required
def route_warmup_delete():
    name = (flask.request.get_json(silent=True) or {}).get('file', '')
    safe = music.safe_filename(name)
    if not safe or safe != name or not os.path.isfile(os.path.join(_warmup_folder(), safe)):
        return flask.jsonify({'error': 'not found'}), 404
    playing_this = music_state.mode == 'warmup' and music_state.track == safe
    os.remove(os.path.join(_warmup_folder(), safe))
    music.forget_meta(_warmup_folder(), safe)
    if playing_this:
        # Deleting the song that is playing shouldn't silence the warm-up.
        music_state.skip_warmup(_warmup_files())
    _music_broadcast(include_songs=True)
    return flask.jsonify({'warmup': _warmup_songs()})


@socketio.on('connect', namespace='/music')
def ws_music_connect():
    flask_socketio.emit('music_state', _music_state_dict(_music_songs(), _warmup_songs()))

@socketio.on('disconnect', namespace='/music')
def ws_music_disconnect(*args):
    if flask.request.sid == music_state.speaker_sid:
        music_state.speaker_sid = None
        music_state.speaker_ready = False
        _music_broadcast()

@socketio.on('speaker_hello', namespace='/music')
def ws_music_speaker(d=None):
    # Newest speaker tab wins; an older one is told to stand by.
    old = music_state.speaker_sid
    music_state.speaker_sid = flask.request.sid
    music_state.speaker_ready = bool((d or {}).get('ready'))
    if old and old != flask.request.sid:
        socketio.emit('speaker_standby', {}, namespace='/music', to=old)
    _music_broadcast()

@socketio.on('mic_report', namespace='/music')
def ws_mic_report(d=None):
    """The speaker tab: its input devices and whether the mic is live."""
    if flask.request.sid != music_state.speaker_sid:
        return
    d = d or {}
    devs = [{'id': str(x.get('id') or '')[:200], 'label': str(x.get('label') or '')[:120]}
            for x in (d.get('devices') or []) if isinstance(x, dict)][:20]
    status = str(d.get('status') or '')[:200]
    if devs != _mic_runtime['devices'] or status != _mic_runtime['status']:
        _mic_runtime.update(devices=devs, status=status)
        _music_broadcast()

@socketio.on('mic_level', namespace='/music')
def ws_mic_level(d=None):
    """Live input level from the speaker tab, passed on to the remotes' meters."""
    if flask.request.sid != music_state.speaker_sid:
        return
    try:
        db = max(-90.0, min(0.0, float((d or {}).get('db', -90))))
    except (TypeError, ValueError):
        return
    out = {'db': round(db, 1), 'ducking': bool((d or {}).get('ducking')), 'talk': bool((d or {}).get('talk'))}
    a = (d or {}).get('auto')
    if isinstance(a, dict):      # what auto mode is doing, for the remotes' readout
        keep = {}
        for k in ('scene', 'scene_label'):
            if isinstance(a.get(k), str):
                keep[k] = a[k][:60]
        for k in ('gain_db', 'low_cut_hz', 'duck_db', 'duck_threshold_db', 'floor_db', 'voice_db', 'feedback_cut_db'):
            v = a.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                keep[k] = round(float(v), 1)
        keep['ready'] = bool(a.get('ready'))
        out['auto'] = keep
    socketio.emit('mic_level', out, namespace='/music', skip_sid=flask.request.sid)

# Phone in the stands (/listen): what it hears, shown on the Music pages
_listen_last = {}

@socketio.on('listen_report', namespace='/music')
def ws_listen_report(d=None):
    out = {}
    for k in ('snr', 'room_db', 'speech_db'):
        try:
            out[k] = max(-120, min(120, int(round(float((d or {}).get(k))))))
        except (TypeError, ValueError):
            return
    t = time.localtime()
    out['at'] = '%d:%02d %s' % ((t.tm_hour % 12) or 12, t.tm_min, 'pm' if t.tm_hour >= 12 else 'am')
    _listen_last.clear(); _listen_last.update(out)
    socketio.emit('listen_status', out, namespace='/music')

# --- the phone-mic HTTPS listener's certificate (Settings -> Phone mic) ---
def _https_folder():
    return app_paths.data_path('https')

@app.route('/https/info')
def route_https_info():
    info = https_server.cert_info(_https_folder())
    info.update(port=https_server.STATE.get('port') or 0, error=https_server.STATE.get('error') or '')
    return flask.jsonify(info)

@app.route('/https/cert', methods=['POST'])
@flask_login.login_required
def route_https_cert():
    """Install a certificate + key (e.g. from the home-lab CA). Files or pasted text."""
    def part(name):
        f = flask.request.files.get(name)
        if f and f.filename:
            return f.read()
        v = flask.request.form.get(name, '')
        return v.encode() if v.strip() else None
    cert, key, ca = part('cert'), part('key'), part('ca')
    if not cert or not key:
        return flask.jsonify({'ok': False, 'error': 'send both the certificate and its key'}), 400
    try:
        info = https_server.install(_https_folder(), cert, key, ca)
    except Exception as e:
        return flask.jsonify({'ok': False, 'error': str(e)}), 400
    info.update(ok=True, port=https_server.STATE.get('port') or 0, error=https_server.STATE.get('error') or '')
    return flask.jsonify(info)

@app.route('/https/ca.crt')
def route_https_ca():
    """The CA that signed the certificate, for installing on a phone once."""
    p = os.path.join(_https_folder(), 'ca.pem')
    if not os.path.exists(p):
        return flask.abort(404)
    return flask.send_file(p, mimetype='application/x-x509-ca-cert', as_attachment=True, download_name='meet-board-ca.crt')

@app.route('/listen')
def route_listen():
    """Phone page: listen from the stands and set the mic's level in the mix."""
    port = https_server.STATE.get('port') or 0
    # plain http can't use a phone's mic: with a proper (CA-signed) certificate, just go to https
    if port and not flask.request.is_secure and not https_server.cert_info(_https_folder()).get('self_signed', True):
        return flask.redirect('https://%s:%d/listen' % (flask.request.host.split(':')[0], port))
    return flask.render_template('listen.html', https_port=port)

@socketio.on('speaker_report', namespace='/music')
def ws_music_report(d=None):
    if flask.request.sid != music_state.speaker_sid:
        return
    ended = music_state.report(d or {})
    # Learn a song's length from the player when its tags don't say
    if music_state.track and music_state.duration:
        song = next((x for x in _music_songs() if x['file'] == music_state.track), None)
        if song and not song.get('duration'):
            music.update_meta(_music_folder(), music_state.track, duration=music_state.duration)
            _music_broadcast(include_songs=True)
    if ended:
        # In warm-up the end of a track is a cue, not a stop.
        if music_state.mode == 'warmup':
            music_state.advance(_warmup_files())
        _music_broadcast()
    else:
        socketio.emit('music_progress', {'position': music_state.position, 'duration': music_state.duration,
                                         'track': music_state.track}, namespace='/music')

@socketio.on('music_cmd', namespace='/music')
def ws_music_cmd(d=None):
    d = d or {}
    action = d.get('action')
    if action == 'warmup_start':
        err = music_state.start_warmup(_warmup_files())
    elif action == 'warmup_skip':
        err = music_state.skip_warmup(_warmup_files())
    elif action == 'warmup_stop':
        music_state.stop_warmup()
        err = None
    elif action == 'mic':
        settings['mic'] = music.mic_merge(settings.get('mic'), d.get('mic') if isinstance(d.get('mic'), dict) else {})
        save_settings()
        err = None
    else:
        err = music_state.command(action, d.get('track'), d.get('volume'),
                                  [x['file'] for x in _music_songs()])
    if err:
        flask_socketio.emit('music_error', {'error': err})
        return
    _music_broadcast()

# Scoreboard Templates
@app.route('/overlay/<name>')
def route_overlay(name):
    overlay_name = "overlay/" + name + '.html'
    return flask.render_template(overlay_name, meet_title=settings['meet_title'], test_background='test' in flask.request.args.keys(), num_lanes=settings['num_lanes'],
                                 overlay_delay=float(settings.get('overlay_delay', 0) or 0))
    
@app.route('/web/<name>')
def route_web(name):
    web_name = "web/" + name + '.html'
    test_event = flask.request.args.get('event', None)
    test_heat = flask.request.args.get('heat', None)
    return flask.render_template(web_name, meet_title=settings['meet_title'], test_background='test' in flask.request.args.keys(), num_lanes=settings['num_lanes'], test_event=test_event, test_heat=test_heat, ad_url='', schedule_has_names=event_info.has_names, team_names=[('score_home', settings.get('team_home', '')), ('score_guest1', settings.get('team_guest1', '')), ('score_guest2', settings.get('team_guest2', '')), ('score_guest3', settings.get('team_guest3', ''))], team_tags=[('score_home', settings.get('team_home_tag', '')), ('score_guest1', settings.get('team_guest1_tag', '')), ('score_guest2', settings.get('team_guest2_tag', '')), ('score_guest3', settings.get('team_guest3_tag', ''))])

@app.route('/settings', methods=['POST', 'GET'])
@app.route('/meet_files', methods=['POST', 'GET'])
@flask_login.login_required
def route_settings():
    global settings
    schedule_error = None
    standards_error = None
    records_error = None
    if flask.request.method == 'POST':
        modified = False
        
        # check if the post request has the file part
        scb_uploads = [f for f in flask.request.files.getlist('meet_schedule')
                       if f and f.filename and f.filename.lower().endswith(('.scb', '.zip'))]
        if scb_uploads:
            try:
                texts = scb_loader.expand_uploads([(f.filename, f.stream.read()) for f in scb_uploads])
                # The browser fills file_mtimes with each file's lastModified (ms)
                try:
                    mt = json.loads(flask.request.form.get('file_mtimes') or '{}')
                except ValueError:
                    mt = {}
                texts = [(n, t, (mt.get(n) or 0) / 1000.0) for n, t in texts]
                staged = copy.deepcopy(event_info)
                n_events, n_heats, errs = scb_loader.load_scb_into(
                    staged, texts, settings.get('scb_name_style', 'first_last'),
                    force='force_timestamps' in flask.request.form)
                n_rich = enrich_from_saved_hy3(staged)
                backup_schedule()
                event_info.from_object(staged.to_object())
            except Exception as e:
                schedule_error = 'Start lists NOT loaded (the board is unchanged): %s' % e
            else:
                settings['event_info'] = event_info.to_object()
                settings['schedule_filename'] = '%d CTS start lists (%d events, %d heats)%s' % (
                    len(texts), n_events, n_heats,
                    ' + seed times/relay swimmers from the .hy3 for %d lanes' % n_rich if n_rich else '')
                if errs:
                    schedule_error = 'Loaded, with warnings: ' + '; '.join(errs)
                send_event_info()
                modified = True

        if 'meet_schedule' in flask.request.files:
            file = flask.request.files['meet_schedule']
            # if user does not select file, browser also
            # submit a empty part without filename
            if file and file.filename and file.filename.lower().endswith('.hy3'):
                try:
                    backup_schedule()
                    event_info.load_from_bytestream(file.stream)
                except Exception as e:
                    detail = str(e)
                    schedule_error = 'Failed to parse the schedule file'
                    if detail:
                        schedule_error += ': ' + detail
                else:
                    settings['event_info'] = event_info.to_object()
                    settings['hy3_source'] = settings['event_info']   # .scb reloads borrow its seeds/relay swimmers
                    settings['schedule_filename'] = file.filename
                    settings['schedule_meet'] = None   # a .hy3 from Meet Manager: not a MeetDeck meet
                    send_event_info()
                    modified = True
        
        if 'time_standards_file' in flask.request.files:
            file = flask.request.files['time_standards_file']
            if file and file.filename and file.filename.endswith('.st2'):
                import pickle, base64
                import tempfile
                try:
                    with tempfile.NamedTemporaryFile(suffix='.st2', delete=False) as tmp:
                        tmp.write(file.stream.read())
                        tmp_path = tmp.name
                    global time_standards
                    time_standards = parse_st2_file(tmp_path)
                except Exception as e:
                    detail = str(e)
                    standards_error = 'Failed to parse the time standards file'
                    if detail:
                        standards_error += ': ' + detail
                else:
                    settings['time_standards'] = base64.b64encode(pickle.dumps(time_standards)).decode('ascii')
                    settings['standards_filename'] = file.filename
                    # Auto-populate desc overrides for new tags, preserve existing
                    new_tags = {s.tag for s in time_standards.header.standards}
                    overrides = settings.get('std_desc_overrides', {})
                    for std in time_standards.header.standards:
                        if std.tag not in overrides:
                            overrides[std.tag] = std.description
                    # Remove stale tags no longer in the file
                    settings['std_desc_overrides'] = {k: v for k, v in overrides.items() if k in new_tags}
                    modified = True
                finally:
                    try:
                        os.unlink(tmp_path)
                    except:
                        pass
        
        if 'records_file' in flask.request.files:
            file = flask.request.files['records_file']
            if file and file.filename and file.filename.endswith('.rec'):
                import pickle, base64
                import tempfile
                try:
                    with tempfile.NamedTemporaryFile(suffix='.rec', delete=False) as tmp:
                        tmp.write(file.stream.read())
                        tmp_path = tmp.name
                    global _next_rec_set_id
                    new_rec = parse_rec_file(tmp_path)
                except Exception as e:
                    detail = str(e)
                    records_error = 'Failed to parse the records file'
                    if detail:
                        records_error += ': ' + detail
                else:
                    swim_record_sets.append({
                        'rec_file': new_rec,
                        'filename': file.filename,
                        'team_tag': 'ALL',
                        'set_id': _next_rec_set_id,
                    })
                    _next_rec_set_id += 1
                    settings['swim_record_sets'] = base64.b64encode(pickle.dumps(swim_record_sets)).decode('ascii')
                    modified = True
                finally:
                    try:
                        os.unlink(tmp_path)
                    except:
                        pass
        
        # Handle record set team_tag dropdown updates
        for rec_set in swim_record_sets:
            form_key = 'rec_team_%d' % rec_set['set_id']
            if form_key in flask.request.form:
                new_tag = flask.request.form[form_key]
                if new_tag != rec_set['team_tag']:
                    rec_set['team_tag'] = new_tag
                    import pickle, base64
                    settings['swim_record_sets'] = base64.b64encode(pickle.dumps(swim_record_sets)).decode('ascii')
                    modified = True
        
        # Handle time standard description overrides
        if time_standards is not None:
            overrides = settings.get('std_desc_overrides', {})
            for std in time_standards.header.standards:
                form_key = 'std_desc_' + std.tag
                if form_key in flask.request.form:
                    new_desc = flask.request.form[form_key].strip()[:15]
                    if new_desc and new_desc != overrides.get(std.tag):
                        overrides[std.tag] = new_desc
                        modified = True
            settings['std_desc_overrides'] = overrides

        # Handle team tag auto-fill: if tag field is empty on Update, auto-fill from name
        for team_base in ['team_home', 'team_guest1', 'team_guest2', 'team_guest3']:
            tag_key = team_base + '_tag'
            if team_base in flask.request.form:
                name_val = flask.request.form.get(team_base, '').strip()
                tag_val = flask.request.form.get(tag_key, '').strip()
                if name_val and not tag_val:
                    # Auto-fill tag from name
                    tag_val = name_val[:5].upper()
                elif not name_val:
                    # Clear clears both
                    tag_val = ''
                tag_val = tag_val[:5]
                if settings.get(tag_key) != tag_val:
                    settings[tag_key] = tag_val
                    modified = True
        
        for k in settings.keys(): 
            if k in ('obs_url', 'obs_password'):
                continue   # handled below (a blank password field must not wipe the saved one)
            if k in ('num_lanes', 'pool_course', 'ad_url', 'warmup_title'):
                continue   # fixed (8 lanes, SCY) or removed
            if k in flask.request.form and settings[k]!=flask.request.form.get(k):
                if k == 'num_lanes':
                    val = int(flask.request.form.get(k))
                    if val != settings[k]:
                        settings[k] = val
                        modified = True
                elif k.endswith('_tag'):
                    pass  # Already handled above
                else:
                    val = flask.request.form.get(k)
                    if k.startswith('team_') and not k.endswith('_tag'):
                        val = val[:15]
                    settings[k]=val
                    modified = True
        
        # Handle checkbox fields (not present in form when unchecked)
        if 'show_pr_tags_form' in flask.request.form:
            new_val = 'show_pr_tags' in flask.request.form
            if settings.get('show_pr_tags') != new_val:
                settings['show_pr_tags'] = new_val
                modified = True

        if 'show_confetti_form' in flask.request.form:
            new_val = 'show_confetti' in flask.request.form
            if settings.get('show_confetti') != new_val:
                settings['show_confetti'] = new_val
                modified = True

        if 'show_time_decorations_form' in flask.request.form:
            new_val = 'show_time_decorations' in flask.request.form
            if settings.get('show_time_decorations') != new_val:
                settings['show_time_decorations'] = new_val
                modified = True

        if 'blank_message_form' in flask.request.form:
            raw = flask.request.form.get('blank_message', '')
            # Normalize line endings and enforce 10 lines × 60 *visible* chars
            # (markdown markers don't count toward the limit).
            def _visible_len(line):
                s = re.sub(r'^\s*#{1,4}\s+', '', line)
                s = re.sub(r'^\s*(\d+\.|[-*])\s+', '', s)
                s = re.sub(r'`([^`\n]+)`', r'\1', s)
                s = re.sub(r'\*\*([^*\n]+)\*\*', r'\1', s)
                s = re.sub(r'~~([^~\n]+)~~', r'\1', s)
                s = re.sub(r'(^|[^*])\*([^*\n]+)\*(?!\*)', r'\1\2', s)
                s = re.sub(r'(^|[^_])_([^_\n]+)_(?!_)', r'\1\2', s)
                return len(s)
            lines = raw.replace('\r\n', '\n').replace('\r', '\n').split('\n')[:10]
            trimmed = []
            for ln in lines:
                while _visible_len(ln) > 60:
                    ln = ln[:-1]
                trimmed.append(ln)
            new_msg = '\n'.join(trimmed)
            new_align = flask.request.form.get('blank_message_align', 'left')
            if new_align not in ('left', 'center', 'right'):
                new_align = 'left'
            # Visible only when textarea has content (auto-flip to False if empty)
            new_vis = ('blank_message_visible' in flask.request.form) and bool(new_msg.strip())

            msg_changed = False
            if settings.get('blank_message', '') != new_msg:
                settings['blank_message'] = new_msg
                modified = True
                msg_changed = True
            if settings.get('blank_message_align', 'left') != new_align:
                settings['blank_message_align'] = new_align
                modified = True
                msg_changed = True
            if bool(settings.get('blank_message_visible', False)) != new_vis:
                settings['blank_message_visible'] = new_vis
                modified = True
                msg_changed = True
            if msg_changed:
                # Persisted below with `modified`; push live update to clients.
                blank_message_needs_broadcast = True
            else:
                blank_message_needs_broadcast = False
        else:
            blank_message_needs_broadcast = False

        if 'obs_url_form' in flask.request.form:
            new_url = flask.request.form.get('obs_url', '').strip() or 'ws://127.0.0.1:4455'
            new_pw = flask.request.form.get('obs_password', '')
            changed = new_url != settings.get('obs_url')
            settings['obs_url'] = new_url
            if new_pw or 'obs_password_clear' in flask.request.form:
                changed = changed or new_pw != settings.get('obs_password')
                settings['obs_password'] = '' if 'obs_password_clear' in flask.request.form else new_pw
            modified = True
            if changed:
                obs.reconnect()

        if 'scb_folder' in flask.request.form:
            settings['scb_folder'] = flask.request.form.get('scb_folder', '').strip().strip('"')
            _scb_watch['signature'] = None  # force a reload on the next poll
            if settings['scb_folder'] and not os.path.isdir(settings['scb_folder']):
                schedule_error = 'Watch folder not found: %s' % settings['scb_folder']
            modified = True

        if modified:
            save_settings()

        if blank_message_needs_broadcast:
            send_blank_message()
                
    if flask.request.method == 'POST' and flask.request.form.get('return_to') in ('/team_colors', '/meet_files'):
        return flask.redirect(flask.request.form['return_to'])

    comm_port_list = [(port, "%s: %s" % (port,desc)) for port, desc, id in serial.tools.list_ports.comports()]
    if settings['serial_port'] not in [port for port,desc in comm_port_list]:
        comm_port_list.insert(0, (settings['serial_port'], settings['serial_port']))
        
    schedule_loaded = bool(event_info.event_names)
    standards_loaded = time_standards is not None
    
    # Build record set info for template
    rec_set_info = []
    for rs in swim_record_sets:
        rec_set_info.append({
            'set_id': rs['set_id'],
            'filename': rs['filename'],
            'set_name': rs['rec_file'].header.record_set_name or '',
            'team_tag': rs['team_tag'],
        })
    
    # Build team tag options for record set dropdown
    team_tag_options = [('ALL', 'All')]
    for tag_key, name_key in [('team_home_tag', 'team_home'), ('team_guest1_tag', 'team_guest1'), ('team_guest2_tag', 'team_guest2'), ('team_guest3_tag', 'team_guest3')]:
        tag = settings.get(tag_key, '')
        name = settings.get(name_key, '')
        if tag:
            team_tag_options.append((tag, '%s (%s)' % (tag, name) if name else tag))
    
    # /meet_files is the same handler (uploads post here) with its own page
    page = 'meet_files.html' if flask.request.path == '/meet_files' else 'settings.html'
    return flask.render_template(page, 
                meet_title=settings['meet_title'], 
                serial_port=settings['serial_port'],
                serial_port_list=comm_port_list,
                user_name=settings['username'],
                seed_time_label=settings.get('seed_time_label', 'Seed Time'),
                results_url=settings.get('results_url', ''), results_qr=settings.get('results_qr', 'slide'),
                results_every=settings.get('results_every', '4'), results_secs=settings.get('results_secs', '15'),
                youtube_channel=settings.get('youtube_channel', ''),
                schedule_loaded=schedule_loaded,
                schedule_error=schedule_error,
                schedule_filename=settings.get('schedule_filename', ''),
                standards_loaded=standards_loaded,
                standards_error=standards_error,
                standards_filename=settings.get('standards_filename', ''),
                std_tag_info=[{'tag': s.tag, 'original_desc': s.description, 'desc_override': settings.get('std_desc_overrides', {}).get(s.tag, s.description)} for s in time_standards.header.standards] if time_standards else [],
                rec_set_info=rec_set_info,
                records_error=records_error,
                team_tag_options=team_tag_options,
                show_pr_tags=settings.get('show_pr_tags', True),
                show_confetti=settings.get('show_confetti', True),
                show_time_decorations=settings.get('show_time_decorations', False),
                blank_message=settings.get('blank_message', ''),
                blank_message_visible=settings.get('blank_message_visible', False),
                blank_message_align=settings.get('blank_message_align', 'left'),
                team_home=settings.get('team_home', ''),
                team_home_tag=settings.get('team_home_tag', ''),
                team_guest1=settings.get('team_guest1', ''),
                team_guest1_tag=settings.get('team_guest1_tag', ''),
                team_guest2=settings.get('team_guest2', ''),
                team_guest2_tag=settings.get('team_guest2_tag', ''),
                team_guest3=settings.get('team_guest3', ''),
                team_guest3_tag=settings.get('team_guest3_tag', ''),
                scb_folder=settings.get('scb_folder', ''),
                obs_url=settings.get('obs_url', 'ws://127.0.0.1:4455'),
                obs_password_set=bool(settings.get('obs_password')),
                can_undo_schedule=bool(settings.get('event_info_prev')),
                schedule_prev_name=settings.get('schedule_filename_prev', ''),
                scb_watch_status=_scb_watch.get('message', ''),
                app_version=__version__,
                can_self_update=app_paths.FROZEN and os.name == 'nt',
                shutdown_nonce=_new_shutdown_nonce())
                
@app.route('/schedule_clear')
@flask_login.login_required
def route_schedule_clear():
    event_info.clear()
    settings['event_info'] = event_info.to_object()
    settings.pop('schedule_filename', None)
    settings.pop('hy3_source', None)
    with open(settings_file, "wt") as f:
        json.dump(settings, f, sort_keys=True, indent=4)
    return flask.redirect('/meet_files')

@app.route('/schedule_undo')
@flask_login.login_required
def route_schedule_undo():
    prev = settings.get('event_info_prev')
    if prev:
        cur, cur_name = event_info.to_object(), settings.get('schedule_filename', '')
        event_info.from_object(prev)
        settings['event_info'] = prev
        settings['schedule_filename'] = settings.get('schedule_filename_prev', '')
        settings['event_info_prev'], settings['schedule_filename_prev'] = cur, cur_name  # undo the undo
        save_settings()
        send_event_info()
    return flask.redirect('/meet_files')

# ---------------------------------------------------------------- team logos
def _logo_folder():
    return team_logos.logo_dir(app_paths.data_path)

def seed_builtin_logos():
    """First start of a version with new built-in logos: copy them in (never
    over an uploaded logo, never back after someone removed one)."""
    try:
        if meet_board.migrate_team_codes(settings):
            save_settings()
            print('Team Colors: moved Crimson Cliffs to CRIM and Timpanogos to THS')
        copied, done = team_logos.seed_builtin(_logo_folder(), app_paths.bundle_path('static', 'logos'),
                                               settings.get('logos_seeded'))
        if done != settings.get('logos_seeded'):
            settings['logos_seeded'] = done
            save_settings()
        if copied:
            print('Added built-in logos:', ', '.join(copied))
    except Exception:
        traceback.print_exc()


def _team_names():
    return getattr(event_info, 'team_names', None) or {}


def _logo_path(code):
    """A team's logo file, also found under any other code for the same school
    (SHS.png serves STAN and the reverse)."""
    folder = _logo_folder()
    code = (code or '').upper()
    path = team_logos.find_logo(folder, code)
    if path:
        return path
    home = meet_board.canonical_codes(settings, [code], _team_names()).get(code, code)
    for c in meet_board.alias_codes(settings, home):
        path = team_logos.find_logo(folder, c)
        if path:
            return path
    return None


def _meet_logos(meet_teams):
    """{code: mtime} for every logo file, plus each meet code whose school has
    a logo under another code."""
    out = team_logos.list_logos(_logo_folder())
    for code in meet_teams or []:
        if code and code not in out:
            p = _logo_path(code)
            if p:
                out[code] = int(os.path.getmtime(p))
    return out


@app.route('/team_logo/<code>')
def route_team_logo(code):
    path = _logo_path(code)
    if not path:
        return flask.abort(404)
    resp = flask.send_file(path, max_age=86400)   # ?v=<mtime> on the URL busts the cache
    return resp


@app.route('/team_logos/upload', methods=['POST'])
@flask_login.login_required
def route_team_logos_upload():
    files = flask.request.files.getlist('logos')
    # one "logo_code" box per picked file, in the same order (blank = use the file name)
    codes = flask.request.form.getlist('logo_code')
    uploads = [(f.filename, f.read(), codes[i] if i < len(codes) else '')
               for i, f in enumerate(files) if f and f.filename]
    try:
        saved, skipped = team_logos.save_uploads(_logo_folder(), uploads)
        msg = 'Saved %d logo(s)%s' % (len(saved), (': ' + ', '.join(sorted(saved))) if saved else '')
        if skipped:
            msg += '. Skipped (give each a team code, letters/numbers, up to 8): ' + ', '.join(skipped[:8])
    except Exception as e:
        msg = 'Logos NOT saved: %s' % e
    send_event_info()
    return flask.redirect('/team_colors?msg=' + urllib.parse.quote(msg))


@app.route('/team_logos/delete', methods=['POST'])
@flask_login.login_required
def route_team_logos_delete():
    code = flask.request.form.get('logo_code', '')
    if team_logos.valid_code(code):
        team_logos.remove_logo(_logo_folder(), code.upper())
        send_event_info()
    return flask.redirect('/team_colors')


# ---------------------------------------------------------------- refresh the TVs
@app.route('/screens/reload', methods=['POST'])
@flask_login.login_required
def route_screens_reload():
    """Every meet board / overlay / scoreboard page reloads itself."""
    socketio.emit('reload', {'at': time.time()}, namespace='/scoreboard')
    if flask.request.is_json or flask.request.args.get('json'):
        return flask.jsonify({'ok': True})
    return flask.redirect(flask.request.referrer or '/settings')


@app.route('/api/serial')
def route_api_serial():
    """Serial input diagnostics: port state, bytes/records received, last raw bytes."""
    snap = serial_mon.snapshot()
    try:
        import serial.tools.list_ports
        snap['ports'] = [{'device': p.device, 'description': p.description}
                         for p in serial.tools.list_ports.comports()]
    except Exception:
        snap['ports'] = []
    snap['serial_format'] = settings.get('serial_format') or '9600-8N1'
    snap['update_interval'] = float(settings.get('update_interval', 1.0) or 0)
    snap['update_intervals'] = UPDATE_INTERVALS
    snap['serial_formats'] = SERIAL_FORMATS
    snap['race_state'] = race_fsm.state_name
    snap['event'], snap['heat'] = last_event_sent[0], last_event_sent[1]
    snap['event_name'] = event_info.get_event_name(last_event_sent[0])
    # Race time as the console last sent it (zero-filled, like the boards show it)
    snap['running_time'] = _ZERO_FILL.sub(r'\g<1>0', running_time or '').strip()
    # What the parser has decoded so far: the values the boards are showing
    e, h = last_event_sent
    lanes = []
    for i in range(1, 11):
        t = (_live_race.get('lane_time%d' % i) or '').strip()
        lanes.append({
            'lane': i,
            'running': bool(_live_race.get('lane_running%d' % i)),
            'place': (_live_race.get('lane_place%d' % i) or '').strip(),
            'time': t,
            'name': event_info.get_display_string(e, h, i),
            'team': event_info.get_team_code(e, h, i),
            'seed': event_info.get_seed_time(e, h, i),
        })
    snap['decoded'] = {
        'running_time': snap['running_time'],
        'event': e, 'heat': h,
        'race_state': race_fsm.state_name,
        'lanes': lanes,
        'scores': {k: (v or '').strip() for k, v in team_scores.items()},
    }
    return flask.jsonify(snap)


def _serial_detail():
    """The Debug page's detail panel: connection log, port hardware, garbled records,
    timing, the race clock as it changed and what the race book decided."""
    d = serial_mon.detail()
    port = serial_mon.port
    d['port_info'] = None
    try:
        import serial.tools.list_ports
        for p in serial.tools.list_ports.comports():
            if p.device == port:
                d['port_info'] = {k: getattr(p, k, None) for k in ('device', 'description', 'hwid', 'manufacturer',
                                                                    'product', 'serial_number', 'location')}
                if getattr(p, 'vid', None) is not None:
                    d['port_info']['vid_pid'] = '%04X:%04X' % (p.vid, p.pid or 0)
    except Exception as e:
        d['port_info'] = {'error': str(e)}
    d['race_book'] = race_book.debug()
    d['race_state'] = race_fsm.state_name
    try:
        import platform
        d['versions'] = {'meet_board': __version__, 'python': platform.python_version(),
                         'pyserial': getattr(serial, '__version__', '?'), 'os': platform.platform()}
    except Exception:
        d['versions'] = {'meet_board': __version__}
    d['in_file'] = os.path.basename(in_file) if in_file else ''
    return d


@app.route('/api/serial/detail')
def route_api_serial_detail():
    return flask.jsonify(_serial_detail())


@app.route('/api/serial/debug.json')
def route_api_serial_debug_download():
    """One file with everything: send it in when the console data looks wrong."""
    data = {'summary': route_api_serial().get_json(), 'detail': _serial_detail(),
            'settings': {k: settings.get(k) for k in ('serial_port', 'serial_format', 'update_interval')}}
    name = 'meet-board-serial-debug-%s.json' % time.strftime('%Y%m%d-%H%M%S')
    return flask.Response(json.dumps(data, indent=1, default=str), mimetype='application/json',
                          headers={'Content-Disposition': 'attachment; filename="%s"' % name})


_autodetect = {'running': False, 'results': [], 'message': ''}


def _autodetect_worker():
    """Try each serial format for a few seconds; keep the one whose data looks
    most like real CTS records."""
    original = settings.get('serial_format') or '9600-8N1'
    results = []
    try:
        for fmt in SERIAL_FORMATS:
            _autodetect['message'] = 'Trying %s...' % fmt
            serial_mon.log('format', 'Find the right format: trying %s' % fmt)
            settings['serial_format'] = fmt
            socketio.sleep(1.0)                      # reader reopens with the new format
            serial_mon.reset(serial_mon.port, serial_mon.state, serial_mon.source)
            socketio.sleep(4.0)
            snap = serial_mon.snapshot()
            results.append({'format': fmt, 'bytes': snap['bytes_total'], 'records': snap['records_total'],
                            'valid_pct': snap['valid_pct'] or 0, 'state': snap['state'], 'error': snap['error']})
            _autodetect['results'] = list(results)
        good = [r for r in results if r['records'] >= 50]
        best = max(good, key=lambda r: (r['valid_pct'], r['records'])) if good else None
        if best and best['valid_pct'] >= 75:
            settings['serial_format'] = best['format']
            save_settings()
            _autodetect['message'] = 'Found it: %s (%.0f%% clean CTS records). Saved.' % (best['format'], best['valid_pct'])
        else:
            settings['serial_format'] = original
            _autodetect['message'] = ('No format gave clean CTS data (best: %s at %.0f%%). The signal may be inverted '
                                      'or not a CTS scoreboard feed - record a capture and send it in. Kept %s.'
                                      % ((best or {}).get('format', '-'), (best or {}).get('valid_pct', 0), original))
    finally:
        serial_mon.reset(serial_mon.port, serial_mon.state, serial_mon.source)
        _autodetect['running'] = False


@app.route('/api/serial/autodetect', methods=['GET', 'POST'])
@flask_login.login_required
def route_api_serial_autodetect():
    if flask.request.method == 'POST' and not _autodetect['running']:
        if in_file:
            return flask.jsonify({'ok': False, 'message': 'Replaying a file; nothing to detect.'})
        _autodetect.update(running=True, results=[], message='Starting...')
        socketio.start_background_task(_autodetect_worker)
    return flask.jsonify(dict(_autodetect, formats=SERIAL_FORMATS, current=settings.get('serial_format')))


# Board update rate choices: seconds between pushes of console data to the screens
UPDATE_INTERVALS = [[1.0, '1 per second'], [0.5, '2 per second'], [0.25, '4 per second'],
                    [0.1, '10 per second'], [0.0, 'Every console update (fastest)']]


@app.route('/api/serial/update_interval', methods=['POST'])
@flask_login.login_required
def route_api_update_interval():
    try:
        v = float((flask.request.get_json(silent=True) or flask.request.form or {}).get('interval'))
    except (TypeError, ValueError):
        return flask.jsonify({'ok': False, 'message': 'bad interval'}), 400
    if v not in [i for i, _ in UPDATE_INTERVALS]:
        return flask.jsonify({'ok': False, 'message': 'unknown interval'}), 400
    settings['update_interval'] = v
    save_settings()
    return flask.jsonify({'ok': True, 'interval': v})


@app.route('/api/serial/format', methods=['POST'])
@flask_login.login_required
def route_api_serial_format():
    fmt = (flask.request.get_json(silent=True) or flask.request.form or {}).get('format', '')
    if fmt not in SERIAL_FORMATS:
        return flask.jsonify({'ok': False, 'message': 'unknown format'}), 400
    old = settings.get('serial_format') or '9600-8N1'
    settings['serial_format'] = fmt
    save_settings()
    serial_mon.reset(serial_mon.port, serial_mon.state, serial_mon.source)
    serial_mon.log('format', 'Serial format set to %s (was %s) on the Debug page' % (fmt, old))
    return flask.jsonify({'ok': True, 'format': fmt})


def _captures_dir():
    d = app_paths.data_path('captures')
    os.makedirs(d, exist_ok=True)
    return d


_capture_last = {'file': ''}


def _save_capture(data):
    name = 'serial-%s-%s.bin' % (time.strftime('%Y%m%d-%H%M%S'), (settings.get('serial_format') or '').replace('-', '_'))
    with open(os.path.join(_captures_dir(), name), 'wb') as f:
        f.write(data)
    _capture_last['file'] = name
    print('Saved serial capture %s (%d bytes)' % (name, len(data)))


@app.route('/api/serial/capture', methods=['GET', 'POST'])
@flask_login.login_required
def route_api_serial_capture():
    if flask.request.method == 'POST':
        secs = max(5, min(300, int((flask.request.get_json(silent=True) or {}).get('seconds', 30))))
        _capture_last['file'] = ''
        serial_mon.start_capture(secs, _save_capture)
    serial_mon.poll_capture()
    files = sorted((n for n in os.listdir(_captures_dir()) if n.endswith('.bin')), reverse=True)[:10]
    return flask.jsonify({'status': serial_mon.capture_status(), 'last': _capture_last['file'], 'files': files})


@app.route('/api/serial/captures/<name>')
@flask_login.login_required
def route_api_serial_capture_file(name):
    if not re.match(r'^serial-[\w-]+\.bin$', name):
        return flask.abort(404)
    return flask.send_from_directory(_captures_dir(), name, as_attachment=True)


@app.route('/api/serial/reset', methods=['POST'])
@flask_login.login_required
def route_api_serial_reset():
    serial_mon.reset(serial_mon.port, serial_mon.state, serial_mon.source)
    return flask.jsonify({'ok': True})


@app.route('/api/health')
def route_health():
    return flask.jsonify({
        'ok': True, 'version': __version__,
        'serial_port': settings.get('serial_port'),
        'race_state': race_fsm.state_name,
        'event': last_event_sent[0], 'heat': last_event_sent[1],
        'schedule_events': len(event_info.event_names),
        'schedule': settings.get('schedule_filename', ''),
        'watch': _scb_watch.get('message', ''),
    })

# ---------------------------------------------------------------------------
# Start lists pushed from MeetDeck (or anything else that speaks the format in
# startlist_json.py). Same effect as loading a .hy3 on the Meet tab, relay
# swimmers' last names included. Open to other machines on purpose: MeetDeck
# sends from the browser on the timing laptop, so it needs CORS.
# ---------------------------------------------------------------------------
def _cors(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    resp.headers['Access-Control-Allow-Private-Network'] = 'true'
    resp.headers['Cache-Control'] = 'no-store'
    return resp

@app.route('/api/results_qr')
def results_qr_info():
    """What the meet boards need for the phone-results QR code (public: the boards don't log in)."""
    def num(k, d, lo, hi):
        try:
            return max(lo, min(hi, float(settings.get(k) or d)))
        except (TypeError, ValueError):
            return d
    mode = settings.get('results_qr') if settings.get('results_qr') in ('off', 'corner', 'slide') else 'slide'
    return flask.jsonify(url=(settings.get('results_url') or '').strip(), mode=mode,
                         every=num('results_every', 4, 1, 60), secs=num('results_secs', 15, 5, 120),
                         stream=bool((settings.get('youtube_channel') or '').strip()))


def youtube_live_url(chan):
    """@handle, a handle without @, a channel id (UC...) or any youtube.com channel URL -> its /live address,
    which YouTube sends to the broadcast that's on right now (or the channel page when nothing is)."""
    c = (chan or '').strip()
    if not c:
        return ''
    m = re.search(r'youtube\.com/((?:@[\w.-]+)|(?:channel/UC[\w-]+)|(?:c/[\w.-]+)|(?:user/[\w.-]+))', c)
    if m:
        path = m.group(1)
    elif re.fullmatch(r'UC[\w-]{20,}', c):
        path = 'channel/' + c
    else:
        path = '@' + c.lstrip('@')
    return 'https://www.youtube.com/%s/live' % path


@app.route('/api/stream_link', methods=['GET', 'OPTIONS'])
def stream_link():
    """The public link to the meet's live stream, for MeetDeck's phone results page (public, read-only)."""
    try:
        live = bool(obs.status().get('streaming')) if obs else False
    except Exception:
        live = False
    r = flask.jsonify(url=youtube_live_url(settings.get('youtube_channel')), live=live,
                      channel=(settings.get('youtube_channel') or '').strip())
    r.headers['Access-Control-Allow-Origin'] = '*'
    return r


@app.route('/results_qr.svg')
def results_qr_svg():
    url = (settings.get('results_url') or '').strip()
    if not url:
        return flask.Response('', status=404)
    import io
    import segno
    buf = io.BytesIO()
    segno.make(url, error='m', micro=False).save(buf, kind='svg', border=0, dark='#000', light=None, xmldecl=False, svgns=True, omitsize=True)   # viewBox only: scales to its box
    r = flask.Response(buf.getvalue(), mimetype='image/svg+xml')
    r.headers['Cache-Control'] = 'no-store'
    return r


@app.route('/api/startlists', methods=['GET', 'POST', 'OPTIONS'])
def route_api_startlists():
    if flask.request.method == 'OPTIONS':
        return _cors(flask.make_response('', 204))
    if flask.request.method == 'POST':
        doc = flask.request.get_json(silent=True, force=True)
        staged = copy.deepcopy(event_info)
        try:
            n_events, n_heats, n_lanes, n_legs = startlist_json.load_into(staged, doc)
        except startlist_json.StartListError as e:
            return _cors(flask.make_response(flask.jsonify({'ok': False, 'error': str(e)}), 400))
        except Exception as e:
            traceback.print_exc()
            return _cors(flask.make_response(flask.jsonify({'ok': False, 'error': 'could not read the start list: %s' % e}), 400))
        backup_schedule()
        event_info.from_object(staged.to_object())
        event_info.meet_info = staged.meet_info
        _store_usc(doc)
        _ann.update(announced=set(), swum=set(), override=None, last=None)   # new start lists: start announcing over
        m = (doc or {}).get('meet') or {}
        settings['schedule_meet'] = {'id': str(m.get('id') or '')[:40], 'name': str(m.get('name') or '')[:120],
                                     'date': str(m.get('date') or '')[:20]}
        hm = _HHMM.match(str(m.get('start') or '').strip())     # MeetDeck's meet start time -> warm-up slide
        if hm:
            settings['warmup_start'] = '%02d:%s' % (int(hm.group(1)), hm.group(2))
        settings['event_info'] = event_info.to_object()
        settings['hy3_source'] = settings['event_info']   # .scb reloads borrow its seeds/relay swimmers
        src = str((doc or {}).get('source') or 'MeetDeck')[:60]
        meet_name = str(((doc or {}).get('meet') or {}).get('name') or '').strip()
        settings['schedule_filename'] = '%s%s: %d events, %d heats%s (%s)' % (
            src, (' - ' + meet_name) if meet_name else '', n_events, n_heats,
            ', relay swimmers for %d relays' % n_legs if n_legs else '',
            time.strftime('%a %H:%M'))
        if meet_name:
            settings['meet_title'] = meet_name[:120]      # the boards' and overlay's meet name follows MeetDeck
        save_settings()
        send_event_info()
        return _cors(flask.jsonify({'ok': True, 'events': n_events, 'heats': n_heats, 'lanes': n_lanes,
                                    'relays_with_swimmers': n_legs, 'schedule': settings['schedule_filename'],
                                    'version': __version__}))
    return _cors(flask.jsonify({'ok': True, 'version': __version__,
                                'meet': settings.get('schedule_meet') or None,
                                'schedule': settings.get('schedule_filename', ''),
                                'schedule_events': len(event_info.event_names),
                                'accepts': [startlist_json.FORMAT]}))

@app.route('/standards_clear')
@flask_login.login_required
def route_standards_clear():
    global time_standards
    time_standards = None
    settings.pop('time_standards', None)
    settings.pop('standards_filename', None)
    settings.pop('std_desc_overrides', None)
    with open(settings_file, "wt") as f:
        json.dump(settings, f, sort_keys=True, indent=4)
    return flask.redirect('/meet_files')

@app.route('/records_remove/<int:set_id>')
@flask_login.login_required
def route_records_remove(set_id):
    global swim_record_sets
    swim_record_sets = [s for s in swim_record_sets if s['set_id'] != set_id]
    import pickle, base64
    if swim_record_sets:
        settings['swim_record_sets'] = base64.b64encode(pickle.dumps(swim_record_sets)).decode('ascii')
    else:
        settings.pop('swim_record_sets', None)
    with open(settings_file, "wt") as f:
        json.dump(settings, f, sort_keys=True, indent=4)
    return flask.redirect('/meet_files')
                
_shutdown_nonces = []

def _new_shutdown_nonce():
    import secrets
    nonce = secrets.token_hex(16)
    _shutdown_nonces.append(nonce)
    if len(_shutdown_nonces) > 10:
        del _shutdown_nonces[:-10]
    return nonce

@app.route('/shutdown', methods=['POST'])
@flask_login.login_required
def route_shutdown():
    nonce = flask.request.form.get('nonce', '')
    if not nonce or nonce not in _shutdown_nonces:
        return 'Invalid request', 403
    _shutdown_nonces.clear()  # Invalidate all
    import threading
    def _exit():
        import time
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=_exit, daemon=True).start()
    return 'Server shutting down...', 200

@app.route('/combine_events')
@flask_login.login_required
def route_combine_events():
    event_heat = list(event_info.events_uncombined.keys())
    event_heat.sort()
    return flask.render_template('schedule_preview.html', 
                event_heat = event_heat, 
                event_names = event_info.event_names, 
                events = event_info.events_uncombined,
                combined = event_info.combined,
                show_combine_select = True)

@app.route('/schedule_preview', methods=["GET", "POST"])
@flask_login.login_required
def route_schedule_preview():
    if flask.request.method == 'POST':
        # Posted from combine events
        combined = {}
        for key, value in flask.request.form.items():
            if key.startswith('combine_') and value.strip():
                k = key.split('_')
                v = value.split(',')
                combined[(int(k[1]), int(k[2]))] = ( int(v[0]), int(v[1]) )
        event_info.combine_events(combined)
        settings['event_info'] = event_info.to_object()
        with open(settings_file, "wt") as f:
            json.dump(settings, f, sort_keys=True, indent=4)
    event_heat = list(event_info.events.keys())
    event_heat.sort()
    return flask.render_template('schedule_preview.html', 
                event_heat = event_heat, 
                event_names = event_info.event_names, 
                events = event_info.events,
                show_combine_select = False)

    
# somewhere to login
@app.route("/login", methods=["GET", "POST"])
def route_login():
    if app.config.get('LOGIN_DISABLED'):
        nxt = flask.request.args.get("next") or ''
        return flask.redirect(nxt if nxt.startswith('/') and not nxt.startswith('//') else '/settings')
    if flask.request.method == 'POST':
        if ((flask.request.form['username']==settings['username']) and
            (flask.request.form['password']==settings['password'])):        
            user = User(0)
            flask_login.login_user(user)
            return flask.redirect(flask.request.args.get("next"))
        else:
            return flask.abort(401)
    else:
        return flask.render_template('login.html')


# somewhere to logout
@app.route("/logout")
@flask_login.login_required
def route_logout():
    flask_login.logout_user()
    return flask.redirect('/')


# handle login failed
@app.errorhandler(401)
def page_not_found(e):
    return flask.render_template('login.html', login_failed=True)
    

@app.route('/api/obs')
def route_obs_status():
    # Read-only and safe to poll; never includes the password.
    st = obs.status()
    st['password_set'] = bool(settings.get('obs_password'))
    return flask.jsonify(st)

@app.route('/api/obs/diagnostics')
def route_obs_diagnostics():
    d = obs.diagnostics()
    d['status']['password_set'] = bool(settings.get('obs_password'))
    return flask.jsonify(d)

@app.route('/api/obs/test', methods=['POST'])
@flask_login.login_required
def route_obs_test():
    """Step-by-step connection test with the saved settings."""
    steps = obs_client.diagnose(settings.get('obs_url'), settings.get('obs_password'))
    obs.note('Test connection: ' + ('all steps passed' if all(x['ok'] for x in steps)
                                    else 'failed at "%s"' % next(x['step'] for x in steps if not x['ok'])))
    if all(x['ok'] for x in steps):
        obs.reconnect()          # a passing test means the link should come up now
    return flask.jsonify({'steps': steps})

@app.route('/api/obs/stream', methods=['POST'])
@flask_login.login_required
def route_obs_stream():
    data = flask.request.get_json(silent=True) or {}
    action = str(data.get('action', ''))
    if action not in ('start', 'stop'):
        return flask.jsonify({'error': 'action must be "start" or "stop"'}), 400
    try:
        if action == 'start':
            obs.start_stream()
        else:
            obs.stop_stream()
    except Exception as e:
        # OBS closed, wrong password, already streaming... the boards are unaffected
        return flask.jsonify({'error': str(e)}), 502
    time.sleep(0.4)          # let StreamStateChanged land so the reply is truthful
    return flask.jsonify(obs.status())

# ---------------------------------------------------------------- stream auto-stop
# A stream left running after the meet: stop it once there's been no race (start, finish or touch) for
# stream_autostop_min minutes. The warm-up and break slides count as activity, and so does the stream having
# just started (warm-ups), so it never stops before the meet gets going.
STREAM_WATCH = {'activity': time.time(), 'stopped_at': None, 'last': ''}
STREAM_LOG = yt_chapters.StreamLog(app_paths.data_path('stream_log.json'))


def _stream_logger():
    """Note when the OBS stream started (for YouTube chapters)."""
    while True:
        time.sleep(15)
        try:
            st = obs.status()
            STREAM_LOG.stream(st.get('streaming'), st.get('seconds'))
        except Exception:
            pass


threading.Thread(target=_stream_logger, daemon=True).start()


def _stream_activity():
    STREAM_WATCH['activity'] = time.time()


def _stream_watchdog():
    while True:
        time.sleep(60)
        try:
            mins = int(settings.get('stream_autostop_min', 60) or 0)
            if race_book.racing or settings.get('warmup_slide_on') or BREAK.get('on'):
                _stream_activity()
            if mins <= 0:
                continue
            st = obs.status()
            if not st.get('streaming'):
                continue
            started = time.time() - float(st.get('seconds') or 0)
            quiet = time.time() - max(STREAM_WATCH['activity'], started)
            if quiet >= mins * 60:
                obs.stop_stream()
                STREAM_WATCH['stopped_at'] = time.strftime('%H:%M')
                STREAM_WATCH['last'] = 'Stopped the stream at %s: no races for %d min' % (STREAM_WATCH['stopped_at'], mins)
                obs.note(STREAM_WATCH['last'])
                print(STREAM_WATCH['last'])
        except Exception as e:
            print('stream auto-stop:', e)


threading.Thread(target=_stream_watchdog, daemon=True).start()


@app.route('/api/obs/autostop', methods=['GET', 'POST'])
def route_obs_autostop():
    """GET/POST {minutes}: stop the stream after this many minutes with no races (0 = never)."""
    if flask.request.method == 'POST':
        if not app.config.get('LOGIN_DISABLED') and not flask_login.current_user.is_authenticated:
            return flask.jsonify({'error': 'login required'}), 401
        d = flask.request.get_json(silent=True) or {}
        try:
            settings['stream_autostop_min'] = max(0, min(600, int(d.get('minutes'))))
        except (TypeError, ValueError):
            return flask.jsonify({'error': 'minutes must be a number'}), 400
        save_settings()
    quiet = int((time.time() - STREAM_WATCH['activity']) / 60)
    return flask.jsonify({'minutes': int(settings.get('stream_autostop_min', 60) or 0), 'quiet_min': quiet, 'last': STREAM_WATCH['last']})


@app.route('/api/youtube/chapters')
def route_youtube_chapters():
    """Chapters for a streamed meet: ?stream=<index> (default the latest stream that had races)."""
    try:
        idx = int(flask.request.args['stream']) if flask.request.args.get('stream') else None
    except ValueError:
        idx = None
    text, info = yt_chapters.chapters(STREAM_LOG, dict(event_info.event_names), idx)
    fmt = lambda t: time.strftime('%a %b %d, %I:%M %p', time.localtime(t)).replace(' 0', ' ')
    sess = [{'index': x['index'], 'label': '%s - %s (%d races)' % (fmt(x['start']), time.strftime('%I:%M %p', time.localtime(x['end'])).lstrip('0'), x['races'])}
            for x in STREAM_LOG.sessions()]
    return flask.jsonify({'text': text, **info, 'sessions': sess})


@app.route('/api/overlay/delay', methods=['GET', 'POST'])
def route_overlay_delay():
    """GET/POST {seconds}: hold the stream overlays' updates this long (0-60 s) so they match a late camera feed.
    Pushed to open overlays at once; ?delay=N on an overlay URL overrides it for that overlay."""
    if flask.request.method == 'POST':
        if not app.config.get('LOGIN_DISABLED') and not flask_login.current_user.is_authenticated:
            return flask.jsonify({'error': 'login required'}), 401
        d = flask.request.get_json(silent=True) or {}
        try:
            settings['overlay_delay'] = round(max(0.0, min(60.0, float(d.get('seconds')))), 1)
        except (TypeError, ValueError):
            return flask.jsonify({'error': 'seconds must be a number'}), 400
        save_settings()
        _socketio_emit('overlay_delay', {'seconds': settings['overlay_delay']}, namespace='/scoreboard')
    return flask.jsonify({'seconds': float(settings.get('overlay_delay', 0) or 0)})


@app.route('/favicon.ico')
def route_favicon():
    return flask.send_from_directory(app.static_folder, 'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/team_colors', methods=['GET', 'POST'])
@flask_login.login_required
def route_team_colors():
    teams_in_meet = meet_board.schedule_teams(event_info)
    if flask.request.method == 'POST':
        saved = {}
        codes = set(flask.request.form.getlist('code'))
        new_code = flask.request.form.get('new_code', '').strip().upper()[:6]
        if new_code:
            codes.add(new_code)
        # codes another school lists as its own (typed here or built in): a
        # nameless row for one of those is just the auto color the form carried
        # along - saving it would split it off as a separate "team"
        claimed = set()
        for c in codes:
            claimed.update(a for a in re.split(r'[\s,;/]+', flask.request.form.get('aliases_' + c, '').upper()) if a and a != c)
        for c, v in meet_board.DEFAULT_TEAM_COLORS.items():
            claimed.update(a.upper() for a in v.get('aliases', []) if a.upper() != c)
        for code in codes:
            if flask.request.form.get('reset_' + code):
                continue
            if code in claimed and not flask.request.form.get('name_' + code, '').strip():
                continue
            aliases = [a for a in re.split(r'[\s,;/]+', flask.request.form.get('aliases_' + code, '').upper())
                       if a and a != code and team_logos.valid_code(a)][:8]
            entry = {
                'aliases': aliases,
                'name': flask.request.form.get('name_' + code, '').strip()[:30],
                'color': flask.request.form.get('color_' + code, '').strip(),
                'alt': flask.request.form.get('alt_' + code, '').strip(),
            }
            if code == new_code and not entry['color']:
                entry['color'] = flask.request.form.get('new_color', '').strip()
            if flask.request.form.get('noalt_' + code):
                entry['alt'] = ''
            if entry['color']:
                saved[code] = entry
        settings['team_colors'] = saved
        style = flask.request.form.get('board_style', '')
        if style in meet_board.BOARD_STYLES:
            settings['board_style'] = style
        home = flask.request.form.get('board_home_team', '').strip()
        settings['board_home_team'] = home
        save_settings()
        send_event_info()
        return flask.redirect('/team_colors')
    colors = meet_board.team_colors(settings, teams_in_meet, _team_names())
    order = teams_in_meet + sorted(c for c in colors if c not in teams_in_meet)
    return flask.render_template('team_colors.html', colors=colors, order=order,
                                 in_meet=set(teams_in_meet),
                                 home_team=settings.get('board_home_team') or settings.get('team_home_tag', ''),
                                 board_style=settings.get('board_style', 'classic'),
                                 board_styles=meet_board.BOARD_STYLES,
                                 logos=_meet_logos(order),   # STAN shows the logo saved as SHS
                                 score_teams=[(k, label, settings.get(k, ''), settings.get(k + '_tag', ''))
                                              for k, label in (('team_home', 'Home'), ('team_guest1', 'Guest 1'),
                                                               ('team_guest2', 'Guest 2'), ('team_guest3', 'Guest 3'))],
                                 msg=flask.request.args.get('msg', ''))

@app.route('/software_update')
@flask_login.login_required
def route_software_update():
    return flask.render_template('update.html', app_version=updater.display_version(),
                                 can_self_update=app_paths.FROZEN and os.name == 'nt',
                                 repo=updater.REPO)

@app.route('/update_check')
@flask_login.login_required
def route_update_check():
    info = updater.check(settings.get('github_token') or None)
    info['status'] = updater.status()
    return flask.jsonify(info)

@app.route('/update_apply', methods=['POST'])
@flask_login.login_required
def route_update_apply():
    ok, msg = updater.start_update(settings.get('github_token') or None)
    return flask.jsonify({'ok': ok, 'message': msg, 'status': updater.status()})

@app.route('/update_status')
@flask_login.login_required
def route_update_status():
    return flask.jsonify(updater.status())

def has_no_empty_params(rule):
    defaults = rule.defaults if rule.defaults is not None else ()
    arguments = rule.arguments if rule.arguments is not None else ()
    return len(defaults) >= len(arguments)

@app.route("/")
def route_site_map():
    # Collect all browsable routes (keyed by endpoint) so we can group them
    all_links = {}
    for rule in app.url_map.iter_rules():
        if "GET" in rule.methods and has_no_empty_params(rule):
            url = flask.url_for(rule.endpoint, **(rule.defaults or {}))
            title = rule.endpoint.replace("_", " ")
            if title.startswith('route '):
                title = title[6:]
            if title in ['login', 'logout', 'site map']:
                continue
            # Hide these action-style endpoints from the site map
            if title in ['favicon', 'schedule clear', 'schedule undo', 'standards clear', 'update check', 'update status', 'health']:
                continue
            all_links[title] = (url, title.title())

    # Discover web/ scoreboard templates
    web_links = {}
    for file in glob.glob(app_paths.bundle_path("templates", "web", "*.html")):
        name = os.path.basename(file).rsplit('.', 1)[0]
        url = "/web/" + name
        title = "Meet Board (spectator TVs)" if name == 'meetboard' else "Web " + name
        web_links[name] = (url, title)

    def _pop(d, key):
        return d.pop(key, None)

    sections = []

    # View Scoreboard: Web Home first, then any other web templates
    view_items = []
    home = _pop(web_links, 'home')
    if home:
        view_items.append(home)
    board = _pop(web_links, 'meetboard')
    if board:
        view_items.append(board)
    view_items.append(('/overlay/race?test', 'Stream overlay (OBS Browser Source: /overlay/race, 1920x1080)'))
    for key in sorted(web_links.keys()):
        view_items.append(web_links[key])
    if view_items:
        sections.append(("View Scoreboard", view_items))

    # Settings section: Settings, Combine Events, Schedule Preview (in that order)
    settings_items = []
    for key in ['settings', 'team colors', 'combine events', 'schedule preview', 'software update']:
        link = _pop(all_links, key)
        if link:
            settings_items.append(link)
    if settings_items:
        sections.append(("Settings", settings_items))

    # Everything else falls into "Other" so nothing disappears accidentally
    other_items = [all_links[k] for k in sorted(all_links.keys())]
    if other_items:
        sections.append(("Other", other_items))

    return flask.render_template('site_map.html', sections=sections)
    

@app.context_processor
def inject_ad():
    return dict(ad_url=settings['ad_url'])
    
# callback to reload the user object        
@login_manager.user_loader
def load_user(userid):
    return User(userid)
    
    
def main():
    global in_file, out_file, in_speed, debug_console

    import sys
    if '--version' in sys.argv[1:]:
        # Answer before touching settings.json: the launcher asks this while the server runs
        print(__version__)
        return

    load_settings()
    migrate_settings()
    ensure_secret_key()

    parser = argparse.ArgumentParser(description='meet-board: CTS scoreboard overlay and spectator meet board.')
    parser.add_argument('--port', '-p', action = 'store', default = '', 
        help='Serial port input from CTS scoreboard')
    parser.add_argument('--in', '-i', action = 'store', default = '', dest='in_file',
        help='Input file to use instead of serial port')
    parser.add_argument('--out', '-o', action = 'store', default = '', 
        help='Output file to dump data')
    parser.add_argument('--portlist', '-l', action = 'store_const', const=True, default = False,
        help='List of available serial ports')        
    parser.add_argument('--speed', '-s', action = 'store', default = 1.0, dest='in_speed',
        help='Speed to play input file at')
    parser.add_argument('--debug', '-d', action = 'store_const', const=True, default = False,
        help='Display debug info at console')
    parser.add_argument('--version', action='version', version=__version__)
    args = parser.parse_args()

    try:
        if (args.portlist):
            print ("Available COM ports:")
            for port, desc, id in serial.tools.list_ports.comports():
                print (port, desc, id)
        if (args.port):
            settings['serial_port'] = args.port
        in_file = args.in_file
        out_file = args.out
        in_speed = float(args.in_speed)
        debug_console = args.debug
        ap.c()
        port = int(settings.get('http_port', 5000) or 5000)
        print("meet-board %s" % __version__)
        print("  Settings:   http://localhost:%d/settings" % port)
        print("  Meet board: http://<this-pc>:%d/web/meetboard" % port)
        print("  Overlay:    http://localhost:%d/overlay/1080p" % port)
        # Start the CTS reader now rather than waiting for the first browser
        global main_thread
        if main_thread is None:
            main_thread = socketio.start_background_task(target=main_thread_worker)
        socketio.start_background_task(target=scb_watch_worker)
        seed_builtin_logos()
        # phones need https for their mic (/listen); 0 in settings turns it off
        https_server.start(app, int(settings.get('https_port', 5443) or 0), app_paths.data_path('https'))
        obs.start()   # connects in the background; retries every 5 s until OBS is up
        socketio.start_background_task(target=stream_mute_worker)
        socketio.start_background_task(target=blackout_worker)
        socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
    except:
        traceback.print_exc()
    finally:
        input('Press enter to continue...')


if __name__ == '__main__':
    main()
        
