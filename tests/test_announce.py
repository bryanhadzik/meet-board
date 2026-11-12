"""Heat announcements stitched from recorded clips."""
import io
import zipfile

import pytest

import announce as A
import CTS_Scoreboard as C

W200FR = {'sex_codes': [2], 'distance': 200, 'stroke_code': 1, 'relay': True}
M100FL = {'sex_codes': [1], 'distance': 100, 'stroke_code': 4, 'relay': False}


def test_words_and_script():
    assert A.words(5) == 'five' and A.words(21) == 'twenty-one' and A.words(40) == 'forty'
    script = dict(A.clip_script())
    assert script['event_12'] == 'Event twelve.' and script['heat_3'] == 'Heat three.'
    assert script['ev_F_200_1_R'] == "Women's two hundred freestyle relay."
    assert script['ev_M_200_5_I'] == "Men's two hundred individual medley."
    assert len([k for k in script if k.startswith('ev_')]) == 22


def test_build_new_event_and_next_heat():
    p = A.build(3, 1, 2, W200FR)
    assert [x['clip'] for x in p] == ['event_3', 'ev_F_200_1_R', 'heat_1', 'report']
    assert A.text_of(p) == "Event three. Women's 200 freestyle relay. Heat one. Please report to the blocks."
    assert [x['clip'] for x in A.build(3, 2, 2, W200FR, new_event=False)] == ['heat_2', 'report']
    assert [x['clip'] for x in A.build(9, 1, 1, M100FL)] == ['event_9', 'ev_M_100_4_I', 'report']   # one heat: no "heat one"


def test_resolve_falls_back_to_pieces():
    odd = {'sex_codes': [2], 'distance': 50, 'stroke_code': 2, 'relay': False}
    p = A.build(20, 1, 1, odd)
    have = {'event_20', 'g_F', 'd_50', 's_2', 'report'}
    assert [x['clip'] for x in A.resolve(p, have, odd)] == ['event_20', 'g_F', 'd_50', 's_2', 'report']
    assert [x['clip'] for x in A.resolve(p, {'event_20'}, odd)] == ['event_20', 'ev_F_50_2_I', 'report']


def test_pick_target():
    order = [(1, 1), (1, 2), (2, 1), (3, 1)]
    assert A.pick_target(order, (1, 1), False, set(), set()) == (1, 1)            # before the first race
    assert A.pick_target(order, (1, 1), True, set(), set()) == (1, 2)             # heat 1 in the water
    assert A.pick_target(order, (1, 1), True, set(), {(1, 2)}) == (2, 1)          # heat 2 already announced
    assert A.pick_target(order, (1, 2), False, {(1, 1), (1, 2)}, {(1, 2)}) == (2, 1)
    assert A.pick_target(order, (1, 1), False, set(), set(), override=(3, 1)) == (3, 1)
    assert A.pick_target(order, (3, 1), False, {(3, 1)}, set()) is None


@pytest.fixture
def board(monkeypatch, tmp_path):
    monkeypatch.setattr(C.app_paths, 'data_path', lambda *p: str(tmp_path.joinpath(*p)))
    monkeypatch.setattr(C, 'save_settings', lambda: None)
    monkeypatch.setitem(C.settings, 'announce', {'auto': False, 'delay': 0})
    monkeypatch.setattr(C.meet_board, 'heat_order', lambda ei: [(1, 1), (1, 2), (2, 1)])
    monkeypatch.setattr(C.meet_board, 'heat_count', lambda ei, e: 2 if e == 1 else 1)
    monkeypatch.setattr(C.event_info, 'event_meta', {1: W200FR, 2: M100FL}, raising=False)
    monkeypatch.setattr(C, 'last_event_sent', (1, 1))
    monkeypatch.setattr(C.music_state, 'speaker_sid', 'spk')
    C._ann.update(announced=set(), swum=set(), override=None, last=None, timer=None)
    sent = []
    monkeypatch.setattr(C.socketio, 'emit', lambda ev, data=None, **k: sent.append((ev, data, k)))
    return sent


