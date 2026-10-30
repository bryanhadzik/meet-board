"""Warm-up slide: who's on it, what it says, and when it comes down."""
import CTS_Scoreboard as C
import meet_board


def test_slide_teams_dedupes_aliases_and_keeps_order():
    names = {'TOOEL': 'TOOELE HIGH SCHOOL', 'SHS': 'Stansbury High School'}
    teams = meet_board.slide_teams({}, ['TOOEL', 'SHS', 'STAN', 'EMERY'], names, {'TOOEL': 123})
    codes = [t['code'] for t in teams]
    assert codes == ['TOOEL', 'SHS', 'EMERY']              # STAN is the same school as SHS
    assert teams[0]['name'] == 'Tooele High School'         # all-caps meet-file names tidied
    assert teams[0]['logo'] == 123 and teams[0]['color']
    assert teams[2]['name'] == 'Emery' and teams[2]['logo'] is None


def test_payload_prefers_typed_values_then_meet_file():
    info = {'name': 'Demo Invitational', 'facility': 'Demo Aquatic Center', 'date': '2025-12-19'}
    p = meet_board.slide_payload({}, info, [])
    assert (p['title'], p['subtitle'], p['date'], p['on']) == ('Demo Invitational', 'Demo Aquatic Center', '2025-12-19', False)
    p = meet_board.slide_payload({'meet_title': 'Region 10', 'warmup_subtitle': 'Tooele HS', 'warmup_start': '16:00',
                                  'warmup_slide_on': True}, info, [])
    assert (p['title'], p['subtitle'], p['start'], p['on']) == ('Region 10', 'Tooele HS', '16:00', True)
    p = meet_board.slide_payload({'meet_title': 'Region 10', 'warmup_title': 'Senior Night'}, info, [])
    assert p['title'] == 'Senior Night'


def test_api_and_race_start_takes_it_down(monkeypatch):
    monkeypatch.setattr(C, 'save_settings', lambda: None)
    c = C.app.test_client()
    sent = []
    real = C._socketio_emit
    monkeypatch.setattr(C, '_socketio_emit', lambda ev, *a, **k: sent.append((ev, a[0] if a else None)))
    try:
        assert c.post('/api/warmup_slide', json={'start': '4:5'}).status_code == 400
        r = c.post('/api/warmup_slide', json={'on': True, 'start': '9:30', 'title': 'Home Opener'}).get_json()
        assert r['on'] is True and r['start'] == '09:30' and r['title'] == 'Home Opener'
        assert sent and sent[-1][0] == 'warmup_slide' and sent[-1][1]['on'] is True
        # the first race of the meet
        C.socketio.emit('update_scoreboard', {'race_state': 'Running'}, namespace='/scoreboard')
        assert C.settings['warmup_slide_on'] is False
        assert sent[-1][0] == 'warmup_slide' and sent[-1][1]['on'] is False
        assert c.get('/api/warmup_slide').get_json()['on'] is False
    finally:
        monkeypatch.setattr(C, '_socketio_emit', real)
        for k in ('warmup_title', 'warmup_start'):
            C.settings[k] = ''


def test_listing_has_every_heat_in_order_with_swimmers():
    import os, scb_loader
    from hytek_event_loader import HytekEventLoader
    ei = HytekEventLoader()
    scb_loader.load_scb_into(ei, scb_loader.read_scb_folder(os.path.join(os.path.dirname(__file__), '..', 'samples', 'scb')))
    evs = meet_board.slide_events(ei)
    keys = [(e['event'], h['heat']) for e in evs for h in e['heats']]
    assert keys == [k for k in meet_board.heat_order(ei) if k in set(keys)] and keys
    for e in evs:
        assert e['name']
        for h in e['heats']:
            assert h['count'] == len(e['heats'])
            assert h['lanes'] and all(l['name'] or l['team'] for l in h['lanes'])


def test_listing_only_sent_while_the_slide_is_up(monkeypatch):
    monkeypatch.setattr(C, 'save_settings', lambda: None)
    C.settings['warmup_slide_on'] = False
    assert 'events' not in C._warmup_slide()
    C.settings['warmup_slide_on'] = True
    try:
        d = C._warmup_slide()
        assert 'events' in d and 'colors' in d
    finally:
        C.settings['warmup_slide_on'] = False


def test_go_black_default_is_four_hours():
    assert C.settings['blackout_after_hours'] == 4.0 or C.settings.get('blackout_hours_v4')


def test_speed_and_hold_defaults_and_clamping():
    p = meet_board.slide_payload({}, {}, [])
    assert p['speed'] == 30 and p['hold'] == 7
    p = meet_board.slide_payload({'warmup_speed': 999, 'warmup_hold': 1}, {}, [])
    assert p['speed'] == meet_board.SPEED_MAX and p['hold'] == meet_board.HOLD_MIN
    p = meet_board.slide_payload({'warmup_speed': 'junk', 'warmup_hold': None}, {}, [])
    assert p['speed'] == 30 and p['hold'] == 7


def test_api_sets_speed_and_broadcasts_it(monkeypatch):
    monkeypatch.setattr(C, 'save_settings', lambda: None)
    c = C.app.test_client()
    sent = []
    real = C._socketio_emit
    monkeypatch.setattr(C, '_socketio_emit', lambda ev, *a, **k: sent.append((ev, a[0] if a else None)))
    old = {k: C.settings.get(k) for k in ('warmup_speed', 'warmup_hold', 'warmup_title')}
    try:
        C.settings['warmup_title'] = 'Keep Me'
        assert c.post('/api/warmup_slide', json={'speed': 'fast'}).status_code == 400
        r = c.post('/api/warmup_slide', json={'speed': 55}).get_json()
        assert r['speed'] == 55 and r['hold'] == 7 and r['title'] == 'Keep Me'   # other fields untouched
        assert sent[-1][0] == 'warmup_slide' and sent[-1][1]['speed'] == 55
        r = c.post('/api/warmup_slide', json={'speed': 500, 'hold': 12}).get_json()
        assert r['speed'] == meet_board.SPEED_MAX and r['hold'] == 12
        assert C.settings['warmup_speed'] == meet_board.SPEED_MAX
        assert r['limits']['speed'][2] == 30
    finally:
        monkeypatch.setattr(C, '_socketio_emit', real)
        defaults = {'warmup_speed': 30, 'warmup_hold': 7, 'warmup_title': ''}
        for k, v in old.items():
            C.settings[k] = defaults[k] if v is None else v
