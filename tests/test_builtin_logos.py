"""Built-in logos (static/logos) seed the logos folder once per code."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import team_logos

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_shipped_logos_are_named_by_code():
    names = os.listdir(os.path.join(ROOT, 'static', 'logos'))
    assert names and all(team_logos.code_from_filename(n) for n in names)
    for code in ('OGDEN', 'LHS', 'SJCHS', 'BLHS', 'FREM', 'MOR', 'GHS', 'WFHS', 'WJHS'):
        assert any(team_logos.code_from_filename(n) == code for n in names), code


def test_seed_once_and_never_overwrite(tmp_path):
    src, dst = tmp_path / 'src', tmp_path / 'dst'
    src.mkdir(); dst.mkdir()
    (src / 'AAA.png').write_bytes(b'builtin-a')
    (src / 'BBB.png').write_bytes(b'builtin-b')
    (dst / 'BBB.jpg').write_bytes(b'uploaded-b')          # someone's own logo
    copied, done = team_logos.seed_builtin(str(dst), str(src), [])
    assert copied == ['AAA'] and done == ['AAA', 'BBB']
    assert (dst / 'BBB.jpg').read_bytes() == b'uploaded-b' and not (dst / 'BBB.png').exists()
    # removed later -> stays removed
    os.remove(dst / 'AAA.png')
    copied, done = team_logos.seed_builtin(str(dst), str(src), done)
    assert copied == [] and not (dst / 'AAA.png').exists()
    # a new built-in in a later version is picked up
    (src / 'CCC.png').write_bytes(b'builtin-c')
    copied, done = team_logos.seed_builtin(str(dst), str(src), done)
    assert copied == ['CCC'] and 'CCC' in done
    # missing built-in folder is harmless
    assert team_logos.seed_builtin(str(dst), str(tmp_path / 'nope'), done)[0] == []


def test_2026_27_county_schedule_teams_have_logo_and_colors():
    """Every school on the 2026-27 Tooele County home-meet schedule has a built-in logo and board colors."""
    import meet_board
    names = {team_logos.code_from_filename(n) for n in os.listdir(os.path.join(ROOT, 'static', 'logos'))}
    for code in ('TOOEL', 'STAN', 'GHS', 'DPEAK', 'THS', 'MILL', 'BLHS', 'FREM', 'BRHS', 'RIDGE', 'GCHS',
                 'MCHS', 'SVHS', 'JMCHS', 'WFHS', 'WJHS'):
        assert code in meet_board.DEFAULT_TEAM_COLORS, code
        assert code in meet_board.DEFAULT_TEAM_CLASS, code
        assert code in names or any(a in names for a in meet_board.DEFAULT_TEAM_COLORS[code].get('aliases', [])), code
