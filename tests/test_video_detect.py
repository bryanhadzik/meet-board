import numpy as np
import video_detect as vd


def test_zones_cover_each_lane_in_order():
    fin = [[0.1, 0.9 - 0.08 * i] for i in range(9)]          # lane-1 edge at the bottom
    trn = [[0.9, 0.85 - 0.06 * i] for i in range(9)]
    z = vd.lane_zones({'finish': fin, 'turn': trn})
    assert sorted(z) == list(range(1, 9))
    ys = [np.argwhere(z[l][0])[:, 0].mean() for l in range(1, 9)]
    assert all(a > b for a, b in zip(ys, ys[1:]))           # lane 1 lowest in the picture
    assert all(z[l][0].sum() > 0 and z[l][1].sum() > 0 for l in z)


def test_onset_finds_the_rise():
    t = np.arange(600) / 30.0
    x = np.where(t > 12.0, 20.0, 2.0)
    assert abs(vd._onset(x, t, 10, 14, 3, 7) - 12.0) < 0.05
