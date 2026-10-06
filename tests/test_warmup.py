"""Warm-up music: the shuffle and the two-list music state (no Flask needed)."""
import collections
import os

import music

FILES = ['s%d.mp3' % i for i in range(8)]


def test_a_pass_plays_every_song_once_then_reshuffles():
    q = music.ShuffleQueue()
    first = [q.next(FILES) for _ in range(8)]
    second = [q.next(FILES) for _ in range(8)]
    assert sorted(first) == sorted(FILES)
    assert sorted(second) == sorted(FILES)


def test_no_back_to_back_repeats_even_across_passes():
    for _ in range(400):
        q, prev = music.ShuffleQueue(), None
        for _ in range(25):
            n = q.next(FILES)
            assert n != prev
            prev = n


def test_fair_over_a_long_warmup():
    q = music.ShuffleQueue()
    counts = collections.Counter(q.next(FILES) for _ in range(8 * 500))
    assert set(counts.values()) == {500}


def test_songs_deleted_or_added_mid_warmup():
    q = music.ShuffleQueue()
    q.next(FILES)
    smaller = FILES[:3]
    assert all(q.next(smaller) in smaller for _ in range(6))
    bigger = FILES + ['new.mp3']
    assert 'new.mp3' in {q.next(bigger) for _ in range(40)}


def test_degenerate_lists():
    q = music.ShuffleQueue()
    assert q.next([]) is None and q.next(None) is None
    q = music.ShuffleQueue()
    assert [q.next(['only.mp3']) for _ in range(3)] == ['only.mp3'] * 3


def test_start_skip_stop():
    st = music.MusicState()
    assert st.mode == 'manual'
    assert st.start_warmup(FILES) is None and st.mode == 'warmup' and st.status == 'playing'
    first, pid = st.track, st.play_id
    assert st.skip_warmup(FILES) is None
    assert st.track != first and st.play_id == pid + 1
    st.stop_warmup()
    assert (st.mode, st.status, st.track) == ('manual', 'stopped', None)
    assert st.skip_warmup(FILES) == 'warm-up is not running'
    assert music.MusicState().start_warmup([]) == 'no warm-up songs'


def test_meet_playlist_tap_or_stop_ends_warmup():
    st = music.MusicState()
    st.start_warmup(FILES)
    assert st.command('play', track='anthem.mp3', known_tracks=['anthem.mp3']) is None
    assert st.mode == 'manual' and st.track == 'anthem.mp3' and st.shuffle.remaining == 0
    st.start_warmup(FILES)
    st.command('stop')
    assert st.mode == 'manual' and st.status == 'stopped'


def test_pause_resume_stay_in_warmup():
    st = music.MusicState()
    st.start_warmup(FILES)
    t = st.track
    st.command('pause')
    assert st.status == 'paused' and st.mode == 'warmup'
    st.command('resume')
    assert st.status == 'playing' and st.track == t


def test_finished_track_advances():
    st = music.MusicState()
    st.start_warmup(FILES)
    t, pid = st.track, st.play_id
    assert st.report({'position': 180, 'duration': 180, 'ended': True, 'track': t}) is True
    assert st.advance(FILES) is None and st.track != t and st.play_id > pid
    assert music.MusicState().advance(FILES) == 'warm-up is not running'


def test_as_dict_carries_mode_and_both_lists():
    st = music.MusicState()
    d = st.as_dict(songs=[{'file': 'a.mp3'}], warmup=[{'file': 'w.mp3'}])
    assert d['mode'] == 'manual' and d['songs'][0]['file'] == 'a.mp3' and d['warmup'][0]['file'] == 'w.mp3'
    d2 = st.as_dict()
    assert d2['songs'] is None and d2['warmup'] is None


def test_separate_folders_and_warmup_not_anthem_pinned(tmp_path):
    m, w = tmp_path / 'music', tmp_path / 'warmup'
    m.mkdir(); w.mkdir()
    for n in ('01 - Opening.mp3', 'National Anthem.mp3'):
        (m / n).write_bytes(b'')
    for n in ('Zebra.mp3', 'Alpha.mp3', 'Star Spangled Remix.mp3'):
        (w / n).write_bytes(b'')
    ms = music.list_songs(str(m))
    ws = music.list_songs(str(w), limit=music.MAX_WARMUP, anthem_first=False)
    assert ms[0]['file'] == 'National Anthem.mp3'
    assert [x['file'] for x in ws] == ['Alpha.mp3', 'Star Spangled Remix.mp3', 'Zebra.mp3']
    wd = music.warmup_dir(lambda *p: os.path.join(str(tmp_path), *p))
    assert os.path.isdir(wd) and os.path.basename(wd) == 'warmup'
