"""Last season's state cuts on the meet board, per UHSAA classification."""
import json

import pytest

import CTS_Scoreboard as C
import meet_board as M


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(C, 'save_settings', lambda: None)
    sent = []
    monkeypatch.setattr(C.socketio, 'emit', lambda ev, data=None, **k: sent.append((ev, data)))
    old = json.loads(json.dumps(C.settings.get('state_cuts')))
    yield sent
    C.settings['state_cuts'] = old


def test_parse_and_format_times():
    assert M.parse_swim_time('1:52.34') == 112.34
    assert M.parse_swim_time(' 23.4 ') == 23.4
    assert M.parse_swim_time('') is None
    for bad in ('abc', '1:75.00', '0', '1.2.3'):
        with pytest.raises(ValueError):
            M.parse_swim_time(bad)
    assert M.format_swim_time(112.3) == '1:52.30'
    assert M.format_swim_time(59.999) == '1:00.00'
    assert M.format_swim_time(23.05) == '23.05'


def test_builtin_2026_cuts_are_complete_and_sane():
    b = M.builtin_state_cuts()
    assert set(b) == {'4A', '3A'}
    for cls, t in b.items():
        assert len(t) == 22 and all(v and v > 20 for v in t.values())
    # spot checks against the Feb 2026 psych sheets
    assert b['4A'][M.cut_key(2, 1, 100, False)] == 62.24       # girls 100 free 1:02.24
    assert b['4A'][M.cut_key(1, 1, 400, True)] == 215.87       # boys 400 free relay 3:35.87
    assert b['3A'][M.cut_key(2, 1, 50, False)] == 33.85
    assert b['3A'][M.cut_key(1, 1, 500, False)] == 437.28      # 7:17.28
    # 4A is faster than 3A in every individual girls event
    for stroke, dist, relay, _ in M.HS_EVENTS:
        k = M.cut_key(2, stroke, dist, relay)
        assert b['4A'][k] < b['3A'][k]


def test_county_teams_classes():
    canon = {'SHS': 'STAN', 'DPHS': 'DPEAK'}
    assert M.team_class_map({}, ['TOOEL', 'SHS', 'GHS', 'DPHS', 'XYZ'], canon) == \
        {'TOOEL': '4A', 'SHS': '4A', 'GHS': '3A', 'DPHS': '4A'}
    over = {'team_class': {'GHS': '4A', 'TOOEL': ''}}
    assert M.team_class_map(over, ['TOOEL', 'GHS'], {}) == {'GHS': '4A'}


def test_migrates_the_old_single_table():
    m = M.migrate_state_cuts({'on': False, 'label': '', 'times': {}})
    assert m['v'] == 2 and m['on'] is True and set(m['classes']) == {'4A', '3A'}
    k = M.cut_key(2, 1, 50, False)
    m = M.migrate_state_cuts({'on': False, 'label': 'Mine', 'times': {k: 26.0}})
    assert m['on'] is False and m['label'] == 'Mine' and m['classes']['4A'][k] == 26.0
    assert M.migrate_state_cuts(m) is m


def test_cut_per_class_and_sex():
    cuts = M.default_state_cuts()
    girls100 = {'stroke_code': 1, 'distance': 100, 'relay': False, 'sex_codes': [2]}
    got = M.state_cut_for(girls100, cuts, ['4A', '3A'])
    assert [(x['cls'], x['time']) for x in got] == [('4A', '1:02.24'), ('3A', '1:35.41')]
    mixed = dict(girls100, sex_codes=[1, 2])
    assert [(x['cls'], x['sex']) for x in M.state_cut_for(mixed, cuts, ['4A'])] == [('4A', 'Girls'), ('4A', 'Boys')]
    assert M.state_cut_for(dict(girls100, distance=25), cuts, ['4A']) == []
    assert M.state_cut_for(girls100, dict(cuts, on=False), ['4A']) == []
    assert M.state_cut_for(None, cuts, ['4A']) == []


