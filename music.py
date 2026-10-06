"""Meet music: a short playlist (anthem + swim songs) played by a browser tab
on the streaming PC, controlled from any device on the pool network.

  /music                 control page (phones, tablets, the PC itself)
  /music?speaker=1       the tab that actually plays audio - open it on the
                         streaming PC (its sound goes to the PC speakers/PA)

Two separate lists, one player:

  music   - the short meet playlist you tap song by song (anthem, swim songs)
  warmup  - a longer pile of tracks for warm-up, played on shuffle by one
            button. Deliberately NOT the same list: warm-up runs for forty
            minutes unattended and must not put the anthem on at random.

Audio files live in the "music" and "warmup" folders next to meet-board.exe.
Anything with "anthem" or "star spangled" in the name is pinned to the top of
the meet playlist (warm-up is not sorted that way - it is shuffled).
"""
import json
import os
import random
import re
import threading

try:
    import mutagen               # reads/writes song tags and length (MP3, M4A, FLAC, OGG, WAV)
except ImportError:              # pragma: no cover - the app still works, just without tags/lengths
    mutagen = None

AUDIO_EXT = ('.mp3', '.m4a', '.aac', '.wav', '.ogg', '.oga', '.flac', '.webm')
MAX_SONGS = 30
MAX_WARMUP = 300          # a 40-minute warm-up is ~12 songs; the cap is just a sanity bound
_ANTHEM = re.compile(r'anthem|star[\s_-]*spangled', re.I)


def music_dir(data_path):
    d = data_path('music')
    os.makedirs(d, exist_ok=True)
    return d


def warmup_dir(data_path):
    d = data_path('warmup')
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


META_FILE = 'songs.json'      # per-song details you edited, next to the songs
_meta_lock = threading.Lock()
_tag_cache = {}                # (path, mtime) -> {title, artist, duration}


