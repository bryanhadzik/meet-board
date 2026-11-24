"""Touch watch: is each lane's touchpad armed, and did a touch go missing?

The CTS console ignores a lane's pad for a while after every touch (about 15 s - so a relay swimmer getting
out, or a swimmer hanging on the wall, doesn't make a second touch), then re-arms it. We can't see the console's
armed light, but we see every touch arrive (the race book's splits), so: a lane is disarmed for `lockout` seconds
after each touch, armed otherwise.

A missed touch raises an alarm two ways:
  - overdue: the lane is armed and its next touch at the pad end is well past due for its pace (from its splits,
    or its seed time before the first one)
  - camera: the live camera sees a touch at the wall (motion in the lane's wall zone) while the lane is armed,
    and no split arrives - only counted near when a touch is due, so people walking past don't set it off

An unofficial aid for the timing table: it never changes a time.
Needs ffmpeg and numpy for the camera part; the overdue part works without the camera.
"""
import collections
import json
import os
import subprocess
import threading
import time

import numpy as np

AW, AH, FPS, LAG = 480, 270, 10, 3


def _secs(v):
    try:
        s = str(v or '').strip()
        if ':' in s:
            m, x = s.split(':', 1)
            return int(m) * 60 + float(x)
        return float(s) if s else None
    except ValueError:
        return None


