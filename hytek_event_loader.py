import pickle
import re
import copy
import tempfile
import os

from hytek_parser.hy3_parser import parse_hy3
from hytek_parser.hy3.enums import Stroke, Gender, GenderAge, Course, ReplacedTimeTimeCode

# Monkey-patch e2_parser to handle empty/invalid date fields in .hy3 files
import hytek_parser.hy3.line_parsers.e_event_parsers as _e_parsers
from datetime import datetime as _datetime

import hytek_parser.hy3.line_parsers.f_relay_parsers as _f_parsers


def _date_tolerant(original, date_col):
    """Wrap an E2/F2 parser so a blank or bad date (Meet Manager leaves it
    empty in seeded Meet Entries exports) doesn't abort the whole file.
    date_col is the 1-based column extract() reads the 8-char date from."""
    placeholder = _datetime(1900, 1, 1).date()

    def parser(line, file, opts):
        try:
            return original(line, file, opts)
        except (ValueError, IndexError):
            padded = line.ljust(date_col + 8)
            patched = padded[:date_col - 1] + "01011900" + padded[date_col + 7:]
            result = original(patched, file, opts)
            _, event = result.meet.last_event
            entry = event.last_entry
            for prefix in ("prelim", "swimoff", "finals"):
                if getattr(entry, f"{prefix}_date", None) == placeholder:
                    setattr(entry, f"{prefix}_date", None)
            return result
    return parser


_patched_e2_parser = _date_tolerant(_e_parsers.e2_parser, 88)    # individual heat/lane
_patched_f2_parser = _date_tolerant(_f_parsers.f2_parser, 103)   # relay heat/lane
_e_parsers.e2_parser = _patched_e2_parser
_f_parsers.f2_parser = _patched_f2_parser
from hytek_parser.hy3 import HY3_LINE_PARSERS
HY3_LINE_PARSERS["E2"] = _patched_e2_parser
HY3_LINE_PARSERS["F2"] = _patched_f2_parser

# hytek_parser reads the relay letter (A/B/C) off the F1 line but doesn't keep
# it. Remember it per entry so relays show as "Stansbury A" instead of blank.
_RELAY_LETTERS = {}
_original_f1_parser = _f_parsers.f1_parser


def _patched_f1_parser(line, file, opts):
    result = _original_f1_parser(line, file, opts)
    try:
        _, event = result.meet.last_event
        _RELAY_LETTERS[id(event.last_entry)] = line[7:8].strip()
    except Exception:
        pass
    return result


_f_parsers.f1_parser = _patched_f1_parser
HY3_LINE_PARSERS["F1"] = _patched_f1_parser

_TEAM_SUFFIX = re.compile(r'\s+(high\s+school|high|hs)?\s*(swim(ming)?(\s+(and|&)\s+dive)?(\s+team)?)?\s*$', re.I)


def _team_label(team):
    """Short school name for relay rows: 'Stansbury', 'Emery', 'North Summit'."""
    if team is None:
        return ""
    short = (getattr(team, 'short_name', '') or '').strip()
    if short and short.upper() != (team.code or '').strip().upper() and not team.code.upper().startswith(short.upper()):
        return short
    name = (team.name or '').strip()
    return _TEAM_SUFFIX.sub('', name).strip() or name or team.code


STROKE_NAMES = {
    Stroke.FREESTYLE: "Freestyle",
    Stroke.BACKSTROKE: "Backstroke",
    Stroke.BREASTSTROKE: "Breaststroke",
    Stroke.BUTTERFLY: "Butterfly",
    Stroke.MEDLEY: "Medley",
    Stroke.UNKNOWN: "",
}

COURSE_NAMES = {
    Course.SCY: "Yard",
    Course.SCM: "Meter",
    Course.LCM: "Meter",
    Course.UNKNOWN: "",
}

GENDER_AGE_NAMES = {
    GenderAge.GIRL_S: "Girls",
    GenderAge.BOY_S: "Boys",
    GenderAge.WOMEN_S: "Women",
    GenderAge.MEN_S: "Men",
    GenderAge.UNKNOWN: "",
}


MALE_GENDERS = {GenderAge.BOY_S, GenderAge.MEN_S}
FEMALE_GENDERS = {GenderAge.GIRL_S, GenderAge.WOMEN_S}


