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


def basis(elevation_deg, azimuth_deg, roll_deg=0.0):
    """(forward, right, up) of a camera looking at its subject from (elevation, azimuth): azimuth 0 = camera on -Y
    looking +Y, the same convention as camera_moves_core._dir; roll matches camera_rig_core.look_rotation."""
    a, e = math.radians(azimuth_deg), math.radians(elevation_deg)
    toward_camera = (math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e))
    forward = _mul(toward_camera, -1.0)
    across = _cross(forward, (0.0, 0.0, 1.0))
    # straight down or up the view has no horizontal right: the azimuth's own, which is the limit as the view tilts there
    right = _norm(across) if _dot(across, across) > 1e-18 else (math.cos(a), math.sin(a), 0.0)
    up = _cross(right, forward)
    if not roll_deg:
        return forward, right, up
    r = math.radians(roll_deg)
    return (forward, tuple(a * math.cos(r) + b * math.sin(r) for a, b in zip(right, up)),
            tuple(b * math.cos(r) - a * math.sin(r) for a, b in zip(right, up)))


def view_of(eye, aim):
    """(elevation_deg, azimuth_deg) of a camera at eye looking at aim, in basis()'s convention."""
    f = _norm(_sub(aim, eye))
    return math.degrees(math.asin(max(-1.0, min(1.0, -f[2])))), math.degrees(math.atan2(-f[0], f[1]))


def screen_tilt_deg(eye, aim, axis, roll_deg=0.0):
    """How far a subject's long axis leans on screen from the vertical, in degrees (+ its top leans right), for a camera
    at eye looking at aim and rolled by roll_deg about its view axis (camera_rig_core.look_rotation's roll: + turns the
    picture's content clockwise, so it adds to the lean). The long axis is undirected: an inverted subject has the
    same lean, rather than requiring a 180-degree camera roll. None when the axis points along the view.
    Why (2026-10-11): s06's last picture stands the booster upright; the scene flies it 15 deg over, and with no roll
    the built frame could only show it leaning."""
    f = _norm(_sub(aim, eye))
    across = _cross(f, (0.0, 0.0, 1.0))
    right = _norm(across) if _dot(across, across) > 1e-18 else (1.0, 0.0, 0.0)   # a vertical view: look_rotation's fallback
    up = _cross(right, f)
    r = math.radians(roll_deg)
    right_r = tuple(a * math.cos(r) + b * math.sin(r) for a, b in zip(right, up))
    up_r = tuple(b * math.cos(r) - a * math.sin(r) for a, b in zip(right, up))
    x, y = _dot(axis, right_r), _dot(axis, up_r)
    if math.hypot(x, y) < 1e-6:
        return None
    return ((math.degrees(math.atan2(x, y)) + 90.0) % 180.0) - 90.0


def seen_lean(axis, eye, centre, right, up):
    """How a subject's long axis shows to a camera standing at eye with screen axes right / up (a built camera's own,
    rolled or shifted as it is): {'tilt_deg': its lean on screen as screen_tilt_deg measures it (None when it points
    along the view), 'sight_deg': its angle to the line of sight from the camera to the subject's centre, 0 end-on to 90
    side-on}. Why (2026-10-14): a cylinder turned down its axis read a -30 deg lean the picture's -23 matched, while the
    frame showed a disc: a lean seen end-on says nothing."""
    if math.sqrt(_dot(axis, axis)) < 1e-9 or math.sqrt(_dot(_sub(centre, eye), _sub(centre, eye))) < 1e-9:
        return {'tilt_deg': None, 'sight_deg': None}   # no axis, or the camera at the subject's centre: nothing to say
    a = _norm(axis)
    sight = _norm(_sub(centre, eye))
    angle = math.degrees(math.acos(min(1.0, abs(_dot(a, sight)))))
    x, y = _dot(a, right), _dot(a, up)
    tilt = None if math.hypot(x, y) < 1e-6 else ((math.degrees(math.atan2(x, y)) + 90.0) % 180.0) - 90.0
    return {'tilt_deg': None if tilt is None else round(tilt, 2), 'sight_deg': round(angle, 2)}


