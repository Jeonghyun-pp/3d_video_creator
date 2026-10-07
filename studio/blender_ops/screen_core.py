"""Screen targets, the pure part (no bpy, no numpy): what a shot declares about its picture (`shot.screen`) against what
the frame probe measured.

The probe (frame_probe.py) already renders an id pass of 8-12 frames for its own checks; from the same label images it
keeps, per class and frame, a shape: pixel count, bounding box, centroid and the pixels outside the platform UI rect
(normalized, 0,0 top-left). From the scene alone (no render) it projects each class's bounding boxes on every
`stride`-th frame, which gives screen speed. Nothing here renders.

Judgement follows studio/gates.py: a declared target that is missed is taste (SCREEN_TARGET_MISSED, SCREEN_SPEED_HIGH -
warnings by default); a declared key part covered by the platform UI for its whole window is broken (KEY_PART_UNDER_UI).
A shot with no `screen` gets measurements only. Scores are Gaussian, exp(-(deviation / tol)^2): 1 on target, 0.37 at
the tolerance (Abdullah & Christie 2011 rate composition factors the same way).
"""
from __future__ import annotations

import math
import statistics

THIRDS = [(x, y) for x in (1 / 3, 2 / 3) for y in (1 / 3, 2 / 3)]
UI_COVERED_SHARE = 0.5     # KEY_PART_UNDER_UI: half of the part's pixels lie where the platform UI draws
SPEED_QUANTILE = 0.95      # a shot's speed is its 95th-percentile step, not one whip frame


def _bbox(shape):
    return shape['bbox']


METRICS = {   # name -> (value of one frame's shape, what it means); `speed` is measured on the motion series instead
    'center_x': (lambda s: s['centroid'][0], 'centroid x (0 left, 1 right)'),
    'center_y': (lambda s: s['centroid'][1], 'centroid y (0 top, 1 bottom)'),
    'width_share': (lambda s: _bbox(s)[2] - _bbox(s)[0], 'bounding box width / frame width'),
    'height_share': (lambda s: _bbox(s)[3] - _bbox(s)[1], 'bounding box height / frame height'),
    'area_share': (lambda s: s['share'], 'pixels / frame pixels'),
    'edge_margin': (lambda s: min(_bbox(s)[0], _bbox(s)[1], 1 - _bbox(s)[2], 1 - _bbox(s)[3]), 'nearest frame edge, normalized'),
    'thirds_distance': (lambda s: min(math.dist(s['centroid'], p) for p in THIRDS), 'centroid to the nearest thirds point'),
    'ui_overlap': (lambda s: s['outside_ui_px'] / s['px'] if s['px'] else 0.0, 'share of pixels under the platform UI'),
    'speed': (None, f"screen speed: p{round(SPEED_QUANTILE * 100)} centre step per frame, as a share of the frame width"),
}


def score(deviation, tol):
    return math.exp(-(deviation / tol) ** 2) if tol > 0 else float(deviation == 0)


def _window(item, count):
    lo = int(item.get('from_frame', 0))
    hi = int(item.get('to_frame', count - 1))
    return lo, min(hi, count - 1)


def class_of(of):
    """The probe class a target is about: 'subject', 'all' (everything that renders) or a key part's class."""
    return of if of in ('subject', 'all') else f'key:{of}'


def speeds(motion, cls, aspect, lo=0, hi=None):
    """Per-step centre speeds (share of frame width per frame) of one class inside [lo, hi]. motion: {'stride': n,
    'frames': [f...], 'centers': {cls: [[x, y] | None ...]}}; y is converted to width units with the frame aspect."""
    frames, centers = motion.get('frames', []), motion.get('centers', {}).get(cls, [])
    out = []
    for (f0, a), (f1, b) in zip(zip(frames, centers), list(zip(frames, centers))[1:]):
        if a is None or b is None or f0 < lo or (hi is not None and f1 > hi) or f1 <= f0:
            continue
        out.append(math.hypot(b[0] - a[0], (b[1] - a[1]) / aspect) / (f1 - f0))
    return out


