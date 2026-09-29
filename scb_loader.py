"""Loader for Hy-Tek Meet Manager "Start Lists for CTS" exports (.scb).

Meet Manager: File > Export > Start Lists for Scoreboard > Start Lists for CTS
writes one file per event (E001.scb, E002.scb, ...). Format:

    #3 WOMEN 200 FREE                     <- line 1: #<event number> <event name>
    UNDERWOOD, ELLA     --GHS             <- then 10 lines per heat, lanes 1..10
                        --                <- empty lane

Name is a fixed 20-char field (truncated by Meet Manager), then "--", then the
team abbreviation. There are no seed times, ages or explicit heat numbers:
heat N is lines (N-1)*10+2 .. N*10+1. Heats with no swimmers are dropped.

Relays put the relay name ("TOOELE  A") in the name field, with no comma.

The parsed data is loaded into a HytekEventLoader so the rest of the app
(scoreboard, overlays, meet board, combine events) works the same as .hy3.
"""
import copy
import io
import os
import re
import zipfile

from hytek_parser.hy3.enums import GenderAge

LANES_PER_HEAT = 10
NAME_WIDTH = 20

_STROKES = [
    # (tokens, display name, st2 stroke code)
    (('FREE', 'FREESTYLE', 'FR'), 'Freestyle', 1),
    (('BACK', 'BACKSTROKE', 'BK'), 'Backstroke', 2),
    (('BREAST', 'BREASTSTROKE', 'BR'), 'Breaststroke', 3),
    (('FLY', 'BUTTERFLY', 'FL'), 'Butterfly', 4),
    (('IM', 'I.M.', 'MEDLEY'), None, 5),  # display resolved below
]

_GENDERS = {
    'WOMEN': (GenderAge.WOMEN_S, [2], 'Women'),
    "WOMEN'S": (GenderAge.WOMEN_S, [2], 'Women'),
    'GIRLS': (GenderAge.GIRL_S, [2], 'Girls'),
    'GIRL': (GenderAge.GIRL_S, [2], 'Girls'),
    'MEN': (GenderAge.MEN_S, [1], 'Men'),
    "MEN'S": (GenderAge.MEN_S, [1], 'Men'),
    'BOYS': (GenderAge.BOY_S, [1], 'Boys'),
    'BOY': (GenderAge.BOY_S, [1], 'Boys'),
    'MIXED': (GenderAge.UNKNOWN, [1, 2], 'Mixed'),
    'COED': (GenderAge.UNKNOWN, [1, 2], 'Mixed'),
}

def _title_word(w):
    """Title-case one name token, keeping hyphen/apostrophe parts and Mc."""
    def cap(part):
        if not part:
            return part
        p = part[0].upper() + part[1:].lower()
        if len(p) > 2 and p.startswith('Mc'):
            p = 'Mc' + p[2].upper() + p[3:]
        return p
    w = '-'.join(cap(x) for x in w.split('-'))
    w = "'".join(x[:1].upper() + x[1:] for x in w.split("'"))
    return w


def title_name(s):
    return ' '.join(_title_word(w) for w in s.split())


def format_swimmer_name(raw, style='first_last'):
    """'UNDERWOOD, ELLA' -> 'Ella Underwood' (style first_last),
    'Underwood, Ella' (last_first) or unchanged (raw).
    Names without a comma (relays) are only title-cased."""
    raw = raw.strip()
    if not raw or style == 'raw':
        return raw
    if ',' not in raw:
        # Relay names: keep the relay letter uppercase ("Tooele A")
        return title_name(raw)
    last, first = [p.strip() for p in raw.split(',', 1)]
    last, first = title_name(last), title_name(first)
    if style == 'last_first':
        return '%s, %s' % (last, first) if first else last
    return ('%s %s' % (first, last)).strip()


