"""Pure geometry for the environment fill functions (no bpy, unit-tested on the host): where things go.

Every generator draws from its own random.Random(f'{seed}:{name}:{part}') so changing one element (more cars, a
different block) never moves another.
"""
from __future__ import annotations

import math
import random


def rng(seed, *parts):
    return random.Random(':'.join(str(p) for p in (seed, *parts)))


def _segments(path):
    out, start = [], 0.0
    for a, b in zip(path, path[1:]):
        length = math.dist(a[:2], b[:2])
        if length > 1e-9:
            out.append((a, b, start, length))
            start += length
    return out, start


def length(path):
    return _segments(path)[1]


def at(path, s):
    """(x, y, z), heading (radians, +x = 0) at arc length s along a polyline."""
    segments, total = _segments(path)
    if not segments:
        raise ValueError('path needs two distinct points')
    s = min(max(s, 0.0), total)
    for a, b, start, seg in segments:
        if s <= start + seg or (a, b, start, seg) == segments[-1]:
            t = (s - start) / seg
            z_a, z_b = (a[2] if len(a) > 2 else 0.0), (b[2] if len(b) > 2 else 0.0)
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, z_a + (z_b - z_a) * t), math.atan2(b[1] - a[1], b[0] - a[0])


def offset_point(point, heading, lateral):
    """`lateral` metres to the left of the travel direction."""
    return (point[0] - math.sin(heading) * lateral, point[1] + math.cos(heading) * lateral, point[2])


def inside(s, avoid, margin=0.0):
    return any(a - margin <= s <= b + margin for a, b in avoid)


def along(path, pitch_m, *, offset_m=0.0, start_m=0.0, end_margin_m=0.0, jitter_m=0.0, seed=0, name='along', avoid=(),
          avoid_margin_m=0.0):
    """[(point, heading)] every pitch_m metres along the path, `offset_m` to its left (negative: right).
    `avoid` [(s0, s1)]: arc-length ranges left empty (an opening, an intersection) - the same ranges the road
    strips skip, so a lamp never stands in a hole or a crossing. The rhythm outside them is unchanged."""
    if pitch_m <= 0:
        raise ValueError('pitch_m must be > 0')
    r = rng(seed, name, 'jitter')
    total = length(path)
    out, s = [], start_m
    while s <= total - end_margin_m + 1e-9:
        jitter = r.uniform(-jitter_m, jitter_m) if jitter_m else 0.0
        at_s = min(max(s + jitter, 0.0), total)
        if not inside(at_s, avoid, avoid_margin_m):
            point, heading = at(path, at_s)
            out.append((offset_point(point, heading, offset_m), heading))
        s += pitch_m
    return out


def keep_out(points, obstacles, radius_m):
    """Points farther than radius_m (in plan) from every obstacle: people never stand inside a pole or a trunk."""
    r2 = radius_m * radius_m
    return [p for p in points if all((p[0] - o[0]) ** 2 + (p[1] - o[1]) ** 2 >= r2 for o in obstacles)]


def scatter_in_rect(rect_centre, heading, size, count, *, seed=0, name='rect', z=0.0):
    """count points uniformly in a rotated rectangle (centre, heading of its length axis, (length, width))."""
    r = rng(seed, name)
    c, s = math.cos(heading), math.sin(heading)
    out = []
    for _ in range(count):
        u, v = r.uniform(-size[0] / 2, size[0] / 2), r.uniform(-size[1] / 2, size[1] / 2)
        out.append((rect_centre[0] + u * c - v * s, rect_centre[1] + u * s + v * c, z))
    return out


# Road marking and signal geometry (Korean road rules / KS, collected in references/environment_kits.md); every
# number is a parameter with this default, so another country is a different dict, not new code.
MARKINGS = {
    'crosswalk_setback_m': 1.0,      # kerb line of the crossing road -> crosswalk
    'crosswalk_bands': [3.3, 3.3],   # band depths along the approach (pedestrian lanes), split by band_gap_m
    'band_gap_m': 0.5,
    'stripe_w_m': 0.45, 'stripe_pitch_m': 1.0,
    'stop_line_w_m': 0.45, 'stop_line_gap_m': 3.0,     # stop line 3 m behind the crosswalk
    'arrow_len_m': 5.0, 'arrow_gap_m': 3.0,            # arrow tip 3 m behind the stop line
    'signal_side': 'near',                             # signals stand before the conflict area (reference)
    'signal_offset_m': 0.6,                            # pole behind the kerb
    'right_hand_traffic': True,
}


def _xy(origin, u, n, du, dn):
    return (origin[0] + u[0] * du + n[0] * dn, origin[1] + u[1] * du + n[1] * dn)


