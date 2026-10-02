from blackout import Blackout


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


def make(**settings):
    c = Clock()
    return Blackout(dict(settings), lambda: None, clock=c), c


def test_auto_black_after_two_hours_idle():
    b, c = make()
    assert not b.is_black()
    c.t += 2 * 3600 - 1
    assert not b.is_black()
    c.t += 2
    assert b.is_black() and b.status()['reason'] == 'auto'


def test_repeated_identical_updates_are_not_activity():
    b, c = make()
    b.watch_update({'current_event': '4', 'current_heat': '1', 'race_state': 'Clear', 'running_time': '0.0'})
    for _ in range(100):
        c.t += 100
        b.watch_update({'current_event': '4', 'current_heat': '1', 'race_state': 'Clear', 'running_time': '0.0'})
    assert b.is_black()


def test_clock_changes_only_count_while_running():
    b, c = make()
    b.watch_update({'race_state': 'Clear', 'running_time': '10:01'})
    c.t += 3 * 3600
    assert not b.watch_update({'running_time': '10:02'})   # time of day on an idle console
    assert b.is_black()
    assert b.watch_update({'race_state': 'Running'})          # race starts: wakes
    assert not b.is_black()
    c.t += 60
    b.watch_update({'running_time': '1:00.0'})
    assert b.status()['idle_seconds'] == 0


def test_lane_time_and_event_change_wake():
    b, c = make()
    b.watch_update({'lane_time4': '', 'current_event': '4'})
    c.t += 3 * 3600
    assert b.is_black()
    assert b.watch_update({'current_event': '5'})
    assert not b.is_black()
    c.t += 3 * 3600
    assert b.watch_update({'lane_time4': '1:05.23'})
    assert not b.is_black()


def test_manual_stays_until_woken_and_auto_can_be_off():
    b, c = make(blackout_auto=False)
    c.t += 10 * 3600
    assert not b.is_black()
    b.set_manual(True)
    b.watch_update({'race_state': 'Running'})
    assert b.is_black() and b.status()['reason'] == 'manual'
    b.set_manual(False)
    assert not b.is_black()


def test_configure_hours():
    b, c = make()
    b.configure(auto=True, hours=0.5)
    c.t += 1801
    assert b.is_black()
    b.configure(hours=100)
    assert b.after_hours == 24.0
