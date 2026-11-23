"""Video timing (unofficial reference): record the pool camera for every heat, and the lane geometry
needed to read touches off those clips later.

- Each heat that starts on the console is recorded straight from the camera (the raw feed, no overlay)
  into its own clip, stream-copied by ffmpeg so it costs almost no CPU. Recording stops a few seconds
  after the race is over.
- The camera address is the RTSP source OBS already uses, read through the OBS connection (or a
  setting). It holds the camera's login, so it is never shown on a page or written to the clip index.
- Lane geometry: on a still from the camera, the 9 lane lines (lane-1 edge, 7 ropes, lane-8 edge) are
  marked where they meet the finish wall and the turn wall. Stored as fractions of the frame.

Never official: clips are a reference for checking a disputed lane, nothing more.
"""
import glob, json, os, re, shutil, subprocess, threading, time, urllib.request, zipfile

FFMPEG_RELEASE = 'https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest'
FFMPEG_FALLBACK = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl.zip'
STOP_AFTER_S = 3.0          # keep recording this long after the race is over
MAX_CLIP_S = 20 * 60        # safety cap on one clip
_NOWIN = 0x08000000 if os.name == 'nt' else 0   # CREATE_NO_WINDOW


def mask(url):
    """rtsp://user:pass@host/... -> rtsp://user:***@host/..."""
    return re.sub(r'(//[^:/@]+:)[^@]*@', r'\1***@', url or '')


