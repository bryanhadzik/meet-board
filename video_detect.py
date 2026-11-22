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


def lane_zones(geom):
    """{lane: (near_mask, wall_mask, quad_pixels)} from the marked geometry (fractions of the frame).
    finish[i] / turn[i]: lane line i (0 = lane-1 edge ... 8 = lane-8 edge) at the finish and turn walls."""
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
        zones[lane] = (_quad_mask(near), _quad_mask(wall), [[round(float(x) / AW, 4), round(float(y) / AH, 4)] for x, y in full])
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


def detect(ffmpeg, path, geom, refs, start_guess):
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
    agree = [l for l, L in lanes.items() if L['agree']]
    need = max(2, (len(agree) + 1) // 2)
    for shift in sorted(np.arange(-10, 10.5, 1.0), key=abs):
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
    return {'start_in_clip': round(off, 3), 'lanes': res, 'zones': {l: z[2] for l, z in zones.items()},
            'motion': {l: [round(float(v), 1) for v in sig[l][::3]] for l in sig}, 'motion_fps': fps / 3}