ATMOSPHERE_NEAR_M = 1.0   # a camera nearer an atmosphere volume than this stands in it (measured 2026-10-14: well-framed
                          # builds keep 38 m and more from their volumes; one 0.85 m beside a volume and one inside it showed
                          # the volume, 0.61 and 0.997 of the frame); a piece smaller than this is a speck, not a volume


def box_distance(box, point):
    """How far `point` is from the box (lo, hi); 0 inside it."""
    return math.sqrt(sum(max(lo - p, 0.0, p - hi) ** 2 for lo, hi, p in zip(box[0], box[1], point)))


def box_entry(box, origin, direction, distance):
    """Where along the ray (origin, unit direction) it is first inside the box, within `distance`; 0 when it starts
    inside, None when it never is (slab test)."""
    near, far = 0.0, distance
    for lo, hi, o, d in zip(box[0], box[1], origin, direction):
        if abs(d) < 1e-12:
            if not lo <= o <= hi:
                return None
            continue
        t0, t1 = sorted(((lo - o) / d, (hi - o) / d))
        near, far = max(near, t0), min(far, t1)
        if near > far:
            return None
    return near


def roll_for_tilt(eye, aim, axis, tilt_deg):
    """The roll that shows the axis at tilt_deg on screen (rolling turns the whole picture, so it adds one to one)."""
    now = screen_tilt_deg(eye, aim, axis)
    if now is None or tilt_deg is None:
        return None
    return ((tilt_deg - now + 90.0) % 180.0) - 90.0


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


def screen_box(box, eye, view, lens_mm, aspect, roll_deg=0.0):
    """[x0, y0, x1, y1] (frame coordinates, not clipped) a world box's 8 corners project to from a camera at `eye`
    looking along `view` (elevation, azimuth): a cheap screen place for a thing round the subject - the corners in
    front of the camera only (a box the camera stands in or beside reads larger than it shows); None when all are
    behind it."""
    corners = [(x, y, z) for x in (box[0][0], box[1][0]) for y in (box[0][1], box[1][1]) for z in (box[0][2], box[1][2])]
    screen, _behind = project(corners, eye, basis(view[0], view[1], roll_deg=roll_deg), tangents(lens_mm, aspect))
    if not screen:
        return None
    xs, ys = [s[0] for s in screen], [s[1] for s in screen]
    return [round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4)]


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


class FrameSolveError(ValueError):
    """A solve that did not reach its framing: the message starts with `code`; `data` says what stood in the way
    (FRAME_UNSOLVED; FRAME_BELOW_SUPPORT: the support top, margin and camera z)."""

    def __init__(self, code, message, **data):
        super().__init__(f'{code}: {message}')
        self.code, self.data = code, data


# The sight angles (a long axis to the line of sight: 0 end-on, 90 across the view) each read side allows (frame_targets.SIDES).
# A lean the camera explains only from outside its picture's read side is the subject's own pose - nose down, sinking -
# not where the camera stands (E2E 2026-10-21: a hull drawn side-on and trimmed 12 deg down turned the camera 50 deg
# toward end-on). The bands overlap: a reader's side is a word, not a measure. Below/above say nothing of the sight.
SIDE_SIGHT_DEG = {'side': (55.0, 90.0), 'three_quarter': (20.0, 75.0), 'front': (0.0, 40.0), 'back': (0.0, 40.0)}


