"""The race book against what the pool's CTS console really sent (Nov 2026)."""
from race_book import RaceBook, secs


class Clock:
    t = 1000.0
    def __call__(self):
        return self.t


def heat(ev, names, dist=100):
    u = {'current_event': str(ev), 'current_heat': '1', 'event_name': 'Event %d' % ev, 'event_distance': dist}
    for i in range(1, 9):
        u['lane_name%d' % i] = names.get(i, '')
        u['lane_team%d' % i] = 'SHS' if i in names else ''
    return u


def test_secs():
    assert secs(' 1:16.80') == 76.8 and secs('34.96') == 34.96 and secs('  0.0') == 0.0 and secs('') is None


def go(book, clock_s, lanes=None, **kw):
    u = {'running_time': clock_s}
    u.update(kw)
    for i, v in (lanes or {}).items():
        if v == 'R':
            u['lane_running%d' % i] = True
        else:
            u['lane_running%d' % i] = False
            u['lane_time%d' % i] = v
    book.feed(u)


def start(book, lanes):
    go(book, '  0.0', race_state='PreRace')
    go(book, '1', {i: 'R' for i in lanes})            # the odd first readings the console sends
    go(book, '8')
    go(book, '1.4')
    go(book, '2.1')
    go(book, '2.7')


def test_e13_100_breast_splits_laps_and_lane_flips():
    book = RaceBook()
    book.feed(heat(13, {3: 'Ava', 4: 'Mia', 5: 'Ivy', 6: 'Bea'}))
    start(book, [3, 4, 5, 6])
    assert book.racing
    # 50 splits: every lane stopped at once, console says Finished - still racing
    go(book, '50.5', {4: '34.96', 6: '38.68', 5: '40.45', 3: '49.68'}, race_state='Finished')
    assert book.racing and book.payload()['last']['4'] == '34.96' and book.payload()['hold'] is None
    go(book, '57', {3: 'R', 4: 'R', 5: 'R', 6: 'R'}, race_state='Clear')
    assert book.payload()['last']['4'] == '34.96'            # last touch stays up
    go(book, '1:16.8', {4: '1:16.80'}); go(book, '1:17.5', {4: '41.84'})
    assert book.payload()['last']['4'] == '41.84'            # mid-race: what the scoreboard shows (the lap time it flashes)
    assert book.lanes[4]['max'] == 76.8                      # ... while the total is kept for the results
    go(book, '1:18', {4: 'R'})
    go(book, '1:16.8', {4: '1:16.80'}); go(book, '1:17.5', {4: '41.84'})   # finish, then the lap time
    go(book, '1:20', {6: '1:20.00'}); go(book, '1:25.1', {5: '1:25.10'}); go(book, '1:40', {3: '1:40.00'})
    go(book, '1:56', {4: 'R', 6: 'R'})                       # lanes flip back to running
    go(book, '  0.0')                                        # operator resets: race over
    p = book.payload()
    assert not p['racing']
    lanes = p['hold']['lanes']
    assert {k: (v['time'], v['place']) for k, v in lanes.items()} == \
        {'4': ('1:16.80', '1'), '6': ('1:20.00', '2'), '5': ('1:25.10', '3'), '3': ('1:40.00', '4')}
    assert lanes['4']['lane_name'] == 'Mia' and p['hold']['heat_key'] == '13/1'
    # the computer moves to event 14: still holding event 13
    book.feed(heat(14, {3: 'Max', 4: 'Josh'}))
    go(book, '  0.0', race_state='PreRace')
    assert book.payload()['hold']['current_event'] == '13'
    # one stray reading doesn't start a race; a real count-up does
    go(book, '3')
    assert book.payload()['hold'] is not None
    go(book, '  0.0'); go(book, '0.4', {3: 'R', 4: 'R'}); go(book, '1.1'); go(book, '1.8')
    p = book.payload()
    assert p['racing'] and p['hold'] is None


