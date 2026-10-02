import io
import os

import pytest

import CTS_Scoreboard as C
import music


def test_list_songs_pins_anthem_and_cleans_titles(tmp_path):
    for n in ['02 - Eye of the Tiger.mp3', '01_Jump.mp3', 'The Star Spangled Banner.mp3', 'notes.txt', 'Anthem (band).m4a']:
        (tmp_path / n).write_bytes(b'x')
    songs = music.list_songs(str(tmp_path))
    assert [s['anthem'] for s in songs[:2]] == [True, True]
    assert [s['title'] for s in songs[2:]] == ['Jump', 'Eye of the Tiger']
    assert all(s['file'].endswith(('.mp3', '.m4a')) for s in songs)


@pytest.mark.parametrize('raw,ok', [('song.mp3', True), ('../../etc/passwd', False), ('x.exe', False),
                                    ('C:\\Users\\me\\Anthem.mp3', True)])
def test_safe_filename(raw, ok):
    assert bool(music.safe_filename(raw)) == ok


def test_state_machine():
    m = music.MusicState()
    assert m.command('play', 'a.mp3', known_tracks=['a.mp3']) is None
    assert (m.status, m.play_id) == ('playing', 1)
    assert m.command('play', 'nope.mp3', known_tracks=['a.mp3']) == 'unknown song'
    m.command('pause'); assert m.status == 'paused'
    m.command('resume'); assert m.status == 'playing'
    m.command('volume', volume=3); assert m.volume == 1.0
    m.command('play', 'a.mp3', known_tracks=['a.mp3']); assert m.play_id == 2   # replay restarts
    assert m.report({'track': 'a.mp3', 'ended': True}) and m.status == 'stopped'
    m.command('stop'); assert m.status == 'stopped'


@pytest.fixture
def music_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(C, '_music_folder', lambda: str(tmp_path))
    return tmp_path


def test_upload_serve_delete(music_dir):
    c = C.app.test_client()
    r = c.post('/music/upload', data={'songs': [(io.BytesIO(b'ID3fake'), 'Star Spangled Banner.mp3'),
                                                (io.BytesIO(b'MZ'), 'virus.exe')]},
               content_type='multipart/form-data')
    d = r.get_json()
    assert d['saved'] == ['Star Spangled Banner.mp3'] and d['skipped'] == ['virus.exe']
    assert c.get('/music/file/Star Spangled Banner.mp3').data == b'ID3fake'
    assert c.get('/music/file/..%2Fsettings.json').status_code == 404
    page = c.get('/music').get_data(as_text=True)
    assert 'Star Spangled Banner' in page and 'class="mbnav"' in page
    assert c.post('/music/delete', json={'file': 'Star Spangled Banner.mp3'}).status_code == 200
    assert not os.listdir(music_dir)


def test_socket_commands_broadcast(music_dir):
    (music_dir / 'Jump.mp3').write_bytes(b'x')
    remote = C.socketio.test_client(C.app, namespace='/music')
    speaker = C.socketio.test_client(C.app, namespace='/music')
    speaker.emit('speaker_hello', {'ready': True}, namespace='/music')
    remote.get_received('/music'); speaker.get_received('/music')
    remote.emit('music_cmd', {'action': 'play', 'track': 'Jump.mp3'}, namespace='/music')
    got = [m for m in speaker.get_received('/music') if m['name'] == 'music_state']
    st = got[-1]['args'][0]
    assert st['track'] == 'Jump.mp3' and st['status'] == 'playing' and st['speaker'] and st['speaker_ready']
    speaker.emit('speaker_report', {'track': 'Jump.mp3', 'position': 12.5, 'duration': 180}, namespace='/music')
    prog = [m for m in remote.get_received('/music') if m['name'] == 'music_progress']
    assert prog and prog[-1]['args'][0]['position'] == 12.5
    remote.emit('music_cmd', {'action': 'stop'}, namespace='/music')
    speaker.disconnect(namespace='/music')
    assert C.music_state.speaker_sid is None
    remote.disconnect(namespace='/music')


def test_song_details_tags_and_length(tmp_path):
    import shutil, subprocess
    import music
    if not shutil.which('ffmpeg'):
        import pytest; pytest.skip('ffmpeg not available')
    f = tmp_path / '00 - Star Spangled Banner.mp3'
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=5',
                    '-ac', '1', '-b:a', '32k', str(f)], check=True)
    s = music.list_songs(str(tmp_path))[0]
    assert s['title'] == 'Star Spangled Banner' and s['anthem'] and not s['stream_ok']
    assert 4.5 <= s['duration'] <= 5.5
    music.update_meta(str(tmp_path), f.name, title='The Star-Spangled Banner', artist='U.S. Army Band', stream_ok=True)
    s = music.list_songs(str(tmp_path))[0]
    assert (s['title'], s['artist'], s['stream_ok']) == ('The Star-Spangled Banner', 'U.S. Army Band', True)
    import mutagen
    assert mutagen.File(str(f), easy=True).tags['artist'] == ['U.S. Army Band']   # written into the file too
    music.forget_meta(str(tmp_path), f.name)
    assert not music.list_songs(str(tmp_path))[0]['stream_ok']