def azimuth_for_tilt(elevation_deg, axis, tilt_deg, near_deg=0.0, side=None):
    """The azimuth an unrolled camera at elevation_deg shows a lying long axis from at tilt_deg on screen (+ its top
    leans right), nearest near_deg (the side the shot holds) and within the read side's sight band (SIDE_SIGHT_DEG), or
    None when none does. A lying body's lean is where the
    camera stands around it, not a roll (2026-10-14: a -73 deg lean of a lying hull asked a 73 deg roll). Roots by a
    2-degree scan and bisection; they come in pairs half a turn apart (the same lean from the other side), so the
    nearest to the held side is the one."""
    def lean(az):
        t = screen_tilt_deg(_mul(basis(elevation_deg, az)[0], -1.0), (0.0, 0.0, 0.0), axis)
        return None if t is None else ((t - tilt_deg + 90.0) % 180.0) - 90.0
    roots, last = [], None
    for k in range(181):
        az = -180.0 + 2.0 * k
        f = lean(az)
        if f is not None and last is not None:
            a, fa = last
            if fa == 0.0:
                roots.append(a)
            elif fa * f < 0 and abs(fa - f) < 90.0:   # a crossing, not the lean wrapping at +-90
                lo, hi, f_lo = a, az, fa
                for _ in range(40):
                    mid = (lo + hi) / 2
                    fm = lean(mid)
                    if fm is None:
                        break
                    if f_lo * fm <= 0:
                        hi = mid
                    else:
                        lo, f_lo = mid, fm
                roots.append((lo + hi) / 2)
        last = (az, f) if f is not None else None
    band = SIDE_SIGHT_DEG.get(side)
    if band and roots:   # only the views that show the side the picture was read from
        a = _norm(axis)
        roots = [az for az in roots if band[0] <= math.degrees(math.acos(min(1.0, abs(_dot(a, basis(elevation_deg, az)[0]))))) <= band[1]]
    if not roots:
        return None
    best = min(roots, key=lambda az: abs((az - near_deg + 180.0) % 360.0 - 180.0))
    return round(((best + 180.0) % 360.0) - 180.0, 4)


class _Course:
    """Where the steps may stand: a pose that frames the focus under the support is refused (FRAME_BELOW_SUPPORT) -
    never lifted onto it. Keeps the step log (trace)."""

    def __init__(self, floor, trace):
        self.floor, self.trace = floor, trace

    def below(self, position):
        """(top, margin) of the support a camera at position stands too low over, or None."""
        found = self.floor(position) if self.floor is not None else None
        if found is None or found[0] is None or position[2] >= found[0] + found[1]:
            return None
        return found

    def refuse(self, position, found, why):
        top, margin = found
        raise FrameSolveError('FRAME_BELOW_SUPPORT', f'{why}: the camera at z {position[2]:.2f} m is {position[2] - top:+.2f} m from the '
                              f'top of the support under it (z {top:.2f} m); it stays {margin:g} m above',
                              top=round(top, 4), margin=margin, z=round(position[2], 4), position=[round(v, 4) for v in position])

    def log(self, **row):
        if self.trace is not None:
            self.trace.append({k: ([round(x, 5) for x in v] if isinstance(v, (list, tuple)) else round(v, 5) if isinstance(v, float) else v)
                               for k, v in row.items()})


def solve(visible, start, view, target, lens_mm, aspect, sensor_mm=SENSOR_MM, roll_deg=0.0, floor=None, trace=None, tol=None):
    """Camera position for `target` = {height_share, centre: [x, y]} seen from view = (elevation_deg, azimuth_deg): the
    focus framed whole, sized by the distance along the view and placed by a sideways shift. visible(position) -> world
    points of the focus seen from there. floor(position) -> (support top z, margin) or None: no pose that frames the
    focus stands under it (FRAME_BELOW_SUPPORT). trace: a list the steps are logged to. Returns {position, aim,
    achieved, steps, limited_by, mode} or raises FrameSolveError (a ValueError) whose code says what stood in
    the way. tol: {size, place, steps} for a framing kept by its intent rather than its numbers (camera_moves
    _intent_pose: the size class's band) - None: SIZE_TOL, PLACE_TOL, MAX_STEPS.
    A frame target is no longer matched to its picture's numbers here (2026-10-17: the exact search, its cut edges,
    stations and rescue pass were removed when the intent camera became the only one)."""
    return _solve_plain(visible, start, view, target, basis(*view, roll_deg=roll_deg), tangents(lens_mm, aspect, sensor_mm), aspect,
                        floor, trace, tol)


