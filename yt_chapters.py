"""YouTube chapters for a streamed meet.

meet-board knows when every race starts (race_book) and when the OBS stream started (obs status:
outputDuration), so after a meet it can list "M:SS Event N Name" lines to paste into the video's
description. YouTube's rules: the first chapter at 0:00, at least three, each at least 10 s long.

Stored in DATA_DIR/stream_log.json:
  {"streams": [{"start": ts, "end": ts}], "races": [{"t": ts, "event": "4", "heat": "1"}]}
"""
import json
import os
import threading
import time

LEAD = 30          # a chapter starts this long before the event's first race (swimmers on the blocks)
KEEP_DAYS = 45

_lock = threading.Lock()


class StreamLog:
    def __init__(self, path):
        self.path = path
        self.d = {'streams': [], 'races': []}
        try:
            with open(path) as f:
                got = json.load(f)
            self.d['streams'] = list(got.get('streams') or [])
            self.d['races'] = list(got.get('races') or [])
        except (OSError, ValueError):
            pass

    def _save(self):
        cut = time.time() - KEEP_DAYS * 86400
        self.d['streams'] = [s for s in self.d['streams'] if s.get('end', s.get('start', 0)) > cut]
        self.d['races'] = [r for r in self.d['races'] if r.get('t', 0) > cut]
        tmp = self.path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(self.d, f)
        os.replace(tmp, self.path)

    def race(self, event, heat, clock_secs=0.0, now=None):
        """A race started (clock already reading clock_secs when we noticed)."""
        now = time.time() if now is None else now
        event, heat = str(event or '').strip(), str(heat or '').strip()
        if not event:
            return
        t = now - max(0.0, float(clock_secs or 0))
        with _lock:
            last = self.d['races'][-1] if self.d['races'] else None
            if last and last['event'] == event and last['heat'] == heat and abs(last['t'] - t) < 60:
                return                                   # the same start seen twice (server rejoin)
            self.d['races'].append({'t': round(t, 1), 'event': event, 'heat': heat})
            self._save()

    def stream(self, streaming, seconds, now=None):
        """Called every poll with OBS's stream status."""
        if not streaming:
            return
        now = time.time() if now is None else now
        start = now - float(seconds or 0)
        with _lock:
            cur = self.d['streams'][-1] if self.d['streams'] else None
            if cur and abs(cur['start'] - start) < 5:
                cur['end'] = round(now, 1)
            else:
                self.d['streams'].append({'start': round(start, 1), 'end': round(now, 1)})
            self._save()

    def sessions(self):
        """Streams that had races, newest first: [{index, start, end, races}]."""
        out = []
        for i, s in enumerate(self.d['streams']):
            n = sum(1 for r in self.d['races'] if s['start'] <= r['t'] <= s['end'] + 5)
            out.append({'index': i, 'start': s['start'], 'end': s['end'], 'races': n})
        return [s for s in reversed(out) if s['races']]


def _fmt(sec):
    sec = int(sec)
    if sec >= 3600:
        return '%d:%02d:%02d' % (sec // 3600, sec % 3600 // 60, sec % 60)
    return '%d:%02d' % (sec // 60, sec % 60)


def chapters(log, names, index=None, lead=LEAD):
    """-> (text, info). names: {event number (int or str): 'Women 200 Yard Freestyle'}."""
    sess = log.sessions()
    if not sess:
        return '', {'error': 'No streamed races recorded yet'}
    s = next((x for x in sess if x['index'] == index), sess[0]) if index is not None else sess[0]
    races = sorted((r for r in log.d['races'] if s['start'] <= r['t'] <= s['end'] + 5), key=lambda r: r['t'])
    seen, marks = set(), []
    for r in races:
        if r['event'] in seen:
            continue
        seen.add(r['event'])
        name = names.get(r['event']) or names.get(_int(r['event'])) or ''
        name = name.replace(' Yard ', ' ').replace('Individual Medley', 'IM')
        marks.append([max(0.0, r['t'] - s['start'] - lead), ('Event %s %s' % (r['event'], name)).strip()])
    lines, last = [], None
    if marks and marks[0][0] > 60:
        lines.append([0.0, 'Warm-ups'])
    elif marks:
        marks[0][0] = 0.0
    for t, label in marks:
        if lines and t < lines[-1][0] + 10:              # YouTube needs 10 s per chapter
            t = lines[-1][0] + 10
        lines.append([t, label])
    text = '\n'.join('%s %s' % (_fmt(t), label) for t, label in lines)
    info = {'session': s, 'events': len(marks), 'races': len(races), 'ok': len(lines) >= 3,
            'note': '' if len(lines) >= 3 else 'YouTube needs at least 3 chapters'}
    return text, info


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return x