def approach_layout(centre, out_heading, road, crossing_half_w, rules=None):
    """Markings and signals of one approach to an intersection.
    centre: conflict-area centre (x, y); out_heading: direction from the centre out along this approach road;
    road: {'lane_w', 'lanes' (inbound), 'median_w'}; crossing_half_w: kerb half-width of the road being crossed.
    Inbound traffic drives toward the centre on its right (right_hand_traffic) - the +n side of u.
    Returns {'paint': [(cx, cy, heading, length, width)], 'arrows': [((tip_x, tip_y), heading)], 'signal': {...},
    'ped_signals': [...], 'extent_m': distance from the centre past the last marking}."""
    m = {**MARKINGS, **(rules or {})}
    u = (math.cos(out_heading), math.sin(out_heading))
    side = 1 if m['right_hand_traffic'] else -1
    n = (-u[1] * side, u[0] * side)
    inner = road.get('median_w', 0.0) / 2
    outer = inner + road['lanes'] * road['lane_w']
    half_w = outer + road.get('shoulder_w', 0.0)   # kerb half-width (symmetric: outbound lanes mirror the inbound ones)
    paint = []
    d = crossing_half_w + m['crosswalk_setback_m']
    stripes = []
    for k, band in enumerate(m['crosswalk_bands']):
        mid = d + band / 2
        x = -half_w + m['stripe_pitch_m'] / 2
        while x <= half_w - m['stripe_w_m'] / 2 + 1e-9:
            c = _xy(centre, u, n, mid, x)
            stripes.append((c[0], c[1], out_heading, band, m['stripe_w_m']))
            x += m['stripe_pitch_m']
        d += band + (m['band_gap_m'] if k < len(m['crosswalk_bands']) - 1 else 0.0)
    paint += stripes
    crosswalk_far = d
    stop = crosswalk_far + m['stop_line_gap_m'] + m['stop_line_w_m'] / 2
    c = _xy(centre, u, n, stop, (inner + outer) / 2)
    paint.append((c[0], c[1], out_heading, m['stop_line_w_m'], outer - inner))
    arrows = []
    tip = stop + m['stop_line_w_m'] / 2 + m['arrow_gap_m']
    for lane in range(road['lanes']):
        lateral = inner + (lane + 0.5) * road['lane_w']
        arrows.append((_xy(centre, u, n, tip, lateral), out_heading + math.pi))   # points toward the centre
    near = m['signal_side'] == 'near'
    pole_u = stop if near else -crossing_half_w - m['signal_offset_m']
    signal = {'point': _xy(centre, u, n, pole_u, half_w + m['signal_offset_m']), 'heading': out_heading,   # faces the drivers
              'arm_m': half_w - inner + m['signal_offset_m'] - road['lane_w'] / 2}
    ped = []
    for sign in (1, -1):   # both ends of the crosswalk, each facing across the road
        p = _xy(centre, u, n, crossing_half_w + m['crosswalk_setback_m'], sign * (half_w + m['signal_offset_m']))
        ped.append({'point': p, 'heading': out_heading + (-math.pi / 2 if sign > 0 else math.pi / 2) * side})
    return {'paint': paint, 'arrows': arrows, 'signal': signal, 'ped_signals': ped, 'stripes': len(stripes),
            'stop_m': stop, 'extent_m': tip + m['arrow_len_m']}


def intersection_layout(path, s, main, cross, rules=None):
    """A four-way crossing of `path` at arc length s by a perpendicular street.
    main / cross: {'lane_w', 'lanes' (per direction), 'median_w', 'sidewalk_w'}. Returns per-approach layouts,
    the corner pads and `clear` - the arc-length range of `path` kept free of lamps, trees and lane dashes."""
    point, heading = at(path, s)
    centre = (point[0], point[1])
    half = lambda r: r.get('median_w', 0.0) / 2 + r['lanes'] * r['lane_w'] + r.get('shoulder_w', 0.0)
    main_half, cross_half = half(main), half(cross)
    approaches = []
    for out_heading, road, crossing in ((heading, main, cross_half), (heading + math.pi, main, cross_half),
                                        (heading + math.pi / 2, cross, main_half), (heading - math.pi / 2, cross, main_half)):
        approaches.append(approach_layout(centre, out_heading, road, crossing, rules))
    corners = []
    for su in (1, -1):
        for sn in (1, -1):
            u = (math.cos(heading), math.sin(heading)); n = (-u[1], u[0])
            c = _xy(centre, u, n, su * (cross_half + cross['sidewalk_w'] / 2), sn * (main_half + main['sidewalk_w'] / 2))
            corners.append({'point': c, 'size': (cross['sidewalk_w'], main['sidewalk_w']), 'heading': heading})
    reach = max(a['extent_m'] for a in approaches[:2])
    stop = max(a['stop_m'] for a in approaches[:2])
    return {'centre': centre, 'heading': heading, 'main_half_w': main_half, 'cross_half_w': cross_half,
            'approaches': approaches, 'corners': corners,
            'clear': (s - reach, s + reach),          # nothing on the kerb side between here and the last arrow (lamps, trees)
            'stop_clear': (s - stop, s + stop),       # no lane lines inside the stop lines
            'crossing': (s - cross_half, s + cross_half),                                   # the main sidewalks stop here
            'frontage': (s - cross_half - cross['sidewalk_w'] - 2.0, s + cross_half + cross['sidewalk_w'] + 2.0)}  # no lots


