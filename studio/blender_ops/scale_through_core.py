"""A dive through scales (handover scale_through): pure math the build (scale_through.py, camera_moves.py), the brief's
join check (storyboard_brief.join, SCALE_JOIN_OFF) share. Host-tested.

One metric scene holds one scale: a factory, a wafer, a chip's wiring and a transistor are each a scene at their own true
size, and no single camera flies from one to the next. A dive is cut A pushing into a thing (its move target, the
carried thing) and cut B - the scene at the next smaller scale, which holds that thing at its own size - starting
where A left it: the carried thing at the same place on screen, at the same size (A one frame on), growing at the same
rate. They are cut together on that matched frame, so the eye reads one push.

The join is a matched cut, not a dissolve (2026-10-26, V3 smoke, local codex judge on the join strips, 3 runs each): a
0.3 s dissolve from A's last picture zoomed on at its rate was harsh 3/3 on both joins - a flat zoom of a picture is not
a dolly's perspective, so everything off the carried thing doubled; the same joins cut on the matched frame were smooth
3/3. What the deeper scene adds (detail too fine for the scene above) appears at the cut, smaller than the eye reads it
there, and grows in with the push.

The measure, on the carried thing's projected box (the same sampled surface points in both scenes, not clipped to the
frame) over A's last and B's first JOIN_S:
- position: the box centre's distance across the cut, in frame heights (x is converted from frame widths) <= POSITION_MAX;
- size: |ln| of B's first height share over A's last carried one frame on at A's rate <= ln(1 + SIZE_SAME);
- speed: the zoom rate, the least-squares slope of ln(height share) per second, both pushing in (above PUSH_MIN) and
  within SPEED_RATIO of each other.

The timing: at a scale_through end the camera does not arrive. The end that hands over keeps its cruise to the last
frame; the end that receives starts at the rate it was handed (the carried thing's rate, as the zoom of its log
distance) and ramps to its own cruise. The director's timing (settle_at, its ramp) shapes the rest; the log distance
follows it (camera_moves_core.approach_timing), so the rate is a rate of screen size.

Why (2026-10-26, the user on the semiconductor reel: "반도체 안으로 들어가서 그 내부 구조 보여주는거였으면 더 좋지
않았을까"): the agent could think the dive into the chip and the engine could not make it.
"""
from __future__ import annotations

import math

JOIN_S = 0.3            # seconds each side of the cut a join is read over (grammar_core.JOIN_S, the reel's other joins)
WINDOW_S = 0.25         # a pace change spread over less than this is a jerk to the eye (camera_follow.WINDOW_S)
POSITION_MAX = 0.05     # the carried thing's centre moves at most this share of the frame height across the cut
SIZE_SAME = 0.15        # sizes within +-15 % read as the same size (grammar_core.SCALE_STEP_SAME)
SPEED_RATIO = 1.25      # zoom rates within this factor read as one push: the engine sets B's start rate to A's exactly,
                        # what is left is the carried thing standing nearer or farther than the aim (well inside the
                        # reel's own carry ratio, grammar_core.JOIN_CARRY_RATIO 2.5)
PUSH_MIN = 0.08         # a zoom rate (ln size per second) under this is no push: camera_follow.REST (0.02) per WINDOW_S
SETTLED = 1e-4          # progress within this of 1 is arrived
SHAPE_SAMPLES = 400     # points a director's timing is read at (its settle and ramp)
REACH_STEPS = 80        # bisection steps of a receiving camera's start distance


def rate(samples, fps):
    """The zoom rate of [[frame, cx, cy, height_share], ...]: least-squares slope of ln(height share) per second, or
    None with fewer than two usable samples."""
    pts = [(s[0] / fps, math.log(s[3])) for s in samples if s[3] and s[3] > 0]
    if len(pts) < 2:
        return None
    mt = sum(t for t, _ in pts) / len(pts)
    ml = sum(v for _, v in pts) / len(pts)
    den = sum((t - mt) ** 2 for t, _ in pts)
    return sum((t - mt) * (v - ml) for t, v in pts) / den if den > 0 else None


def window(samples, fps, side):
    """The samples of the JOIN_S at a shot's 'start' or 'end' (samples sorted by frame)."""
    rows = sorted(samples, key=lambda s: s[0])
    n = max(2, round(JOIN_S * fps) + 1)
    return rows[:n] if side == 'start' else rows[-n:]


def state(samples, fps, side):
    """{frame, centre, share, rate} of a carried thing at a shot's start or end, or None when not measured."""
    rows = window(samples or [], fps, side)
    if not rows:
        return None
    at = rows[0] if side == 'start' else rows[-1]
    return {'frame': at[0], 'centre': [round(at[1], 5), round(at[2], 5)], 'share': round(at[3], 5),
            'rate': None if (r := rate(rows, fps)) is None else round(r, 5)}


