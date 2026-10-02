import os
import time

import CTS_Scoreboard as C
import scb_loader

SCB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'samples', 'scb')


def setup_module(_):
    scb_loader.load_scb_into(C.event_info, scb_loader.read_scb_folder(SCB_DIR))


def post(client, action, **body):
    r = client.post('/debug/' + action, json=body)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def test_goto_next_prev_and_state():
    c = C.app.test_client()
    d = post(c, 'goto', event=3, heat=5)
    assert (d['event'], d['heat'], d['race_state']) == (3, 5, 'PreRace')
    d = post(c, 'next')
    assert (d['event'], d['heat']) == (10, 1)
    d = post(c, 'prev')
    assert (d['event'], d['heat']) == (3, 5)
    assert [3, 1] in d['heats'] and len(d['heats']) == 9


def test_race_runs_to_finished_with_places():
    c = C.app.test_client()
    post(c, 'goto', event=3, heat=1)
    post(c, 'start', speed=50)
    assert C.race_fsm.state_name == 'Running'
    post(c, 'finish')
    for _ in range(50):
        if C.race_fsm.state_name == 'Finished':
            break
        time.sleep(0.1)
    assert C.race_fsm.state_name == 'Finished'
    d = post(c, 'blank')
    assert d['race_state'] == 'TotalBlank'


def test_unknown_action():
    assert C.app.test_client().post('/debug/nope', json={}).status_code == 404


def test_seed_mode_beat_and_miss(monkeypatch):
    import CTS_Scoreboard as app
    seeds = {1: 60.0, 2: 30.0}
    monkeypatch.setattr(app, '_dbg_occupied_lanes', lambda: [1, 2])
    monkeypatch.setattr(app.event_info, 'get_seed_time', lambda e, h, l: seeds[l])
    for mode, ok in (('beat', lambda t, sd: t < sd), ('miss', lambda t, sd: t > sd)):
        app._dbg['seeds'] = mode
        for _ in range(50):
            for lane, t in app._dbg_finish_times().items():
                assert ok(t, seeds[lane]), (mode, lane, t)
    app._dbg['seeds'] = 'mixed'
