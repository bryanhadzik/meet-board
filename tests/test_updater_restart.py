"""After an update the new exe has to start as a fresh app."""
import updater


def test_clean_env_drops_pyinstaller_leftovers_and_sets_reset():
    env = updater.clean_env({'PATH': r'C:\Windows', '_PYI_APPLICATION_HOME_DIR': r'C:\T\_MEI123',
                             '_PYI_PARENT_PROCESS_LEVEL': '1', '_pyi_archive_file': 'x', '_MEIPASS2': 'y'})
    assert env == {'PATH': r'C:\Windows', 'PYINSTALLER_RESET_ENVIRONMENT': '1'}


def test_swap_script_waits_swaps_then_starts_fresh():
    s = updater._swap_script(r'C:\MeetBoard\meet-board.exe', r'C:\MeetBoard\meet-board.exe.new', 1234, [])
    i_wait, i_move = s.index('PID eq 1234'), s.index('move /y')
    i_reset, i_start = s.index('set PYINSTALLER_RESET_ENVIRONMENT=1'), s.index('start ""')
    assert i_wait < i_move < i_reset < i_start
    assert r'start "" "C:\MeetBoard\meet-board.exe"' in s