def test_payload_uses_the_classes_in_the_water(monkeypatch, quiet):
    meta = {7: {'stroke_code': 3, 'distance': 100, 'relay': False, 'sex_codes': [1]}}
    monkeypatch.setattr(C.event_info, 'event_meta', meta, raising=False)
    lanes = {1: 'TOOEL', 2: 'GHS', 3: ''}
    monkeypatch.setattr(C.event_info, 'get_team_code', lambda e, h, l: lanes.get(l, ''), raising=False)
    monkeypatch.setattr(C.meet_board, 'schedule_teams', lambda ei: ['TOOEL', 'GHS', 'STAN'])
    monkeypatch.setitem(C.settings, 'board_home_team', 'TOOEL')
    C.settings['state_cuts'] = M.default_state_cuts()
    p = C._state_cut_payload(7, 1)
    assert p['label'] == '2026 State'
    assert [(x['cls'], x['time']) for x in p['items']] == [('4A', '1:10.96'), ('3A', '1:24.78')]
    assert p['team_class']['GHS'] == '3A' and p['team_class']['TOOEL'] == '4A'
    lanes.clear(); lanes.update({1: 'TOOEL', 2: 'STAN'})   # a 4A-only heat
    assert [x['cls'] for x in C._state_cut_payload(7, 1)['items']] == ['4A']
    assert C._state_cut_payload(8, 1) is None
    C.settings['state_cuts']['on'] = False
    assert C._state_cut_payload(7, 1) is None


def test_api_edit_reset_and_team_class(quiet):
    C.settings['state_cuts'] = M.default_state_cuts()
    c = C.app.test_client()
    k = M.cut_key(2, 1, 50, False)
    v = c.get('/api/state_cuts').get_json()
    assert v['classes'] == ['4A', '3A'] and len(v['rows']) == 11
    assert {t['code']: t['cls'] for t in v['teams']}['GHS'] == '3A'
    assert c.post('/api/state_cuts', json={'times': {k: '25'}}).status_code == 400          # no class
    assert c.post('/api/state_cuts', json={'cls': '4A', 'times': {k: 'fast'}}).status_code == 400
    v = c.post('/api/state_cuts', json={'cls': '4A', 'times': {k: '26.10'}}).get_json()
    cell = [r for r in v['rows'] if r['name'] == '50 Freestyle'][0]['cells']['4A']['girls']
    assert cell['time'] == '26.10' and cell['builtin'] is False
    assert any(ev == 'update_scoreboard' and 'state_cut' in (d or {}) for ev, d in quiet)
    c.post('/api/state_cuts', json={'cls': '4A', 'reset': True})
    assert C.settings['state_cuts']['classes']['4A'][k] == 27.95
    # Grantsville moved up; then back to its default (override removed)
    c.post('/api/state_cuts', json={'team_class': {'GHS': '4A'}})
    assert C.settings['state_cuts']['team_class'] == {'GHS': '4A'}
    c.post('/api/state_cuts', json={'team_class': {'GHS': '3A'}})
    assert C.settings['state_cuts']['team_class'] == {}
    # a class with no built-in times can be typed in
    c.post('/api/state_cuts', json={'cls': '5A', 'times': {k: '26.50'}})
    assert '5A' in c.get('/api/state_cuts').get_json()['classes']


def test_fill_from_loaded_st2(monkeypatch, quiet):
    from datetime import date
    from hytek_st2_parser import St2File, St2Header, St2Event, CourseStandards, QualifyingTime, TimeStandard
    st, other = TimeStandard('ST', 'State'), TimeStandard('AA', 'All-American')

    def ev(sex, stroke, dist, kind, lo, hi, secs):
        return St2Event(1, '', sex, '', stroke, dist, lo, hi, kind,
                        [CourseStandards('SCY', [QualifyingTime(st, secs, ''), QualifyingTime(other, secs - 5, '')]),
                         CourseStandards('LCM', [QualifyingTime(st, secs + 9, '')])])
    std = St2File(St2Header(2, date.today(), [st, other]), [
        ev(2, 1, 50, 'Individual', 14, 15, 27.0),      # narrow age group loses
        ev(2, 1, 50, 'Individual', None, None, 25.5),  # open wins
        ev(1, 1, 400, 'Relay', None, None, 200.1),
        ev(1, 1, 25, 'Individual', None, None, 12.0),  # not a high school event
    ])
    monkeypatch.setattr(C, 'time_standards', std)
    monkeypatch.setitem(C.settings, 'pool_course', 'SCY')
    C.settings['state_cuts'] = M.default_state_cuts()
    assert C._state_cuts_from_st2('ST') == {M.cut_key(2, 1, 50, False): 25.5, M.cut_key(1, 1, 400, True): 200.1}
    c = C.app.test_client()
    d = c.post('/api/state_cuts', json={'cls': '3A', 'from_tag': 'ST'}).get_json()
    assert [t['tag'] for t in d['st2_tags']] == ['ST', 'AA']
    assert C.settings['state_cuts']['classes']['3A'][M.cut_key(2, 1, 50, False)] == 25.5
    assert c.post('/api/state_cuts', json={'cls': '3A', 'from_tag': 'ZZ'}).status_code == 400
