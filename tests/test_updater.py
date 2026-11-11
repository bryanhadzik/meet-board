import updater


def test_version_compare():
    assert updater.is_newer('0.2.10', '0.2.9')
    assert not updater.is_newer('0.2.9', '0.2.9')
    assert updater.is_newer('v0.3.0', '0.2.99')
    assert updater.is_newer('0.2.1', '0.0.0-dev')


def test_swap_script():
    script = updater._swap_script(r'C:\MeetBoard\meet-board.exe', r'C:\MeetBoard\meet-board.exe.new', 4242, ['--port', 'COM3'])
    assert 'PID eq 4242' in script
    assert r'move /y "C:\MeetBoard\meet-board.exe.new" "C:\MeetBoard\meet-board.exe"' in script
    assert r'start "" "C:\MeetBoard\meet-board.exe" --port COM3' in script
    assert '\r\n' in script


def test_source_mode_refuses_update():
    ok, msg = updater.start_update()
    assert not ok and 'source' in msg


def test_release_notes_drop_attribution_trailers():
    body = ("Fix a thing\n\nCo-Authored-By: Someone <x@y.z>\nClaude-Session: https://example.test/s\n\n"
            "Install: download meet-board.exe")
    assert updater.clean_notes(body) == "Fix a thing\n\nInstall: download meet-board.exe"


def test_source_commit_reads_git_without_git(tmp_path):
    g = tmp_path / '.git'
    (g / 'refs' / 'heads').mkdir(parents=True)
    (g / 'HEAD').write_text('ref: refs/heads/main\n')
    (g / 'refs' / 'heads' / 'main').write_text('3e30f0a' + '0' * 33 + '\n')
    assert updater.source_commit(str(tmp_path)).startswith('3e30f0a')
    (g / 'refs' / 'heads' / 'main').unlink()
    (g / 'packed-refs').write_text('# pack-refs\n' + 'abcdef1' + '0' * 33 + ' refs/heads/main\n')
    assert updater.source_commit(str(tmp_path)).startswith('abcdef1')