def _solve_plain(visible, start, view, target, frame_basis, tans, aspect, floor, trace, tol=None):
    """A focus framed whole: size by the distance along the view, place by a sideways shift."""
    size_tol, place_tol = (tol or {}).get('size', SIZE_TOL), (tol or {}).get('place', PLACE_TOL)
    forward, right, up = frame_basis
    want_h, (want_x, want_y) = float(target['height_share']), target['centre']
    course = _Course(floor, trace)
    steps = (tol or {}).get('steps') or MAX_STEPS
    position, limited, m = tuple(start), None, None
    for step in range(1, steps + 1):
        points = visible(position)
        if not points:
            raise FrameSolveError('FRAME_UNSOLVED', f'nothing of the focus is visible from {[round(v, 3) for v in position]} '
                                  f'(view {view}); choose another direction or check what stands in front of it')
        screen, behind = project(points, position, frame_basis, tans)
        if behind or not screen:   # inside or past the focus: step back along the view and look again
            course.log(step=step, mode='plain', position=position, behind=behind)
            position = _sub(position, _mul(forward, max(1.0, measure(screen, aspect)['depth'] if screen else 1.0)))
            continue
        m = measure(screen, aspect)
        band = m['band']
        depth = m['depth']
        new_depth = depth * m['height'] / want_h if m['height'] > 0 else depth
        limited = None
        if not band and m['width'] * depth / new_depth > 1 - 2 * MARGIN:   # an object fits whole: width wins
            new_depth, limited = depth * m['width'] / (1 - 2 * MARGIN), 'width'
        if want_h <= 1 - 2 * MARGIN and m['height'] * depth / new_depth > 1 - 2 * MARGIN:
            new_depth, limited = depth * m['height'] / (1 - 2 * MARGIN), 'height'
        # a band already running past both side edges has no sideways place to choose (and its box centre swings with
        # the nearest points): only its height and vertical place are solved; until it runs across, it is steered
        across = band and m['box'][0] <= 0.0 and m['box'][2] >= 1.0
        miss_x = 0.0 if across else m['centre'][0] - want_x
        miss_y = m['centre'][1] - want_y
        size_ok = abs(m['height'] - want_h) <= size_tol * want_h or (limited and abs(new_depth - depth) / depth < size_tol / 2)
        course.log(step=step, mode='plain', position=position, depth=depth, have=m['height'], want=want_h, miss=[miss_x, miss_y], behind=0)
        if size_ok and abs(miss_x) < place_tol and abs(miss_y) < place_tol:
            found = course.below(position)
            if found is not None:
                course.refuse(position, found, 'the pose that frames the focus is under the support')
            achieved = {'height_share': round(m['height'], 4), 'width_share': round(m['width'], 4),
                        'centre': [round(v, 4) for v in m['centre']], 'box': m['box'], 'band': band}
            return {'position': [round(v, 5) for v in position], 'aim': [round(v, 5) for v in _add(position, _mul(forward, depth))],
                    'achieved': achieved, 'steps': step, 'limited_by': limited, 'mode': 'plain'}
        # size: the distance along the view; place: a sideways shift at the new distance (screen moves opposite).
        # After the first steps the distance moves part way: what is on screen of a band changes with it
        if step > 3:
            new_depth = depth + DAMPING * (new_depth - depth)
        moved = _add(position, _mul(forward, depth - new_depth))
        moved = _add(moved, _mul(right, miss_x * 2 * tans[0] * new_depth))
        position = _sub(moved, _mul(up, miss_y * 2 * tans[1] * new_depth))
    last = (f"last: height {round(m['height'], 3)}, centre {[round(v, 3) for v in m['centre']]}" if m else
            'every step stood inside or past the focus')
    raise FrameSolveError('FRAME_UNSOLVED', f'no camera position from view {view} reached height {want_h} at {target["centre"]} in '
                          f'{steps} steps ({last}); the focus may change shape with distance (occlusion) - try another direction')



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
