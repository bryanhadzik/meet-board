"""Meet music: a short playlist (anthem + swim songs) played by a browser tab
on the streaming PC, controlled from any device on the pool network.

  /music                 control page (phones, tablets, the PC itself)
  /music?speaker=1       the tab that actually plays audio - open it on the
                         streaming PC (its sound goes to the PC speakers/PA)

Audio files live in the "music" folder next to meet-board.exe (upload them on
the Music tab or copy them in). Anything with "anthem" or "star spangled" in
the name is pinned to the top.
"""
import os
import re

AUDIO_EXT = ('.mp3', '.m4a', '.aac', '.wav', '.ogg', '.oga', '.flac', '.webm')
MAX_SONGS = 30
_ANTHEM = re.compile(r'anthem|star[\s_-]*spangled', re.I)


def music_dir(data_path):
    d = data_path('music')
    os.makedirs(d, exist_ok=True)
    return d


def is_anthem(name):
    return bool(_ANTHEM.search(name))


def display_name(filename):
    base = os.path.splitext(filename)[0]
    base = re.sub(r'^\d+\s*[-_. ]\s*', '', base)      # "03 - Song" -> "Song"
    base = base.replace('_', ' ').strip()
    return base or filename


def safe_filename(name):
    name = os.path.basename(name or '').strip()
    name = re.sub(r'[^\w.\- ()&\']+', '_', name)
    if not name.lower().endswith(AUDIO_EXT):
        return None
    return name[:120]


def list_songs(folder):
    """[{file, title, anthem}] anthem first, then by filename."""
    try:
        names = [n for n in os.listdir(folder) if n.lower().endswith(AUDIO_EXT) and not n.startswith('.')]
    except OSError:
        names = []
    names.sort(key=lambda n: (not is_anthem(n), n.lower()))
    return [{'file': n, 'title': display_name(n), 'anthem': is_anthem(n)} for n in names[:MAX_SONGS]]


class MusicState:
    """What should be playing. The server is the source of truth; the
    speaker tab follows it and reports position back."""

    def __init__(self):
        self.track = None          # filename
        self.status = 'stopped'    # stopped | playing | paused
        self.volume = 0.8
        self.position = 0.0
        self.duration = 0.0
        self.seq = 0               # bumps on every change
        self.play_id = 0           # bumps on every "play": the speaker (re)starts the song from the top
        self.speaker_sid = None
        self.speaker_ready = False  # the speaker tab has been clicked (browser autoplay rule)

    def as_dict(self, songs=None):
        return {'track': self.track, 'status': self.status, 'volume': self.volume,
                'position': self.position, 'duration': self.duration, 'seq': self.seq, 'play_id': self.play_id,
                'speaker': bool(self.speaker_sid), 'speaker_ready': self.speaker_ready,
                'songs': songs if songs is not None else None}

    def command(self, action, track=None, volume=None, known_tracks=()):
        """Apply a control command. Returns an error string or None."""
        if action == 'play':
            if track not in known_tracks:
                return 'unknown song'
            self.track, self.status, self.position, self.duration = track, 'playing', 0.0, 0.0
            self.play_id += 1
        elif action == 'pause':
            if self.status == 'playing':
                self.status = 'paused'
        elif action == 'resume':
            if self.status == 'paused' and self.track:
                self.status = 'playing'
        elif action == 'stop':
            self.status, self.position = 'stopped', 0.0
        elif action == 'volume':
            try:
                self.volume = max(0.0, min(1.0, float(volume)))
            except (TypeError, ValueError):
                return 'bad volume'
        else:
            return 'unknown action'
        self.seq += 1
        return None

    def report(self, data):
        """Speaker tab's progress report."""
        try:
            self.position = float(data.get('position') or 0)
            self.duration = float(data.get('duration') or 0)
        except (TypeError, ValueError):
            pass
        if data.get('ended') and data.get('track') == self.track:
            self.status, self.position = 'stopped', 0.0
            self.seq += 1
            return True
        return False
