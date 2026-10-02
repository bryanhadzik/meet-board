"""Seeded Meet Manager 'Meet Entries' exports leave the E2/F2 date blank;
the loader must still read heats/lanes, relay names and seed times."""
import os

import hytek_event_loader as h

SAMPLE = os.path.join(os.path.dirname(__file__), '..', 'samples', 'blank-dates-seeded.hy3')


def test_blank_dates_load_heats_and_lanes():
    L = h.HytekEventLoader(SAMPLE)
    assert L.events[(8, 7)][2] == 'Alpha Swimmer'
    assert L.seed_times[(8, 7)][2] == 27.07
    assert L.age_codes[(8, 7)][2] == '16M'


def test_relay_shows_team_and_letter_with_seed():
    L = h.HytekEventLoader(SAMPLE)
    assert L.events[(1, 4)][7] == 'Demo A'
    assert L.teams[(1, 4)][7] == 'SHS'
    assert L.seed_times[(1, 4)][7] == 137.54


def test_open_events_drop_0_109_age_label():
    L = h.HytekEventLoader(SAMPLE)
    assert L.event_names[8] == 'Men 50 Yard Freestyle'
    assert L.event_names[1] == 'Women 200 Yard Medley Relay'


def test_team_label_strips_school_suffixes():
    class T:
        def __init__(self, name, short='', code='X'):
            self.name, self.short_name, self.code = name, short, code
    assert h._team_label(T('Uintah High School Swim Team', 'UHS', 'UHS')) == 'Uintah'
    assert h._team_label(T('Tooele High School Swimming', '', 'TOOEL')) == 'Tooele'
    assert h._team_label(T('Morgan High Swim Team', 'Mor', 'MOR')) == 'Morgan'
    assert h._team_label(T('North Summit')) == 'North Summit'


def test_relay_legs_last_names_survive_save_and_reload():
    L = h.HytekEventLoader(SAMPLE)
    assert L.get_relay_legs(1, 4, 7) == ['Swimmer'] * 4
    assert L.get_relay_legs(8, 7, 2) == []          # individual swim
    L2 = h.HytekEventLoader()
    L2.from_object(L.to_object())
    assert L2.get_relay_legs(1, 4, 7) == ['Swimmer'] * 4


def test_board_payload_carries_relay_legs():
    import meet_board
    L = h.HytekEventLoader(SAMPLE)
    lanes = meet_board.lanes_for(L, (1, 4), 8)
    assert lanes[6]['name'] == 'Demo A' and lanes[6]['legs'] == ['Swimmer'] * 4