def join(tail, head, fps, aspect):
    """How cut A (its tail samples) hands a carried thing to cut B (its head samples): {position_off (frame heights),
    size_off (|ln| of the size ratio), speed_ratio, a, b, problems}. aspect: frame width / height (x shares become
    heights). problems is [] when the join reads as one push."""
    a, b = state(tail, fps, 'end'), state(head, fps, 'start')
    out = {'a': a, 'b': b, 'position_off': None, 'size_off': None, 'speed_ratio': None, 'problems': []}
    if not a or not b:
        out['problems'].append('not measured on both sides')
        return out
    out['position_off'] = round(math.hypot((b['centre'][0] - a['centre'][0]) * aspect, b['centre'][1] - a['centre'][1]), 4)
    if out['position_off'] > POSITION_MAX:
        out['problems'].append(f"the carried thing moves {round(out['position_off'] * 100, 1)} % of the frame height across the cut "
                               f"(at most {round(POSITION_MAX * 100)} %)")
    moving = [x['rate'] is not None and x['rate'] >= PUSH_MIN for x in (a, b)]
    if not moving[0]:
        out['problems'].append(f"A no longer pushes in at its end (zoom rate {a['rate']} per s, under {PUSH_MIN})")
    if not moving[1]:
        out['problems'].append(f"B does not push in at its start (zoom rate {b['rate']} per s, under {PUSH_MIN})")
    carried = a['share'] * math.exp((a['rate'] or 0.0) / fps)   # A one frame on, where B's first frame stands
    out['size_off'] = round(abs(math.log(b['share'] / carried)), 4) if carried > 0 and b['share'] > 0 else None
    if out['size_off'] is None or out['size_off'] > math.log(1 + SIZE_SAME):
        out['problems'].append(f"the carried thing stands {b['share']} tall after the cut, {round(carried, 4)} before "
                               f"(within {round(SIZE_SAME * 100)} %)")
    if all(moving):
        out['speed_ratio'] = round(b['rate'] / a['rate'], 4)
        if abs(math.log(out['speed_ratio'])) > math.log(SPEED_RATIO):
            out['problems'].append(f"the push runs at {a['rate']} per s before the cut and {b['rate']} after (within x{SPEED_RATIO})")
    return out


# --- the timing at a scale_through end ------------------------------------------------------------------------------

def curve_shape(curve, samples=SHAPE_SAMPLES):
    """(settle_at, ramp) of a director's timing u(t): when it arrives (first t at 1) and how long it takes to reach its
    top speed (first t at 95 % of it) - the ease the director chose, kept at the ends that are not joined."""
    ts = [i / samples for i in range(samples + 1)]
    us = [curve(t) for t in ts]
    settle = next((t for t, u in zip(ts, us) if u >= 1 - SETTLED), 1.0)
    slopes = [(us[i + 1] - us[i]) * samples for i in range(samples)]
    top = max(slopes) if slopes else 0.0
    ramp = next((ts[i] for i, v in enumerate(slopes) if v >= 0.95 * top), 0.0) if top > 0 else 0.0
    return round(settle, 4), round(ramp, 4)


def start_speed(rate_in, seconds, span):
    """The normalised start speed (progress per shot) of a receiving cut: the rate it is handed (ln size per second)
    over its own log span (ln of its start over its landing distance) across its seconds."""
    if span <= 0:
        raise ValueError(f'SCALE_THROUGH_NO_ROOM: the receiving camera does not close in (log span {span})')
    return rate_in * seconds / span


def through_curve(start, settle_at, ramp, keeps_moving, seconds):
    """(g(t), cruise): progress in log distance over the shot, t in [0, 1]. Its speed starts at `start` (0 from rest, a
    received rate otherwise - held over the JOIN_S the join is read on, so the push runs on unchanged across the cut),
    ramps over `ramp` (at least one WINDOW_S of the shot's `seconds`) to an even cruise, and either keeps that cruise to
    the last frame (keeps_moving: the cut hands over scale_through) or ramps down to rest at settle_at and holds. A
    trapezoid generalised: settle_timing is start 0, not keeps_moving."""
    s = 1.0 if keeps_moving else float(settle_at)
    if not 0.0 < s <= 1.0:
        raise ValueError(f'SCALE_THROUGH: settle_at {settle_at!r} must be in (0, 1]')
    a = float(start)
    h = min(JOIN_S / max(seconds, 1e-9), s / 3) if a > 0 else 0.0   # the received pace held while the join is read
    k = max(float(ramp), min(s / 2, WINDOW_S / max(seconds, 1e-9)))
    k_end = 0.0 if keeps_moving else min(k, (s - h) / 2)
    k = min(k, s - h - k_end)
    cruise = (1 - a * (h + k / 2)) / (s - h - k / 2 - k_end / 2)
    if cruise <= 0:
        raise ValueError(f'SCALE_THROUGH_NO_ROOM: a camera handed {round(a, 3)} shots of its approach per shot would pass its '
                         'landing before it eased - give the receiving cut a longer approach or more time')
    c = cruise
    up = a * h + k * (a + c) / 2             # progress at the end of the first ramp
    level_end = s - k_end

    def g(t):
        t = min(1.0, max(0.0, t))
        if t <= h:
            return a * t
        if t <= h + k:
            u = t - h
            return a * h + a * u + (c - a) * u * u / (2 * k) if k > 0 else a * h
        if t <= level_end:
            return up + c * (t - h - k)
        if t < s:
            left = s - t
            return 1.0 - c * left * left / (2 * k_end)
        return 1.0
    return g, cruise


def end_rate(cruise, span, seconds):
    """The zoom rate (ln size per second) a cut that keeps its cruise to the end hands over."""
    return cruise * span / seconds


def reach_for_share(share_at, share, near, far):
    """The camera distance (along its line of sight, between near and far) at which share_at(distance) - the carried
    thing's height share, falling with distance - equals `share`. Raises SCALE_THROUGH_NO_ROOM when even `near` frames
    it smaller; `far` doubles until it frames it smaller than `share`."""
    if share_at(near) < share:
        raise ValueError(f'SCALE_THROUGH_NO_ROOM: at its landing the receiving camera frames the carried thing {round(share_at(near), 4)} '
                         f'tall, under the {round(share, 4)} it was handed at - land closer, or hand over earlier')
    for _ in range(40):
        if share_at(far) <= share:
            break
        far *= 2
    lo, hi = near, far
    for _ in range(REACH_STEPS):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if share_at(mid) > share else (lo, mid)
    return (lo + hi) / 2
