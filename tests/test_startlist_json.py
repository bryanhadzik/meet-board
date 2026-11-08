"""Start lists pushed from MeetDeck as JSON (POST /api/startlists)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import CTS_Scoreboard as C
import meet_board
import startlist_json
from hytek_event_loader import HytekEventLoader

DOC = {
    "format": "meetdeck-startlist", "version": 1, "source": "MeetDeck test",
    "meet": {"name": "Stansbury @ Tooele", "facility": "Tooele Aquatic Center", "date": "2026-11-14"},
    "teams": {"THS": "Tooele High School", "SHS": "Stansbury High School Swim Team"},
    "events": [
        {"number": 1, "gender": "F", "distance": 200, "stroke": "E", "relay": True, "course": "Y",
         "heats": [{"heat": 1, "lanes": [
             {"lane": 4, "team": "THS", "letter": "A", "seed": 121.4,
              "legs": ["Klay", "Cox", "", "Thayn"]},
             {"lane": 5, "team": "SHS", "letter": "B", "seed": None, "legs": []}]}]},
        {"number": 2, "gender": "M", "distance": 200, "stroke": "A", "relay": False,
         "heats": [{"heat": 1, "lanes": [
             {"lane": 3, "team": "SHS", "first": "Ben", "last": "Lee", "age": 16, "sex": "M", "seed": 125.02},
             {"lane": 4, "team": "THS", "first": "Max", "last": "Ito", "age": 17, "sex": "M", "seed": 0}]},
                   {"heat": 2, "lanes": []}]},
        {"number": 3, "gender": "W", "distance": 50, "stroke": "A", "age_min": 0, "age_max": 109,
         "heats": [{"heat": 1, "lanes": [{"lane": 1, "team": "THS", "first": "Ann", "last": "Bo",
                                          "age": 18, "sex": "F", "seed": 30}]}]},
    ],
}


def test_loader_matches_hy3_shape():
    L = HytekEventLoader()
    n_ev, n_ht, n_ln, n_legs = startlist_json.load_into(L, DOC)
    assert (n_ev, n_ht, n_ln, n_legs) == (3, 3, 5, 1)          # the empty heat 2 is skipped
    assert L.get_event_name(1) == "Girls 200 Yard Medley Relay"
    assert L.get_event_name(2) == "Boys 200 Yard Freestyle"
    assert L.get_event_name(3) == "Women 50 Yard Freestyle"
    assert L.get_display_string(1, 1, 4) == "Tooele A"
    assert L.get_display_string(1, 1, 5) == "Stansbury B"
    assert L.get_relay_legs(1, 1, 4) == ["Klay", "Cox", "Thayn"]
    assert L.get_relay_legs(1, 1, 5) == []
    assert L.get_display_string(2, 1, 3) == "Ben Lee"
    assert L.get_team_code(2, 1, 4) == "THS"
    assert L.get_age_code(2, 1, 3) == "16B" and L.get_age_code(3, 1, 1) == "18W"
    assert L.get_seed_time(2, 1, 3) == 125.02 and L.get_seed_time(2, 1, 4) is None
    assert L.event_meta[1]["relay"] and L.event_meta[1]["stroke_code"] == 5
    assert L.team_names["THS"] == "Tooele High School"
    assert L.meet_info["name"] == "Stansbury @ Tooele"
    # survives the settings round trip the server uses
    L2 = HytekEventLoader()
    L2.from_object(L.to_object())
    assert L2.get_relay_legs(1, 1, 4) == ["Klay", "Cox", "Thayn"]
    # and reaches the meet board's lane rows
    rows = meet_board.lanes_for(L2, (1, 1), 8)
    assert rows[3]["legs"] == ["Klay", "Cox", "Thayn"] and rows[3]["name"] == "Tooele A"


def test_bad_documents_are_refused():
    for doc in (None, [], {"events": []}, {"format": "other", "events": [{}]},
                {"version": 9, "events": [{"number": 1}]},
                {"events": [{"number": "x"}]}):
        try:
            startlist_json.load_into(HytekEventLoader(), doc)
        except startlist_json.StartListError:
            continue
        raise AssertionError("accepted %r" % (doc,))


def test_endpoint_loads_and_answers_cors(monkeypatch):
    monkeypatch.setattr(C, 'save_settings', lambda: None)
    monkeypatch.setattr(C, 'send_event_info', lambda: None)
    client = C.app.test_client()
    pre = client.open('/api/startlists', method='OPTIONS',
                      headers={'Origin': 'http://localhost:8001', 'Access-Control-Request-Method': 'POST'})
    assert pre.status_code == 204 and pre.headers['Access-Control-Allow-Origin'] == '*'
    r = client.post('/api/startlists', json=DOC)
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert j['ok'] and j['events'] == 3 and j['relays_with_swimmers'] == 1
    assert r.headers['Access-Control-Allow-Origin'] == '*'
    assert C.event_info.get_relay_legs(1, 1, 4) == ['Klay', 'Cox', 'Thayn']
    assert 'MeetDeck test - Stansbury @ Tooele' in C.settings['schedule_filename']
    assert C.settings['hy3_source'] == C.settings['event_info']
    g = client.get('/api/startlists').get_json()
    assert g['schedule_events'] == 3 and 'meetdeck-startlist' in g['accepts']
    # a bad push leaves the loaded schedule alone
    bad = client.post('/api/startlists', json={'events': []})
    assert bad.status_code == 400 and not bad.get_json()['ok']
    assert C.event_info.get_relay_legs(1, 1, 4) == ['Klay', 'Cox', 'Thayn']