class VideoTiming:
    def __init__(self, data_dir, settings, save_settings, obs=None, log=print):
        self.dir = os.path.join(data_dir, 'video')
        self.clips_dir = os.path.join(self.dir, 'clips')
        self.index_file = os.path.join(self.dir, 'clips.json')
        self.settings, self.save_settings, self.obs, self.log = settings, save_settings, obs, log
        self._lock = threading.Lock()
        self._proc = None
        self._cur = None
        self._stop_timer = None
        self._url_cache = (0.0, None, '')
        self.last_error = ''
        self.installing = False
        self._detecting = set()
        os.makedirs(self.clips_dir, exist_ok=True)
        try:
            with open(self.index_file) as f:
                self.index = json.load(f) or []
        except Exception:
            self.index = []

    # ---------------------------------------------------------------- setup
    @property
    def enabled(self):
        return bool(self.settings.get('video_record'))

    def ffmpeg(self):
        local = os.path.join(self.dir, 'ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
        if os.path.exists(local):
            return local
        return shutil.which('ffmpeg')

    def install_ffmpeg(self):
        """Download ffmpeg (Windows build) into the data folder - once."""
        if self.ffmpeg():
            return self.ffmpeg()
        if os.name != 'nt':
            raise RuntimeError('install ffmpeg with the system package manager')
        url = FFMPEG_FALLBACK
        try:                                # the newest released version's Windows build (names change per version)
            req = urllib.request.Request(FFMPEG_RELEASE, headers={'Accept': 'application/vnd.github+json'})
            with urllib.request.urlopen(req, timeout=20) as r:
                assets = json.load(r).get('assets') or []
            pick = sorted((a for a in assets if re.match(r'ffmpeg-n[\d.]+-latest-win64-lgpl-[\d.]+\.zip$', a.get('name', ''))),
                          key=lambda a: [int(x) for x in re.findall(r'\d+', a['name'].split('-')[1])], reverse=True)
            if pick:
                url = pick[0]['browser_download_url']
        except Exception:
            pass
        tmp = os.path.join(self.dir, 'ffmpeg-download.zip')
        try:
            with urllib.request.urlopen(url, timeout=300) as r, open(tmp, 'wb') as f:
                shutil.copyfileobj(r, f, 1 << 20)
            with zipfile.ZipFile(tmp) as z:
                name = next(n for n in z.namelist() if n.endswith('/bin/ffmpeg.exe'))
                part = os.path.join(self.dir, 'ffmpeg.exe.part')
                with z.open(name) as src, open(part, 'wb') as dst:
                    shutil.copyfileobj(src, dst)
                os.replace(part, os.path.join(self.dir, 'ffmpeg.exe'))
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        return self.ffmpeg()

    def ensure_ffmpeg(self):
        """Recording is on but the recorder (ffmpeg) isn't here yet: fetch it in the background."""
        if self.ffmpeg() or self.installing:
            return
        self.installing = True

        def run():
            try:
                self.install_ffmpeg()
                self.last_error = ''
            except Exception as e:
                self.last_error = 'Could not download the recorder (ffmpeg): %s' % e
            finally:
                self.installing = False
        threading.Thread(target=run, daemon=True).start()

    def camera_url(self, refresh=False):
        """(url, where it came from). The setting wins; otherwise the RTSP source in OBS."""
        u = (self.settings.get('video_cam_url') or '').strip()
        if u:
            return u, 'setting'
        at, url, src = self._url_cache
        if url and not refresh and time.time() - at < 600:
            return url, src
        if self.obs is None:
            return None, ''
        try:
            want = (self.settings.get('video_obs_input') or '').strip()
            inputs = self.obs.call('GetInputList').get('inputs') or []
            names = [i['inputName'] for i in inputs if i.get('inputKind') in ('ffmpeg_source', 'vlc_source')]
            if want:
                names = [n for n in names if n == want] or names
            for n in sorted(names, key=lambda n: (n != (want or 'Media'), n)):
                s = self.obs.call('GetInputSettings', {'inputName': n}).get('inputSettings') or {}
                cand = s.get('input') or ''
                if not cand and s.get('playlist'):
                    cand = (s['playlist'][0] or {}).get('value', '')
                if cand.lower().startswith(('rtsp://', 'rtsps://', 'rtmp://', 'srt://', 'http')):
                    self._url_cache = (time.time(), cand, 'OBS source "%s"' % n)
                    return cand, 'OBS source "%s"' % n
        except Exception as e:
            self.last_error = 'OBS: %s' % e
        return None, ''

    def snapshot(self, clip=None, at=None):
        """One JPEG straight from the camera - or from a recorded heat (clip), `at` seconds in (default: the
        middle), for marking the lanes when the lane ropes are out between meets."""
        ff = self.ffmpeg()
        if not ff:
            raise RuntimeError('the recorder (ffmpeg) is not set up yet')
        args = [ff, '-hide_banner', '-loglevel', 'error']
        if clip:
            path = self.clip_path(clip)
            if not path:
                raise RuntimeError('no such clip')
            c = next((x for x in self.index if x.get('file') == clip), {})
            if at is None:
                at = max(0.0, ((c.get('rec_end') or 0) - (c.get('rec_start') or 0)) / 2)
            args += ['-ss', '%.2f' % float(at), '-i', path]
        else:
            url = self.camera_url()[0]
            if not url:
                raise RuntimeError('no camera address (OBS not connected, or no RTSP source in OBS)')
            if url.lower().startswith('rtsp'):
                args += ['-rtsp_transport', 'tcp']
            args += ['-i', url]
        args += ['-frames:v', '1', '-q:v', '3', '-f', 'image2pipe', '-vcodec', 'mjpeg', '-']
        p = subprocess.run(args, capture_output=True, timeout=20, creationflags=_NOWIN)
        if p.returncode or not p.stdout:
            raise RuntimeError(mask(p.stderr.decode(errors='replace').strip()[-300:]) or 'no picture from the camera')
        return p.stdout

    # ------------------------------------------------------------ recording
    def race_started(self, event, heat, started_at=None):
        if not self.enabled:
            return
        try:
            e, h = int(str(event).strip()), int(str(heat).strip())
        except (TypeError, ValueError):
            return
        with self._lock:
            self._stop_locked()
            ff, (url, _) = self.ffmpeg(), self.camera_url()
            if not ff or not url:
                self.last_error = 'not recording: ' + ('ffmpeg is not installed' if not ff else 'no camera address')
                return
            now = time.time()
            day = time.strftime('%Y%m%d', time.localtime(now))
            os.makedirs(os.path.join(self.clips_dir, day), exist_ok=True)
            rel = '%s/E%02dH%02d_%s.mp4' % (day, e, h, time.strftime('%H%M%S', time.localtime(now)))
            args = [ff, '-hide_banner', '-loglevel', 'error', '-nostats']
            if url.lower().startswith('rtsp'):
                args += ['-rtsp_transport', 'tcp']
            args += ['-use_wallclock_as_timestamps', '1', '-i', url, '-map', '0:v:0', '-c', 'copy',
                     '-t', str(MAX_CLIP_S), '-f', 'mp4', '-movflags', '+frag_keyframe+empty_moov+default_base_moof',
                     os.path.join(self.clips_dir, rel)]
            try:
                self._proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.PIPE, creationflags=_NOWIN)
            except Exception as ex:
                self.last_error = 'ffmpeg would not start: %s' % ex
                self._proc = None
                return
            # race_start: when meet-board saw the console's clock start; rec_start: when ffmpeg was launched.
            # The touch-reading step lines the video up with the lanes whose pad and button agree.
            self._cur = {'event': e, 'heat': h, 'file': rel, 'race_start': started_at or now, 'rec_start': now,
                         'rec_end': None, 'ok': None}
            self.last_error = ''

    def race_over(self, event=None, heat=None):
        with self._lock:
            if not self._proc or self._stop_timer:
                return
            t = threading.Timer(STOP_AFTER_S, self.stop)
            t.daemon = True
            self._stop_timer = t
            t.start()

    def stop(self):
        with self._lock:
            self._stop_locked()

    def _stop_locked(self):
        if self._stop_timer:
            self._stop_timer.cancel()
            self._stop_timer = None
        p, cur = self._proc, self._cur
        self._proc, self._cur = None, None
        if not p:
            return
        try:
            p.stdin.write(b'q')
            p.stdin.flush()
        except Exception:
            pass
        try:
            p.wait(timeout=8)
        except Exception:
            p.kill()
        err = b''
        try:
            err = p.stderr.read() or b''
        except Exception:
            pass
        cur['rec_end'] = time.time()
        path = os.path.join(self.clips_dir, cur['file'])
        cur['bytes'] = os.path.getsize(path) if os.path.exists(path) else 0
        cur['ok'] = cur['bytes'] > 0
        if not cur['ok']:
            self.last_error = 'clip failed: ' + (mask(err.decode(errors='replace').strip()[-200:]) or 'empty file')
        self.index.append(cur)
        self._prune()
        self._save_index()

    def _prune(self):
        keep_days = float(self.settings.get('video_keep_days') or 14)
        keep_gb = float(self.settings.get('video_keep_gb') or 40)
        now = time.time()
        live = []
        for c in sorted(self.index, key=lambda c: c.get('rec_start') or 0):
            path = os.path.join(self.clips_dir, c['file'])
            if now - (c.get('rec_start') or 0) > keep_days * 86400 or not os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
                continue
            live.append(c)
        total = sum(c.get('bytes') or 0 for c in live)
        while live and total > keep_gb * 1e9:
            c = live.pop(0)
            total -= c.get('bytes') or 0
            try:
                os.remove(os.path.join(self.clips_dir, c['file']))
            except OSError:
                pass
        self.index = live
        for d in glob.glob(os.path.join(self.clips_dir, '*')):
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)

    def _save_index(self):
        tmp = self.index_file + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(self.index, f)
        os.replace(tmp, self.index_file)

    # ---------------------------------------------------------------- state
    def geom(self):
        return self.settings.get('video_geom') or {}

    def set_geom(self, g):
        def pts(a):
            out = []
            for p in (a or [])[:9]:
                x, y = float(p[0]), float(p[1])
                if not (0 <= x <= 1 and 0 <= y <= 1):
                    raise ValueError('points must be fractions of the frame')
                out.append([round(x, 5), round(y, 5)])
            return out
        g = {'finish': pts(g.get('finish')), 'turn': pts(g.get('turn')), 'lane1': g.get('lane1') or 'first',
             'board': self.geom().get('board'), 'strobe': self.geom().get('strobe'), 'saved': time.strftime('%Y-%m-%d %H:%M')}
        self.settings['video_geom'] = g
        self.save_settings()
        return g

    @staticmethod
    def _board(b):
        box = [float(v) for v in (b.get('box') or [])]
        if len(box) != 4 or not all(0 <= v <= 1 for v in box):
            raise ValueError('scoreboard box must be 4 fractions of the frame')
        x0, x1, y0, y1 = sorted(box[0::2])[0], sorted(box[0::2])[1], sorted(box[1::2])[0], sorted(box[1::2])[1]
        if x1 - x0 < 0.005 or y1 - y0 < 0.005:
            raise ValueError('scoreboard box is too small')
        top, bot = int(b.get('top') or 1), int(b.get('bottom') or 8)
        if not (1 <= top <= 10 and 1 <= bot <= 10):
            raise ValueError('lanes must be 1-10')
        return {'box': [round(v, 5) for v in (x0, y0, x1, y1)], 'top': top, 'bottom': bot}

    def set_strobe(self, b, clip=None):
        """Where the starter's strobe is in the picture ({box: [x0, y0, x1, y1]} fractions, or None), same rules as
        the scoreboard: on every imported clip with the same picture, or on the camera's."""
        if b:
            box = self._board({'box': b.get('box'), 'top': 1, 'bottom': 1})['box']
            b = {'box': box}
        return self._set_pic('strobe', b or None, clip)

    def set_start(self, rel, at):
        """Fix a clip's start by hand (clip seconds, or None to go back to automatic) and find the touches again."""
        c = self.entry(rel)
        if not c:
            raise ValueError('no such clip')
        with self._lock:
            if at is None:
                c.pop('start_fixed', None)
            else:
                c['start_fixed'] = round(float(at), 3)
            self._save_index()

    def set_touch(self, rel, lane, at):
        """Your own mark of a lane's touch (clip seconds of the frame the hand hits the wall; None removes it).
        Kept with the clip, shown beside the video's guess, and used later to tune the detection."""
        c = self.entry(rel)
        if not c:
            raise ValueError('no such clip')
        lane = str(int(lane))
        with self._lock:
            m = c.setdefault('marks', {})
            if at is None:
                m.pop(lane, None)
            else:
                m[lane] = {'clip_t': round(float(at), 3), 'at': time.strftime('%Y-%m-%d %H:%M')}
            self._save_index()
        return c.get('marks')

    def all_marks(self):
        """Every hand-marked touch, with what's needed to tune the detection against it."""
        out = []
        for c in self.index:
            if c.get('marks'):
                d = c.get('detect') or {}
                out.append({'file': c['file'], 'event': c['event'], 'heat': c['heat'], 'marks': c['marks'],
                            'start_in_clip': d.get('start_in_clip'), 'start_src': d.get('start_src'),
                            'start_fixed': c.get('start_fixed'), 'refs': c.get('refs_used') or c.get('refs'),
                            'detected': {l: v.get('clip_t') for l, v in (d.get('lanes') or {}).items()},
                            'geom': c.get('geom') or self.geom()})
        return out

    def _set_pic(self, key, val, clip=None):
        c = self.entry(clip) if clip else None
        if c and c.get('geom'):
            sig = json.dumps([c['geom'].get('finish'), c['geom'].get('turn')])
            with self._lock:
                for x in self.index:
                    if x.get('geom') and json.dumps([x['geom'].get('finish'), x['geom'].get('turn')]) == sig:
                        x['geom'][key] = val
                self._save_index()
            return val
        g = dict(self.geom())
        g[key] = val
        self.settings['video_geom'] = g
        self.save_settings()
        return val

    def set_board(self, b, clip=None):
        """Where the lane-time scoreboard is in the picture (None clears it). With clip = an imported clip that
        has its own picture geometry, it goes on every clip with that same geometry; otherwise on the camera's."""
        b = self._board(b) if b else None
        c = self.entry(clip) if clip else None
        if c and c.get('geom'):
            key = json.dumps([c['geom'].get('finish'), c['geom'].get('turn')])
            with self._lock:
                for x in self.index:
                    if x.get('geom') and json.dumps([x['geom'].get('finish'), x['geom'].get('turn')]) == key:
                        x['geom']['board'] = b
                self._save_index()
            return b
        g = dict(self.geom())
        g['board'] = b
        self.settings['video_geom'] = g
        self.save_settings()
        return b

    def board_for(self, rel, key='board'):
        c = self.entry(rel) if rel else None
        return ((c or {}).get('geom') or {}).get(key) if c and c.get('geom') else self.geom().get(key)

    def status(self):
        url, src = self.camera_url()
        with self._lock:
            cur = dict(self._cur) if self._cur else None
        if self.enabled and not self.ffmpeg():
            self.ensure_ffmpeg()
        return {'enabled': self.enabled, 'ffmpeg': bool(self.ffmpeg()), 'installing': self.installing, 'camera': mask(url) if url else '',
                'camera_from': src, 'recording': cur, 'clips': len(self.index),
                'gb': round(sum(c.get('bytes') or 0 for c in self.index) / 1e9, 2),
                'keep_days': float(self.settings.get('video_keep_days') or 14),
                'keep_gb': float(self.settings.get('video_keep_gb') or 40),
                'geom': self.geom(), 'error': self.last_error,
                'recent': [dict({k: c.get(k) for k in ('event', 'heat', 'file', 'race_start', 'rec_start', 'rec_end', 'bytes',
                                                        'ok', 'label', 'imported')},
                                detected=bool(c.get('detect')), detecting=c.get('file') in self._detecting)
                           for c in self.index[-200:]][::-1]}

    def entry(self, rel):
        return next((c for c in self.index if c.get('file') == rel), None)

    def add_import(self, src, meta):
        """A clip from somewhere else (e.g. cut from a stream recording), with its own lane geometry and the
        lanes' reference times. meta: {event, heat, name, start_guess, refs, geom, label}"""
        name = re.sub(r'[^A-Za-z0-9_.-]', '_', str(meta.get('name') or 'clip.mp4'))[:80]
        rel = 'import/' + name
        os.makedirs(os.path.join(self.clips_dir, 'import'), exist_ok=True)
        path = os.path.join(self.clips_dir, rel)
        with open(path + '.part', 'wb') as f:
            shutil.copyfileobj(src, f, 1 << 20)
        os.replace(path + '.part', path)
        c = {'event': int(meta['event']), 'heat': int(meta['heat']), 'file': rel, 'imported': True,
             'label': str(meta.get('label') or '')[:80], 'start_guess': float(meta.get('start_guess') or 0),
             'refs': meta.get('refs') or {}, 'geom': meta.get('geom') or None,
             'rec_start': float(meta.get('rec_start') or time.time()), 'rec_end': meta.get('rec_end'),
             'race_start': meta.get('race_start'), 'bytes': os.path.getsize(path), 'ok': True}
        with self._lock:
            self.index = [x for x in self.index if x.get('file') != rel] + [c]
            self._save_index()
        return c

    def run_detect(self, rel, refs=None):
        """Find the touches in one clip, in the background. refs: {lane: {'times': {...}, 'name': ...}};
        an imported clip brings its own."""
        import video_detect
        c = self.entry(rel)
        if not c:
            raise ValueError('no such clip')
        ff = self.ffmpeg()
        if not ff:
            raise RuntimeError('the recorder (ffmpeg) is not set up yet')
        geom = dict(c.get('geom') or self.geom())
        if not c.get('geom'):
            geom['board'] = self.geom().get('board')
            geom['strobe'] = self.geom().get('strobe')
        if not geom or len(geom.get('finish') or []) != 9 or len(geom.get('turn') or []) != 9:
            raise RuntimeError('mark the lanes first')
        refs = c.get('refs') or refs or {}
        if not refs:
            raise RuntimeError("no reference times for this heat (the console's results weren't recorded)")
        guess = c.get('start_guess')
        if guess is None:
            guess = max(0.0, (c.get('race_start') or c['rec_start']) - c['rec_start'])
        if rel in self._detecting:
            return
        self._detecting.add(rel)

        def go():
            try:
                r = video_detect.detect(ff, os.path.join(self.clips_dir, rel), geom, refs, guess, c.get('start_fixed'))
            except Exception as e:
                r = {'error': str(e)}
            r['at'] = time.time()
            with self._lock:
                c['detect'] = r
                c['refs_used'] = refs
                self._save_index()
            self._detecting.discard(rel)
        threading.Thread(target=go, daemon=True).start()

    def clip_path(self, rel):
        p = os.path.normpath(os.path.join(self.clips_dir, rel))
        if not p.startswith(os.path.normpath(self.clips_dir) + os.sep) or not os.path.exists(p):
            return None
        return p
