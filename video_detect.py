"""Find each lane's finish touch in a heat clip - an unofficial reference.

How it works (same rule that measured ~0.15-0.25 s on the Nov 20 meet):
- Each lane gets a small zone at the finish wall, from the marked lane lines (geometry) toward the turn wall.
- Motion in the zone = how much the picture changes over 6 frames (0.2 s).
- Each lane has reference times (the console's time for that lane, or pad / button / watches for an imported
  clip). Around each reference, the touch is the first moment the motion climbs halfway from its quiet level
  to its peak.
- The clip's clock (where the race started) is lined up from all the lanes together (median), so one lane with
  a wrong reference time can't pull it off.
Needs ffmpeg and numpy.
"""
import json, subprocess
import numpy as np

AW, AH = 960, 540            # analysed at this size
LAG = 6


def _quad_mask(quad, w=AW, h=AH):
    """Boolean mask of a convex quadrilateral (4 x 2 pixel points, either winding)."""
    yy, xx = np.mgrid[0:h, 0:w]
    q = np.asarray(quad, float)
    pos = np.ones((h, w), bool)
    neg = np.ones((h, w), bool)
    for i in range(4):
        (x1, y1), (x2, y2) = q[i], q[(i + 1) % 4]
        cross = (x2 - x1) * (yy - y1) - (y2 - y1) * (xx - x1)
        pos &= cross >= 0
        neg &= cross <= 0
    return pos | neg


def lane_zones(geom, AW=AW, AH=AH):
    """{lane: (near_mask, wall_mask, quad_pixels)} from the marked geometry (fractions of the frame), for a
    picture AW x AH. finish[i] / turn[i]: lane line i (0 = lane-1 edge ... 8 = lane-8 edge) at the walls."""
    fin = np.array(geom['finish'], float) * [AW, AH]
    trn = np.array(geom['turn'], float) * [AW, AH]
    zones = {}
    for lane in range(1, 9):
        a, b = fin[lane - 1], fin[lane]
        da, db = trn[lane - 1] - a, trn[lane] - b
        da, db = da / (np.linalg.norm(da) or 1), db / (np.linalg.norm(db) or 1)
        hgt = np.linalg.norm(b - a)
        d = max(24.0, 2.2 * hgt)
        a2, b2 = a + (b - a) * 0.12, b - (b - a) * 0.12
        near = [a2, a2 + da * d * 0.35, b2 + db * d * 0.35, b2]
        wall = [a2 - da * 2, a2 + da * d * 0.15, b2 + db * d * 0.15, b2 - db * 2]
        full = [a2, a2 + da * d, b2 + db * d, b2]
        zones[lane] = (_quad_mask(near, AW, AH), _quad_mask(wall, AW, AH), [[round(float(x) / AW, 4), round(float(y) / AH, 4)] for x, y in full])
    return zones


def motion(ffmpeg, path, zones, fps_out=30):
    """Per-frame motion per lane: {lane: array}, and the frame rate."""
    p = subprocess.Popen([ffmpeg, '-v', 'error', '-i', path, '-an', '-vf',
                          'fps=%d,scale=%d:%d,format=gray' % (fps_out, AW, AH), '-f', 'rawvideo', '-'],
                         stdout=subprocess.PIPE, creationflags=0x08000000 if __import__('os').name == 'nt' else 0)
    n = AW * AH
    hist, out = [], {lane: [] for lane in zones}
    while True:
        buf = p.stdout.read(n)
        if len(buf) < n:
            break
        f = np.frombuffer(buf, np.uint8).reshape(AH, AW).astype(np.int16)
        hist.append(f)
        if len(hist) > LAG:
            d = np.abs(f - hist.pop(0))
            for lane, (mn, mw, _) in zones.items():
                out[lane].append(float(d[mn].mean()) + float(d[mw].mean()))
        else:
            for lane in zones:
                out[lane].append(0.0)
    p.wait()
    return {lane: np.array(v) for lane, v in out.items()}, float(fps_out)


def _onset(x, t, lo, hi, qlo, qhi, frac=0.5):
    q, w = (t > qlo) & (t < qhi), (t > lo) & (t < hi)
    if q.sum() < 10 or w.sum() < 5:
        return None
    base, peak = float(np.median(x[q])), float(x[w].max())
    if peak - base < 3:
        return None
    i = np.where(w & (x > base + frac * (peak - base)))[0]
    return float(t[i[0]]) if len(i) else None


