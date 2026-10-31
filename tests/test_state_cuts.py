"""Last season's state qualifying times on the meet board."""
import pytest

import CTS_Scoreboard as C
import meet_board as M


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


def test_cut_matches_event_by_sex_stroke_distance_relay():
    times = {M.cut_key(2, 1, 200, False): 120.5, M.cut_key(2, 1, 200, True): 105.0,
             M.cut_key(1, 1, 200, False): 110.0}
    cuts = {'on': True, 'times': times}
    girls_free = {'stroke_code': 1, 'distance': 200, 'relay': False, 'sex_codes': [2]}
    assert M.state_cut_for(girls_free, cuts) == [{'sex': 'Girls', 'time': '2:00.50', 'seconds': 120.5}]
    relay = dict(girls_free, relay=True)
    assert M.state_cut_for(relay, cuts)[0]['time'] == '1:45.00'
    mixed = dict(girls_free, sex_codes=[1, 2])
    assert [x['sex'] for x in M.state_cut_for(mixed, cuts)] == ['Girls', 'Boys']
    assert M.state_cut_for(dict(girls_free, distance=50), cuts) == []
    assert M.state_cut_for(girls_free, dict(cuts, on=False)) == []
    assert M.state_cut_for(None, cuts) == []


def test_api_saves_validates_and_broadcasts(monkeypatch):
    monkeypatch.setattr(C, 'save_settings', lambda: None)
    sent = []
    monkeypatch.setattr(C.socketio, 'emit', lambda ev, data=None, **k: sent.append((ev, data)))
    old = C.settings.get('state_cuts')
    c = C.app.test_client()
    k = M.cut_key(2, 1, 50, False)
    try:
        assert c.post('/api/state_cuts', json={'times': {k: 'fast'}}).status_code == 400
        d = c.post('/api/state_cuts', json={'on': True, 'label': '2025 4A State',
                                            'times': {k: '25.9', 'junk|key': '1:00'}}).get_json()
        assert d['on'] and d['label'] == '2025 4A State'
        row = [r for r in d['rows'] if r['name'] == '50 Freestyle'][0]
        assert row['girls']['time'] == '25.90' and row['boys']['time'] == ''
        assert C.settings['state_cuts']['times'] == {k: 25.9}
        assert any(ev == 'update_scoreboard' and 'state_cut' in (data or {}) for ev, data in sent)
        # blank box removes that cut; clear removes them all
        c.post('/api/state_cuts', json={'times': {k: ''}})
        assert C.settings['state_cuts']['times'] == {}
        c.post('/api/state_cuts', json={'times': {k: '25.9'}})
        c.post('/api/state_cuts', json={'clear': True})
        assert C.settings['state_cuts']['times'] == {}
        assert len(c.get('/api/state_cuts').get_json()['rows']) == 11
    finally:
        C.settings['state_cuts'] = old if old is not None else {'on': False, 'label': '', 'times': {}}


def test_fill_from_loaded_st2(monkeypatch):
    from datetime import date
    from hytek_st2_parser import St2File, St2Header, St2Event, CourseStandards, QualifyingTime, TimeStandard
    monkeypatch.setattr(C, 'save_settings', lambda: None)
    monkeypatch.setattr(C.socketio, 'emit', lambda *a, **k: None)
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
    old = C.settings.get('state_cuts')
    try:
        assert C._state_cuts_from_st2('ST') == {M.cut_key(2, 1, 50, False): 25.5, M.cut_key(1, 1, 400, True): 200.1}
        c = C.app.test_client()
        d = c.post('/api/state_cuts', json={'from_tag': 'ST'}).get_json()
        assert [t['tag'] for t in d['st2_tags']] == ['ST', 'AA']
        assert c.post('/api/state_cuts', json={'from_tag': 'ZZ'}).status_code == 400
    finally:
        C.settings['state_cuts'] = old if old is not None else {'on': False, 'label': '', 'times': {}}


def test_payload_for_current_event(monkeypatch):
    meta = {7: {'stroke_code': 3, 'distance': 100, 'relay': False, 'sex_codes': [1]}}
    monkeypatch.setattr(C.event_info, 'event_meta', meta, raising=False)
    monkeypatch.setitem(C.settings, 'state_cuts',
                        {'on': True, 'label': '2025 4A State', 'times': {M.cut_key(1, 3, 100, False): 61.2}})
    assert C._state_cut_payload(7) == {'label': '2025 4A State',
                                       'items': [{'sex': 'Boys', 'time': '1:01.20', 'seconds': 61.2}]}
    assert C._state_cut_payload(8) is None
