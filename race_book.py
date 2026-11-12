"""The race book: when a race is on, over, and what its results were.

Kept on the server (not in each TV's browser) so every screen shows the same
thing and a refresh, a TV reboot or the page reloading after an update keeps
the results that are up.

What the CTS console actually sends (watched live at the pool, Nov 2026):
  - no places
  - a touch can show the running total OR just the lap time, and stray
    touches happen (an extra 15.54 in the middle of a 200)
  - on a split every lane can be "stopped" at once, so the console's own
    race state reads Finished halfway through a race
  - a lane flips back to a running clock ~15 s after each touch
What IS reliable: the operator resets the race clock to 0.0 when a heat is
over, and at the next start the clock counts up from zero.

So:
  - racing: from the clock starting from zero until it's reset (or the
    event/heat changes); each lane shows its last touch, and keeps it after
    the lane flips back to running
  - race over: each lane's final is its largest time (lap times and stray
    touches are smaller); places come from those
  - those results stay up - through the move to the next heat - until the
    next race actually starts (or HOLD_S with no race)
"""
import collections
import re
import time

_TIME = re.compile(r'^(?:(\d+):)?(\d+(?:\.\d+)?)$')
HEAT_KEYS = ('current_event', 'current_heat', 'heat_count', 'event_name', 'event_distance', 'event_is_relay')
LANE_KEYS = ('lane_name', 'lane_team', 'lane_legs', 'lane_seed_time', 'lane_usc')


def secs(v):
    m = _TIME.match(str(v if v is not None else '').strip())
    if not m:
        return None
    return (int(m.group(1)) * 60 if m.group(1) else 0) + float(m.group(2))


def fmt(t):
    m, s = divmod(round(t + 1e-9, 2), 60)
    return '%d:%05.2f' % (m, s) if m else '%.2f' % s


def places_for(finals):
    """{lane: seconds} -> {lane: '1'..}; ties share a place."""
    return {i: str(1 + sum(1 for j in finals if finals[j] < finals[i] - 1e-6)) for i in finals}