def parse_event_name(raw):
    """Turn 'WOMEN 200 FREE' into a display name plus st2-style metadata."""
    tokens = raw.upper().replace('-', ' - ').split()
    meta = {
        'stroke_code': None, 'distance': None, 'relay': False,
        'age_min': None, 'age_max': None, 'sex_codes': [],
        'is_mixed': False, 'gender_age': GenderAge.UNKNOWN,
    }
    if 'RELAY' in tokens or 'RELAYS' in tokens:
        meta['relay'] = True
    for t in tokens:
        if t in _GENDERS:
            ga, codes, _ = _GENDERS[t]
            meta['gender_age'], meta['sex_codes'] = ga, codes
            meta['is_mixed'] = len(codes) > 1
            break
    # Age group: "13-14", "10 & UNDER", "15 & OVER", "8&U", "11 12"
    joined = ' '.join(tokens)
    m = re.search(r'\b(\d{1,2})\s*-\s*(\d{1,2})\b', joined)
    if m and int(m.group(1)) < int(m.group(2)) <= 99:
        meta['age_min'], meta['age_max'] = int(m.group(1)), int(m.group(2))
        joined = joined[:m.start()] + joined[m.end():]
    else:
        m = re.search(r'\b(\d{1,2})\s*&\s*(U|UN|UNDER)\b', joined)
        if m:
            meta['age_max'] = int(m.group(1))
            joined = joined[:m.start()] + joined[m.end():]
        else:
            m = re.search(r'\b(\d{1,2})\s*&\s*(O|OV|OVER)\b', joined)
            if m:
                meta['age_min'] = int(m.group(1))
                joined = joined[:m.start()] + joined[m.end():]
    # Distance: first number >= 25 that's left
    for n in re.findall(r'\b(\d{2,4})\b', joined):
        if int(n) >= 25:
            meta['distance'] = int(n)
            break
    for toks, _, code in _STROKES:
        if any(t in tokens for t in toks):
            meta['stroke_code'] = code
            break

    # Pretty name: title-case, expand stroke abbreviations
    expand = {'FREE': 'Freestyle', 'FR': 'Freestyle', 'BACK': 'Backstroke', 'BK': 'Backstroke',
              'BREAST': 'Breaststroke', 'BR': 'Breaststroke', 'FLY': 'Butterfly', 'FL': 'Butterfly',
              'IM': 'IM', 'I.M.': 'IM', 'MTR': 'Meter', 'YD': 'Yard', 'YDS': 'Yard',
              'U': 'Under', 'O': 'Over', '&': '&'}
    out = []
    for w in raw.split():
        up = w.upper()
        if up in expand:
            out.append(expand[up])
        elif up in _GENDERS:
            out.append(_GENDERS[up][2])
        elif re.match(r'^\d', w):
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:].lower())
    return ' '.join(out), meta


def parse_scb(text, filename=None):
    """Parse one .scb file. Returns dict:
    {event_number, event_name, meta, heats: {heat: {lane: (raw_name, team)}}}
    or raises ValueError."""
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines or not lines[0].lstrip().startswith('#'):
        raise ValueError('%s: first line should look like "#3 WOMEN 200 FREE"' % (filename or 'file'))
    header = lines[0].strip()[1:]
    # "#3 WOMEN 200 FREE" (a letter suffix like "#3A" is tolerated and dropped)
    m = re.match(r'\s*(\d+)[A-Za-z]?(?:\s+(.*))?$', header)
    event_number = None
    name = header.strip()
    if m:
        event_number = int(m.group(1))
        name = (m.group(2) or '').strip()
    if event_number is None and filename:
        fm = re.search(r'(\d+)', os.path.basename(filename))
        if fm:
            event_number = int(fm.group(1))
    if event_number is None:
        raise ValueError('%s: no event number' % (filename or 'file'))

    body = lines[1:]
    # Drop trailing blank lines (not lane lines, which contain "--")
    while body and not body[-1].strip():
        body.pop()

    heats = {}
    for idx, line in enumerate(body):
        heat = idx // LANES_PER_HEAT + 1
        lane = idx % LANES_PER_HEAT + 1
        if '--' in line:
            name_part, team = line.split('--', 1)
        else:
            name_part, team = line[:NAME_WIDTH], line[NAME_WIDTH:]
        swimmer = name_part.strip()
        team = team.strip()
        if not swimmer and not team:
            continue
        heats.setdefault(heat, {})[lane] = (swimmer, team)

    display_name, meta = parse_event_name(name)
    return {
        'event_number': event_number,
        'event_name': display_name,
        'raw_event_name': name,
        'meta': meta,
        'heats': heats,
    }


