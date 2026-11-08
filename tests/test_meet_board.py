import os

import meet_board
import scb_loader
from hytek_event_loader import HytekEventLoader

SCB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'samples', 'scb')


def loaded():
    loader = HytekEventLoader()
    scb_loader.load_scb_into(loader, scb_loader.read_scb_folder(SCB_DIR))
    return loader


def test_next_within_event_and_across_events():
    ei = loaded()
    assert meet_board.next_heats(ei, (3, 1), 2) == [(3, 2), (3, 3)]
    assert meet_board.next_heats(ei, (3, 5), 2) == [(10, 1), (10, 2)]
    assert meet_board.next_heats(ei, (10, 4), 2) == []


def test_next_when_console_event_not_in_schedule():
    ei = loaded()
    assert meet_board.next_heats(ei, (5, 1), 1) == [(10, 1)]
    assert meet_board.next_heats(ei, (0, 0), 1) == [(3, 1)]


def test_combined_heats_are_skipped():
    ei = loaded()
    ei.combine_events({(3, 1): (3, 2)})
    assert (3, 1) not in meet_board.heat_order(ei)
    assert meet_board.next_heats(ei, (3, 1), 1) == [(3, 3)]   # console showing source heat
    assert meet_board.heat_count(ei, 3) == 4


def test_payload():
    ei = loaded()
    p = meet_board.board_payload(ei, {'num_lanes': 8}, (3, 5))
    assert p['heat_count'] == 5
    assert (p['next_event'], p['next_heat'], p['next_heat_count']) == (10, 1, 4)
    assert p['next_event_name'] == 'Men 100 Butterfly'
    assert len(p['next_lanes']) == 8
    assert p['next_lanes'][3] == {'lane': 4, 'name': 'Finn Pemberton', 'team': 'NSHS', 'seed': '', 'legs': []}
    assert (p['then_event'], p['then_heat']) == (10, 2)


def test_team_colors_defaults_overrides_and_auto():
    ei = loaded()
    teams = meet_board.schedule_teams(ei)
    assert teams[:3] == ['GHS', 'CDRV', 'MILL']
    colors = meet_board.team_colors({'team_colors': {'GHS': {'name': 'Grantsville', 'color': '#FF0000', 'alt': ''}}}, teams)
    assert colors['GHS']['color'] == '#FF0000' and not colors['GHS']['auto']
    assert colors['TOOEL']['color'] == '#9B5DE5'
    assert colors['CDRV']['color'] == '#DC143C' and not colors['CDRV']['auto']   # built-in school color
    assert colors['MILL']['color'] == '#2F7BE6' and not colors['MILL']['auto']
    autos = [colors[t]['color'] for t in teams if colors[t]['auto']]
    assert len(autos) == len(set(autos))   # distinct placeholder colors


def test_lanes_past_configured_width_are_not_hidden():
    ei = loaded()
    p = meet_board.board_payload(ei, {'num_lanes': 6}, (3, 3))
    assert len(p['next_lanes']) == 8 and p['next_lanes'][7]['name'] == 'Grace Whitaker'
    p = meet_board.board_payload(ei, {'num_lanes': 6}, (3, 5))
    assert len(p['next_lanes']) == 6   # heat 10/1 only uses lanes 3-5


def test_fixed_pool_settings(monkeypatch):
    """8 lanes and short course yards always; ad display and the warm-up
    slide's own meet name are gone (the Meet tab's name is the one)."""
    import CTS_Scoreboard as C
    monkeypatch.setattr(C, 'save_settings', lambda: None)
    monkeypatch.setattr(C, 'settings', dict(C.settings, num_lanes=6, pool_course='LCM', ad_url='x.jpg', warmup_title='Old'))
    C.settings.pop('settings_version', None)
    C.migrate_settings()
    assert (C.settings['num_lanes'], C.settings['pool_course'], C.settings['ad_url'], C.settings['warmup_title']) == (8, 'SCY', '', '')
    assert C.settings['settings_version'] == C.SETTINGS_VERSION
    C.settings['num_lanes'] = 6           # even if something sets it, the next start puts it back
    C.migrate_settings()
    assert C.settings['num_lanes'] == 8


