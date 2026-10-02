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
import meet_board
import team_logos
import updater
import obs_client
import music
from _version import __version__

DEBUG = False
#DEBUG = True
settings_file = app_paths.data_path('settings.json')

settings = {
    'meet_title': '',
    'serial_port': 'COM1',
    'username': 'admin',
    'password': 'password',
    'ad_url': '',
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
    return _socketio_emit(event, *args, **kwargs)


socketio.emit = _emit_and_remember

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

SETTINGS_VERSION = 1

def migrate_settings():
    """One-time fixes for settings.json files written by older versions."""
    v = int(settings.get('settings_version', 0) or 0)
    if v < 1:
        # v0 defaulted to 6 lanes and saved it; the pool has 8.
        if int(settings.get('num_lanes', 6) or 6) == 6:
            settings['num_lanes'] = 8
    if v < SETTINGS_VERSION:
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
        if "current_event" in update or "running_time" in update:
            race_fsm.evaluate_update(channel_running, update)
            update["race_state"] = race_fsm.state_name
            socketio.emit('update_scoreboard', update, namespace='/scoreboard')
            update.clear()
            
        if (datetime.datetime.now() > next_update) and debug_console:
            next_update = datetime.datetime.now() + datetime.timedelta(seconds=0.2)
            ap.render()


def main_thread_worker():
    j = None
    if in_file and in_file.lower().endswith('.bin'):
        # Raw bytes captured from the serial port: replay at 9600 baud x speed
        with open(in_file, 'rb') as f:
            data = f.read()
        while True:
            l = []
            for n, c in enumerate(data):
                if c:
                    if (c & 0x80) or (len(l) > 8):
                        if len(l):
                            parse_line(l)
                        l = []
                    l.append(c)
                if n % 96 == 0:
                    socketio.sleep(0.1 / max(in_speed, 0.01))   # 96 bytes = 0.1 s at 9600 baud (8N1 ~ 960 B/s)
            socketio.sleep(2.0)  # loop the recording
    elif in_file:
        delay = 0.0
        start_time = None
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
                if c:
                    if (c & 0x80) or (len(l) > 8):
                        if len(l):
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
            try:
                with serial.Serial(port, 9600, timeout=0) as f:
                    print("Reading CTS data from %s" % port)
                    last_error = None
                    l = []
                    while port == settings['serial_port']:
                        c = f.read(1)
                        if c:
                            c=c[0]
                            if (c & 0x80) or (len(l) > 8):
                                if len(l):
                                    parse_line(l, j)
                                l=[]
                            l.append(c)
                        else:
                            socketio.sleep(0.01)
            except Exception as e:
                if str(e) != last_error:
                    print("Serial port %s unavailable (%s); retrying every 5 s" % (port, e))
                    last_error = str(e)
                socketio.sleep(5.0)

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

    socketio.emit('update_scoreboard', update, namespace='/scoreboard')

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
    flask_socketio.emit('server_info', {'version': __version__, 'boot': BOOT_ID})

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
            'status': _dbg['status'], 'heats': [list(k) for k in meet_board.heat_order(event_info)]}


# ---------------------------------------------------------------------------
# Music tab: playlist in DATA_DIR/music, played by /music?speaker=1 on the
# streaming PC, controlled from any /music page.
# ---------------------------------------------------------------------------
music_state = music.MusicState()

def _music_folder():
    return music.music_dir(app_paths.data_path)

def _music_songs():
    return music.list_songs(_music_folder())

def _music_broadcast(include_songs=False):
    socketio.emit('music_state', music_state.as_dict(_music_songs() if include_songs else None),
                  namespace='/music')

@app.route('/music')
def route_music():
    return flask.render_template('music.html', speaker='speaker' in flask.request.args,
                                 songs=_music_songs(), music_folder=_music_folder())

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
    _music_broadcast(include_songs=True)
    return flask.jsonify({'songs': _music_songs()})

@socketio.on('connect', namespace='/music')
def ws_music_connect():
    flask_socketio.emit('music_state', music_state.as_dict(_music_songs()))

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

