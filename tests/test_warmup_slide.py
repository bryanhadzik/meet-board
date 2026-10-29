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