def load_meta(folder):
    try:
        with open(os.path.join(folder, META_FILE), encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_meta(folder, meta):
    tmp = os.path.join(folder, META_FILE + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=1, sort_keys=True)
    os.replace(tmp, os.path.join(folder, META_FILE))


def read_tags(path):
    """{title, artist, duration} from the file's own tags ('' / None when unknown)."""
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return {'title': '', 'artist': '', 'duration': None}
    if key in _tag_cache:
        return _tag_cache[key]
    out = {'title': '', 'artist': '', 'duration': None}
    if mutagen is not None:
        try:
            f = mutagen.File(path, easy=True)
            if f is not None:
                if getattr(f, 'info', None) is not None and getattr(f.info, 'length', None):
                    out['duration'] = round(float(f.info.length), 1)
                tags = f.tags or {}
                out['title'] = (tags.get('title') or [''])[0] if hasattr(tags, 'get') else ''
                out['artist'] = (tags.get('artist') or [''])[0] if hasattr(tags, 'get') else ''
        except Exception:
            pass
    _tag_cache[key] = out
    return out


def write_tags(path, title, artist):
    """Best effort: put title/artist into the file's own tags too, so they
    travel with the file. Returns True when the file was updated."""
    if mutagen is None:
        return False
    try:
        f = mutagen.File(path, easy=True)
        if f is None:
            return False
        if f.tags is None:
            try:
                f.add_tags()
            except Exception:
                return False
        f.tags['title'] = [title] if title else []
        f.tags['artist'] = [artist] if artist else []
        f.save()
        return True
    except Exception:
        return False


def update_meta(folder, name, title=None, artist=None, stream_ok=None, duration=None):
    """Edit one song's details. title/artist also go into the file's tags."""
    with _meta_lock:
        meta = load_meta(folder)
        m = dict(meta.get(name) or {})
        if title is not None:
            m['title'] = title.strip()[:80]
        if artist is not None:
            m['artist'] = artist.strip()[:80]
        if stream_ok is not None:
            m['stream_ok'] = bool(stream_ok)
        if duration is not None:
            m['duration'] = round(float(duration), 1)
        meta[name] = {k: v for k, v in m.items() if v not in ('', None)}
        save_meta(folder, meta)
    if title is not None or artist is not None:
        write_tags(os.path.join(folder, name), meta[name].get('title', ''), meta[name].get('artist', ''))
    return meta[name]


def forget_meta(folder, name):
    with _meta_lock:
        meta = load_meta(folder)
        if meta.pop(name, None) is not None:
            save_meta(folder, meta)


def list_songs(folder, limit=MAX_SONGS, anthem_first=True):
    """[{file, title, artist, duration, anthem, stream_ok}] anthem first, then by filename.
    Title/artist: what you typed on the Music tab, else the file's tags, else the file name.
    The warm-up list passes anthem_first=False - it is shuffled, so pinning is meaningless."""
    try:
        names = [n for n in os.listdir(folder) if n.lower().endswith(AUDIO_EXT) and not n.startswith('.')]
    except OSError:
        names = []
    names.sort(key=lambda n: ((not is_anthem(n)) if anthem_first else False, n.lower()))
    meta = load_meta(folder)
    out = []
    for n in names[:limit]:
        m = meta.get(n) or {}
        tags = read_tags(os.path.join(folder, n))
        out.append({
            'file': n,
            'title': m.get('title') or tags['title'] or display_name(n),
            'artist': m.get('artist') or tags['artist'] or '',
            'duration': tags['duration'] or m.get('duration'),
            'anthem': is_anthem(n) or is_anthem(m.get('title', '')),
            'stream_ok': bool(m.get('stream_ok')),
        })
    return out


class ShuffleQueue:
    """Shuffle that actually behaves at a swim meet.

    Plain random repeats songs while others never play, which parents notice
    over a forty-minute warm-up. This deals the whole list in random order and
    only reshuffles once every song has had a turn, and it will not open a new
    pass with the song that just finished.

    The file list is passed in each time rather than held, so adding or
    deleting warm-up songs mid-meet is picked up without restarting anything.
    """

    def __init__(self):
        self._queue = []
        self._last = None

    def reset(self):
        self._queue = []
        self._last = None

    @property
    def remaining(self):
        return len(self._queue)

    def next(self, files, rng=random):
        files = [f for f in (files or []) if f]
        if not files:
            self._queue = []
            return None
        # drop anything deleted since the pass was dealt
        self._queue = [f for f in self._queue if f in files]
        if not self._queue:
            self._queue = list(files)
            rng.shuffle(self._queue)
            # don't play the same song twice across the seam between passes
            if len(self._queue) > 1 and self._queue[0] == self._last:
                self._queue.append(self._queue.pop(0))
        nxt = self._queue.pop(0)
        self._last = nxt
        return nxt


class MusicState:
    """What should be playing. The server is the source of truth; the
    speaker tab follows it and reports position back."""

    def __init__(self):
        self.track = None          # filename
        self.mode = 'manual'       # manual (meet playlist) | warmup (shuffle)
        self.shuffle = ShuffleQueue()
        self.status = 'stopped'    # stopped | playing | paused
        self.volume = 0.8
        self.position = 0.0
        self.duration = 0.0
        self.seq = 0               # bumps on every change
        self.play_id = 0           # bumps on every "play": the speaker (re)starts the song from the top
        self.speaker_sid = None
        self.speaker_ready = False  # the speaker tab has been clicked (browser autoplay rule)

    def as_dict(self, songs=None, warmup=None):
        return {'track': self.track, 'status': self.status, 'volume': self.volume, 'mode': self.mode,
                'position': self.position, 'duration': self.duration, 'seq': self.seq, 'play_id': self.play_id,
                'speaker': bool(self.speaker_sid), 'speaker_ready': self.speaker_ready,
                'songs': songs if songs is not None else None,
                'warmup': warmup if warmup is not None else None}

    def command(self, action, track=None, volume=None, known_tracks=()):
        """Apply a control command. Returns an error string or None."""
        if action == 'play':
            if track not in known_tracks:
                return 'unknown song'
            # Tapping a song in the meet playlist ends warm-up: one player, and
            # the anthem must never be followed by a shuffled pop track.
            self.mode = 'manual'
            self.shuffle.reset()
            self.track, self.status, self.position, self.duration = track, 'playing', 0.0, 0.0
            self.play_id += 1
        elif action == 'pause':
            if self.status == 'playing':
                self.status = 'paused'
        elif action == 'resume':
            if self.status == 'paused' and self.track:
                self.status = 'playing'
        elif action == 'stop':
            # Stop means stop, so it leaves warm-up too; otherwise the next
            # track would start on its own a second later.
            self.mode = 'manual'
            self.shuffle.reset()
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

    # --- warm-up shuffle -------------------------------------------------
    def start_warmup(self, files):
        """Begin the shuffled warm-up list. Returns an error string or None."""
        self.shuffle.reset()
        return self._play_next(files)

    def skip_warmup(self, files):
        """Operator pressed Skip."""
        if self.mode != 'warmup':
            return 'warm-up is not running'
        return self._play_next(files)

    def advance(self, files):
        """The track finished on its own - roll on to the next one."""
        if self.mode != 'warmup':
            return 'warm-up is not running'
        return self._play_next(files)

    def stop_warmup(self):
        self.mode = 'manual'
        self.shuffle.reset()
        self.track, self.status, self.position, self.duration = None, 'stopped', 0.0, 0.0
        self.seq += 1

    def _play_next(self, files):
        nxt = self.shuffle.next(files)
        if nxt is None:
            self.stop_warmup()
            return 'no warm-up songs'
        self.mode = 'warmup'
        self.track, self.status, self.position, self.duration = nxt, 'playing', 0.0, 0.0
        self.play_id += 1
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