def test_e15_200_lap_only_and_stray_touch():
    book = RaceBook()
    book.feed(heat(15, {2: 'A', 3: 'B', 4: 'C'}, dist=200))
    start(book, [2, 3, 4])
    seq = [('31', {4: '30.70'}), ('38.7', {2: '38.11', 3: '38.58'}), ('46', {4: 'R', 2: 'R', 3: 'R'}),
           ('1:07.4', {4: '33.51'}), ('1:19', {3: '40.32'}), ('1:20.5', {2: '41.35', 4: 'R'}),
           ('1:35', {3: 'R', 2: 'R'}), ('1:35.7', {3: '15.54'}),                  # a stray touch
           ('1:33.6', {4: '1:33.33'}), ('1:34', {4: '29.12'}), ('1:50', {4: 'R', 3: 'R'}),
           ('2:02.7', {2: '2:02.38'}), ('2:03.7', {2: '42.92'}), ('2:05.1', {3: '1:25.98', 4: '2:04.63'}),
           ('2:18.9', {2: 'R', 3: 'R'}), ('2:40.8', {3: ''}), ('2:41.5', {3: '2:40.50'}), ('2:58.7', {2: '2:58.30'})]
    for c, lanes in seq:
        go(book, c, lanes)
    go(book, '3:00.4'); go(book, '0.0')
    got = {k: (v['time'], v['place']) for k, v in book.payload()['hold']['lanes'].items()}
    assert got == {'4': ('2:04.63', '1'), '3': ('2:40.50', '2'), '2': ('2:58.30', '3')}


def test_results_expire_and_a_heat_change_mid_race_ends_it():
    clk = Clock()
    book = RaceBook(clock=clk)
    book.feed(heat(5, {1: 'X', 2: 'Y'}, dist=50))
    start(book, [1, 2])
    go(book, '30', {1: '29.10', 2: '31.00'})
    book.feed(heat(6, {1: 'P'}))                  # moved on before resetting the clock
    p = book.payload()
    assert not p['racing'] and p['hold']['lanes']['1']['time'] == '29.10' and p['hold']['current_event'] == '5'
    clk.t += RaceBook.HOLD_S + 1
    assert book.payload()['hold'] is None


def test_no_swimmer_time_keeps_the_name():
    book = RaceBook()
    book.feed(heat(7, {1: 'Has Time', 2: 'No Touch'}, dist=50))
    start(book, [1, 2])
    go(book, '30', {1: '29.10'})
    go(book, '0.0')
    lanes = book.payload()['hold']['lanes']
    assert lanes['2']['lane_name'] == 'No Touch' and lanes['2']['time'] == ''


def test_server_sends_the_book_on_connect_and_on_change(monkeypatch):
    import CTS_Scoreboard as C
    monkeypatch.setattr(C, 'race_book', RaceBook())
    monkeypatch.setattr(C, '_race_book_last', None)
    monkeypatch.setattr(C, 'main_thread', object())       # don't start the serial worker
    client = C.socketio.test_client(C.app, namespace='/scoreboard')
    got = [m for m in client.get_received('/scoreboard') if m['name'] == 'board_race']
    assert got and got[-1]['args'][0]['hold'] is None
    emit = lambda u: C.socketio.emit('update_scoreboard', u, namespace='/scoreboard')
    emit({'current_event': '3', 'current_heat': '1', 'lane_name1': 'Zed', 'running_time': '0.0'})
    for c in ('0.4', '1.1', '1.8'):
        emit({'running_time': c, 'lane_running1': True})
    emit({'running_time': '30', 'lane_running1': False, 'lane_time1': '29.50'})
    emit({'running_time': '0.0'})
    races = [m['args'][0] for m in client.get_received('/scoreboard') if m['name'] == 'board_race']
    assert races[-1]['hold']['lanes']['1']['time'] == '29.50' and races[-1]['expires_in'] > 0
    # a page connecting now (a refresh) gets the held results straight away
    again = C.socketio.test_client(C.app, namespace='/scoreboard')
    first = [m['args'][0] for m in again.get_received('/scoreboard') if m['name'] == 'board_race']
    assert first[-1]['hold']['lanes']['1']['lane_name'] == 'Zed'