def _decode(data):
    if isinstance(data, str):
        return data
    for enc in ('utf-8', 'cp1252', 'latin-1'):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode('latin-1', errors='replace')


def expand_uploads(files):
    """files: iterable of (filename, bytes). Zip files are expanded.
    Returns list of (filename, text) for every .scb found."""
    out = []
    for fname, data in files:
        low = fname.lower()
        if low.endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    if info.filename.lower().endswith('.scb') and not info.is_dir():
                        out.append((os.path.basename(info.filename), _decode(z.read(info))))
        elif low.endswith('.scb'):
            out.append((os.path.basename(fname), _decode(data)))
    return out


def read_scb_folder(folder):
    """Read every .scb in a folder. Returns list of (filename, text)."""
    out = []
    for name in sorted(os.listdir(folder)):
        if name.lower().endswith('.scb'):
            with open(os.path.join(folder, name), 'rb') as f:
                out.append((name, _decode(f.read())))
    return out


def folder_signature(folder):
    """Cheap change detector for a watch folder: (name, mtime, size) of each .scb."""
    sig = []
    try:
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith('.scb'):
                st = os.stat(os.path.join(folder, name))
                sig.append((name, st.st_mtime_ns, st.st_size))
    except OSError:
        return None
    return tuple(sig)


def load_scb_into(loader, scb_texts, name_style='first_last', keep_combined=True):
    """Replace the contents of a HytekEventLoader with parsed .scb files.

    scb_texts: list of (filename, text). Raises ValueError if nothing parsed.
    Returns (event_count, heat_count, errors) where errors lists files skipped.
    Existing combine-events choices are re-applied where the heats still exist.
    """
    parsed, errors = [], []
    for fname, text in scb_texts:
        try:
            parsed.append(parse_scb(text, fname))
        except ValueError as e:
            errors.append(str(e))
    if not parsed:
        raise ValueError('No .scb start lists found' + (': ' + '; '.join(errors) if errors else ''))

    previous_combined = dict(loader.combined) if keep_combined else {}
    loader.clear()

    heat_count = 0
    for ev in parsed:
        n = ev['event_number']
        loader.event_names[n] = ev['event_name']
        loader.event_meta[n] = ev['meta']
        for heat, lanes in ev['heats'].items():
            key = (n, heat)
            loader.events[key] = {}
            loader.teams[key] = {}
            loader.age_codes[key] = {}
            loader.seed_times[key] = {}
            heat_count += 1
            for lane, (raw, team) in lanes.items():
                display = format_swimmer_name(raw, name_style)
                loader.events[key][lane] = display
                loader.teams[key][lane] = team
                loader.age_codes[key][lane] = ''
                loader.seed_times[key][lane] = None
                loader.max_display_string_length = max(loader.max_display_string_length, len(display))

    loader.events_uncombined = copy.deepcopy(loader.events)
    loader.teams_uncombined = copy.deepcopy(loader.teams)
    loader.age_codes_uncombined = copy.deepcopy(loader.age_codes)
    loader.seed_times_uncombined = copy.deepcopy(loader.seed_times)
    loader._compute_has_names()

    still_valid = {s: d for s, d in previous_combined.items()
                   if s in loader.events_uncombined and d in loader.events_uncombined}
    if still_valid:
        loader.combine_events(still_valid)
    return len(parsed), heat_count, errors


if __name__ == '__main__':
    import sys
    from hytek_event_loader import HytekEventLoader
    paths = sys.argv[1:]
    texts = []
    for p in paths:
        if os.path.isdir(p):
            texts.extend(read_scb_folder(p))
        else:
            with open(p, 'rb') as f:
                texts.extend(expand_uploads([(p, f.read())]))
    loader = HytekEventLoader()
    print(load_scb_into(loader, texts))
    for (e, h) in sorted(loader.events):
        print('Event %d Heat %d  %s' % (e, h, loader.event_names[e]))
        for lane in sorted(loader.events[(e, h)]):
            print('   %2d  %-24s %s' % (lane, loader.events[(e, h)][lane], loader.teams[(e, h)][lane]))
