"""Heat announcements from short recorded clips.

Each announcement is stitched together from a small, reusable set of clips
(recorded once in the announcer's voice) so no new audio has to be made per
meet:

    new event:   "Event five." + "Women's 200 freestyle." + "Heat one." + "Please report to the blocks."
    next heat:   "Heat two." + "Please report to the blocks."

Clip names (files <name>.mp3 in the announce folder):
    event_<n>                      "Event <n>."                      n = 1..40
    ev_<g>_<dist>_<stroke>_<I|R>   one phrase for a standard HS event, e.g. ev_F_200_1_I "Women's 200 freestyle."
    g_<g>                          "Women's" / "Men's" / "Mixed"      (fallback pieces for any other event)
    d_<dist>                       "200"
    s_<stroke>[_R]                 "freestyle" / ... / "medley relay"
    heat_<n>                       "Heat <n>."                       n = 1..15
    report                         "Please report to the blocks."
g: F women, M men, X mixed. stroke: 1 free 2 back 3 breast 4 fly 5 IM/medley (Hy-Tek numbers).
A clip that is missing is spoken by the speaker PC's built-in voice instead.
"""
import os
import re

ONES = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve',
        'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen']
TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety']

GENDER_WORD = {'F': "Women's", 'M': "Men's", 'X': 'Mixed'}
STROKE_WORD = {1: 'freestyle', 2: 'backstroke', 3: 'breaststroke', 4: 'butterfly', 5: 'individual medley'}
RELAY_WORD = {1: 'freestyle relay', 5: 'medley relay'}
DISTANCES = [25, 50, 100, 200, 400, 500, 800, 1000, 1650]
MAX_EVENT, MAX_HEAT = 40, 15

# the 11 UHSAA high school events, both genders
HS_EVENTS = [(200, 5, True), (200, 1, False), (200, 5, False), (50, 1, False), (100, 4, False), (100, 1, False),
             (500, 1, False), (200, 1, True), (100, 2, False), (100, 3, False), (400, 1, True)]


def words(n):
    n = int(n)
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ('-' + ONES[n % 10] if n % 10 else '')
    return str(n)


def dist_words(d):
    return {25: 'twenty-five', 50: 'fifty', 100: 'one hundred', 200: 'two hundred', 400: 'four hundred',
            500: 'five hundred', 800: 'eight hundred', 1000: 'one thousand', 1650: 'sixteen fifty'}.get(int(d), str(d))


def stroke_phrase(stroke, relay):
    if relay:
        return RELAY_WORD.get(stroke, STROKE_WORD.get(stroke, '') + ' relay')
    return STROKE_WORD.get(stroke, '')


def gender_of(meta):
    sc = sorted((meta or {}).get('sex_codes') or [])
    if sc == [2]:
        return 'F'
    if sc == [1]:
        return 'M'
    return 'X'


def event_key(meta):
    return 'ev_%s_%d_%d_%s' % (gender_of(meta), int(meta.get('distance') or 0), int(meta.get('stroke_code') or 0),
                               'R' if meta.get('relay') else 'I')


def event_label(meta):
    """'Women's 200 freestyle relay' (digits, for the screen)."""
    g = GENDER_WORD[gender_of(meta)]
    return '%s %s %s' % (g, meta.get('distance') or '', stroke_phrase(meta.get('stroke_code'), meta.get('relay')))


# ---------------------------------------------------------------- the clip set (what gets recorded)
# one-off announcements, each a button on the music page: (clip, button label, text)
EXTRAS = [
    ('x_anthem', 'National anthem', 'Please stand for the national anthem.'),
    ('x_meeting', 'Coaches & captains', 'Coaches and captains meeting under the scoreboard.'),
    ('x_welcome', 'Welcome', 'Welcome to our meet.'),
]


def clip_script():
    """[(clip name, text to record)] - every clip the announcements can use."""
    out = [('report', 'Please report to the blocks.')]
    out += [('event_%d' % n, 'Event %s.' % words(n)) for n in range(1, MAX_EVENT + 1)]
    out += [('heat_%d' % n, 'Heat %s.' % words(n)) for n in range(1, MAX_HEAT + 1)]
    for g in ('F', 'M'):
        for dist, stroke, relay in HS_EVENTS:
            meta = {'sex_codes': [2] if g == 'F' else [1], 'distance': dist, 'stroke_code': stroke, 'relay': relay}
            out.append((event_key(meta), '%s %s %s.' % (GENDER_WORD[g], dist_words(dist), stroke_phrase(stroke, relay))))
    out += [('g_' + g, w) for g, w in GENDER_WORD.items()]
    out += [('d_%d' % d, dist_words(d)) for d in DISTANCES]
    out += [('s_%d' % s, w) for s, w in STROKE_WORD.items()]
    out += [('s_%d_R' % s, w) for s, w in RELAY_WORD.items()]
    out += [(k, text) for k, _label, text in EXTRAS]
    return out


# ---------------------------------------------------------------- one announcement
def build(event_number, heat, heat_count, meta, new_event=True):
    """The pieces of one announcement: [{'clip': name, 'say': text}] in order, plus the full text."""
    parts = []
    if new_event:
        parts.append(('event_%d' % int(event_number), 'Event %s.' % words(event_number)))
        if meta:
            k = event_key(meta)
            parts.append((k, event_label(meta) + '.'))
    if heat_count != 1 or not new_event:
        parts.append(('heat_%d' % int(heat), 'Heat %s.' % words(heat)))
    parts.append(('report', 'Please report to the blocks.'))
    return [{'clip': c, 'say': s} for c, s in parts]


def resolve(pieces, have, meta=None):
    """Swap a missing standard-event clip for gender + distance + stroke pieces when those exist."""
    out = []
    for p in pieces:
        c = p['clip']
        if c in have or not c.startswith('ev_') or not meta:
            out.append(p)
            continue
        g, d, s, r = c.split('_')[1:]
        sub = [('g_' + g, GENDER_WORD.get(g, '')), ('d_' + d, d), ('s_%s%s' % (s, '_R' if r == 'R' else ''),
                                                                  stroke_phrase(int(s), r == 'R'))]
        if all(x in have for x, _ in sub):
            out += [{'clip': x, 'say': w} for x, w in sub]
        else:
            out.append(p)
    return out


def text_of(pieces):
    return ' '.join(p['say'] for p in pieces)


# ---------------------------------------------------------------- which heat is next
def pick_target(order, current, racing, swum, announced, override=None):
    """The heat to announce: the override if set, else the first heat from the one on the
    board onward that hasn't swum, isn't in the water, and hasn't been announced."""
    if override in order:
        return override
    if not order:
        return None
    if current in order:
        i = order.index(current)
    else:
        i = next((n for n, k in enumerate(order) if k > current), len(order)) if current else 0
    for k in order[i:]:
        if k in swum or k in announced or (racing and k == current):
            continue
        return k
    return None


def clip_folder(data_path):
    d = data_path('announce')
    os.makedirs(d, exist_ok=True)
    return d


def clips_on_disk(folder):
    try:
        return {f[:-4] for f in os.listdir(folder) if f.lower().endswith('.mp3')}
    except OSError:
        return set()


SAFE = re.compile(r'^[A-Za-z0-9_]{1,40}$')
