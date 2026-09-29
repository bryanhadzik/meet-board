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