@socketio.on('speaker_report', namespace='/music')
def ws_music_report(d=None):
    if flask.request.sid != music_state.speaker_sid:
        return
    ended = music_state.report(d or {})
    if ended:
        _music_broadcast()
    else:
        socketio.emit('music_progress', {'position': music_state.position, 'duration': music_state.duration,
                                         'track': music_state.track}, namespace='/music')

@socketio.on('music_cmd', namespace='/music')
def ws_music_cmd(d=None):
    d = d or {}
    err = music_state.command(d.get('action'), d.get('track'), d.get('volume'),
                              [x['file'] for x in _music_songs()])
    if err:
        flask_socketio.emit('music_error', {'error': err})
        return
    _music_broadcast()

# Scoreboard Templates
@app.route('/overlay/<name>')
def route_overlay(name):
    overlay_name = "overlay/" + name + '.html'
    return flask.render_template(overlay_name, meet_title=settings['meet_title'], test_background='test' in flask.request.args.keys(), num_lanes=settings['num_lanes'])
    
@app.route('/web/<name>')
def route_web(name):
    web_name = "web/" + name + '.html'
    test_event = flask.request.args.get('event', None)
    test_heat = flask.request.args.get('heat', None)
    return flask.render_template(web_name, meet_title=settings['meet_title'], test_background='test' in flask.request.args.keys(), num_lanes=settings['num_lanes'], test_event=test_event, test_heat=test_heat, ad_url=settings['ad_url'], schedule_has_names=event_info.has_names, team_names=[('score_home', settings.get('team_home', '')), ('score_guest1', settings.get('team_guest1', '')), ('score_guest2', settings.get('team_guest2', '')), ('score_guest3', settings.get('team_guest3', ''))], team_tags=[('score_home', settings.get('team_home_tag', '')), ('score_guest1', settings.get('team_guest1_tag', '')), ('score_guest2', settings.get('team_guest2_tag', '')), ('score_guest3', settings.get('team_guest3_tag', ''))])

@app.route('/settings', methods=['POST', 'GET'])
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
                
    comm_port_list = [(port, "%s: %s" % (port,desc)) for port, desc, id in serial.tools.list_ports.comports()]
    if settings['serial_port'] not in [port for port,desc in comm_port_list]:
        comm_port_list.insert(0, (settings['serial_port'], settings['serial_port']))
        
    ad_url_list = []
    for dirpath, dir, file in os.walk(app_paths.bundle_path("static", "ad")):
        ad_url_list.extend(file)
 
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
    
    return flask.render_template('settings.html', 
                meet_title=settings['meet_title'], 
                serial_port=settings['serial_port'],
                serial_port_list=comm_port_list,
                user_name=settings['username'],
                ad_url_list = ad_url_list,
                ad_url=settings['ad_url'],
                num_lanes=settings['num_lanes'],
                pool_course=settings.get('pool_course', 'SCY'),
                seed_time_label=settings.get('seed_time_label', 'Seed Time'),
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
    return flask.redirect('/settings')

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
    return flask.redirect('/settings')

# ---------------------------------------------------------------- team logos
def _logo_folder():
    return team_logos.logo_dir(app_paths.data_path)


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
    uploads = [(f.filename, f.read()) for f in flask.request.files.getlist('logos') if f and f.filename]
    try:
        saved, skipped = team_logos.save_uploads(_logo_folder(), uploads)
        msg = 'Saved %d logo(s)%s' % (len(saved), (': ' + ', '.join(sorted(saved))) if saved else '')
        if skipped:
            msg += '. Skipped (name files CODE.png): ' + ', '.join(skipped[:8])
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
    return flask.redirect('/settings')

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
    return flask.redirect('/settings')
                
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
        for code in codes:
            if flask.request.form.get('reset_' + code):
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
                                 logos=_meet_logos(teams_in_meet),
                                 msg=flask.request.args.get('msg', ''))

@app.route('/software_update')
@flask_login.login_required
def route_software_update():
    return flask.render_template('update.html', app_version=__version__,
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
        obs.start()   # connects in the background; retries every 5 s until OBS is up
        socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
    except:
        traceback.print_exc()
    finally:
        input('Press enter to continue...')


if __name__ == '__main__':
    main()
        