def test_a_split_without_a_place_drops_off_after_15_seconds():
    clk = Clock()
    book = RaceBook(clock=clk)
    book.feed(heat(21, {3: 'GHS B', 4: 'MILL A'}, dist=400))
    start(book, [3, 4])
    go(book, '1:12.9', {4: '1:12.88'}); go(book, '1:27.3', {3: '1:27.27'})
    assert book.payload()['last'] == {'4': '1:12.88', '3': '1:27.27'}
    clk.t += 16
    go(book, '1:44')                                   # clock keeps ticking, no new touch
    assert book.payload()['last'] == {}                # both splits gone
    go(book, '2:28.9', {4: '2:28.89'})                 # next touch shows again
    assert book.payload()['last'] == {'4': '2:28.89'}
    # a touch the console gives a place stays up
    book.feed({'lane_place4': '1'}); go(book, '2:30')
    clk.t += 60; go(book, '3:30')
    assert book.payload()['last'] == {'4': '2:28.89'}
    # and the results at the reset still use every lane's largest time
    go(book, '0.0')
    assert {k: v['time'] for k, v in book.payload()['hold']['lanes'].items()} == {'4': '2:28.89', '3': '1:27.27'}


def test_all_touched_fires_once_when_every_swimmer_has_a_time():
    b = RaceBook(Clock())
    got = []
    b.on_all_touched = lambda e, h: got.append((e, h))
    b.feed(heat(7, {3: 'A', 4: 'B'}))
    start(b, [3, 4])
    go(b, '30.0', {3: '29.50', 4: '30.10'})      # the 50 split: both stopped, no places
    assert got == []
    go(b, '31.0', {3: 'R', 4: 'R'})
    go(b, '59.0', {3: '58.50'}, lane_place3='1')  # one touched, one still swimming
    assert got == []
    go(b, '61.0', {4: '1:00.80'}, lane_place4='2')
    assert got == [('7', '1')]
    go(b, '32.0')
    assert got == [('7', '1')]


def test_places_show_as_each_lane_finishes():
    """The console places a lane when it finishes; the boards show it then, not when the whole heat is done."""
    book = RaceBook()
    book.feed(heat(2, {3: 'Ava', 4: 'Mia', 5: 'Ivy', 6: 'Bea'}, dist=200))
    start(book, [3, 4, 5, 6])
    go(book, '50.5', {6: '46.74'})                                 # a split: no place
    assert book.payload()['place'] == {}
    go(book, '1:56', {5: '1:55.98'}, lane_place5='1')
    go(book, '2:09', {4: '2:08.91'}, lane_place4='2')
    p = book.payload()
    assert p['racing'] and p['place'] == {'5': '1', '4': '2'}
    assert p['last']['5'] == '1:55.98'


def test_near_end_from_splits_and_seeds():
    """Announce when the slowest swimmer still in the water should have ~5 yards to go."""
    book = RaceBook()
    fired = []
    book.on_near_end = lambda e, h: fired.append((e, h))
    book.near_yards = 5
    u = heat(9, {4: 'Ava', 5: 'Mia'}, dist=100)
    u.update(lane_seed_time4='1:05.00', lane_seed_time5='1:10.00')
    book.feed(u)
    start(book, [4, 5])
    assert abs(book.near_end_estimate() - 70 * 95 / 100) < 0.01      # before any split: the slowest seed
    go(book, '31', {4: '30.00'})                                    # lane 4's 50
    go(book, '34', {5: '33.00'})                                    # lane 5's 50: 33 + 45 yd at 33/50*1.08
    est = book.near_end_estimate()
    assert abs(est - (33 + 45 * 33 / 50 * 1.08)) < 0.01
    go(book, '%.1f' % (est - 1))
    assert not fired
    go(book, '%.1f' % (est + 0.2))
    assert fired == [('9', '1')]
    go(book, '%.1f' % (est + 1))
    assert len(fired) == 1                                          # once per race
