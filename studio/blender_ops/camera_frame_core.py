"""Framing solver, the pure part (no bpy): where the camera stands so the shot's focus shows at the size and place the
shot asks for (`camera.move.frame`), seen from the direction the shot chose.

Why (2026-10-10): the camera was set in metres and degrees and judged on the screen. Perspective made the one a poor
guess of the other (a 5 m deep floor seen from above shows its top, not its 35 cm section), and a frame clipped at an
edge measured the focus small, so stepping closer clipped it more: eight rebuilds of one shot never settled. Here the
focus is projected without clipping - how far it runs off an edge is known - and the camera is moved until it fits:
size by the distance along the view, place by a sideways shift, the direction fixed (it is the shot's choice, not the
solver's). The user's split: people and the agent choose the picture (size, place, angle from the picked concept and
taste); this only places the camera exactly and keeps it from the mistakes (too far, cut, under the platform UI).

The caller (camera_frame.py) gives `visible(position)`: the world points of the focus a camera there sees (ray-cast,
so what is hidden does not count).
"""
from __future__ import annotations

import math

SENSOR_MM = 36.0
BAND_ELONGATION = 3.0   # a focus seen this many times wider than tall is a band (a layer stack): it may run off the
                        # sides, its height is what is fitted (the structure clause and the pattern library's reveal
                        # framing - width_share median 0.97, edge_margin median 0 - read sections the same way)
MARGIN = 0.02           # what must fit stays this far inside the frame edges
SIZE_TOL = 0.03         # converged: height within 3 % of the target ...
PLACE_TOL = 0.02        # ... and the centre within 0.02 of the frame of its target
MAX_STEPS = 16
DAMPING = 0.6


def _sub(a, b): return tuple(a[i] - b[i] for i in range(3))
def _add(a, b): return tuple(a[i] + b[i] for i in range(3))
def _mul(a, s): return tuple(x * s for x in a)
def _dot(a, b): return sum(a[i] * b[i] for i in range(3))


def _norm(a):
    n = math.sqrt(_dot(a, a))
    return tuple(x / n for x in a)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def basis(elevation_deg, azimuth_deg):
    """(forward, right, up) of a camera looking at its subject from (elevation, azimuth): azimuth 0 = camera on -Y
    looking +Y, the same convention as camera_moves_core._dir; no roll."""
    a, e = math.radians(azimuth_deg), math.radians(elevation_deg)
    toward_camera = (math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e))
    forward = _mul(toward_camera, -1.0)
    right = _norm(_cross(forward, (0.0, 0.0, 1.0)))
    return forward, right, _cross(right, forward)


def view_of(eye, aim):
    """(elevation_deg, azimuth_deg) of a camera at eye looking at aim, in basis()'s convention."""
    f = _norm(_sub(aim, eye))
    return math.degrees(math.asin(max(-1.0, min(1.0, -f[2])))), math.degrees(math.atan2(-f[0], f[1]))


def tangents(lens_mm, aspect, sensor_mm=SENSOR_MM):
    """(tan of half the horizontal view, tan of half the vertical view): the sensor fits the long side (Blender AUTO)."""
    tan_long = sensor_mm / (2 * lens_mm)
    return (tan_long * aspect, tan_long) if aspect < 1 else (tan_long, tan_long / aspect)


def project(points, position, frame_basis, tans):
    """Frame coordinates (0..1, top-left origin, not clipped) and depth of points in front of the camera; points behind
    it are counted, not projected."""
    forward, right, up = frame_basis
    tan_w, tan_h = tans
    out, behind = [], 0
    for p in points:
        v = _sub(p, position)
        z = _dot(v, forward)
        if z <= 1e-6:
            behind += 1
            continue
        out.append((0.5 + _dot(v, right) / (z * tan_w) / 2, 0.5 - _dot(v, up) / (z * tan_h) / 2, z))
    return out, behind


TRIM = 0.005            # the box spans the 0.5th-99.5th percentile of the points: a few points grazing past the camera
                        # project far out and would steer the solve (they are a sliver of the picture, not its edge)


def _quantile(sorted_values, q):
    return sorted_values[min(len(sorted_values) - 1, max(0, round(q * (len(sorted_values) - 1))))]


