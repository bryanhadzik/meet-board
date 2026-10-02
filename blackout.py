"""Go black: blank the meet boards and scoreboard TVs when no meet is running.

Two ways a screen goes black:
  - manual: the "Go black" button in the admin tab bar. Stays black until
    someone presses "Wake screens".
  - auto: no meet activity for `blackout_after_hours` (default 2). Activity is
    anything that means a meet is happening: a race starting or finishing,
    the race clock running, lane times or places changing, or the event/heat
    changing (from the console or the admin pages). Auto black clears on its
    own the moment activity comes back, so the first race of the next meet
    wakes every TV with nobody touching it.

The OBS overlay never goes black (that would put black on the stream); it just
hides, which it already does between races.
"""
import threading
import time

# Fields that, when their value changes, mean a meet is in progress.
_CHANGE_KEYS = ('lane_time', 'lane_place', 'current_event', 'current_heat', 'race_state')


class Blackout:
    def __init__(self, settings, save, clock=time.time):
        self.settings = settings
        self.save = save
        self.clock = clock
        self._lock = threading.Lock()
        self._seen = {}
        self.last_activity = clock()
        self.last_activity_what = 'server started'

    # ---------------------------------------------------------------- config
    @property
    def auto_enabled(self):
        return bool(self.settings.get('blackout_auto', True))

    @property
    def after_hours(self):
        try:
            return max(0.05, float(self.settings.get('blackout_after_hours', 2.0)))
        except (TypeError, ValueError):
            return 2.0

    @property
    def manual(self):
        return bool(self.settings.get('blackout_manual', False))

    def _persist(self):
        try:
            self.save()
        except Exception:
            pass

    # ---------------------------------------------------------------- activity
    def activity(self, what='activity'):
        """Something meet-like happened. Returns True if this woke an auto blackout."""
        with self._lock:
            was = self._auto_black_locked()
            self.last_activity = self.clock()
            self.last_activity_what = what
            return was

    def watch_update(self, update):
        """Feed every scoreboard update; returns True if it counted as activity
        that woke the screens from an auto blackout."""
        changed = None
        running = update.get('race_state', self._seen.get('race_state')) == 'Running'
        for k, v in update.items():
            # The clock only counts while a race runs: an idle console may show time of day
            if not (k.startswith(_CHANGE_KEYS) or k == 'running_time'):
                continue
            v = v.strip() if isinstance(v, str) else v
            counts = k != 'running_time' or running
            if counts and self._seen.get(k, v) != v and changed is None:
                changed = k
            self._seen[k] = v
        if changed:
            return self.activity(changed)
        return False

    # ---------------------------------------------------------------- state
    def _auto_black_locked(self):
        return self.auto_enabled and self.clock() - self.last_activity >= self.after_hours * 3600

    def auto_black(self):
        with self._lock:
            return self._auto_black_locked()

    def is_black(self):
        return self.manual or self.auto_black()

    def set_manual(self, black):
        self.settings['blackout_manual'] = bool(black)
        if not black:
            self.activity('woken from the admin page')   # also clears an auto blackout
        self._persist()

    def configure(self, auto=None, hours=None):
        if auto is not None:
            self.settings['blackout_auto'] = bool(auto)
        if hours is not None:
            self.settings['blackout_after_hours'] = max(0.25, min(24.0, float(hours)))
        self._persist()

    def status(self):
        now = self.clock()
        with self._lock:
            auto = self._auto_black_locked()
            idle = now - self.last_activity
        reason = 'manual' if self.manual else ('auto' if auto else '')
        left = None
        if self.auto_enabled and not auto:
            left = max(0, round(self.after_hours * 3600 - idle))
        return {'black': bool(reason), 'reason': reason, 'manual': self.manual,
                'auto': self.auto_enabled, 'after_hours': self.after_hours,
                'idle_seconds': round(idle), 'black_in_seconds': left,
                'last_activity': self.last_activity_what}
