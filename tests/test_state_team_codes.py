"""Every non-6A school code in the state Team Manager list (utahswimcoach.com,
regions 4-22) resolves to a built-in school with that school's name."""
import os
import meet_board as M
import team_logos as T

STATE = {  # code: school, from utahswimcoach.com (Nov 2026)
    'CYP': 'Cyprus', 'GRAN': 'Granger', 'HILL': 'Hillcrest', 'HNTR': 'Hunter', 'KHS': 'Kearns',
    'TAYL': 'Taylorsville', 'WEST': 'West', 'WJHS': 'West Jordan',
    'BEHS': 'Box Elder', 'BONN': 'Bonneville', 'CLEA': 'Clearfield', 'FREM': 'Fremont',
    'NORT': 'Northridge', 'ROY': 'Roy', 'WFHS': 'West Field',
    'ALTA': 'Alta', 'BNTF': 'Bountiful', 'OLY': 'Olympus', 'SKY': 'Skyline', 'VHS': 'Viewmont', 'WXHS': 'Woods Cross',
    'MMHS': 'Maple Mountain', 'PAYS': 'Payson', 'PGHS': 'Pleasant Grove', 'SFHS': 'Spanish Fork',
    'SHHS': 'Salem Hills', 'SPRV': 'Springville', 'TIMP': 'Timpview',
    'DCHS': 'Deer Creek', 'MVHS': 'Mountain View', 'OREM': 'Orem', 'PROV': 'Provo', 'THS': 'Timpanogos', 'UHS': 'Uintah',
    'CCHS': 'Cedar', 'CRIM': 'Crimson Cliffs', 'DHHS': 'Desert Hills', 'DIXI': 'Dixie', 'HURR': 'Hurricane',
    'PVHS': 'Pine View', 'SCHS': 'Snow Canyon',
    'COLT': 'Cottonwood', 'EAST': 'East', 'HIGH': 'Highland', 'JD': 'Juan Diego', 'JM': 'Judge Memorial',
    'JOR': 'Jordan', 'MHS': 'Murray',
    'BRHS': 'Bear River', 'DPHS': 'Deseret Peak', 'GCHS': 'Green Canyon', 'MCHS': 'Mountain Crest',
    'RIDG': 'Ridgeline', 'SHS': 'Stansbury', 'SVHS': 'Sky View', 'TOOE': 'Tooele',
    'BLHS': 'Ben Lomond', 'GHS': 'Grantsville', 'LHS': 'Logan', 'MOR': 'Morgan', 'OGDE': 'Ogden',
    'EMER': 'Emery', 'MANT': 'Manti', 'NSPH': 'North Sanpete', 'UNIO': 'Union',
    'CVHS': 'Canyon View', 'DELT': 'Delta', 'JUAB': 'Juab',
    'GVHS': 'Gunnison Valley', 'NSHS': 'North Summit', 'SSHS': 'South Summit',
    'VA': 'Vanguard Academy', 'WA': 'Wasatch Academy', 'RH': 'Rowland Hall', 'HA': 'Havenwood Academy',
    'SJCH': 'St. Joseph', 'WASA': 'Wasatch', 'CARB': 'Carbon', 'MILL': 'Millard', 'RICH': 'Richfield',
    'LIAH': 'Liahona', 'PHAL': 'Providence Hall', 'PCHS': 'Park City', 'SA': 'Summit Academy',
    'UAA': 'Utah Arts Academy', 'VS': 'Vista School', 'LCA': 'Layton Christian Academy',
}


def test_every_state_code_maps_to_its_school():
    canon = M.canonical_codes({}, list(STATE))
    for code, school in STATE.items():
        home = canon[code]
        assert home in M.DEFAULT_TEAM_COLORS, code
        assert M.DEFAULT_TEAM_COLORS[home]['name'] == school, (code, home)


def test_moved_codes_migrate_saved_rows():
    s = {'team_colors': {'CCHS': {'name': 'Crimson Cliffs', 'color': '#0461e4', 'alt': '', 'aliases': ['CRIMS']},
                         'TIMP': {'name': 'Timpanogos', 'color': '#3f7fd9', 'alt': '', 'aliases': ['TIMPA']},
                         'THS': {'name': 'Timp', 'color': '#ffe066', 'alt': '', 'aliases': []}}}
    assert M.migrate_team_codes(s)
    tc = s['team_colors']
    assert 'CCHS' not in tc and 'TIMP' not in tc
    assert tc['CRIM']['name'] == 'Crimson Cliffs' and tc['CRIM']['aliases'] == ['CRIMS']
    assert tc['THS']['name'] == 'Timp' and tc['THS']['aliases'] == ['TIMPA']
    assert not M.migrate_team_codes(s)
    assert M.canonical_codes(s, ['CCHS', 'TIMP']) == {'CCHS': 'CEDAR', 'TIMP': 'TIMP'}


def test_retired_builtin_logo_is_replaced(tmp_path):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    builtin = os.path.join(here, 'static', 'logos')
    logos = tmp_path / 'logos'
    logos.mkdir()
    # the old Timpanogos logo seeded as TIMP.png, and one someone uploaded as CCHS.png
    (logos / 'TIMP.png').write_bytes(open(os.path.join(builtin, 'THS.png'), 'rb').read())
    (logos / 'CCHS.png').write_bytes(b'uploaded')
    copied, done = T.seed_builtin(str(logos), builtin, ['TIMP', 'CCHS', 'THS'])
    assert 'TIMP' in copied   # Timpview's logo replaces it
    assert open(logos / 'TIMP.png', 'rb').read() == open(os.path.join(builtin, 'TIMP.png'), 'rb').read()
    assert (logos / 'CCHS.png').read_bytes() == b'uploaded'   # never touch an upload


def test_saved_auto_color_gives_way_to_new_builtin():
    s = {'team_colors': {'RH': {'name': 'Rowland Hall', 'color': '#F2A33A', 'alt': '#9aa6ab', 'aliases': []},
                         'CARB': {'name': 'Carbon', 'color': '#9fd356', 'alt': '', 'aliases': []}}}
    assert M.migrate_team_codes(s)
    assert M.team_colors(s, ['RH'])['RH']['color'] == M.DEFAULT_TEAM_COLORS['RH']['color']
    assert s['team_colors']['CARB']['color'] == '#9fd356'   # older choices stay


def test_uploaded_timpanogos_copy_under_timp_is_replaced(tmp_path):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    builtin = os.path.join(here, 'static', 'logos')
    (tmp_path / 'THS.png').write_bytes(b'timpanogos upload')
    (tmp_path / 'TIMP.png').write_bytes(b'timpanogos upload')
    copied, _ = T.seed_builtin(str(tmp_path), builtin, ['TIMP', 'THS'])
    assert 'TIMP' in copied and (tmp_path / 'THS.png').read_bytes() == b'timpanogos upload'
    assert (tmp_path / 'TIMP.png').read_bytes() == open(os.path.join(builtin, 'TIMP.png'), 'rb').read()
