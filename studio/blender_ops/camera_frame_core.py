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
RESCUE_STEPS = 24       # the second pass of a picture's solve (solve: when the first fails) brackets a swing: a few more
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


EDGE_NAMES = ('left', 'top', 'right', 'bottom')   # box index 0..3


# A picture's target (frames apply writes cut_edges; camera_moves_core.frame_segment rebuilds them for a frame written
# before) brings what the solve below needs to reach it from every view the picture allows (A4, 2026-10-14: close
# low pictures fitted a whole box past the vanishing point - no distance shows it; a step went 59-110 m and under the
# support; a size swung between two depths for 16 steps; an axis with no edge was never placed). What the picture says
# (its cut edges, its whole box, its long axis) is read on the first pass; what rescues a step - cap, clip, cycle,
# edge_depth, backtrack - acts only on a second pass (engaged starts with 'rescue'), run when every first pass of the
# pose's seeds failed to converge: a solve that worked before works the same. A target without cut_edges solves as it
# always did.
VP_MARGIN = 0.05        # a whole box running past its long axis's vanishing point by more than this, on both sides, is
                        # out of reach from that view: a straight body in front of the camera projects to one side of
                        # where its axis vanishes (its thickness passes it by about its own width, never more)
SECTION_HALF = 0.02     # a station of a long subject: its points within this share of its length of the cut
STATION_ALONG = 0.2     # stations place a long axis only when it runs along that screen axis at least this much
TRAVEL_MAX = 0.5        # one step moves the camera at most this share of the depth it measured (cap)
BEHIND_MAX = 0.5        # a cut picture goes on with the points in front when no more than this share is behind (clip) ...
NEAR_M, NEAR_SHARE = 0.05, 0.02   # ... behind meaning nearer than max(NEAR_M, NEAR_SHARE x the median depth in front)
EDGE_DEPTH_SHARE = 0.05 # a single edge's depth once its nearest point jumps (a near flange takes the edge) or the size
EDGE_DEPTH_MIN = 5      # swings: the median of this share of the points nearest it (at least EDGE_DEPTH_MIN of them)
EDGE_DEPTH_JUMP = 0.25  # ... a jump: the nearest point's depth changes by more than this share between steps
CYCLE_GROWTH = 0.5      # moves along the view alternating without shrinking below this ratio: the size swings (cycle)
CYCLE_WIDTH = 0.002     # a bracket narrower than this share of the depth that still misses the size: FRAME_SIZE_CYCLE
BACKTRACK_HALVINGS = 2  # a step landing under the support is halved this many times before the solve gives up


class FrameSolveError(ValueError):
    """A solve that did not reach the picture: the message starts with `code`; `data` says what stood in the way
    (FRAME_UNSOLVED, FRAME_UNREACHABLE: the vanishing point and the whole box, FRAME_SIZE_CYCLE: the bracket,
    FRAME_BELOW_SUPPORT: the support top, margin and camera z)."""

    def __init__(self, code, message, rescuable=False, **data):
        super().__init__(f'{code}: {message}')
        # rescuable: the steps did not converge, converged under the support, or took the camera where nothing shows -
        # the second pass may reach it; a focus not visible from the seed, edges enclosing nothing, nothing giving the
        # size cannot change by stepping otherwise
        self.code, self.data, self.rescuable = code, data, rescuable


def _length(a): return math.sqrt(_dot(a, a))


def axis_extent(points, direction):
    """(t_min, t_max) of the points along a unit direction, trimmed as measure() trims."""
    ts = sorted(_dot(p, direction) for p in points)
    trim = TRIM if len(ts) >= 200 else 0.0
    return _quantile(ts, trim), _quantile(ts, 1 - trim)


def vanishing_point(direction, frame_basis, tans):
    """Frame coordinates where a line along `direction` vanishes (its end going away from the camera), or None when
    it runs across the view (|direction . forward| < 1e-3: no vanishing point on screen)."""
    forward, right, up = frame_basis
    along = _dot(direction, forward)
    if abs(along) < 1e-3:
        return None
    d = direction if along > 0 else _mul(direction, -1.0)
    along = abs(along)
    return 0.5 + _dot(d, right) / (along * tans[0]) / 2, 0.5 - _dot(d, up) / (along * tans[1]) / 2


def screen_axis(direction, frame_basis):
    """'y' when a direction runs more up the screen than across it, else 'x'."""
    _forward, right, up = frame_basis
    return 'y' if abs(_dot(direction, up)) >= abs(_dot(direction, right)) else 'x'