def strobe_flash(ffmpeg, path, box, guess, span=12.0, fps=30.0, min_jump=25.0):
    """The starter's strobe: the clip time of the biggest jump in brightness inside `box` (fractions
    [x0, y0, x1, y1]) within `span` s of the guess. Returns (time, jump) - time is None when no flash shows."""
    x0, y0, x1, y1 = [float(v) for v in box]
    t0 = max(0.0, guess - span)
    vf = 'crop=w=iw*%.5f:h=ih*%.5f:x=iw*%.5f:y=ih*%.5f,scale=24:24,fps=%d,format=gray' % (x1 - x0, y1 - y0, x0, y0, fps)
    p = subprocess.run([ffmpeg, '-v', 'error', '-ss', '%.2f' % t0, '-i', path, '-t', '%.2f' % (2 * span), '-an', '-vf', vf,
                        '-f', 'rawvideo', '-'], capture_output=True,
                       creationflags=0x08000000 if __import__('os').name == 'nt' else 0)
    a = np.frombuffer(p.stdout, np.uint8)
    n = len(a) // 576
    if n < 3:
        return None, 0.0
    b = a[:n * 576].reshape(n, 576).astype(float).mean(axis=1)
    jump = np.diff(b, prepend=b[0])
    i = int(np.argmax(jump))
    if jump[i] < min_jump:
        return None, float(jump[i])
    return round(t0 + i / fps, 3), float(jump[i])


