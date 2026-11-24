"""Touch watch: armed / disarmed lanes and the missed-touch alarms (no camera)."""
import time

import wall_watch
from race_book import RaceBook


class FakeVideo:
    def geom(self):
        return {}

    def ffmpeg(self):
        return None


def _race(tmp_path):
    book = RaceBook()
    tw = wall_watch.TouchWatch(str(tmp_path), {}, lambda: None, FakeVideo(), book)
    heat = {'current_event': '7', 'current_heat': '1', 'event_distance': '100',
            'lane_name1': 'A', 'lane_seed_time1': '1:00.00', 'lane_name2': 'B', 'lane_seed_time2': '1:00.00',
            'running_time': '0.0'}
    book.feed(heat)
    for c in ('0.3', '0.6', '0.9'):
        book.feed({'running_time': c, 'lane_running1': True, 'lane_running2': True,
                   'lane_time1': c, 'lane_time2': c})
    assert book.racing
    tw.on_start()
    tw.on_feed()
    return book, tw


def test_disarmed_after_a_touch_then_armed(tmp_path):
    book, tw = _race(tmp_path)
    book.feed({'running_time': '31.0', 'lane_running1': False, 'lane_time1': '30.50'})
    tw.on_feed()
    now = time.time()
    L = tw._lane_info(1, now)
    assert L['state'] == 'disarmed' and L['splits'] == 1 and L['touches'] == 2
    assert tw._lane_info(1, now + 16)['state'] == 'armed'
    assert tw._lane_info(2, now)['state'] == 'armed'


def test_overdue_touch_raises_an_alarm(tmp_path):
    book, tw = _race(tmp_path)
    book.feed({'running_time': '31.0', 'lane_running1': False, 'lane_time1': '30.50'})
    tw.on_feed()
    tw._clock = (time.time(), 31.0)          # lane 2 due ~30.0 from its seed, not 3 s late yet
    with tw._lock:
        tw._check()
    assert not tw.alarms
    tw._clock = (time.time(), 35.5)          # 30 + 4.5 s grace (15% of a lap)
    with tw._lock:
        tw._check()
    assert [a['lane'] for a in tw.alarms] == [2] and tw.alarms[0]['kind'] == 'overdue'
    with tw._lock:
        tw._check()
    assert len(tw.alarms) == 1               # once per touch


def test_camera_touch_without_split(tmp_path):
    book, tw = _race(tmp_path)
    now = time.time()
    tw._clock = (now, 33.0)
    tw._cands.append((2, now - 3.0))         # camera: lane 2 touched at ~30 s, no split
    tw._cands.append((1, now - 3.0))
    book.feed({'running_time': '33.0', 'lane_running1': False, 'lane_time1': '30.10'})
    tw.split_walls[1] = [now - 2.9]          # lane 1's split arrived right after: the pad got it
    tw.seen[1] = 1
    with tw._lock:
        tw._check()
    kinds = {(a['lane'], a['kind']) for a in tw.alarms}
    assert (2, 'camera') in kinds and not any(l == 1 for l, _ in kinds)
