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
    assert p['next_lanes'][3] == {'lane': 4, 'name': 'Finn Pemberton', 'team': 'NSHS', 'seed': ''}
    assert (p['then_event'], p['then_heat']) == (10, 2)


def test_team_colors_defaults_overrides_and_auto():
    ei = loaded()
    teams = meet_board.schedule_teams(ei)
    assert teams[:3] == ['GHS', 'CDRV', 'MILL']
    colors = meet_board.team_colors({'team_colors': {'GHS': {'name': 'Grantsville', 'color': '#FF0000', 'alt': ''}}}, teams)
    assert colors['GHS']['color'] == '#FF0000' and not colors['GHS']['auto']
    assert colors['TOOEL']['color'] == '#D18AE0'
    assert colors['CDRV']['auto'] and colors['CDRV']['color'].startswith('#')
    autos = [colors[t]['color'] for t in teams if colors[t]['auto']]
    assert len(autos) == len(set(autos))   # distinct placeholder colors


def test_lanes_past_configured_width_are_not_hidden():
    ei = loaded()
    p = meet_board.board_payload(ei, {'num_lanes': 6}, (3, 3))
    assert len(p['next_lanes']) == 8 and p['next_lanes'][7]['name'] == 'Grace Whitaker'
    p = meet_board.board_payload(ei, {'num_lanes': 6}, (3, 5))
    assert len(p['next_lanes']) == 6   # heat 10/1 only uses lanes 3-5
