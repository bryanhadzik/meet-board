"""Start lists pushed as JSON (MeetDeck -> POST /api/startlists).

Fills a HytekEventLoader exactly the way a Meet Manager .hy3 does, so every
board, overlay, record and time-standard lookup works unchanged - including
relay swimmers' last names, which a CTS .scb start list cannot carry.

Document (format "meetdeck-startlist", version 1):

    {"format": "meetdeck-startlist", "version": 1,
     "source": "MeetDeck 2026.11.08.1",
     "meet": {"name": "...", "facility": "...", "date": "2026-11-14"},
     "teams": {"THS": "Tooele High School", ...},
     "events": [
       {"number": 1, "gender": "F", "distance": 200, "stroke": "E", "relay": true,
        "course": "Y", "age_min": 0, "age_max": 109, "name": "(optional override)",
        "heats": [
          {"heat": 1, "lanes": [
            {"lane": 4, "team": "THS", "letter": "A", "seed": 118.52,
             "legs": ["Smith", "Jones", "Lee", "Park"]},
            {"lane": 5, "team": "SHS", "first": "Ava", "last": "Cox", "age": 16, "sex": "F",
             "seed": 61.02}]}]}]}

gender: F girls, M boys, W women, X mixed (Meet Manager's letters).
stroke: A free, B back, C breast, D fly, E medley/IM (Meet Manager's letters).
seed: seconds, or null/0 for NT.
"""
from hytek_parser.hy3.enums import GenderAge

FORMAT = "meetdeck-startlist"

_STROKE = {"A": (1, "Freestyle"), "B": (2, "Backstroke"), "C": (3, "Breaststroke"),
           "D": (4, "Butterfly"), "E": (5, "Medley")}
_GENDER = {"F": (GenderAge.GIRL_S, "Girls", [2]), "M": (GenderAge.BOY_S, "Boys", [1]),
           "W": (GenderAge.WOMEN_S, "Women", [2]), "X": (GenderAge.UNKNOWN, "Mixed", [1, 2])}
_COURSE = {"Y": "Yard", "S": "Meter", "L": "Meter"}


class StartListError(ValueError):
    pass


def _s(v):
    return ("" if v is None else str(v)).strip()


def _int(v, what):
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise StartListError("%s is not a number: %r" % (what, v))
    return n


def event_name(ev):
    if _s(ev.get("name")):
        return _s(ev["name"])
    g = _GENDER.get(_s(ev.get("gender")).upper(), (None, "", []))[1]
    lo, hi = ev.get("age_min") or 0, ev.get("age_max") or 0
    if not lo and (not hi or hi >= 99):
        age = ""                                  # open, like Meet Manager's 0-109
    elif lo and hi and hi < 99:
        age = "%d-%d" % (lo, hi)
    elif lo:
        age = "%d & Over" % lo
    else:
        age = "%d & Under" % hi
    dist = str(ev.get("distance") or "")
    course = _COURSE.get(_s(ev.get("course")).upper() or "Y", "")
    stroke = _STROKE.get(_s(ev.get("stroke")).upper(), (None, ""))[1]
    parts = [g, age, dist, course, stroke, "Relay" if ev.get("relay") else ""]
    return " ".join(p for p in parts if p)


def relay_label(team_code, team_name, letter):
    """'Tooele A' - same short school name the .hy3 loader shows."""
    import hytek_event_loader as H
    name = _s(team_name)
    label = H._TEAM_SUFFIX.sub("", name).strip() if name else ""
    return ("%s %s" % (label or _s(team_code), _s(letter))).strip()


def age_code(lane, gender):
    age = lane.get("age")
    try:
        age = int(age or 0)
    except (TypeError, ValueError):
        age = 0
    sex = _s(lane.get("sex")).upper()[:1]
    if not age or sex not in ("F", "M"):
        return ""
    adult = gender in ("W",)          # Meet Manager's Women/Men events use W/M, girls/boys B/G
    letter = ("W" if sex == "F" else "M") if adult else ("G" if sex == "F" else "B")
    return "%d%s" % (age, letter)


def _seed(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def load_into(loader, doc):
    """Replace loader's schedule with the document. Returns (events, heats, lanes, relays_with_legs)."""
    if not isinstance(doc, dict):
        raise StartListError("expected a JSON object")
    if doc.get("format") not in (None, FORMAT):
        raise StartListError("not a MeetDeck start list (format %r)" % doc.get("format"))
    if _int(doc.get("version", 1), "version") > 1:
        raise StartListError("start list version %s is newer than this meet-board - update meet-board"
                             % doc.get("version"))
    events = doc.get("events")
    if not isinstance(events, list) or not events:
        raise StartListError("no events in the start list")

    teams = {_s(k): _s(v) for k, v in (doc.get("teams") or {}).items() if _s(k)}
    loader.clear()
    meet = doc.get("meet") or {}
    loader.meet_info = {"name": _s(meet.get("name")), "facility": _s(meet.get("facility")),
                        "date": _s(meet.get("date"))}
    loader.team_names = {k: v for k, v in teams.items() if v}

    n_heats = n_lanes = n_legs = 0
    for ev in events:
        num = _int(ev.get("number"), "event number")
        g = _s(ev.get("gender")).upper()
        ga, _, sex_codes = _GENDER.get(g, (GenderAge.UNKNOWN, "", []))
        relay = bool(ev.get("relay"))
        loader.event_names[num] = event_name(ev)
        loader.event_meta[num] = {
            "stroke_code": _STROKE.get(_s(ev.get("stroke")).upper(), (None,))[0],
            "distance": _int(ev.get("distance") or 0, "distance"),
            "relay": relay,
            "age_min": ev.get("age_min") or 0,
            "age_max": ev.get("age_max") or 109,
            "sex_codes": sex_codes,
            "is_mixed": len(sex_codes) > 1,
            "gender_age": ga,
        }
        for ht in ev.get("heats") or []:
            heat = _int(ht.get("heat"), "heat")
            key = (num, heat)
            lanes = [ln for ln in (ht.get("lanes") or []) if isinstance(ln, dict)]
            if not lanes:
                continue
            n_heats += 1
            for d in (loader.events, loader.teams, loader.age_codes, loader.seed_times):
                d.setdefault(key, {})
            for ln in lanes:
                lane = _int(ln.get("lane"), "lane")
                team = _s(ln.get("team"))
                if relay:
                    name = _s(ln.get("name")) or relay_label(team, teams.get(team), ln.get("letter"))
                    legs = [_s(x) for x in (ln.get("legs") or []) if _s(x)]
                    if legs:
                        loader.relay_legs.setdefault(key, {})[lane] = legs
                        n_legs += 1
                else:
                    name = _s(ln.get("name")) or ("%s %s" % (_s(ln.get("first")), _s(ln.get("last")))).strip()
                loader.events[key][lane] = name
                loader.teams[key][lane] = team
                loader.age_codes[key][lane] = "" if relay else age_code(ln, g)
                loader.seed_times[key][lane] = _seed(ln.get("seed"))
                loader.max_display_string_length = max(loader.max_display_string_length, len(name))
                n_lanes += 1

    import copy
    loader.events_uncombined = copy.deepcopy(loader.events)
    loader.teams_uncombined = copy.deepcopy(loader.teams)
    loader.age_codes_uncombined = copy.deepcopy(loader.age_codes)
    loader.seed_times_uncombined = copy.deepcopy(loader.seed_times)
    loader._compute_has_names()
    return len(loader.event_names), n_heats, n_lanes, n_legs