def test_admin_pages_after_the_reshuffle(monkeypatch):
    import CTS_Scoreboard as C
    monkeypatch.setattr(C, 'save_settings', lambda: None)
    c = C.app.test_client()
    st = c.get('/settings').get_data(as_text=True)
    assert 'Number of Lanes' not in st and 'Pool Course' not in st and 'Ad Display' not in st
    assert 'Team Names' not in st and 'id="meet_title"' not in st and 'wu_title' not in st
    mf = c.get('/meet_files').get_data(as_text=True)
    assert 'id="meet_title"' in mf and 'statecuts' not in mf and 'Meet Manager start lists' in mf
    tc = c.get('/team_colors').get_data(as_text=True)
    assert 'Team names (scores)' in tc and 'name="team_home"' in tc
    stt = c.get('/state_times').get_data(as_text=True)
    assert 'id="sc_body"' in stt and '>State Times<' in stt
    # team names saved from Team Colors land back there
    old = C.settings.get('team_guest3', ''), C.settings.get('team_guest3_tag', '')
    try:
        r = c.post('/settings', data={'team_guest3': 'Timpanogos', 'team_guest3_tag': 'TIMP', 'return_to': '/team_colors'})
        assert r.status_code == 302 and r.headers['Location'].endswith('/team_colors')
        assert C.settings['team_guest3'] == 'Timpanogos' and C.settings['team_guest3_tag'] == 'TIMP'
        # the meet name saved from the Meet tab
        old_title = C.settings.get('meet_title', '')
        c.post('/meet_files', data={'meet_title': 'Black and Blue Meet'})
        assert C.settings['meet_title'] == 'Black and Blue Meet'
        C.settings['meet_title'] = old_title
    finally:
        C.settings['team_guest3'], C.settings['team_guest3_tag'] = old


def test_resolved_colors_separate_close_teams():
    colors = {'A': {'color': '#3D8BF5', 'alt': '#9AA6AB'}, 'B': {'color': '#3E8CF4', 'alt': '#F0546A'},
              'C': {'color': '#3D8BF6', 'alt': '', 'auto': True}}
    out = meet_board.resolved_colors(colors, ['B', 'A', 'C'], home='A')
    assert out['A'] == '#3D8BF5'          # home keeps primary
    assert out['B'] == '#F0546A'          # clash -> alternate
    assert meet_board.delta_e(out['C'], out['A']) >= 15   # auto -> distinct palette color


def test_one_school_several_codes():
    # built-in alias: SHS and STAN are both Stansbury
    home = meet_board.canonical_codes({}, ['SHS', 'STAN', 'DPHS', 'XYZ'])
    assert home == {'SHS': 'STAN', 'STAN': 'STAN', 'DPHS': 'DPEAK', 'XYZ': 'XYZ'}
    c = meet_board.team_colors({}, ['SHS'])
    assert c['SHS']['alias_of'] == 'STAN' and c['SHS']['color'] == c['STAN']['color'] and not c['SHS']['auto']
    # .hy3 school name links an unknown code to a known school
    c = meet_board.team_colors({}, ['THS'], {'THS': 'Tooele High School Swimming'})
    assert c['THS']['alias_of'] == 'TOOEL'
    # aliases typed on the Team Colors page
    s = {'team_colors': {'GHS': {'name': 'Grantsville', 'color': '#E5383B', 'alt': '', 'aliases': ['GRAN']}}}
    assert meet_board.canonical_codes(s, ['GRAN'])['GRAN'] == 'GHS'
    assert meet_board.alias_codes(s, 'GHS') == ['GHS', 'GRAN']


def test_school_key():
    assert meet_board.school_key('Uintah High School Swim Team') == 'uintah'
    assert meet_board.school_key('Cedar Valley High School Swim') == 'cedar valley'


def test_blank_saved_name_falls_back_to_school_name():
    import meet_board as M
    st = {'team_colors': {'MILL': {'name': '', 'color': '#ff7f6b'}}}
    t = M.team_colors(st, ['MILL', 'ZZHS'], {'ZZHS': 'Zebra Valley High School', 'MILL': 'Millard High School'})
    assert t['MILL']['name'] == 'Millard' and t['MILL']['color'] == '#ff7f6b'
    assert t['ZZHS']['name'] == 'Zebra Valley'
    assert M.team_colors({}, ['QQQ'], {})['QQQ']['name'] == ''