def _build_event_name(event):
    gender = GENDER_AGE_NAMES.get(event.gender_age, "")
    if not gender and event.entries:
        genders = {s.gender for e in event.entries for s in e.swimmers}
        has_male = Gender.MALE in genders
        has_female = Gender.FEMALE in genders
        if has_male and has_female:
            gender = "Mixed"
    if not event.age_min and (event.age_max or 0) >= 99:
        age = ""                      # Meet Manager's "open" (0-109): "Women 50 Yard Freestyle"
    elif event.age_min and event.age_max:
        age = "%d-%d" % (event.age_min, event.age_max)
    elif event.age_min:
        age = "%d & Over" % event.age_min
    elif event.age_max:
        age = "%d & Under" % event.age_max
    else:
        age = "Open"

    distance = str(event.distance)
    course = COURSE_NAMES.get(event.course, "")
    stroke = STROKE_NAMES.get(event.stroke, "")
    relay = "Relay" if event.relay else ""

    parts = [p for p in [gender, age, distance, course, stroke, relay] if p]
    return " ".join(parts)


def _build_display_string(entry, teams=None):
    if entry.relay:
        code = entry.swimmers[0].team_code if entry.swimmers else ""
        label = _team_label((teams or {}).get(code)) or code
        letter = _RELAY_LETTERS.get(id(entry), "")
        return ("%s %s" % (label, letter)).strip()
    elif entry.swimmers:
        swimmer = entry.swimmers[0]
        return "%s %s" % (swimmer.first_name, swimmer.last_name)
    return ""


def _get_team_code(entry):
    if entry.swimmers:
        return entry.swimmers[0].team_code
    return ""


def _get_age_code(entry, gender_age):
    """Build abbreviated age+gender code like '8B', '12G'."""
    if entry.relay or not entry.swimmers:
        return ""
    swimmer = entry.swimmers[0]
    age = getattr(swimmer, 'age', None)
    if not age:                       # None, or 0 = no birth date on file
        return ""
    gender = swimmer.gender
    if gender_age in (GenderAge.MEN_S, GenderAge.WOMEN_S):
        letter = 'M' if gender == Gender.MALE else 'W' if gender == Gender.FEMALE else ''
    else:
        letter = 'B' if gender == Gender.MALE else 'G' if gender == Gender.FEMALE else ''
    return "%d%s" % (age, letter) if letter else ""


def _get_seed_time_seconds(entry):
    """Return seed time in seconds as a float, or None if unavailable/NT."""
    st = entry.seed_time
    if isinstance(st, ReplacedTimeTimeCode):
        return None
    if isinstance(st, (int, float)) and st > 0:
        return float(st)
    return None