def measure(screen, aspect):
    """{box [x0, y0, x1, y1], height, width, centre [x, y], band, depth} of projected points. Across: all points,
    unclipped and trimmed (how far the focus runs off the sides). Up and down: the points that are on screen across -
    a band's ends past the sides never show, so its height is what the frame shows of it (as the probe measures it)."""
    xs = sorted(p[0] for p in screen)
    shown = [p[1] for p in screen if 0.0 <= p[0] <= 1.0] or [p[1] for p in screen]
    ys = sorted(shown)
    trim = TRIM if len(screen) >= 200 else 0.0
    box = [_quantile(xs, trim), ys[0], _quantile(xs, 1 - trim), ys[-1]]
    height, width = box[3] - box[1], box[2] - box[0]
    depths = sorted(p[2] for p in screen)
    return {'box': [round(v, 4) for v in box], 'height': height, 'width': width,
            'centre': [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2],
            'band': height > 0 and width * aspect / height >= BAND_ELONGATION, 'depth': depths[len(depths) // 2]}


def solve(visible, start, view, target, lens_mm, aspect, sensor_mm=SENSOR_MM):
    """Camera position for `target` = {height_share, centre: [x, y]} seen from view = (elevation_deg, azimuth_deg).
    visible(position) -> world points of the focus seen from there. Returns {position, aim, achieved, steps, limited_by}
    or raises ValueError('FRAME_UNSOLVED: ...') saying what stood in the way."""
    frame_basis = basis(*view)
    forward, right, up = frame_basis
    tans = tangents(lens_mm, aspect, sensor_mm)
    if target.get('edges'):
        return _solve_edges(visible, start, view, target['edges'], frame_basis, tans, aspect)
    want_h, (want_x, want_y) = float(target['height_share']), target['centre']
    position, limited = tuple(start), None
    for step in range(1, MAX_STEPS + 1):
        points = visible(position)
        if not points:
            raise ValueError(f'FRAME_UNSOLVED: nothing of the focus is visible from {[round(v, 3) for v in position]} '
                             f'(view {view}); choose another direction or check what stands in front of it')
        screen, behind = project(points, position, frame_basis, tans)
        if behind or not screen:   # inside or past the focus: step back along the view and look again
            position = _sub(position, _mul(forward, max(1.0, measure(screen, aspect)['depth'] if screen else 1.0)))
            continue
        m = measure(screen, aspect)
        depth = m['depth']
        new_depth = depth * m['height'] / want_h if m['height'] > 0 else depth
        limited = None
        if not m['band'] and m['width'] * depth / new_depth > 1 - 2 * MARGIN:   # an object fits whole: width wins
            new_depth, limited = depth * m['width'] / (1 - 2 * MARGIN), 'width'
        if want_h <= 1 - 2 * MARGIN and m['height'] * depth / new_depth > 1 - 2 * MARGIN:
            new_depth, limited = depth * m['height'] / (1 - 2 * MARGIN), 'height'
        # a band already running past both side edges has no sideways place to choose (and its box centre swings with
        # the nearest points): only its height and vertical place are solved; until it runs across, it is steered
        across = m['band'] and m['box'][0] <= 0.0 and m['box'][2] >= 1.0
        miss_x = 0.0 if across else m['centre'][0] - want_x
        miss_y = m['centre'][1] - want_y
        size_ok = abs(m['height'] - want_h) <= SIZE_TOL * want_h or (limited and abs(new_depth - depth) / depth < SIZE_TOL / 2)
        if size_ok and abs(miss_x) < PLACE_TOL and abs(miss_y) < PLACE_TOL:
            achieved = {'height_share': round(m['height'], 4), 'width_share': round(m['width'], 4),
                        'centre': [round(v, 4) for v in m['centre']], 'box': m['box'], 'band': m['band']}
            return {'position': [round(v, 5) for v in position], 'aim': [round(v, 5) for v in _add(position, _mul(forward, depth))],
                    'achieved': achieved, 'steps': step, 'limited_by': limited}
        # size: the distance along the view; place: a sideways shift at the new distance (screen moves opposite).
        # After the first steps the distance moves part way: what is on screen of a band changes with it
        if step > 3:
            new_depth = depth + DAMPING * (new_depth - depth)
        position = _add(position, _mul(forward, depth - new_depth))
        position = _add(position, _mul(right, miss_x * 2 * tans[0] * new_depth))
        position = _sub(position, _mul(up, miss_y * 2 * tans[1] * new_depth))
    raise ValueError(f'FRAME_UNSOLVED: no camera position from view {view} reached height {want_h} at {target["centre"]} in '
                     f'{MAX_STEPS} steps (last: height {round(m["height"], 3)}, centre {[round(v, 3) for v in m["centre"]]}); '
                     'the focus may change shape with distance (occlusion) - try another direction')


EDGE_NAMES = ('left', 'top', 'right', 'bottom')   # box index 0..3


def _solve_edges(visible, start, view, edges, frame_basis, tans, aspect):
    """A focus the picture shows cut by the frame: only the edges it shows are placed - {left?, top?, right?, bottom?}
    in frame shares of the whole (unclipped) focus. Size comes from a pair (top and bottom: height, else left and
    right: width), place from every given edge. A booster entering from the top shows its sides and its bottom: its
    width sets the distance, its bottom how far into the frame it hangs."""
    forward, right, up = frame_basis
    given = {k: float(v) for k, v in edges.items() if k in EDGE_NAMES and v is not None}
    if 'top' in given and 'bottom' in given:
        axis, want = 'height', given['bottom'] - given['top']
    elif 'left' in given and 'right' in given:
        axis, want = 'width', given['right'] - given['left']
    else:
        raise ValueError(f'FRAME_UNSOLVED: the picture cuts the focus on both axes (edges {sorted(given)}): nothing gives its size; '
                         'mark the whole box, or frame a part that shows whole')
    if want <= 0:
        raise ValueError(f'FRAME_UNSOLVED: edges {given} enclose nothing')
    position = tuple(start)
    for step in range(1, MAX_STEPS + 1):
        points = visible(position)
        if not points:
            raise ValueError(f'FRAME_UNSOLVED: nothing of the focus is visible from {[round(v, 3) for v in position]} (view {view})')
        screen, behind = project(points, position, frame_basis, tans)
        if behind or not screen:
            position = _sub(position, _mul(forward, max(1.0, measure(screen, aspect)['depth'] if screen else 1.0)))
            continue
        xs, ys = sorted(p[0] for p in screen), sorted(p[1] for p in screen)
        trim = TRIM if len(screen) >= 200 else 0.0
        box = [_quantile(xs, trim), _quantile(ys, trim), _quantile(xs, 1 - trim), _quantile(ys, 1 - trim)]
        depth = sorted(p[2] for p in screen)[len(screen) // 2]
        have = box[3] - box[1] if axis == 'height' else box[2] - box[0]
        new_depth = depth * have / want if have > 0 else depth
        def miss(lo, hi, index_lo, index_hi):
            if lo in given and hi in given:
                return (box[index_lo] + box[index_hi]) / 2 - (given[lo] + given[hi]) / 2
            if lo in given:
                return box[index_lo] - given[lo]
            if hi in given:
                return box[index_hi] - given[hi]
            return 0.0
        miss_x, miss_y = miss('left', 'right', 0, 2), miss('top', 'bottom', 1, 3)
        if abs(have - want) <= SIZE_TOL * want and abs(miss_x) < PLACE_TOL and abs(miss_y) < PLACE_TOL:
            achieved = {'height_share': round(box[3] - box[1], 4), 'width_share': round(box[2] - box[0], 4),
                        'centre': [round((box[0] + box[2]) / 2, 4), round((box[1] + box[3]) / 2, 4)], 'box': [round(v, 4) for v in box],
                        'band': False, 'edges': given}
            return {'position': [round(v, 5) for v in position], 'aim': [round(v, 5) for v in _add(position, _mul(forward, depth))],
                    'achieved': achieved, 'steps': step, 'limited_by': None}
        if step > 3:
            new_depth = depth + DAMPING * (new_depth - depth)
        position = _add(position, _mul(forward, depth - new_depth))
        position = _add(position, _mul(right, miss_x * 2 * tans[0] * new_depth))
        position = _sub(position, _mul(up, miss_y * 2 * tans[1] * new_depth))
    raise ValueError(f'FRAME_UNSOLVED: no camera position from view {view} placed the edges {given} in {MAX_STEPS} steps '
                     f'(last box {[round(v, 3) for v in box]})')


def detail_px(screen, output_height, bins=24):
    """On-screen thickness of a detail in pixels: the median, over the columns it covers, of its vertical extent - a
    layer's thickness even when perspective tilts it (its box would count the tilt)."""
    if not screen:
        return 0.0
    xs = [p[0] for p in screen]
    lo, hi = min(xs), max(xs)
    width = (hi - lo) / bins or 1.0
    columns = {}
    for x, y, _ in screen:
        k = min(bins - 1, int((x - lo) / width))
        a, b = columns.get(k, (y, y))
        columns[k] = (min(a, y), max(b, y))
    keys = sorted(columns)
    mids = {k: sum(columns[k]) / 2 for k in keys}
    extents = []
    for i, k in enumerate(keys):   # a tilted layer also rises across one column: take that rise off (slope x column width)
        near = [n for n in (keys[i - 1] if i else None, keys[i + 1] if i + 1 < len(keys) else None) if n is not None]
        slope = max((abs(mids[n] - mids[k]) / abs(n - k) for n in near), default=0.0)
        a, b = columns[k]
        extents.append(max(0.0, b - a - slope))
    extents.sort()
    return round(extents[len(extents) // 2] * output_height, 2)
