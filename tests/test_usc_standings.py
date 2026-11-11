"""This season's standings (utahswimcoach.com) sent with MeetDeck start lists."""
import pytest

import CTS_Scoreboard as C

DOC = {"format": "meetdeck-startlist", "version": 1, "state_src": {"name": "utahswimcoach.com", "as_of": "2026-11-10T09:00"},
       "events": [
           {"number": 3, "gender": "F", "distance": 50, "stroke": "A",
            "state_now": [{"cls": "4A", "line": 28.2, "spots": 32, "top": 25.1}],
            "heats": [{"heat": 1, "lanes": [
                {"lane": 4, "team": "STAN", "first": "Ava", "last": "Cox", "state": {"cls": "4A", "rank": 5, "sb": 25.5}},
                {"lane": 5, "team": "STAN", "first": "Mia", "last": "Lee"}]}]},
           {"number": 1, "gender": "F", "distance": 200, "stroke": "E", "relay": True,
            "heats": [{"heat": 1, "lanes": [{"lane": 3, "team": "GHS", "letter": "A", "state": {"cls": "3A", "rank": 2, "sb": 121.0}}]}]}]}


@pytest.fixture
def clean(monkeypatch):
    for k in ("usc_lanes", "usc_events", "usc_src", "usc_board"):
        monkeypatch.setitem(C.settings, k, C.settings.get(k))
    yield


def test_store_and_match(clean):
    C._store_usc(DOC)
    assert C._usc_lane(3, "STAN", "Ava Cox") == {"cls": "4A", "rank": 5, "sb": 25.5, "last": "Cox"}
    assert C._usc_lane(3, "STAN", "COX, AVA") is not None        # .scb name styles
    assert C._usc_lane(3, "STAN", "Mia Lee") is None
    assert C._usc_lane(3, "TOOEL", "Ava Cox") is None
    assert C._usc_lane(1, "GHS", 'Grantsville "A"')["rank"] == 2
    assert C._usc_lane(1, "GHS", 'Grantsville "B"') is None
    assert C._usc_event(3)[0]["line"] == 28.2 and C._usc_event(1) is None
    C.settings["usc_board"] = False
    assert C._usc_lane(3, "STAN", "Ava Cox") is None and C._usc_event(3) is None


def test_plain_start_list_clears_old_standings(clean):
    C._store_usc(DOC)
    C._store_usc({"events": [{"number": 3, "heats": []}]})
    assert C._usc_lane(3, "STAN", "Ava Cox") is None and C.settings["usc_src"] == {}