def test_api_play_advances(board):
    c = C.app.test_client()
    v = c.get('/api/announce').get_json()
    assert v['target']['event'] == 1 and v['target']['heat'] == 1
    assert v['target']['text'].startswith("Event one. Women's 200 freestyle relay. Heat one.")
    c.post('/api/announce', json={'action': 'play'})
    play = [d for ev, d, k in board if ev == 'announce_play'][0]
    assert all('say' in it and 'url' not in it for it in play['items'])     # no clips yet: PC voice
    v = c.get('/api/announce').get_json()
    assert (v['target']['event'], v['target']['heat']) == (1, 2) and v['target']['text'] == 'Heat two. Please report to the blocks.'


def test_clip_upload_zip_and_auto(board):
    c = C.app.test_client()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for n in ('event_1', 'heat_1', 'report', '../bad'):
            z.writestr(n + '.mp3', b'ID3fake')
    buf.seek(0)
    r = c.post('/api/announce/clips', data={'clips': (buf, 'clips.zip')}, content_type='multipart/form-data').get_json()
    assert sorted(r['saved']) == ['event_1', 'heat_1', 'report']
    c.post('/api/announce', json={'action': 'settings', 'auto': True, 'delay': 0})
    C._ann_race_over('1', '1')
    import time; time.sleep(0.5)
    play = [d for ev, d, k in board if ev == 'announce_play'][-1]
    # first announcement of event 1: names the event, then heat two; clips where they exist, PC voice otherwise
    assert play['items'][0]['url'] == '/announce/clip/event_1.mp3' and play['items'][-1]['url'] == '/announce/clip/report.mp3'
    assert [it.get('say') for it in play['items']][1:3] == ["Women's 200 freestyle relay.", 'Heat two.'] and 'url' not in play['items'][2]
    assert (1, 2) in C._ann['announced'] and (1, 1) in C._ann['swum']


def test_extra_announcement_buttons(board):
    c = C.app.test_client()
    d = c.get('/api/announce').get_json()
    assert [x['clip'] for x in d['extras']] == ['x_anthem', 'x_meeting', 'x_welcome']
    r = c.post('/api/announce', json={'action': 'extra', 'clip': 'x_anthem'}).get_json()
    assert r['now']['text'] == 'Please stand for the national anthem.'
    assert r['target']['event'] == 1 and r['last'] is None          # the heat queue doesn't move
    assert c.post('/api/announce', json={'action': 'extra', 'clip': 'nope'}).status_code == 400
    assert 'x_welcome' in dict(A.clip_script())


def test_warmup_slide_comes_down_at_start_time(board, monkeypatch):
    import datetime as dt
    monkeypatch.setattr(C, 'send_event_info', lambda: None)
    monkeypatch.setattr(C, '_warmup_broadcast', lambda: None)
    monkeypatch.setitem(C.settings, 'warmup_slide_on', True)
    monkeypatch.setitem(C.settings, 'warmup_start', '15:00')
    monkeypatch.setitem(C.settings, 'warmup_auto_done', '')
    monkeypatch.setitem(C.settings, 'schedule_meet', {'date': '2026-11-12'})
    monkeypatch.setattr(C, 'last_event_sent', (9, 9))
    assert not C.warmup_auto_off(dt.datetime(2026, 11, 12, 14, 59))       # not yet
    assert not C.warmup_auto_off(dt.datetime(2026, 11, 13, 15, 1))        # another day's meet
    assert C.warmup_auto_off(dt.datetime(2026, 11, 12, 15, 0, 5))
    assert C.settings['warmup_slide_on'] is False and C.last_event_sent == (1, 1)
    C.settings['warmup_slide_on'] = True                                  # put back up by hand: stays up
    assert not C.warmup_auto_off(dt.datetime(2026, 11, 12, 15, 30))