def whole_box(target):
    """[x0, y0, x1, y1] of the whole box a picture was read for (x None when its width was not kept)."""
    (cx, cy), h, w = target['centre'], float(target['height_share']), target.get('width_share')
    return [cx - w / 2 if w else None, cy - h / 2, cx + w / 2 if w else None, cy + h / 2]


def whole_reachable(target, frame_basis, tans):
    """None when the picture's whole box can show from this view at some distance; else why: along the screen axis its
    long axis runs on, the box spans the axis's vanishing point by more than VP_MARGIN on both sides - every point of a
    straight body in front of the camera projects to one side of it."""
    axis = target.get('axis')
    vp = vanishing_point(axis['dir'], frame_basis, tans) if axis else None
    if vp is None:
        return None
    i = 1 if screen_axis(axis['dir'], frame_basis) == 'y' else 0
    box = whole_box(target)
    lo, hi = box[i], box[i + 2]
    if lo is None or not (lo < vp[i] - VP_MARGIN and hi > vp[i] + VP_MARGIN):
        return None
    return {'screen_axis': 'xy'[i], 'vanishing_point': [round(v, 4) for v in vp], 'whole': [round(lo, 4), round(hi, 4)]}


def section(points, axis, t, half):
    """The points of a long subject within `half` of t along its axis; its centreline point there when fewer than 3."""
    near = [p for p in points if abs(_dot(p, axis['dir']) - t) <= half]
    if len(near) >= 3:
        return near
    origin = axis['origin']
    return [_add(origin, _mul(axis['dir'], t - _dot(origin, axis['dir'])))]