def lots(path, *, lot_width_range_m, gap_range_m, depth_range_m, setback_m, side, seed=0, name='lots', avoid=()):
    """Building lots along one side of a frontage path: [{centre, heading, width, depth, s}] (heading = along the
    street; the lot extends away from it). `avoid`: [(s0, s1)] arc-length ranges kept empty (openings, crossings)."""
    r = rng(seed, name, side)
    total = length(path)
    out, s = [], r.uniform(0, gap_range_m[1])
    while True:
        width = r.uniform(*lot_width_range_m)
        if s + width > total:
            break
        depth = r.uniform(*depth_range_m)
        if not any(s < b and s + width > a for a, b in avoid):
            mid, heading = at(path, s + width / 2)
            lateral = (setback_m + depth / 2) * (1 if side == 'left' else -1)
            out.append({'centre': offset_point(mid, heading, lateral), 'heading': heading, 'width': width, 'depth': depth, 's': s})
        s += width + r.uniform(*gap_range_m)
    return out


def traffic(lanes, *, per_100m, speed_mps_range, duration_s, min_gap_m, seed=0, name='traffic', avoid=()):
    """Vehicles per lane: [{lane, s0, s1, start_xy, end_xy, heading}] keeping min_gap_m between neighbours at the start.
    lanes: [{'path': [...], 'direction': +1|-1}]; vehicles drive along (+1) or against (-1) the path. Same speed per
    lane keeps the gaps for the whole shot (no overtaking); a lane's speed is drawn once. `avoid` [(s0, s1)] in the
    lane's arc length: no vehicle ever crosses it (an opening in the road)."""
    out = []
    for index, lane in enumerate(lanes):
        r = rng(seed, name, index)
        total = length(lane['path'])
        speed = r.uniform(*speed_mps_range)
        travel = speed * duration_s
        wanted = max(0, round(total * per_100m / 100.0))
        starts, tries = [], 0
        while len(starts) < wanted and tries < wanted * 20:
            tries += 1
            s = r.uniform(0, total)
            lo, hi = sorted((s, s + travel * lane.get('direction', 1)))
            if any(lo - 3 < b and hi + 3 > a for a, b in avoid):
                continue
            if all(abs(s - o) >= min_gap_m for o in starts):
                starts.append(s)
        for s in sorted(starts):
            s1 = s + travel * lane.get('direction', 1)
            (p0, h0), (p1, _) = at(lane['path'], s), at(lane['path'], min(max(s1, 0.0), total))
            heading = h0 if lane.get('direction', 1) > 0 else h0 + math.pi
            out.append({'lane': index, 's0': round(s, 3), 's1': round(s1, 3), 'start': p0, 'end': p1, 'heading': heading,
                        'beyond_path': not (0 <= s1 <= total)})
    return out


def grid_points(rect, spacing_m, *, margin_m=0.0, jitter_m=0.0, seed=0, name='grid'):
    """Points on a rectangle (x0, y0, x1, y1, z) - roofs, plazas - every spacing_m with jitter."""
    x0, y0, x1, y1, z = rect
    r = rng(seed, name)
    out = []
    y = y0 + margin_m
    while y <= y1 - margin_m + 1e-9:
        x = x0 + margin_m
        while x <= x1 - margin_m + 1e-9:
            out.append((x + r.uniform(-jitter_m, jitter_m), y + r.uniform(-jitter_m, jitter_m), z))
            x += spacing_m
        y += spacing_m
    return out


def sightline_cap(lot, rule):
    """Tallest a building on `lot` may be so that, seen from rule['camera'] (level camera, horizon at rule['horizon_v']),
    its top stays below screen height rule['keep_sky_v'] (0 = top): the sky the establishing frame needs. A level
    camera puts height z at depth d at v = horizon_v - (z - z_cam) / (2 d tan_y); solved for z. Lots outside the
    horizontal field of view or behind the camera are not limited (None)."""
    cam, fwd = rule['camera'], rule.get('forward', (0.0, 1.0))
    norm = math.hypot(*fwd)
    fwd = (fwd[0] / norm, fwd[1] / norm)
    right = (fwd[1], -fwd[0])
    tan_y = rule.get('sensor_mm', 36.0) / (2 * rule.get('lens_mm', 24.0))   # portrait, AUTO fit: sensor spans the height
    tan_x = tan_y * rule.get('aspect', 9 / 16)
    c, s_ = math.cos(lot['heading']), math.sin(lot['heading'])
    corners = [(lot['centre'][0] + u * c - v * s_, lot['centre'][1] + u * s_ + v * c)
               for u in (-lot['width'] / 2, lot['width'] / 2) for v in (-lot['depth'] / 2, lot['depth'] / 2)]
    depths = [(p[0] - cam[0]) * fwd[0] + (p[1] - cam[1]) * fwd[1] for p in corners]
    if max(depths) <= 1.0:
        return None
    seen = [d > 1.0 and abs((p[0] - cam[0]) * right[0] + (p[1] - cam[1]) * right[1]) <= d * tan_x for p, d in zip(corners, depths)]
    if not any(seen):
        return None
    d = max(1.0, min(dd for dd, ok in zip(depths, seen) if ok))
    return cam[2] + (rule['horizon_v'] - rule['keep_sky_v']) * 2 * tan_y * d