class RaceBook:
    HOLD_S = 20 * 60
    SPLIT_S = 15        # a touch without a place shows this long, then the lane goes blank

    def __init__(self, clock=time.time):
        self.clock = clock
        self.s = {}
        self.racing = False
        self.lanes = {}            # lane -> {'ran', 'last', 'max'}
        self.hold = None           # results of the last race, until the next start
        self.hold_at = 0
        self.last_clock = None
        self.armed = False         # the clock has been at zero: the next count-up is a start
        self.rising = 0
        self.log = collections.deque(maxlen=80)   # what the race book decided, and why (Debug page)
        self.on_end = None         # callback(event, heat) when a race is over (announcements)
        self.on_all_touched = None # callback(event, heat) once every swimmer in the race has a final time
        self.on_start = None       # callback() when a race starts
        self.touched = False

    def _note(self, text):
        clk = str(self.s.get('running_time') or '').strip() or '-'
        self.log.append((self.clock(), '%s  [clock %s]' % (text.replace('(/)', '(no event)').replace(' / ', ' no event '), clk)))

    def _key(self, o):
        return '%s/%s' % (str(o.get('current_event', '')).strip(), str(o.get('current_heat', '')).strip())

    def _start(self):
        self._note('Race started (%s): clock counting up from zero' % self._key(self.s))
        self.racing, self.lanes, self.hold = True, {}, None
        self.touched = False
        if self.on_start:
            try:
                self.on_start()
            except Exception:
                pass

    def _end(self, heat):
        """The race is over: freeze its results, named from `heat` (the heat as it was)."""
        if not self.racing:
            return
        finals = {i: r['max'] for i, r in self.lanes.items() if r['ran'] and r['max'] > 0}
        self.racing, self.lanes = False, {}
        if self.on_end:
            try:
                self.on_end(str(heat.get('current_event', '')).strip(), str(heat.get('current_heat', '')).strip())
            except Exception:
                pass
        if not finals:
            self._note('Race over (%s), no lane times: nothing held' % self._key(heat))
            return
        self._note('Race over (%s): holding %s' % (self._key(heat), ', '.join(
            'L%d %s' % (i, fmt(finals[i])) for i in sorted(finals))))
        places = places_for(finals)
        snap = {k: heat.get(k) for k in HEAT_KEYS}
        snap['lanes'] = {}
        for i in range(1, 11):
            lane = {k: heat.get('%s%d' % (k, i)) for k in LANE_KEYS}
            if i in finals:
                lane.update(time=fmt(finals[i]), place=places[i])
            elif not (str(lane.get('lane_name') or '').strip() or str(lane.get('lane_team') or '').strip()):
                continue
            else:
                lane.update(time='', place='')         # swam, no time (DQ, no touch)
            snap['lanes'][str(i)] = lane
        snap['heat_key'] = self._key(heat)
        self.hold, self.hold_at = snap, self.clock()

    def feed(self, update):
        """Every update_scoreboard dict goes through here."""
        before = dict(self.s) if self.racing and ('current_event' in update or 'current_heat' in update) else None
        old_key = self._key(self.s)
        self.s.update(update)
        if before is not None and self._key(self.s) != old_key:
            self._note('Heat changed %s -> %s mid-race' % (old_key, self._key(self.s)))
            self._end(before)                       # moved on mid-"race": it's over
        c = secs(self.s.get('running_time'))
        if c is not None:
            if c < 0.05 or (self.last_clock is not None and c < self.last_clock - 1.5):
                if self.racing and c < 0.05:
                    self._note('Clock reset to zero')
                    self._end(self.s)
                if not self.armed:
                    self._note('Armed: clock at zero/went back, the next count-up is a start')
                self.armed, self.rising = True, 0
            elif self.armed and self.last_clock is not None and 0 < c - self.last_clock < 5 and c < 20:
                # two steady ticks up from zero: one odd reading can't start a race
                self.rising += 1
                if self.rising >= 2:
                    self.armed, self.rising = False, 0
                    self._start()
            self.last_clock = c
        # server started (or restarted) in the middle of a race
        if (not self.racing and self.hold is None and not self.armed
                and self.s.get('race_state') == 'Running' and c and c > 0):
            self._note('Joined a race already running (server started mid-race)')
            self.racing = True
        if not self.racing:
            return
        for i in range(1, 11):
            run = bool(self.s.get('lane_running%d' % i))
            ts = str(self.s.get('lane_time%d' % i) or '').strip()
            t = secs(ts)
            rec = self.lanes.setdefault(i, {'ran': False, 'last': '', 'max': 0.0, 'at': 0.0, 'place': ''})
            if run:
                rec['ran'] = True
            elif rec['ran'] and t and t > 0 and t > rec['max']:
                # the largest time so far: the running total (a lap time shown after it is smaller)
                rec['last'] = ts
                rec['max'] = t
                rec['at'] = self.clock()
            if not run and rec['ran']:
                rec['place'] = str(self.s.get('lane_place%d' % i) or '').strip()
        ran = [r for r in self.lanes.values() if r['ran']]
        # a final touch has a place; a split (every lane stops at the 50 too) doesn't
        if (not self.touched and ran and all(r['max'] > 0 and r['place'] for r in ran)
                and not any(self.s.get('lane_running%d' % i) for i in range(1, 11))):
            self.touched = True
            self._note('Everyone touched (%s)' % self._key(self.s))
            if self.on_all_touched:
                try:
                    self.on_all_touched(str(self.s.get('current_event', '')).strip(),
                                        str(self.s.get('current_heat', '')).strip())
                except Exception:
                    pass

    def debug(self):
        """The race book's state in plain values, for the Debug page."""
        now = self.clock()
        return {
            'racing': self.racing, 'armed': self.armed, 'rising': self.rising,
            'last_clock': self.last_clock, 'heat': self._key(self.s),
            'hold': {'heat': self.hold.get('heat_key'), 'age': round(now - self.hold_at, 1),
                     'expires_in': round(self.HOLD_S - (now - self.hold_at), 1),
                     'lanes': {k: (v.get('time'), v.get('place')) for k, v in self.hold.get('lanes', {}).items()}}
            if self.hold else None,
            'lanes': {str(i): {'ran': r['ran'], 'last': r['last'], 'max': round(r['max'], 2),
                               'age': round(now - r['at'], 1) if r.get('at') else None, 'place': r.get('place', '')}
                      for i, r in sorted(self.lanes.items()) if r['ran'] or r['last']},
            'log': [{'age': round(now - t, 1), 'text': x} for t, x in list(self.log)[::-1]],
        }

    def payload(self):
        hold = self.hold if self.hold and not self.racing and self.clock() - self.hold_at <= self.HOLD_S else None
        now = self.clock()
        # mid-race a touch shows for SPLIT_S, then the lane goes blank again -
        # unless the console gave it a place (that's a finish: it stays)
        last = {str(i): r['last'] for i, r in self.lanes.items()
                if self.racing and r['ran'] and r['last'] and (r.get('place') or now - r.get('at', 0) <= self.SPLIT_S)}
        # the console gives a lane its place as it finishes: show it straight away, not only when the heat is over
        place = {str(i): r['place'] for i, r in self.lanes.items()
                 if self.racing and r['ran'] and r['last'] and r.get('place')}
        return {'racing': self.racing, 'last': last, 'place': place, 'hold': hold}
