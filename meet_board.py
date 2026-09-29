"""Server-side helpers for the spectator meet board (/web/meetboard).

The board has two panels: the heat in the water (live CTS data) and the heat
that swims next (from the loaded start lists alone, so it keeps working if the
console link drops).
"""

# Region 11 (UHSAA 2025-27) plus Grantsville. Board tints, not raw school
# colors: each is lifted to clear 3:1 against the #0b1618 board background.
# Every hex is an approximation - override them on the Team Colors page.
DEFAULT_TEAM_COLORS = {
    'TOOEL': {'name': 'Tooele',         'color': '#D18AE0', 'alt': '#E8EEEE'},
    'STAN':  {'name': 'Stansbury',      'color': '#3D8BF5', 'alt': '#9AA6AB'},
    'GHS':   {'name': 'Grantsville',    'color': '#F0546A', 'alt': '#E8EEEE'},
    'DPEAK': {'name': 'Deseret Peak',   'color': '#D9C46E', 'alt': '#BFC7CA'},
    'BRHS':  {'name': 'Bear River',     'color': '#E8EEEE', 'alt': '#9AA6AB'},
    'SVHS':  {'name': 'Sky View',       'color': '#3E9BD6', 'alt': '#FFD24D'},
    'MCHS':  {'name': 'Mountain Crest', 'color': '#FF8C42', 'alt': '#5B8FE8'},
    'RIDGE': {'name': 'Ridgeline',      'color': '#7ACC3E', 'alt': '#A5ACAF'},
    'GCHS':  {'name': 'Green Canyon',   'color': '#3FB58A', 'alt': '#BFC7CA'},
}

# Board-safe fallback colors for teams nobody has configured yet, handed out
# in order of first appearance so a meet's colors are stable.
FALLBACK_PALETTE = [
    '#F2A33A', '#5FD3C6', '#E57AB8', '#9FD356', '#8FA8FF', '#FF7F6B',
    '#C9B3FF', '#4FC3F7', '#FFE066', '#B0E0A8', '#FFB3C7', '#A0C4D8',
]


def team_colors(settings, schedule_teams=()):
    """Merge defaults, saved overrides and auto-assigned colors for any team
    code in the schedule. Returns {code: {name, color, alt, auto}}."""
    merged = {k: dict(v, auto=False) for k, v in DEFAULT_TEAM_COLORS.items()}
    for code, v in (settings.get('team_colors') or {}).items():
        base = merged.get(code, {'name': '', 'color': '', 'alt': ''})
        merged[code] = {
            'name': v.get('name', base['name']),
            'color': v.get('color') or base['color'],
            'alt': v.get('alt') or base.get('alt', ''),
            'auto': False,
        }
    used = {v['color'].upper() for v in merged.values() if v.get('color')}
    palette = [c for c in FALLBACK_PALETTE if c.upper() not in used]
    i = 0
    for code in schedule_teams:
        if not code or code in merged:
            continue
        color = palette[i % len(palette)] if palette else FALLBACK_PALETTE[i % len(FALLBACK_PALETTE)]
        merged[code] = {'name': '', 'color': color, 'alt': '', 'auto': True}
        i += 1
    return merged


def schedule_teams(event_info):
    """Team codes in order of first appearance in the schedule."""
    seen = []
    for key in sorted(event_info.teams_uncombined or event_info.teams):
        for lane in sorted(event_info.teams.get(key, {})):
            code = (event_info.teams[key][lane] or '').strip()
            if code and code not in seen:
                seen.append(code)
    return seen


def heat_order(event_info):
    """Every (event, heat) that actually swims, in order. Heats merged into
    another by Combine Events are skipped - they swim as the destination."""
    combined_sources = {s for s, d in event_info.combined.items() if s != d}
    return [k for k in sorted(event_info.events) if k not in combined_sources]


def next_heats(event_info, current, count=2):
    """The `count` heats after `current`. If `current` isn't in the schedule
    (console on an event we don't have), start from the next one after it."""
    order = heat_order(event_info)
    if not order:
        return []
    if current in order:
        i = order.index(current) + 1
    else:
        # Combined source -> treat as its destination
        dest = event_info.combined.get(current)
        if dest in order:
            i = order.index(dest) + 1
        else:
            i = next((n for n, k in enumerate(order) if k > current), len(order))
    return order[i:i + count]


def heat_count(event_info, event_number):
    return len([k for k in heat_order(event_info) if k[0] == event_number])


def lanes_for(event_info, key, num_lanes):
    e, h = key
    out = []
    # Never hide a swimmer: if the start list uses lanes past the configured
    # pool width (e.g. num_lanes left at 6 in an 8-lane pool), show them.
    occupied = [l for l, n in event_info.events.get(key, {}).items() if n or event_info.get_team_code(e, h, l)]
    last = max([max(num_lanes, 1)] + occupied)
    for lane in range(1, last + 1):
        seed = event_info.get_seed_time(e, h, lane)
        out.append({
            'lane': lane,
            'name': event_info.get_display_string(e, h, lane),
            'team': event_info.get_team_code(e, h, lane),
            'seed': seed if seed is not None else '',
        })
    return out


def board_payload(event_info, settings, current):
    """Extra fields merged into the update_scoreboard message."""
    num_lanes = int(settings.get('num_lanes', 6) or 6)
    upcoming = next_heats(event_info, current, 2)
    payload = {
        'heat_count': heat_count(event_info, current[0]),
        'current_in_schedule': current in event_info.events,
        'next_event': '', 'next_heat': '', 'next_event_name': '',
        'next_heat_count': 0, 'next_lanes': [],
        'then_event': '', 'then_heat': '', 'then_event_name': '',
    }
    if upcoming:
        e, h = upcoming[0]
        payload.update({
            'next_event': e, 'next_heat': h,
            'next_event_name': event_info.get_event_name(e),
            'next_heat_count': heat_count(event_info, e),
            'next_lanes': lanes_for(event_info, upcoming[0], num_lanes),
        })
    if len(upcoming) > 1:
        e, h = upcoming[1]
        payload.update({'then_event': e, 'then_heat': h,
                        'then_event_name': event_info.get_event_name(e)})
    return payload