def detect(ffmpeg, path, geom, refs, start_guess, start_fixed=None):
    """refs: {lane: {'times': {name: seconds}, ...}}; start_guess: clip time (s) where the race started, roughly.
    Returns {'start_in_clip', 'lanes': {lane: {...}}, 'zones': {lane: quad}}."""
    zones = lane_zones(geom)
    sig, fps = motion(ffmpeg, path, zones)
    n = len(next(iter(sig.values()))) if sig else 0
    t = np.arange(n) / fps
    TOL = 0.30
    lanes = {}
    for lane, r in refs.items():
        lane = int(lane)
        ts = {k: float(v) for k, v in (r.get('times') or {}).items() if v}
        if lane not in sig or not ts:
            continue
        vals = sorted(ts.values())
        lanes[lane] = {'times': ts, 'agree': len(vals) >= 2 and vals[-1] - vals[0] <= TOL,
                       'ref': float(np.median(vals)), 'name': r.get('name', '')}
    # line the clip up: for lanes whose times agree, find the touch near (start guess + ref) and take the median
    # of (touch - ref). The guess should be within a second or two; if too few lanes turn up, nearby starts are tried.
    best = None
    src = 'lanes'
    if start_fixed is not None:
        best, src = (None, float(start_fixed)), 'set by hand'
    elif (geom.get('strobe') or {}).get('box'):
        ft, _ = strobe_flash(ffmpeg, path, geom['strobe']['box'], start_guess)
        if ft is not None:
            best, src = (None, ft), 'strobe'
    agree = [l for l, L in lanes.items() if L['agree']]
    need = max(2, (len(agree) + 1) // 2)
    for shift in (sorted(np.arange(-10, 10.5, 1.0), key=abs) if best is None else []):
        s0 = start_guess + shift
        offs = []
        for lane in agree:
            L = lanes[lane]
            o = _onset(sig[lane], t, s0 + L['ref'] - 2.5, s0 + L['ref'] + 1.5, s0 + L['ref'] - 9, s0 + L['ref'] - 5)
            if o is not None:
                offs.append(o - L['ref'])
        if len(offs) >= need:
            best = (None, float(np.median(offs)))
            break
    if best is None:
        return {'error': 'not enough lanes with matching times to line the video up', 'zones': {l: z[2] for l, z in zones.items()}}
    off = best[1]
    res = {}
    for lane, L in lanes.items():
        est = L['ref']
        o = _onset(sig[lane], t, off + est - 2.5, off + est + 1.5, off + est - 9, off + est - 5)
        res[lane] = {'name': L['name'], 'times': L['times'], 'ref': round(est, 2),
                     'clip_t': round(o, 3) if o is not None else None,
                     'video': round(o - off, 2) if o is not None else None,
                     'diff': round(o - off - est, 2) if o is not None else None}
    board = geom.get('board')
    if board:
        # when each lane's time showed up on the scoreboard in the picture (for checking the pad, not the clock:
        # the board shows a time 0.1-0.7 s after the console takes it)
        try:
            rows, bfps = board_rows(ffmpeg, path, board)
            holds = board_holds(rows, bfps)
        except Exception:
            holds = {}
        for lane, R in res.items():
            ts = R['times']
            p = ts.get('pad') or ts.get('console') or ts.get('backup') or R['ref']
            lo, hi = min(list(ts.values()) + [p]), max(list(ts.values()) + [p])
            # the first hold from just before the earliest time to 2 s after the latest one: when a pad is late
            # (or missed), the board shows the backup's time first and that is worth seeing
            near = [h for h in holds.get(lane, []) if off + lo - 0.5 <= h <= off + hi + 2.0]
            if near:
                h = min(near)
                R['board_clip_t'] = h
                R['board_lag'] = round(h - off - p, 2)
    return {'start_in_clip': round(off, 3), 'start_src': src, 'lanes': res, 'board': board, 'zones': {l: z[2] for l, z in zones.items()},
            'motion': {l: [round(float(v), 1) for v in sig[l][::3]] for l in sig}, 'motion_fps': fps / 3}


# ---------------------------------------------------------------- the scoreboard in the picture
# When the lane-time board is in the camera's view, each lane's row shows the running clock until that lane's pad
# (or button) registers, then holds the time. The frame where a row stops changing is when the console took that
# lane's time, and (that moment - the lane's console time) is where the race started in the clip - a start that
# needs no strobe and no guessing. board = {'box': [x0, y0, x1, y1] (fractions of the frame; the time digits from
# the top row to the bottom row), 'top': first lane shown in the box, 'bottom': last lane shown}.

def board_rows(ffmpeg, path, board):
    """Per-frame change in each lane's row of the board: ({lane: array}, fps)."""
    x0, y0, x1, y1 = [float(v) for v in board['box']]
    top, bot = int(board.get('top', 1)), int(board.get('bottom', 8))
    lanes = list(range(top, bot + 1)) if bot >= top else list(range(top, bot - 1, -1))
    n, RH, RW = len(lanes), 12, 64
    fps = 30.0
    vf = 'crop=w=iw*%.5f:h=ih*%.5f:x=iw*%.5f:y=ih*%.5f,scale=%d:%d,fps=%d,format=gray' % (x1 - x0, y1 - y0, x0, y0, RW, RH * n, fps)
    p = subprocess.Popen([ffmpeg, '-v', 'error', '-i', path, '-an', '-vf', vf, '-f', 'rawvideo', '-'], stdout=subprocess.PIPE,
                         creationflags=0x08000000 if __import__('os').name == 'nt' else 0)
    raw = p.stdout.read()
    p.wait()
    fr = np.frombuffer(raw, np.uint8)
    fr = fr[:len(fr) // (RW * RH * n) * RW * RH * n].reshape(-1, RH * n, RW).astype(np.int16)
    if len(fr) < 2:
        return {}, fps
    d = np.abs(np.diff(fr, axis=0))
    out = {}
    for i, lane in enumerate(lanes):
        band = d[:, i * RH + 2:(i + 1) * RH - 2, :]
        out[lane] = np.concatenate([[0.0], band.mean(axis=(1, 2))])
    return out, fps


def board_holds(rows, fps, hold=1.0):
    """{lane: [clip times]} - each moment a row stopped changing and stayed still for at least `hold` seconds."""
    out = {}
    k, h = 4, int(round(hold * fps))
    for lane, x in rows.items():
        if len(x) < h + k + 2:
            continue
        roll = np.array([x[max(0, i - k + 1):i + 1].max() for i in range(len(x))])
        busy = roll[roll > 0]
        if not len(busy):
            continue
        thr = max(1.5, 0.25 * float(np.percentile(roll, 95)))
        still = roll < thr
        ts, i = [], 1
        while i < len(x) - h:
            # a real hold: the row was running (changing most of the last second) and then stays still -
            # a lone blip (a keyframe in the recording) doesn't count
            if still[i] and not still[i - 1] and still[i:i + h].all() and (~still[max(0, i - int(fps)):i]).mean() >= 0.6:
                j = i
                while j > 0 and x[j] < thr:          # the last frame where the digits changed
                    j -= 1
                ts.append(round(j / fps, 3))
                while i < len(x) and still[i]:
                    i += 1
            i += 1
        out[lane] = ts
    return out


def board_start(holds, refs, tol=0.12):
    """Line the clip up from the board: the offset that puts the most lanes' holds on their console/pad times.
    refs: {lane: seconds}. Returns (start_in_clip, {lane: hold time}) or (None, {})."""
    cands = [h - r for l, r in refs.items() for h in holds.get(l, [])]
    best = (0, None)
    for o in cands:
        hit = {}
        for l, r in refs.items():
            near = [h for h in holds.get(l, []) if abs(h - (o + r)) <= tol]
            if near:
                hit[l] = min(near, key=lambda h: abs(h - (o + r)))
        if len(hit) > best[0] or (len(hit) == best[0] and best[1] and
                                   np.std([hit[l] - refs[l] for l in hit]) < np.std([best[1][l] - refs[l] for l in best[1]])):
            best = (len(hit), hit)
    need = 2 if sum(1 for l in refs if l in holds) >= 2 else 1
    if best[0] < need:
        return None, {}
    hit = best[1]
    return float(np.median([hit[l] - refs[l] for l in hit])), hit