class TouchWatch:
    def __init__(self, data_dir, settings, save_settings, video, book):
        self.dir = data_dir
        self.settings, self.save_settings = settings, save_settings
        self.video, self.book = video, book
        self._lock = threading.Lock()
        self.alarms = []                      # newest last
        self.log_path = os.path.join(data_dir, 'touch_watch_log.json')
        self._reset()
        self.cam = {'running': False, 'fps': 0.0, 'error': '', 'frame_at': 0.0}
        self._proc = None
        self._viewer_at = 0.0
        self._cands = collections.deque(maxlen=200)   # (lane, wall time) camera touches waiting to be checked
        self._clock = (None, None)                    # (wall time, race clock) at the last console update
        self.frame_path = os.path.join(data_dir, 'touch_watch.jpg')
        threading.Thread(target=self._loop, daemon=True).start()

    # ------------------------------------------------------------------ settings
    @property
    def enabled(self):
        return bool(self.settings.get('touch_watch', True))

    @property
    def lockout(self):
        return float(self.settings.get('touch_watch_lockout') or 15)

    @property
    def latency(self):
        return float(self.settings.get('touch_watch_cam_latency') if self.settings.get('touch_watch_cam_latency') is not None else 1.0)

    def set(self, d):
        for k, lo, hi in (('lockout', 3, 60), ('cam_latency', 0, 5)):
            if k in d:
                self.settings['touch_watch_' + k] = max(lo, min(hi, float(d[k])))
        if 'enabled' in d:
            self.settings['touch_watch'] = bool(d['enabled'])
        self.save_settings()

    # ------------------------------------------------------------------ race
    def _reset(self):
        self.split_walls = {}                 # lane -> [wall time each split arrived]
        self.seen = {}                        # lane -> splits counted so far
        self.flagged = set()                  # (lane, touch number) already alarmed
        self.race_id = time.time()

    def on_start(self):
        with self._lock:
            self._reset()
            self.alarms = []
            self._cands.clear()

    def on_feed(self):
        """After every console update: note the race clock against the wall clock, and new splits."""
        c = _secs(self.book.s.get('running_time'))
        now = time.time()
        if c is not None:
            self._clock = (now, c)
        if not self.book.racing:
            return
        with self._lock:
            for i, rec in self.book.lanes.items():
                n = len(rec.get('splits') or [])
                if n > self.seen.get(i, 0):
                    self.split_walls.setdefault(i, []).extend([rec.get('at') or now] * (n - self.seen.get(i, 0)))
                    self.seen[i] = n

    def race_clock(self, wall=None):
        w0, c0 = self._clock
        if w0 is None or not self.book.racing:
            return None
        return c0 + ((wall or time.time()) - w0)

    def _lane_info(self, i, now):
        s = self.book.s
        name = str(s.get('lane_name%d' % i) or '').strip()
        team = str(s.get('lane_team%d' % i) or '').strip()
        if not (name or team):
            return None
        try:
            dist = int(float(s.get('event_distance') or 0))
        except (TypeError, ValueError):
            dist = 0
        step = self.book.split_yards or 50
        laps = max(1, dist // step) if dist else None
        rec = self.book.lanes.get(i) or {}
        sp = rec.get('splits') or []
        n = len(sp)
        walls = self.split_walls.get(i) or []
        done = bool(rec.get('place')) or (laps is not None and n >= laps)
        if n >= 2:
            lap = (sp[-1] - sp[0]) / (n - 1)
        elif n == 1:
            lap = sp[0] * 1.08                        # the first 50 has the dive: later laps are slower
        else:
            seed = _secs(s.get('lane_seed_time%d' % i))
            lap = seed * step / dist if seed and dist else None
        due = ((sp[-1] if n else 0.0) + lap) if lap else None
        since = now - walls[-1] if walls else None
        state = ('done' if done else 'waiting' if not self.book.racing
                 else 'disarmed' if since is not None and since < self.lockout else 'armed')
        return {'name': name or team, 'team': team, 'splits': n, 'touches': laps, 'last': sp[-1] if n else None,
                'lap': round(lap, 2) if lap else None, 'due': round(due, 2) if due else None, 'state': state,
                'rearm_in': round(self.lockout - since, 1) if state == 'disarmed' else None}

    def _alarm(self, lane, kind, touch, race_t, msg):
        a = {'id': '%d-%d-%s-%.0f' % (lane, touch, kind, time.time() * 10), 'lane': lane, 'kind': kind, 'touch': touch,
             'race_t': round(race_t, 2) if race_t is not None else None, 'msg': msg, 'at': time.time(), 'ack': False,
             'event': str(self.book.s.get('current_event', '')).strip(), 'heat': str(self.book.s.get('current_heat', '')).strip()}
        self.alarms.append(a)
        try:
            log = json.load(open(self.log_path)) if os.path.exists(self.log_path) else []
        except ValueError:
            log = []
        log = (log + [a])[-500:]
        with open(self.log_path, 'w') as f:
            json.dump(log, f)

    def _check(self):
        """Raise alarms for overdue touches and camera touches with no split."""
        now = time.time()
        clock = self.race_clock(now)
        if clock is None or not self.enabled:
            return
        for i in range(1, 11):
            L = self._lane_info(i, now)
            if not L or L['state'] != 'armed' or not L['due']:
                continue
            k = L['splits'] + 1
            late = max(3.0, 0.15 * L['lap'])
            if clock > L['due'] + late and (i, k) not in self.flagged:
                self.flagged.add((i, k))
                self._alarm(i, 'overdue', k, clock, 'Lane %d (%s): no touch at the %s - due about %s, now %s. '
                            'Missed the pad? Check the timers.' % (i, L['name'], self._yards(k), _fmt(L['due']), _fmt(clock)))
        # camera touches: wait a couple of seconds for the split to arrive, then decide
        keep = collections.deque(maxlen=200)
        while self._cands:
            lane, w = self._cands.popleft()
            if now - w < 2.5:
                keep.append((lane, w))
                continue
            walls = self.split_walls.get(lane) or []
            if any(-1.5 <= sw - w <= 2.5 for sw in walls):
                continue                                   # the pad got it
            if any(0 <= w - sw < self.lockout for sw in walls):
                continue                                   # lane was disarmed: getting out / hanging on the wall
            L = self._lane_info(lane, now)
            if not L or L['state'] == 'done' or L['state'] == 'waiting':
                continue
            t = self.race_clock(w)
            if t is None or t < 8:
                continue                                   # the dive, not a touch
            if L['lap'] and L['due'] and abs(t - L['due']) > max(4.0, 0.3 * L['lap']):
                continue                                   # nowhere near when a touch is due: someone walking past
            k = L['splits'] + 1
            if (lane, k) in self.flagged:
                continue
            self.flagged.add((lane, k))
            self._alarm(lane, 'camera', k, t, 'Lane %d (%s): the camera saw a touch at %s but no split came in. '
                        'Missed the pad? Check the timers.' % (lane, L['name'], _fmt(t)))
        self._cands.extend(keep)

    def _yards(self, k):
        return '%d' % (k * (self.book.split_yards or 50))

    # ------------------------------------------------------------------ camera
    def viewer(self):
        self._viewer_at = time.time()

    def _want_camera(self):
        g = self.video.geom() if self.video else {}
        return (self.enabled and self.video and self.video.ffmpeg() and len(g.get('finish') or []) == 9
                and (self.book.racing or time.time() - self._viewer_at < 20))

    def _cam_url(self):
        url, _ = self.video.camera_url()
        if url and self.settings.get('touch_watch_substream', True) and 'subtype=0' in url:
            url = url.replace('subtype=0', 'subtype=1')      # Dahua/Amcrest: the small stream is plenty here
        return url

    def _run_camera(self):
        url = self._cam_url()
        if not url:
            self.cam['error'] = 'no camera address (Video timing page)'
            time.sleep(5)
            return
        import video_detect
        zones = video_detect.lane_zones(self.video.geom(), AW, AH)
        masks = {l: (z[0] | z[1]) for l, z in zones.items()}
        cmd = [self.video.ffmpeg(), '-v', 'error', '-rtsp_transport', 'tcp', '-i', url, '-an',
               '-map', '0:v:0', '-vf', 'fps=%d,scale=%d:%d,format=gray' % (FPS, AW, AH), '-f', 'rawvideo', 'pipe:1',
               '-map', '0:v:0', '-vf', 'fps=4,scale=640:-2', '-q:v', '6', '-update', '1', '-atomic_writing', '1',
               '-y', self.frame_path]
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                 creationflags=0x08000000 if os.name == 'nt' else 0)
        except OSError as e:
            self.cam['error'] = str(e)
            time.sleep(5)
            return
        self._proc = p
        self.cam.update(running=True, error='')
        n = AW * AH
        hist = collections.deque(maxlen=LAG + 1)
        sig = {l: collections.deque(maxlen=FPS * 8) for l in masks}
        last_c = {l: 0.0 for l in masks}
        t0, cnt = time.time(), 0
        try:
            while self._want_camera():
                buf = p.stdout.read(n)
                if len(buf) < n:
                    break
                now = time.time()
                cnt += 1
                if now - t0 >= 2:
                    self.cam['fps'] = round(cnt / (now - t0), 1)
                    t0, cnt = now, 0
                self.cam['frame_at'] = now
                f = np.frombuffer(buf, np.uint8).reshape(AH, AW).astype(np.int16)
                hist.append(f)
                if len(hist) <= LAG:
                    continue
                d = np.abs(f - hist[0])
                for l, mk in masks.items():
                    m = float(d[mk].mean())
                    s = sig[l]
                    if len(s) > FPS * 4:
                        old = np.array([v for _, v in list(s)[:-FPS]])          # the last 1 s left out of the baseline
                        base = float(np.median(old))
                        mad = float(np.median(np.abs(old - base))) + 0.5
                        thr = base + max(5.0, 5 * mad)
                        prev = s[-1][1]
                        if m > thr >= prev and now - last_c[l] > 3.0:
                            last_c[l] = now
                            self._cands.append((l, now - self.latency))
                    s.append((now, m))
        finally:
            try:
                p.kill()
            except Exception:
                pass
            err = b''
            try:
                err = p.stderr.read()[-300:]
            except Exception:
                pass
            self._proc = None
            self.cam.update(running=False, fps=0.0)
            if err.strip():
                self.cam['error'] = err.decode('utf-8', 'replace').strip().splitlines()[-1]

    def _loop(self):
        cam = None
        while True:
            try:
                with self._lock:
                    self._check()
                if self._want_camera() and (cam is None or not cam.is_alive()):
                    cam = threading.Thread(target=self._run_camera, daemon=True)
                    cam.start()
            except Exception as e:
                self.cam['error'] = 'watch: %s' % e
            time.sleep(0.25)

    # ------------------------------------------------------------------ page
    def ack(self, aid=None):
        with self._lock:
            for a in self.alarms:
                if aid is None or a['id'] == aid:
                    a['ack'] = True

    def test_alarm(self):
        with self._lock:
            self._alarm(0, 'test', 0, self.race_clock(), 'Test alarm - this is what a missed touch looks and sounds like.')

    def status(self):
        now = time.time()
        with self._lock:
            lanes = {}
            for i in range(1, 11):
                L = self._lane_info(i, now)
                if L:
                    lanes[str(i)] = L
            zones = {}
            g = self.video.geom() if self.video else {}
            if len(g.get('finish') or []) == 9 and len(g.get('turn') or []) == 9:
                import video_detect
                zones = {l: z[2] for l, z in video_detect.lane_zones(g, 192, 108).items()}
            return {'enabled': self.enabled, 'lockout': self.lockout, 'cam_latency': self.latency,
                    'racing': self.book.racing, 'clock': self.race_clock(now),
                    'event': str(self.book.s.get('current_event', '')).strip(),
                    'heat': str(self.book.s.get('current_heat', '')).strip(),
                    'distance': self.book.s.get('event_distance'), 'lanes': lanes, 'zones': zones,
                    'alarms': self.alarms[-20:],
                    'camera': dict(self.cam, frame_age=round(now - os.path.getmtime(self.frame_path), 1)
                                   if os.path.exists(self.frame_path) else None)}


def _fmt(t):
    if t is None:
        return '-'
    m, s = divmod(round(t, 1), 60)
    return '%d:%04.1f' % (m, s) if m else '%.1f' % s