def azimuth_for_tilt(elevation_deg, axis, tilt_deg, near_deg=0.0):
    """The azimuth an unrolled camera at elevation_deg shows a lying long axis from at tilt_deg on screen (+ its top
    leans right), nearest near_deg (the side the shot holds), or None when none does. A lying body's lean is where the
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
    if not roots:
        return None
    best = min(roots, key=lambda az: abs((az - near_deg + 180.0) % 360.0 - 180.0))
    return round(((best + 180.0) % 360.0) - 180.0, 4)


class _Course:
    """How the camera moves between steps. On a rescue pass a step goes at most TRAVEL_MAX of the depth (cap), and a step
    from above the support that lands under it is halved (backtrack), then refused (FRAME_BELOW_SUPPORT) - never lifted
    onto it; on either pass a pose that frames the picture under the support is refused. Keeps the mechanisms that acted
    (engaged) and the step log (trace)."""

    def __init__(self, rescue, floor, trace):
        self.rescue, self.floor, self.trace, self.engaged = rescue, floor, trace, []

    def engage(self, name):
        if name not in self.engaged:
            self.engaged.append(name)

    def below(self, position):
        """(top, margin) of the support a camera at position stands too low over, or None."""
        found = self.floor(position) if self.floor is not None else None
        if found is None or found[0] is None or position[2] >= found[0] + found[1]:
            return None
        return found

    def refuse(self, position, found, why, rescuable=False):
        top, margin = found
        raise FrameSolveError('FRAME_BELOW_SUPPORT', f'{why}: the camera at z {position[2]:.2f} m is {position[2] - top:+.2f} m from the '
                              f'top of the support under it (z {top:.2f} m); it stays {margin:g} m above',
                              rescuable=rescuable, top=round(top, 4), margin=margin, z=round(position[2], 4),
                              position=[round(v, 4) for v in position])

    def move(self, position, new, depth, cap=True):
        if self.rescue and cap and depth > 0:
            step = _sub(new, position)
            travel, limit = _length(step), TRAVEL_MAX * depth
            if travel > limit:
                new = _add(position, _mul(step, limit / travel))
                self.engage('cap')
        if self.rescue and self.floor is not None and self.below(position) is None:
            for _ in range(BACKTRACK_HALVINGS):
                if self.below(new) is None:
                    break
                new = _add(position, _mul(_sub(new, position), 0.5))
                self.engage('backtrack')
            found = self.below(new)
            if found is not None:
                self.refuse(new, found, 'every shorter step goes under the support too')
        return new

    def log(self, **row):
        if self.trace is not None:
            self.trace.append({'pass': 'rescue' if self.rescue else 'first', **{k: ([round(x, 5) for x in v] if isinstance(v, (list, tuple)) else round(v, 5) if isinstance(v, float) else v)
                               for k, v in row.items()}})


class _Size:
    """The distance along the view that gives the size. The step's own update (have / want) first; when it swings back
    and forth over the answer (moves alternating along the view without shrinking: 2026-10-14, close cut pictures swung
    between two depths, a near flange taking the edge every other step) a regula falsi (Illinois) inside the bracket
    the swings found, bisection when it leaves it; a bracket closed on a size it never reaches is FRAME_SIZE_CYCLE."""

    def __init__(self, on, course):
        self.on, self.course = on, course
        self.us, self.lo, self.hi, self.kept, self.cycling = [], None, None, None, False

    def reset(self):
        self.us, self.lo, self.hi, self.kept, self.cycling = [], None, None, None, False

    def depth(self, u, have, want, depth, proposed, placed=True):
        """The next depth along the view (proposed: the plain update) for a camera at u along the view whose focus
        shows `have` for `want`; placed: the place is reached too (a closed bracket while it still moves is a new one)."""
        if not self.on or have <= 0 or want <= 0:
            return proposed
        g = math.log(have / want)   # > 0: too large, the answer is further back (smaller u)
        if g > 0:
            if self.kept == 'hi' and self.lo is not None:
                self.lo = (self.lo[0], self.lo[1] / 2)   # Illinois: the end kept twice counts half
            self.hi, self.kept = (u, g), 'hi'
        elif g < 0:
            if self.kept == 'lo' and self.hi is not None:
                self.hi = (self.hi[0], self.hi[1] / 2)
            self.lo, self.kept = (u, g), 'lo'
        self.us.append(u)
        moves = [b - a for a, b in zip(self.us, self.us[1:])][-3:]
        if not self.cycling and self.lo and self.hi and len(moves) == 3 and all(
                a * b < 0 and abs(b) >= CYCLE_GROWTH * abs(a) for a, b in zip(moves, moves[1:])):
            self.cycling = True
            self.course.engage('cycle')
        if not self.cycling or not (self.lo and self.hi):
            return proposed
        (u_lo, g_lo), (u_hi, g_hi) = self.lo, self.hi
        if abs(u_hi - u_lo) < CYCLE_WIDTH * depth and abs(g) > math.log(1 + SIZE_TOL):
            if not placed:   # the place still moves: what the size measures moves with it - bracket again
                self.reset()
                return proposed
            raise FrameSolveError('FRAME_SIZE_CYCLE', f'the size swings between depths {abs(u_hi - u_lo):.3f} m apart and never reaches '
                                  f'{want:.3f} (shows {have:.3f}): what is seen of the focus changes there (a near part takes '
                                  'the edge) - frame a part that shows whole, or another view',
                                  bracket=[round(u_lo, 4), round(u_hi, 4)], have=round(have, 4), want=round(want, 4))
        guess = u_lo - g_lo * (u_hi - u_lo) / (g_hi - g_lo) if g_hi != g_lo else (u_lo + u_hi) / 2
        if not min(u_lo, u_hi) < guess < max(u_lo, u_hi):
            guess = (u_lo + u_hi) / 2
        return depth - (guess - u)


def _proj(points, position, frame_basis, tans):
    """[(world point, (x, y, depth))] of the points in front of the camera - project()'s arithmetic - and how many are
    behind."""
    forward, right, up = frame_basis
    tan_w, tan_h = tans
    out, behind = [], 0
    for p in points:
        v = _sub(p, position)
        z = _dot(v, forward)
        if z <= 1e-6:
            behind += 1
            continue
        out.append((p, (0.5 + _dot(v, right) / (z * tan_w) / 2, 0.5 - _dot(v, up) / (z * tan_h) / 2, z)))
    return out, behind


def _clip(pairs, behind, total):
    """The pairs a cut picture goes on with when some points are behind the camera: those beyond the near plane, when
    no more than BEHIND_MAX of all are nearer (else None: step back)."""
    if not pairs:
        return None
    depths = sorted(s[2] for _, s in pairs)
    near = max(NEAR_M, NEAR_SHARE * depths[len(depths) // 2])
    kept = [ps for ps in pairs if ps[1][2] >= near]
    return kept if kept and 1 - len(kept) / total <= BEHIND_MAX else None


def _picture(target):
    """The target is a read picture (frames apply / frame_segment): its cut edges are known."""
    return target.get('cut_edges') is not None


PASSES = ('first', 'rescue')   # a solve's passes in order (camera_moves._pose runs each seed's first pass before any rescue)


def solve(visible, start, view, target, lens_mm, aspect, sensor_mm=SENSOR_MM, roll_deg=0.0, floor=None, trace=None, passes=PASSES):
    """Camera position for `target` = {height_share, centre: [x, y], edges?, cut_edges?, width_share?, band_ok?,
    segment?, axis?} seen from view = (elevation_deg, azimuth_deg). visible(position) -> world points of the focus seen
    from there. floor(position) -> (support top z, margin) or None: no pose that frames the picture stands under it
    (FRAME_BELOW_SUPPORT). trace: a list the steps are logged to. passes: which of PASSES to run. Returns {position,
    aim, achieved, steps, limited_by, mode, engaged, pass} or raises FrameSolveError (a ValueError) whose code says what
    stood in the way.
    A picture's target (cut_edges known) shown whole (cut_edges []) is sized as an object: a band - running past the
    sides - only when the picture cuts both its sides (band_ok); its height is fitted, its width caps it otherwise
    (2026-10-14: a hull drawn whole, 20 times longer than tall, ran off both sides as a 'band'). A cut picture whose
    given edges are exactly the sides it does not cut is placed by its edges (_solve_edges), all four cut included.
    The rescue pass runs for a picture's target only after a first pass that could not converge (FrameSolveError
    .rescuable): the rescue mechanisms (_Course, _Size, _clip, _edge_depth) and RESCUE_STEPS, engaged naming 'rescue'
    first and then each that acted."""
    frame_basis = basis(*view, roll_deg=roll_deg)
    tans = tangents(lens_mm, aspect, sensor_mm)
    picture = _picture(target)
    cut = set(target.get('cut_edges') or [])
    shown = {k for k, v in (target.get('edges') or {}).items() if k in EDGE_NAMES and v is not None}
    how = _solve_edges if target.get('edges') or (picture and cut and set(EDGE_NAMES) - shown == cut) else _solve_plain
    errors = []
    for name in passes:
        rescue = name == 'rescue'
        if rescue and (not picture or (errors and not errors[-1].rescuable)):
            break
        try:
            if how is _solve_plain:
                return how(visible, start, view, target, frame_basis, tans, aspect, floor, trace, rescue=rescue)
            return _placed(visible, start, view, target, frame_basis, tans, aspect, floor, trace, rescue)
        except FrameSolveError as error:
            if errors:
                error.data.setdefault('first_pass', str(errors[0])[:300])
            errors.append(error)
            if not picture:
                break
    raise errors[-1]


def _placed(visible, start, view, target, frame_basis, tans, aspect, floor, trace, rescue):
    """One pass of _solve_edges for a picture: a whole-mode axis that has no edge placed by the whole box's centre
    (place_whole); when that does not converge, again leaving it to find its own place, as before A4 - a long band the
    picture runs past both sides of, whose span centre swings with perspective (review 2026-10-14: a 120 m slab seen
    from 30 deg aside solved before and not with place_whole) - accepted only when its visible box runs past the sides
    the picture cuts there."""
    try:
        return _solve_edges(visible, start, view, target, frame_basis, tans, aspect, floor, trace, rescue=rescue, place=True)
    except FrameSolveError as error:
        if not (error.rescuable and error.data.get('placed_whole')):
            raise
        placed = error
    try:
        return _solve_edges(visible, start, view, target, frame_basis, tans, aspect, floor, trace, rescue=rescue, place=False)
    except FrameSolveError as error:
        placed.data.setdefault('unplaced_pass', str(error)[:300])
        raise placed


def _solve_plain(visible, start, view, target, frame_basis, tans, aspect, floor, trace, rescue=False):
    """A focus framed whole (no edges): size by the distance along the view, place by a sideways shift."""
    forward, right, up = frame_basis
    picture = _picture(target)
    want_h, (want_x, want_y) = float(target['height_share']), target['centre']
    band_ok = not picture or bool(target.get('band_ok'))
    course = _Course(rescue, floor, trace)
    size = _Size(rescue, course)
    steps = RESCUE_STEPS if rescue else MAX_STEPS
    if rescue:
        course.engage('rescue')
    position, limited, m = tuple(start), None, None
    for step in range(1, steps + 1):
        points = visible(position)
        if not points:
            # from the seed itself, no step changes that; after steps, the steps took the camera there (rescuable)
            raise FrameSolveError('FRAME_UNSOLVED', f'nothing of the focus is visible from {[round(v, 3) for v in position]} '
                                  f'(view {view}); choose another direction or check what stands in front of it', rescuable=step > 1)
        screen, behind = project(points, position, frame_basis, tans)
        if behind or not screen:   # inside or past the focus: step back along the view and look again
            course.log(step=step, mode='plain', position=position, behind=behind)
            position = course.move(position, _sub(position, _mul(forward, max(1.0, measure(screen, aspect)['depth'] if screen else 1.0))),
                                   0.0, cap=False)
            continue
        m = measure(screen, aspect)
        band = m['band'] and band_ok
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
        size_ok = abs(m['height'] - want_h) <= SIZE_TOL * want_h or (limited and abs(new_depth - depth) / depth < SIZE_TOL / 2)
        course.log(step=step, mode='plain', position=position, depth=depth, have=m['height'], want=want_h, miss=[miss_x, miss_y], behind=0)
        if size_ok and abs(miss_x) < PLACE_TOL and abs(miss_y) < PLACE_TOL:
            found = course.below(position)
            if found is not None:
                course.refuse(position, found, 'the pose that frames the picture is under the support', rescuable=True)
            achieved = {'height_share': round(m['height'], 4), 'width_share': round(m['width'], 4),
                        'centre': [round(v, 4) for v in m['centre']], 'box': m['box'], 'band': band}
            return {'position': [round(v, 5) for v in position], 'aim': [round(v, 5) for v in _add(position, _mul(forward, depth))],
                    'achieved': achieved, 'steps': step, 'limited_by': limited, 'mode': 'plain', 'engaged': course.engaged,
                    'pass': 'rescue' if rescue else 'first'}
        # size: the distance along the view; place: a sideways shift at the new distance (screen moves opposite).
        # After the first steps the distance moves part way: what is on screen of a band changes with it
        if step > 3:
            new_depth = depth + DAMPING * (new_depth - depth)
        have, want = {'width': (m['width'], 1 - 2 * MARGIN), 'height': (m['height'], 1 - 2 * MARGIN)}.get(limited, (m['height'], want_h))
        new_depth = size.depth(_dot(position, forward), have, want, depth, new_depth,
                               placed=abs(miss_x) < PLACE_TOL and abs(miss_y) < PLACE_TOL)
        moved = _add(position, _mul(forward, depth - new_depth))
        moved = _add(moved, _mul(right, miss_x * 2 * tans[0] * new_depth))
        moved = _sub(moved, _mul(up, miss_y * 2 * tans[1] * new_depth))
        position = course.move(position, moved, depth)
    last = (f"last: height {round(m['height'], 3)}, centre {[round(v, 3) for v in m['centre']]}" if m else
            'every step stood inside or past the focus')
    raise FrameSolveError('FRAME_UNSOLVED', f'no camera position from view {view} reached height {want_h} at {target["centre"]} in '
                          f'{steps} steps ({last}); the focus may change shape with distance (occlusion) - try another direction',
                          rescuable=True)


def _edge_depth(pool, coord, edge, previous, course, swinging):
    """The depth a single given edge moves sideways at: its nearest point's, as always; the median of the
    EDGE_DEPTH_SHARE nearest (at least EDGE_DEPTH_MIN) for a picture once the size swings or the nearest point's depth
    jumps by more than EDGE_DEPTH_JUMP (a near flange takes the edge every other step). Returns (depth, nearest)."""
    nearest = min(pool, key=lambda p: abs(p[coord] - edge))[2]
    if course.rescue and (swinging or (previous and abs(nearest - previous) > EDGE_DEPTH_JUMP * previous)):
        ranked = sorted(pool, key=lambda p: abs(p[coord] - edge))
        take = ranked[:max(EDGE_DEPTH_MIN, round(EDGE_DEPTH_SHARE * len(ranked)))]
        depths = sorted(p[2] for p in take)
        course.engage('edge_depth')
        return depths[len(depths) // 2], nearest
    return nearest, nearest


def _segment_plan(target, frame_basis, given):
    """The long-axis lines a cut picture is placed by when its whole box is out of reach (segment): per screen side of
    the long axis, the edge it shows (uncut) or the station where the frame cuts it - target.segment.cut_at, the share
    of the subject's length from the end shown at the top (left) - [{side, frame, edge | t}], the screen axis, and
    why not (str) when the axis does not run along the screen or fewer than two lines exist."""
    axis, seg = target['axis'], target.get('segment') or {}
    along_screen = seg.get('screen_axis') or screen_axis(axis['dir'], frame_basis)
    _forward, right, up = frame_basis
    along = _dot(axis['dir'], up if along_screen == 'y' else right)
    if abs(along) < STATION_ALONG:
        return f"its long axis runs {math.degrees(math.acos(min(1.0, abs(along)))):.0f} deg off the screen's {along_screen} axis"
    t0, t1 = axis['t']
    first = (t1 if along > 0 else t0) if along_screen == 'y' else (t0 if along > 0 else t1)   # the end shown at the top / left
    sign = -1.0 if first == t1 else 1.0
    sides = ('top', 'bottom') if along_screen == 'y' else ('left', 'right')
    lines = []
    for side, frame_edge, end in ((sides[0], 0.0, first), (sides[1], 1.0, first + sign * (t1 - t0))):
        if side in given:
            lines.append({'side': side, 'frame': given[side], 'edge': True, 't': end})
        elif side in (seg.get('cut_at') or {}):
            lines.append({'side': side, 'frame': frame_edge, 'edge': False, 't': first + sign * float(seg['cut_at'][side]) * (t1 - t0)})
    if len(lines) < 2 or all(line['edge'] for line in lines):
        return f"the picture gives {len([l for l in lines if not l['edge']])} station(s) and {len([l for l in lines if l['edge']])} edge(s) along it"
    return {'axis': along_screen, 'lines': lines, 'half': SECTION_HALF * (t1 - t0),
            'want': abs(lines[1]['frame'] - lines[0]['frame'])}


def _solve_edges(visible, start, view, target, frame_basis, tans, aspect, floor=None, trace=None, rescue=False, place=True):
    """A focus the picture shows cut by the frame: only the edges it shows are placed - {left?, top?, right?, bottom?}
    in frame shares of the whole (unclipped) focus. Size comes from a pair (top and bottom: height, else left and
    right: width), place from every given edge. A booster entering from the top shows its sides and its bottom: its
    width sets the distance, its bottom how far into the frame it hangs. Cut on both axes (a close-up past two edges), no
    pair gives the size: it comes from the whole box the picture was read for (the target's height_share), the place
    still from the edges it shows (2026-10-13: the r10 close-ups - a foot, a leg, the engine bay - were refused).
    A picture's target (cut_edges known; A4, 2026-10-14) also: a whole box past its long axis's vanishing point is out of
    reach at every distance (whole_reachable) - then the frame's cut is matched as stations on the subject (segment:
    where the frame edge crosses it, a share of its length), or FRAME_UNREACHABLE; an axis with no edge is placed by the
    whole box's centre (place_whole; with place=False a whole-mode axis is left to find its own place and the result
    is refused when the visible box stops short of a side the picture cuts there - _placed); points behind the
    camera are clipped when few (clip); see _Course and _Size."""
    forward, right, up = frame_basis
    picture = _picture(target)
    given = {k: float(v) for k, v in (target.get('edges') or {}).items() if k in EDGE_NAMES and v is not None}
    whole_h = target.get('height_share')
    course = _Course(rescue, floor, trace)
    size = _Size(rescue, course)
    steps = RESCUE_STEPS if rescue else MAX_STEPS
    if rescue:
        course.engage('rescue')
    plan = None
    if 'top' in given and 'bottom' in given:
        axis, want = 'height', given['bottom'] - given['top']
    elif 'left' in given and 'right' in given:
        axis, want = 'width', given['right'] - given['left']
    else:
        far = whole_reachable(target, frame_basis, tans) if picture and whole_h else None
        if far:
            plan = _segment_plan(target, frame_basis, given)
            if isinstance(plan, str):
                raise FrameSolveError('FRAME_UNREACHABLE', f"from view {view} the picture's whole box {far['whole']} (frame {far['screen_axis']}) "
                                      f"spans where the subject's long axis vanishes ({far['vanishing_point']}): no distance shows it whole, "
                                      f'and {plan} to place the cut instead - choose another view or lens, or read the picture again',
                                      **far, view=list(view), why=plan)
            axis, want = 'segment', plan['want']
        elif whole_h:
            axis, want = 'whole', float(whole_h)
        else:
            raise FrameSolveError('FRAME_UNSOLVED', f'the picture cuts the focus on both axes (edges {sorted(given)}): nothing gives its '
                                  'size; mark the whole box, or frame a part that shows whole')
    if want <= 0:
        raise FrameSolveError('FRAME_UNSOLVED', f'edges {given} enclose nothing')
    cuts = bool(target.get('cut_edges'))
    centre = target.get('centre') or [0.5, 0.5]
    position, box, near_depth, stations, unplaced = tuple(start), None, {}, None, set()
    for step in range(1, steps + 1):
        points = visible(position)
        if not points:
            raise FrameSolveError('FRAME_UNSOLVED', f'nothing of the focus is visible from {[round(v, 3) for v in position]} (view {view})',
                                  rescuable=step > 1, **({'placed_whole': True} if 'place_whole' in course.engaged else {}))
        pairs, behind = _proj(points, position, frame_basis, tans)
        if behind and rescue and cuts and axis != 'whole':
            kept = _clip(pairs, behind, len(points))
            if kept is not None:
                pairs, behind = kept, 0
                course.engage('clip')
        screen = [s for _, s in pairs]
        if behind or not screen:
            old = max(1.0, measure(screen, aspect)['depth'] if screen else 1.0)
            back = min(old, TRAVEL_MAX * measure(screen, aspect)['depth']) if rescue and screen else old
            if back < old:
                course.engage('cap')
            course.log(step=step, mode=axis, position=position, behind=behind)
            position = course.move(position, _sub(position, _mul(forward, back)), 0.0, cap=False)
            continue
        # A cut body is sized by the strip the picture shows, not by wide feet below it or fins above it.
        across = [p for p in screen if ('top' in given and 'bottom' in given) or 0 <= p[1] <= 1] or screen
        vertical = [p for p in screen if ('left' in given and 'right' in given) or 0 <= p[0] <= 1] or screen
        xs, ys = sorted(p[0] for p in across), sorted(p[1] for p in vertical)
        trim = TRIM if len(screen) >= 200 else 0.0
        box = [_quantile(xs, trim), _quantile(ys, trim), _quantile(xs, 1 - trim), _quantile(ys, 1 - trim)]
        long_i = None
        if axis == 'segment':
            long_i = 1 if plan['axis'] == 'y' else 0
            world = [w for w, _ in pairs]
            lines = []
            for line in plan['lines']:
                if line['edge']:
                    pool = vertical if long_i == 1 else across
                    edge = box[long_i + (2 if line['side'] in ('bottom', 'right') else 0)]
                    ranked = sorted(pool, key=lambda p: abs(p[long_i] - edge))[:max(EDGE_DEPTH_MIN, round(EDGE_DEPTH_SHARE * len(pool)))]
                    lines.append({**line, 'at': edge, 'depths': [p[2] for p in ranked]})
                    continue
                cut_pts, _ = _proj(section(world, target['axis'], line['t'], plan['half']), position, frame_basis, tans)
                if not cut_pts:
                    break
                coords = sorted(s[long_i] for _, s in cut_pts)
                lines.append({**line, 'at': coords[len(coords) // 2], 'depths': [s[2] for _, s in cut_pts]})
            if len(lines) < len(plan['lines']):   # a station behind the camera: step back
                back = max(1.0, measure(screen, aspect)['depth'])
                back = min(back, TRAVEL_MAX * measure(screen, aspect)['depth']) if rescue else back
                course.log(step=step, mode=axis, position=position, behind='station')
                position = course.move(position, _sub(position, _mul(forward, back)), 0.0, cap=False)
                continue
            # each line's own depth, the two averaged: a median over both swaps from one line to the other as their
            # point counts change (a near foot, a far station)
            depth = sum(sorted(line['depths'])[len(line['depths']) // 2] for line in lines) / len(lines)
            have = abs(lines[1]['at'] - lines[0]['at'])
            stations = {line['side']: round(line['at'], 4) for line in lines if not line['edge']}
        else:
            sized = across if axis == 'width' else screen if axis == 'whole' else vertical
            depth = sorted(p[2] for p in sized)[len(sized) // 2]
            if axis == 'whole':   # the whole focus, past the frame too, as the whole box was read
                every = sorted(p[1] for p in screen)
                have = _quantile(every, 1 - trim) - _quantile(every, trim)
            else:
                have = box[3] - box[1] if axis == 'height' else box[2] - box[0]
        new_depth = depth * have / want if have > 0 else depth

        def miss(lo, hi, index_lo, index_hi):
            if lo in given and hi in given:
                return (box[index_lo] + box[index_hi]) / 2 - (given[lo] + given[hi]) / 2
            if lo in given:
                return box[index_lo] - given[lo]
            if hi in given:
                return box[index_hi] - given[hi]
            return None
        misses = [miss('left', 'right', 0, 2), miss('top', 'bottom', 1, 3)]
        if axis == 'segment' and not any(line['edge'] for line in lines):   # both ends cut: the stations' middle
            misses[long_i] = sum(line['at'] for line in lines) / 2 - sum(line['frame'] for line in lines) / 2
        for i in (0, 1):
            if misses[i] is not None:
                continue
            if not picture or axis in ('height', 'width') or (axis == 'whole' and i == 0 and not target.get('width_share')):
                # an axis with no edge, sized by the other axis's pair, runs past both its sides - a strip, as a band:
                # nothing places it; nor does the whole box across when the picture's width was not kept (a frame
                # written before: its span centre is a guess, review 2026-10-14); a target that is no picture never placed it
                misses[i] = 0.0
                continue
            if axis == 'whole' and not place:   # left to find its own place first (_placed)
                unplaced.add(i)
                misses[i] = 0.0
                continue
            course.engage('place_whole')
            if axis == 'segment':   # the cross axis: the middle station at the whole box's centre
                t_mid = sum(line['t'] for line in lines) / 2
                mid, _ = _proj(section([w for w, _ in pairs], target['axis'], t_mid, plan['half']), position, frame_basis, tans)
                coords = sorted(s[i] for _, s in mid) or [centre[i]]
                misses[i] = coords[len(coords) // 2] - centre[i]
            else:   # the whole focus's span, past the frame too, centred where the whole box is
                span = sorted(p[i] for p in screen)
                misses[i] = (_quantile(span, trim) + _quantile(span, 1 - trim)) / 2 - centre[i]
        miss_x, miss_y = misses
        course.log(step=step, mode=axis, position=position, depth=depth, have=have, want=want, miss=[miss_x, miss_y], behind=behind)
        if abs(have - want) <= SIZE_TOL * want and abs(miss_x) < PLACE_TOL and abs(miss_y) < PLACE_TOL:
            short = [EDGE_NAMES[i + k] for i in sorted(unplaced) for k in (0, 2)
                     if (box[i + k] > PLACE_TOL if k == 0 else box[i + k] < 1 - PLACE_TOL)]
            if short:   # the picture runs the focus past these sides; left to itself it stops short of them
                raise FrameSolveError('FRAME_UNSOLVED', f'from view {view} the focus stops short of the {short} frame edge(s) the picture '
                                      f'runs it past (box {[round(v, 3) for v in box]})', unplaced=sorted(unplaced))
            found = course.below(position)
            if found is not None:
                course.refuse(position, found, 'the pose that frames the picture is under the support', rescuable=True)
            achieved = {'height_share': round(box[3] - box[1], 4), 'width_share': round(box[2] - box[0], 4),
                        'centre': [round((box[0] + box[2]) / 2, 4), round((box[1] + box[3]) / 2, 4)], 'box': [round(v, 4) for v in box],
                        'band': False, 'edges': given, **({'stations': stations} if stations else {})}
            return {'position': [round(v, 5) for v in position], 'aim': [round(v, 5) for v in _add(position, _mul(forward, depth))],
                    'achieved': achieved, 'steps': step, 'limited_by': None, 'mode': axis, 'engaged': course.engaged,
                    'pass': 'rescue' if rescue else 'first'}
        if step > 3:
            new_depth = depth + DAMPING * (new_depth - depth)
        new_depth = size.depth(_dot(position, forward), have, want, depth, new_depth,
                               placed=abs(miss_x) < PLACE_TOL and abs(miss_y) < PLACE_TOL)
        moved = _add(position, _mul(forward, depth - new_depth))
        x_depth, y_depth = depth, depth
        if ('left' in given) != ('right' in given) and long_i != 0:
            x_depth, near_depth['x'] = _edge_depth(across, 0, box[0] if 'left' in given else box[2], near_depth.get('x'), course, size.cycling)
        if ('top' in given) != ('bottom' in given) and long_i != 1:
            y_depth, near_depth['y'] = _edge_depth(vertical, 1, box[1] if 'top' in given else box[3], near_depth.get('y'), course, size.cycling)
        if long_i is not None and any(line['edge'] for line in lines):   # the long axis placed by its uncut edge: at that edge's depth
            edge_depths = sorted(next(line for line in lines if line['edge'])['depths'])
            x_depth, y_depth = (x_depth, edge_depths[len(edge_depths) // 2]) if long_i == 1 else (edge_depths[len(edge_depths) // 2], y_depth)
        # A single near edge needs its own depth; the whole body's median can overshoot it behind the camera.
        moved = _add(moved, _mul(right, miss_x * 2 * tans[0] * max(1e-6, x_depth + new_depth - depth)))
        moved = _sub(moved, _mul(up, miss_y * 2 * tans[1] * max(1e-6, y_depth + new_depth - depth)))
        sideways = _length(_sub(moved, _add(position, _mul(forward, depth - new_depth))))
        position = course.move(position, moved, depth)
        if size.on and sideways > 0.25 * depth:   # a long move sideways changes what the size measures: a new bracket
            size.reset()
    last = f'last box {[round(v, 3) for v in box]}' if box else 'every step stood inside or past the focus'
    raise FrameSolveError('FRAME_UNSOLVED', f'no camera position from view {view} placed the edges {given} ({axis}) in {steps} steps ({last})',
                          rescuable=True, mode=axis, **({'unplaced': sorted(unplaced)} if unplaced else {}),
                          **({'placed_whole': True} if 'place_whole' in course.engaged else {}))


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
