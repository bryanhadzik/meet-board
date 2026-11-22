"""What the timing console showed for each heat: every lane's final time and place, as last sent.

MeetDeck reads it (/api/console_results) as a cross-check on its own times: a lane with a time but no
place (the console didn't place it - usually no pad touch), a time that isn't the lane's pad time, or
an order that doesn't match. Keyed by the console's event/heat, so a heat run under the wrong number
shows up there too.
"""
import json, os, threading, time

_lock = threading.Lock()


class ConsoleLog:
    def __init__(self, path, keep=400):
        self.path, self.keep = path, keep
        self.d = {}
        self._dirty, self._saved = False, 0.0
        try:
            with open(path) as f:
                self.d = json.load(f) or {}
        except Exception:
            self.d = {}

    @staticmethod
    def key(event, heat):
        return '%d:%d' % (int(event), int(heat))

    def start(self, event, heat):
        """A heat started: forget what an earlier run of the same event/heat showed. Only heats seen
        starting are recorded, so results still on the board while the console is dialed to the next
        heat don't land under that heat."""
        try:
            k = self.key(event, heat)
        except (TypeError, ValueError):
            return
        with _lock:
            self.d[k] = {'lanes': {}, 'at': time.time()}
            self._dirty = True
        self.save()

    def lanes(self, event, heat, live):
        """live: the remembered scoreboard fields (lane_timeN, lane_placeN, lane_runningN)."""
        try:
            k = self.key(event, heat)
        except (TypeError, ValueError):
            return
        got = {}
        for i in range(1, 11):
            if live.get('lane_running%d' % i):
                continue
            t = (live.get('lane_time%d' % i) or '').strip()
            if not t or not any(c.isdigit() for c in t):
                continue
            got[str(i)] = {'time': t, 'place': (live.get('lane_place%d' % i) or '').strip()}
        if not got:
            return
        with _lock:
            cur = self.d.get(k)
            if cur is None:
                return
            if cur.get('lanes') != got:
                cur['lanes'] = got
                cur['at'] = time.time()
                self.d[k] = cur
                self._dirty = True
                if len(self.d) > self.keep:
                    for old in sorted(self.d, key=lambda x: self.d[x].get('at') or 0)[:len(self.d) - self.keep]:
                        self.d.pop(old, None)
        if time.time() - self._saved > 5:
            self.save()

    def save(self):
        with _lock:
            if not self._dirty:
                return
            tmp = self.path + '.tmp'
            try:
                with open(tmp, 'w') as f:
                    json.dump(self.d, f)
                os.replace(tmp, self.path)
                self._dirty, self._saved = False, time.time()
            except Exception as e:
                print('console log:', e)

    def clear(self):
        with _lock:
            self.d = {}
            self._dirty = True
        self.save()

    def get(self):
        self.save()
        with _lock:
            return json.loads(json.dumps(self.d))
