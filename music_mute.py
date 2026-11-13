"""Mute the stream's audio in OBS while meet music plays, so copyrighted
songs picked up by the pool mic (or desktop audio) don't reach YouTube.

On Music -> Stream audio you tick which OBS audio sources to silence. When a
song starts, each ticked source that is currently live gets muted; when the
music stops or pauses, after a short delay, exactly those sources are unmuted
again. Sources you had muted yourself are never touched. The list of sources
we muted is saved, so a crash or restart mid-song can't leave the stream
silent: they're restored as soon as OBS is reachable and nothing is playing.

Announcements on the stream: when "Play announcements on the stream" is on, the
race overlay in OBS plays each announcement itself and tells meet-board when it
starts and stops. The same sources are muted while it plays (the camera mic would
otherwise pick up the PA a second or two late, on top of the clean recording) and
turned back on a moment after it ends. Music and announcements share one list of
"muted by us", so neither one unmutes a source the other still needs muted.
"""
import threading
import time


class StreamMute:
    def __init__(self, obs, settings, save, log=print):
        self.obs = obs
        self.settings = settings
        self.save = save
        self.log = log
        self._lock = threading.Lock()
        self._unmute_at = None
        self._ann_until = None        # an announcement is playing on the stream until then (safety cap)
        self._music_on = False
        self.last_error = ''
        self.last_action = ''

    # ---------------------------------------------------------------- config
    @property
    def enabled(self):
        return bool(self.settings.get('music_obs_mute'))

    @property
    def chosen(self):
        return list(self.settings.get('music_obs_inputs') or [])

    @property
    def ann_enabled(self):
        return bool(self.settings.get('ann_on_stream'))

    def ann_active(self, now=None):
        return self._ann_until is not None and (now or time.time()) < self._ann_until

    def _held(self, music_status):
        """Something still wants the stream muted: music (with music muting on) or an announcement."""
        return (music_status == 'playing' and self.enabled) or self.ann_active()

    @property
    def muted_by_us(self):
        return list(self.settings.get('music_obs_muted_by_us') or [])

    def _set_muted_by_us(self, names):
        self.settings['music_obs_muted_by_us'] = list(names)
        try:
            self.save()
        except Exception:
            pass

    # ---------------------------------------------------------------- OBS
    def audio_inputs(self):
        """[{name, kind, muted}] for every OBS input that has audio."""
        out = []
        for inp in (self.obs.call('GetInputList').get('inputs') or []):
            name = inp.get('inputName')
            if not name:
                continue
            try:
                muted = self.obs.call('GetInputMute', {'inputName': name}).get('inputMuted')
            except RuntimeError:
                continue          # no audio on this input (an image, a scene...)
            out.append({'name': name, 'kind': inp.get('inputKind', ''), 'muted': bool(muted)})
        return out

    def _mute(self):
        done = self.muted_by_us
        for name in self.chosen:
            if name in done:
                continue
            try:
                if self.obs.call('GetInputMute', {'inputName': name}).get('inputMuted'):
                    continue      # already muted by someone else: leave it alone
                self.obs.call('SetInputMute', {'inputName': name, 'inputMuted': True})
                done.append(name)
            except RuntimeError as e:
                self.last_error = '%s: %s' % (name, e)
        self._set_muted_by_us(done)
        if done:
            self.last_action = 'Muted %s for the music' % ', '.join(done)
            self.log('[music] ' + self.last_action)

    def _unmute(self):
        left = []
        for name in self.muted_by_us:
            try:
                self.obs.call('SetInputMute', {'inputName': name, 'inputMuted': False})
            except RuntimeError as e:
                self.last_error = '%s: %s' % (name, e)
                left.append(name)       # OBS down: try again later
        restored = [n for n in self.muted_by_us if n not in left]
        self._set_muted_by_us(left)
        if restored:
            self.last_action = 'Unmuted %s' % ', '.join(restored)
            self.log('[music] ' + self.last_action)

    # ---------------------------------------------------------------- music events
    def music_changed(self, status):
        """Call with the music status after every change: playing | paused | stopped."""
        with self._lock:
            self.last_error = ''
            self._music_on = status == 'playing'
            if self.ann_active() and status != 'playing':
                return                    # the announcement still holds the mute; it unmutes when it ends
            if status == 'playing':
                self._unmute_at = None
                if self.enabled and self.chosen:
                    try:
                        self._mute()
                    except Exception as e:
                        self.last_error = str(e)
            else:
                delay = float(self.settings.get('music_unmute_delay', 2.0) or 0)
                self._unmute_at = time.time() + delay if self.muted_by_us else None

    def announce_changed(self, playing, cap=90.0):
        """The overlay in OBS started (True) or finished (False) playing an announcement."""
        with self._lock:
            self.last_error = ''
            if playing:
                if not self.ann_enabled:
                    return
                self._ann_until = time.time() + cap     # never stays muted if the overlay goes away mid-clip
                self._unmute_at = None
                if self.chosen:
                    try:
                        self._mute()
                    except Exception as e:
                        self.last_error = str(e)
            else:
                self._ann_until = None
                if self.muted_by_us and not (self._music_on and self.enabled):
                    delay = float(self.settings.get('ann_unmute_delay', 2.0) or 0)
                    self._unmute_at = time.time() + delay

    def tick(self, music_status):
        """Run every second or so: does delayed unmutes and crash recovery."""
        with self._lock:
            if self._held(music_status) or not self.muted_by_us:
                return
            if self._unmute_at is None:
                self._unmute_at = time.time()          # leftover from before a restart
            if time.time() >= self._unmute_at:
                try:
                    self._unmute()
                except Exception as e:
                    self.last_error = str(e)
                self._unmute_at = None if not self.muted_by_us else time.time() + 5

    def status(self):
        return {'enabled': self.enabled, 'chosen': self.chosen, 'muted_now': self.muted_by_us,
                'ann_on_stream': self.ann_enabled, 'ann_playing': self.ann_active(),
                'unmute_in': max(0, round(self._unmute_at - time.time(), 1)) if self._unmute_at else None,
                'last_action': self.last_action, 'last_error': self.last_error}
