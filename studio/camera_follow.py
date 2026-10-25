"""Can an eye follow the camera: how sharply the camera's own screen motion changes pace, on a per-pair motion series
that a built take and a reference video both give - so both are measured by one ruler.

A pair is {tx, ty, ds}: the picture's slide in frame widths and its log size change between two frames. A built take
gives the camera's own share (grammar_synth pairs' 'camera' fit: scene points held still, camera_pairs); a reference
gives the picture fit (grammar measure motion.json pairs). Screen motion per pair is hypot(tx, ty) + |ds|.

- jerk: the largest change of the screen motion summed over WINDOW_S between neighbouring WINDOW_S windows, per
  second. A camera that creeps and then rushes (or rushes and stops dead) changes pace in a quarter second faster than
  an eye re-finds the picture; one that speeds up and slows down smoothly does not, however fast it cruises.
- arrive_s: when the camera comes to rest for good - the end of the last WINDOW_S window whose motion is above REST.
- moving_s: the stretches [start, end] in seconds where the WINDOW_S motion is above REST (what a key change or a quiet
  tail is read against).

Why (2026-10-25, F0 calibration, local and free): the speed floor (CAMERA_TOO_FAST at 1.5x) blocked a fast but
followable ride-along and caught 22 of 43 harsh takes; jerk at 1.8 per second caught 27 of 43 with 7 of 222 not-harsh
flagged, and no segment of five one-take fly-throughs reached it (their max 1.63). The limit lives in the goal row
`followable` (library/shot_goals.json); this module only measures.
"""
from __future__ import annotations

import math

WINDOW_S = 0.25   # the stretch a pace is read over
REST = 0.02       # screen motion per WINDOW_S below this is a camera at rest (a fiftieth of the frame)
STILL = {'tx': 0.0, 'ty': 0.0, 'ds': 0.0}


def camera_pairs(pairs):
    """grammar_synth rows -> the camera's own pairs (a row without a camera fit counts as still)."""
    return [(p.get('camera') if p.get('ok') else None) or STILL for p in pairs]


def picture_pairs(pairs):
    """grammar measure motion.json rows -> the picture's pairs (a row the fit lost counts as still)."""
    return [p if p.get('ok') else STILL for p in pairs]


def motion(pair):
    """Screen motion of one pair: travel in frame widths plus |log size change|."""
    return math.hypot(pair.get('tx', 0.0), pair.get('ty', 0.0)) + abs(pair.get('ds', 0.0))


def windows(values, n):
    """Sums of `values` over every run of n neighbours: out[k] = sum(values[k:k + n])."""
    if len(values) < n:
        return []
    out, total = [sum(values[:n])], sum(values[:n])
    for i in range(n, len(values)):
        total += values[i] - values[i - n]
        out.append(total)
    return out


def follow(pairs, fps):
    """{jerk, jerk_at_s, arrive_s, moving_s, window_s, rest} of a pair series, or None when it has too few pairs to read
    one pace change."""
    n = max(1, round(WINDOW_S * fps))
    sums = windows([motion(p) for p in pairs], n)
    if len(sums) <= n:
        return None
    jerk, at = 0.0, 0
    for i in range(n, len(sums)):
        change = abs(sums[i] - sums[i - n]) / WINDOW_S
        if change > jerk:
            jerk, at = change, i - n
    moving, start = [], None
    for k, value in enumerate(sums):
        if value > REST and start is None:
            start = k
        elif value <= REST and start is not None:
            moving.append([round(start / fps, 3), round((k - 1 + n) / fps, 3)])
            start = None
    if start is not None:
        moving.append([round(start / fps, 3), round((len(sums) - 1 + n) / fps, 3)])
    return {'jerk': round(jerk, 3), 'jerk_at_s': round(at / fps, 2), 'arrive_s': moving[-1][1] if moving else 0.0,
            'moving_s': moving, 'window_s': WINDOW_S, 'rest': REST}


def moving_over(measured, start_s, end_s):
    """Seconds of [start_s, end_s] during which the camera moves (measured['moving_s'])."""
    return round(sum(max(0.0, min(end_s, b) - max(start_s, a)) for a, b in (measured or {}).get('moving_s') or []), 3)