def quantile(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else None


def measure_target(target, rows, motion, count, aspect):
    """{'measured', 'frames', 'per_frame'} for one declared target (measured None when it never shows)."""
    lo, hi = _window(target, count)
    cls = class_of(target['of'])
    if target['metric'] == 'speed':
        step = speeds(motion, cls, aspect, lo, hi)
        value = quantile(step, SPEED_QUANTILE)
        return {'measured': None if value is None else round(value, 5), 'frames': [lo, hi], 'per_frame': None}
    fn = METRICS[target['metric']][0]
    seen = [(r['frame'], fn(r['shapes'][cls])) for r in rows
            if lo <= r['frame'] <= hi and (r.get('shapes') or {}).get(cls, {}).get('px')]
    if not seen:
        return {'measured': None, 'frames': [lo, hi], 'per_frame': []}
    return {'measured': round(statistics.median(v for _, v in seen), 5), 'frames': [lo, hi],
            'per_frame': [[f, round(v, 5)] for f, v in seen]}


def judge(rows, motion, screen, key_parts, count, aspect, min_px=20):
    """(failures, summary). screen: shot.screen or None; key_parts: the probe's key parts (only declared ones are held
    to KEY_PART_UNDER_UI, like KEY_PART_SMALL); aspect: frame width / height."""
    screen = screen or {}
    failures, measured = [], []
    for target in screen.get('targets', []):
        got = measure_target(target, rows, motion, count, aspect)
        row = {'id': target['id'], 'metric': target['metric'], 'of': target['of'], 'value': target['value'], 'tol': target['tol'], **got}
        if got['measured'] is None:
            row['score'] = 0.0
            failures.append({'code': 'SCREEN_TARGET_MISSED', 'target': target['id'], 'frames': got['frames'],
                             'hint': f"{target['of']} never shows in frames {got['frames']}: nothing to measure"})
        else:
            deviation = got['measured'] - target['value']
            row['deviation'], row['score'] = round(deviation, 5), round(score(deviation, target['tol']), 3)
            if abs(deviation) > target['tol']:
                failures.append({'code': 'SCREEN_TARGET_MISSED', 'target': target['id'], 'metric': target['metric'],
                                 'measured': got['measured'], 'value': target['value'], 'tol': target['tol'], 'frames': got['frames'],
                                 'hint': f"{target['metric']} of {target['of']} is {got['measured']} (asked {target['value']} ±{target['tol']})"})
        measured.append(row)
    subject_steps = speeds(motion, 'subject', aspect)
    p95 = quantile(subject_steps, SPEED_QUANTILE)
    limit = screen.get('max_speed')
    if limit is not None and p95 is not None and p95 > limit:
        failures.append({'code': 'SCREEN_SPEED_HIGH', 'measured': round(p95, 5), 'limit': limit,
                         'hint': 'the subject crosses the frame faster than the shot allows: slow the move or widen the lens'})
    windows = {p['id']: _window(p, count) for p in key_parts}
    for part in key_parts:
        if part.get('source', 'declared') != 'declared':
            continue
        lo, hi = windows[part['id']]
        cls = f"key:{part['id']}"
        seen = [r for r in rows if lo <= r['frame'] <= hi and (r.get('shapes') or {}).get(cls, {}).get('px', 0) >= min_px]
        under = [r['frame'] for r in seen if METRICS['ui_overlap'][0](r['shapes'][cls]) >= UI_COVERED_SHARE]
        if seen and len(under) == len(seen):
            failures.append({'code': 'KEY_PART_UNDER_UI', 'part': part['id'], 'frames': under[:20],
                             'hint': 'the platform UI (top bar, caption and buttons) covers the key part wherever it shows: move it inside the safe rect'})
    total = [r['score'] for r in measured]
    summary = {'targets': measured, 'score': round(math.prod(total) ** (1 / len(total)), 3) if total else None,
               'subject_speed_p95': None if p95 is None else round(p95, 5)}
    return failures, summary
