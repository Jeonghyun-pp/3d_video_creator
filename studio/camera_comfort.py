"""How comfortable a built camera is to watch: its fastest half second, measured on the camera's own share of the
picture's motion (grammar_synth pairs' 'camera' fit: the scene's points held still, so a stone falling past the lens
moves the picture, not the camera) and held against the comfort goal (library/shot_goals.json).

Why (2026-10-23, submarine r3): the user - "카메라 움직임이 너무 빨라서 안보이는것도 있고". CAMERA_HARSH compares a
shot's typical rate with the reference's and passed both cuts the reel's watcher called harsh four times out of four:
each crept, then rushed from the whole boat to one tank in half a second. A rush is a peak, not a mean.
"""
from __future__ import annotations

import math

from .common import REPO, StudioError, read_json

WINDOW_S = 0.5   # the stretch a rush is read over: about what an eye needs to find the subject again
AXES = ('scale', 'travel', 'turn_deg')
GOALS = REPO / 'library' / 'shot_goals.json'


def goals():
    """Every goal row of library/shot_goals.json."""
    return read_json(GOALS)['goals']


def goal(name='comfort'):
    """The goal row of library/shot_goals.json named `name`."""
    rows = {g['id']: g for g in goals()}
    if name not in rows:
        raise StudioError('INPUT_INVALID', f'shot goal {name!r} is not in library/shot_goals.json ({sorted(rows)})')
    return rows[name]


def peaks(pairs, fps, window_s=WINDOW_S):
    """{scale, travel, turn_deg, at_s: {axis: start of its fastest window}} over the camera's own motion, or None when
    no pair carries it (a version measured before 2026-10-23: measure it again). scale: |log size change|, travel: the
    picture's slide in frame widths, turn: the roll, each summed over the window - a slow drift and a rush of the same
    distance read alike only when they take as long."""
    rows = [p.get('camera') if p.get('ok') else None for p in pairs]
    if not any(rows):
        return None
    still = {'tx': 0.0, 'ty': 0.0, 'ds': 0.0, 'rot': 0.0}
    seq = [r or still for r in rows]
    n = max(1, min(len(seq), round(window_s * fps)))
    best = {axis: (0.0, 0) for axis in AXES}
    for i in range(len(seq) - n + 1):
        w = seq[i:i + n]
        got = {'scale': abs(sum(r['ds'] for r in w)),
               'travel': math.hypot(sum(r['tx'] for r in w), sum(r['ty'] for r in w)),
               'turn_deg': abs(math.degrees(sum(r['rot'] for r in w)))}
        for axis, value in got.items():
            if value > best[axis][0]:
                best[axis] = (value, i)
    first = pairs[0].get('frame', 0) if pairs else 0
    return {**{axis: round(v, 4) for axis, (v, _i) in best.items()},
            'at_s': {axis: round((pairs[i].get('frame', first + i) - first) / fps, 2) for axis, (_v, i) in best.items()},
            'window_s': window_s}


def lines(measured, row=None):
    """CAMERA_TOO_FAST lines of measured peaks against the goal's limits ([] when within, or unmeasured)."""
    if not measured:
        return []
    row = row or goal()
    out = []
    for axis, limit in row['limits'].items():
        value = measured.get(axis)
        if value is not None and value > limit:
            out.append(f"CAMERA_TOO_FAST: the camera's {axis} over its fastest {measured['window_s']} s (from {measured['at_s'][axis]} s) is "
                       f"{value:g}, past the comfort limit {limit:g} - {row['try'][axis]}")
    return out