class HytekEventLoader():
    max_display_string_length = 0

    def __init__(self, file_name=None):
        self.event_names = {}
        self.event_meta = {}  # event_number -> {stroke, distance, relay, age_min, age_max, genders}
        self.events = {}
        self.teams = {}
        self.age_codes = {}
        self.seed_times = {}
        self.events_uncombined = {}
        self.teams_uncombined = {}
        self.age_codes_uncombined = {}
        self.seed_times_uncombined = {}
        self.relay_legs = {}   # (event, heat) -> {lane: [last names of the 4 legs]}
        self.team_names = {}   # team code -> full school name from the .hy3 (none in .scb)
        self.combined = {}
        self.has_names = False
        if file_name:
            self.load(file_name)

    def _compute_has_names(self):
        for heat_lanes in self.events.values():
            for name in heat_lanes.values():
                if name and name.strip():
                    self.has_names = True
                    return
        for heat_lanes in self.teams.values():
            for code in heat_lanes.values():
                if code and code.strip():
                    self.has_names = True
                    return
        self.has_names = False

    def clear(self):
        self.event_names.clear()
        self.event_meta.clear()
        self.events.clear()
        self.teams.clear()
        self.age_codes.clear()
        self.seed_times.clear()
        self.relay_legs = {}
        self.team_names = {}
        self.events_uncombined = copy.deepcopy(self.events)
        self.teams_uncombined = copy.deepcopy(self.teams)
        self.age_codes_uncombined = copy.deepcopy(self.age_codes)
        self.seed_times_uncombined = copy.deepcopy(self.seed_times)
        self.combined.clear()
        self.max_display_string_length = 0
        self.has_names = False

    def load(self, file_name):
        self.clear()
        parsed = parse_hy3(file_name)
        self._load_from_parsed(parsed)

    def load_from_bytestream(self, stream):
        self.clear()
        with tempfile.NamedTemporaryFile(suffix='.hy3', delete=False) as tmp:
            tmp.write(stream.read())
            tmp_path = tmp.name
        try:
            parsed = parse_hy3(tmp_path)
            self._load_from_parsed(parsed)
        finally:
            os.unlink(tmp_path)

    def _load_from_parsed(self, parsed):
        meet = parsed.meet
        self.team_names = {}
        for code, team in (getattr(meet, 'teams', None) or {}).items():
            name = (getattr(team, 'name', '') or '').strip()
            if code and name:
                self.team_names[str(code).strip()] = name
        for event_num_str, event in meet.events.items():
            try:
                event_number = int(event_num_str)
            except ValueError:
                event_number = event_num_str

            self.event_names[event_number] = _build_event_name(event)

            # Determine swimmer genders for this event
            swimmer_genders = set()
            for entry in event.entries:
                for swimmer in entry.swimmers:
                    if swimmer.gender == Gender.MALE:
                        swimmer_genders.add('M')
                    elif swimmer.gender == Gender.FEMALE:
                        swimmer_genders.add('F')

            # Map hy3 stroke enum to st2 stroke code
            stroke_map = {
                Stroke.FREESTYLE: 1,
                Stroke.BACKSTROKE: 2,
                Stroke.BREASTSTROKE: 3,
                Stroke.BUTTERFLY: 4,
                Stroke.MEDLEY: 5,
            }
            stroke_code = stroke_map.get(event.stroke)

            # Map gender_age to st2 sex code
            if event.gender_age in MALE_GENDERS:
                sex_codes = [1]
            elif event.gender_age in FEMALE_GENDERS:
                sex_codes = [2]
            elif 'M' in swimmer_genders and 'F' in swimmer_genders:
                sex_codes = [1, 2]  # Mixed
            elif 'M' in swimmer_genders:
                sex_codes = [1]
            elif 'F' in swimmer_genders:
                sex_codes = [2]
            else:
                sex_codes = []

            self.event_meta[event_number] = {
                'stroke_code': stroke_code,
                'distance': event.distance,
                'relay': event.relay,
                'age_min': event.age_min,
                'age_max': event.age_max,
                'sex_codes': sex_codes,
                'is_mixed': len(sex_codes) > 1,
                'gender_age': event.gender_age,
            }

            for entry in event.entries:
                heat = entry.finals_heat
                lane = entry.finals_lane

                if heat is None or lane is None:
                    continue

                display_string = _build_display_string(entry, getattr(parsed.meet, 'teams', None))
                team_code = _get_team_code(entry)
                self.max_display_string_length = max(
                    self.max_display_string_length, len(display_string))

                if (event_number, heat) not in self.events:
                    self.events[(event_number, heat)] = {}
                    self.teams[(event_number, heat)] = {}
                    self.age_codes[(event_number, heat)] = {}
                    self.seed_times[(event_number, heat)] = {}

                self.events[(event_number, heat)][lane] = display_string
                self.teams[(event_number, heat)][lane] = team_code
                self.age_codes[(event_number, heat)][lane] = _get_age_code(entry, event.gender_age)
                self.seed_times[(event_number, heat)][lane] = _get_seed_time_seconds(entry)
                if entry.relay and entry.swimmers:
                    legs = [sw.last_name.strip() for sw in entry.swimmers if sw.last_name and sw.last_name.strip()]
                    if legs:
                        self.relay_legs.setdefault((event_number, heat), {})[lane] = legs

        self.events_uncombined = copy.deepcopy(self.events)
        self.teams_uncombined = copy.deepcopy(self.teams)
        self.age_codes_uncombined = copy.deepcopy(self.age_codes)
        self.seed_times_uncombined = copy.deepcopy(self.seed_times)
        self._compute_has_names()
        _RELAY_LETTERS.clear()

    def combine_events(self, combined=None):
        if combined is not None:
            self.combined = combined
        self.events = copy.deepcopy(self.events_uncombined)
        self.teams = copy.deepcopy(self.teams_uncombined)
        self.age_codes = copy.deepcopy(self.age_codes_uncombined)
        self.seed_times = copy.deepcopy(self.seed_times_uncombined)

        for combine_source, combine_destination in self.combined.items():
            if (combine_source != combine_destination):
                for lane in self.events[combine_source]:
                    self.events[combine_destination][lane] = self.events[combine_source][lane] + '*'
                    if combine_source in self.teams and lane in self.teams[combine_source]:
                        self.teams[combine_destination][lane] = self.teams[combine_source][lane]
                    if combine_source in self.age_codes and lane in self.age_codes[combine_source]:
                        self.age_codes[combine_destination][lane] = self.age_codes[combine_source][lane]
                    if combine_source in self.seed_times and lane in self.seed_times[combine_source]:
                        self.seed_times[combine_destination][lane] = self.seed_times[combine_source][lane]
                del self.events[combine_source]
                self.events[combine_source] = self.events[combine_destination]
                if combine_source in self.teams:
                    del self.teams[combine_source]
                    self.teams[combine_source] = self.teams[combine_destination]
                if combine_source in self.age_codes:
                    del self.age_codes[combine_source]
                    self.age_codes[combine_source] = self.age_codes[combine_destination]
                if combine_source in self.seed_times:
                    del self.seed_times[combine_source]
                    self.seed_times[combine_source] = self.seed_times[combine_destination]
        self._compute_has_names()

    def get_event_name(self, event_number):
        try:
            return self.event_names[event_number]
        except Exception:
            return ""

    def get_display_string(self, event_number, heat_number, lane):
        try:
            return self.events[(event_number, heat_number)][lane]
        except Exception:
            pass
        return ""

    def get_team_code(self, event_number, heat_number, lane):
        try:
            return self.teams[(event_number, heat_number)][lane]
        except Exception:
            pass
        return ""

    def get_age_code(self, event_number, heat_number, lane):
        try:
            return self.age_codes[(event_number, heat_number)][lane]
        except Exception:
            pass
        return ""

    def get_seed_time(self, event_number, heat_number, lane):
        """Return seed time in seconds as a float, or None if unavailable."""
        try:
            return self.seed_times[(event_number, heat_number)][lane]
        except Exception:
            return None

    def get_relay_legs(self, event_number, heat_number, lane):
        """Last names of a relay's swimmers in leg order, or [] (also for
        .scb start lists, which carry no leg names)."""
        key = (event_number, heat_number)
        legs = getattr(self, 'relay_legs', {}) or {}
        if lane in legs.get(key, {}):
            return list(legs[key][lane])
        for src, dst in (self.combined or {}).items():
            if dst == key and lane in legs.get(src, {}):
                return list(legs[src][lane])
        return []

    def get_display_string_uncombined(self, event_number, heat_number, lane):
        try:
            return self.events_uncombined[(event_number, heat_number)][lane]
        except Exception:
            pass
        return ""

    def to_object(self):
        return pickle.dumps({
            "event_names": self.event_names,
            "event_meta": self.event_meta,
            "events": self.events,
            "events_uncombined": self.events_uncombined,
            "teams": self.teams,
            "teams_uncombined": self.teams_uncombined,
            "age_codes": self.age_codes,
            "age_codes_uncombined": self.age_codes_uncombined,
            "seed_times": self.seed_times,
            "seed_times_uncombined": self.seed_times_uncombined,
            "relay_legs": getattr(self, 'relay_legs', {}),
            "team_names": getattr(self, 'team_names', {}),
            "combined": self.combined,
        }, protocol=0).decode('utf8')

    def from_object(self, p):
        o = pickle.loads(p.encode('utf8'))
        self.event_names = o['event_names']
        self.event_meta = o.get('event_meta', {})
        self.events = o['events']
        self.events_uncombined = o['events_uncombined']
        self.teams = o.get('teams', {})
        self.teams_uncombined = o.get('teams_uncombined', {})
        self.age_codes = o.get('age_codes', {})
        self.age_codes_uncombined = o.get('age_codes_uncombined', {})
        self.seed_times = o.get('seed_times', {})
        self.seed_times_uncombined = o.get('seed_times_uncombined', {})
        self.relay_legs = o.get('relay_legs', {})
        self.team_names = o.get('team_names', {})
        self.combined = o['combined']
        self._compute_has_names()


if __name__ == "__main__":
    import sys
    event_info = HytekEventLoader(sys.argv[1])

    event_heat = list(event_info.events.keys())
    event_heat.sort()
    for event, heat in event_heat:
        print("Event", event, " Heat", heat, end=" ")
        if event in event_info.event_names:
            print(event_info.event_names[event])
        else:
            print("")

        lanes = list(event_info.events[(event, heat)].keys())
        lanes.sort()
        for lane in lanes:
            print("\t", lane, "\t", event_info.events[(event, heat)][lane])
