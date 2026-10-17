"""Semantic camera moves -> camera path + aim (pure math, host-tested; camera_moves.py does the Blender side).

The general move is `waypoints`: the camera passes through ordered points (resolved anchors or explicit
coordinates) and aims at a target (a resolved point) or along the path. Every named move only *generates*
waypoints from what it is about - an opening to dive through, a gap between two members, a section to
descend - so a new move needs no new machinery (N+1: express it as waypoints). Distances default to the
geometry (box sizes), never to fixed scene numbers. Timing (how the camera covers the path) is separate:
camera_rig_core.timing_curve, filled from the motion style.

Geometry inputs are resolved by the caller: points {ref: (x, y, z)}, boxes {ref: ((x0, y0, z0), (x1, y1, z1))}.
"""
from __future__ import annotations

import math

MOVES = ('waypoints', 'push_in', 'dive_through', 'pass_between', 'descend_levels', 'crane', 'orbit_reveal', 'section_push',
         'turntable', 'slide', 'macro_push', 'pull_back', 'tilt', 'frame_to_frame')
STEP_M = 0.25
SENSOR_MM = 36.0
REQUIRED, DERIVED = 'required', 'derived'
# Every parameter each move reads, with its default: one table the planner, storyboard edits and lint all use, so
# no value of a move is out of reach of an edit and no edit names a value the move never reads (tests run every
# planner and compare what it reads with this table). REQUIRED: a scene reference the move needs; DERIVED: computed
# from the geometry unless given. Object moves frame their target from its box and the lens (fill, distance_scale), so
# the same move fits a 16 cm gearbox and a 60 m hall.
PARAMS = {
    'waypoints': {'points': REQUIRED, 'aim': 'ahead'},
    'push_in': {'target': REQUIRED, 'aim': DERIVED, 'azimuth_deg': 0.0, 'height_m': 1.6, 'from_m': 12.0, 'to_m': 3.0},
    'dive_through': {'opening': REQUIRED, 'below': REQUIRED, 'above_m': DERIVED, 'back_m': DERIVED, 'inside_depth_m': DERIVED, 'approach': 0.6},
    'section_push': {'section': REQUIRED, 'aim': DERIVED, 'sensor_mm': 36.0, 'aspect': 9 / 16, 'fill': 0.45, 'horizon_v': 0.40,
                     'centre_v': 0.68, 'back_m': 120.0, 'above_m': 45.0, 'into_m': 25.0, 'inside_z': DERIVED},
    'pass_between': {'a': REQUIRED, 'b': REQUIRED, 'target': DERIVED, 'height_m': DERIVED, 'approach_m': 8.0, 'beyond_m': 4.0},
    'descend_levels': {'section': REQUIRED, 'aim': DERIVED, 'inset_m': 4.0, 'from_z': DERIVED, 'to_z': DERIVED},
    'crane': {'target': REQUIRED, 'azimuth_deg': 0.0, 'dist_m': 8.0, 'from_h': 0.6, 'to_h': 8.0},
    'orbit_reveal': {'target': REQUIRED, 'radius_m': 10.0, 'height_m': 3.0, 'start_deg': -60.0, 'sweep_deg': 90.0},
    'turntable': {'target': REQUIRED, 'aspect': 9 / 16, 'fill': 0.8, 'distance_scale': 1.0, 'elevation_deg': 25.0, 'start_deg': -60.0,
                  'sweep_deg': 120.0, 'radius_m': DERIVED, 'height_m': DERIVED},
    'slide': {'target': REQUIRED, 'aspect': 9 / 16, 'fill': 0.8, 'distance_scale': 1.0, 'elevation_deg': 15.0, 'azimuth_deg': 0.0, 'span': 0.8,
              'centre_v': 0.5},
    'macro_push': {'target': REQUIRED, 'detail': DERIVED, 'detail_size_m': DERIVED, 'to_m': DERIVED, 'aspect': 9 / 16, 'fill': 0.7,
                   'distance_scale': 1.0, 'elevation_deg': 30.0, 'azimuth_deg': 0.0, 'detail_fill': 0.6, 'centre_v': 0.5},
    'pull_back': {'target': REQUIRED, 'aspect': 9 / 16, 'fill': 0.8, 'distance_scale': 1.0,
                  'elevation_deg': 15.0, 'azimuth_deg': 0.0, 'span': 0.5},
    'tilt': {'target': REQUIRED, 'aim_from': REQUIRED, 'aim_to': REQUIRED, 'aspect': 9 / 16, 'fill': 0.8,
             'distance_scale': 1.0, 'elevation_deg': 15.0, 'azimuth_deg': 0.0},
    # frame_to_frame: the camera is wherever the shot's frame pictures put it (move.frames, frame_targets.py); `path`
    # how it goes between those poses (auto: an arc around the subject when the poses look from different sides);
    # `follow` how much of the subject's travel the camera shares (0 keeps its place and the subject crosses the frame,
    # 1 rides with it; between, both move - the user, 2026-10-11: "대상이 움직임과 동시에 카메라가 움직이면 동작인 효과를
    # 살림과 동시에 문제 해결도 가능"); `pace` subject: the camera moves while and as fast as the subject travels (the
    # move's timing when the subject is still), timing: the move's timing curve; `parts` what is framed when not the whole
    'frame_to_frame': {'target': REQUIRED, 'parts': DERIVED, 'path': 'auto', 'follow': 0.5, 'pace': 'subject', 'aspect': 9 / 16,
                       'axis': 'z'},   # axis: the subject's long axis in its own frame, the one a picture's lean is read on
}
FRAME_PATHS = ('auto', 'straight', 'arc')

# A frame target is solved by its picture's intent (intent_of: size class, height band, lens class, from a few placed
# views; camera_moves._solve_intent) - the only solve since 2026-10-17. The exact search matched the picture's numbers
# within SIZE_TOL / PLACE_TOL and took 150-1300 s on a failing moment; the user approved intent as the default
# ("결정해야하는것 3개 승인할게", 2026-10-16) and it was removed. A move still naming params.solve is refused as a value
# nothing reads (unknown_params, project.validate_shot).


CLOSER_MAX = 1.33        # a small must-see part: the framing comes up to this much closer (frame_targets.HEIGHT_RATIO's upper
                         # end), within its size class; camera_moves tries it, frame_targets refuses a part even this cannot make thick enough
FLOOR_LENSES = (24.0, 18.0, 14.0)   # the lens ladder a frame target's views go down when its own lens fails (camera_moves._solve_intent):
                                    # wider and closer; frame_targets bounds a part's size at its widest
PLACEMENT_AIMS = ('picture', 'parts', 'between')   # where a placed pose aims (camera_moves.place_and_probe): the picture's own
                                                    # framing, its must-see parts, half way from them to the subject
PLACEMENT_CLOSER_RANGE = (0.25, 4.0)   # a recorded closer this many times the picture's framing at most and least: whole
                                       # (0.6) to tight (2.5) is ~4 (WIDE_GRID); past it the pose frames another picture
PLACEMENT_DEFAULT = {'aim': 'picture', 'closer': 1.0}   # what a recorded pose's placement leaves out: placed as the picture is


def placement_of(row):
    """A recorded pose's placement {aim, closer} (a pick, a candidate, a proposal), what it leaves out at
    PLACEMENT_DEFAULT and anything else it carries (a note) kept. Raises ValueError for an aim placing knows no way to
    make or a closer that is not a finite number within PLACEMENT_CLOSER_RANGE - a row written by hand is said, never read as something else."""
    given = (row or {}).get('placement') or {}
    if not isinstance(given, dict):
        raise ValueError(f'placement must be an object {{aim, closer}}, not {given!r}')
    out = {**PLACEMENT_DEFAULT, **{k: v for k, v in given.items() if v is not None}}
    if out['aim'] not in PLACEMENT_AIMS:
        raise ValueError(f"placement aim {out['aim']!r} is not one of {list(PLACEMENT_AIMS)}")
    if isinstance(out['closer'], bool):
        raise ValueError(f"placement closer {out['closer']!r} is not a number")
    try:
        out['closer'] = float(out['closer'])
    except (TypeError, ValueError):
        raise ValueError(f"placement closer {out['closer']!r} is not a number") from None
    low, high = PLACEMENT_CLOSER_RANGE
    if not (math.isfinite(out['closer']) and low <= out['closer'] <= high):
        raise ValueError(f"placement closer {out['closer']} must be within {low}-{high} (PLACEMENT_CLOSER_RANGE)")
    return out


def follow_subject(eye, aim, then, now, then_pose=None, now_pose=None):
    """A recorded pose (eye, aim) carried with its subject. With the subject root's world matrix when the pose was
    recorded (`then_pose`, 16 numbers row by row: subject_pose) and now (`now_pose`), the whole of how it moved -
    turned, leaned, scaled, slid - is applied to eye and aim: a close-up on a part of a subject leaned since stays on
    that part (review 2026-10-17: following the box centre alone lost a dropped lean, and the centre also moves when legs
    deploy or a cheat moves a part). Else, with `then` and `now` (where the subject's box centre stood, subject_at: an
    older record), it slides with that centre. Without either the pose is the world's, as recorded (a pick made on a
    scene re-authored since - the subject raised, slid - looked at the air where it had stood, 2026-10-17)."""
    if then_pose is not None and now_pose is not None:
        move = affine_mul(affine(now_pose), affine_inverse(affine(then_pose)))
        return affine_apply(move, eye), affine_apply(move, aim)
    if then is None or now is None:
        return tuple(eye), tuple(aim)
    shift = [n - t for n, t in zip(now, then)]
    return tuple(e + s for e, s in zip(eye, shift)), tuple(a + s for a, s in zip(aim, shift))


def affine(flat):
    """A 4x4 world matrix from 16 numbers row by row (subject_pose); its last row must be 0 0 0 1."""
    rows = [list(map(float, flat[i * 4:i * 4 + 4])) for i in range(4)]
    if len(flat) != 16 or any(abs(v - w) > 1e-6 for v, w in zip(rows[3], (0, 0, 0, 1))):
        raise ValueError(f'subject_pose must be a 4x4 affine matrix, 16 numbers row by row ending 0 0 0 1, not {flat!r}')
    return rows


def affine_inverse(m):
    """The inverse of an affine 4x4 (rotation, scale, shear and translation): its 3x3 inverted, the move undone."""
    a = [row[:3] for row in m[:3]]
    det = (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
           + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    if abs(det) < 1e-12:
        raise ValueError('subject_pose has no inverse (a subject scaled to nothing)')
    inv = [[(a[(j + 1) % 3][(i + 1) % 3] * a[(j + 2) % 3][(i + 2) % 3] - a[(j + 1) % 3][(i + 2) % 3] * a[(j + 2) % 3][(i + 1) % 3]) / det
            for j in range(3)] for i in range(3)]
    t = [-sum(inv[i][k] * m[k][3] for k in range(3)) for i in range(3)]
    return [inv[0] + [t[0]], inv[1] + [t[1]], inv[2] + [t[2]], [0.0, 0.0, 0.0, 1.0]]


def affine_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def affine_apply(m, point):
    return tuple(sum(m[i][k] * float(point[k]) for k in range(3)) + m[i][3] for i in range(3))


OBJECT_MOVES = ('turntable', 'slide', 'macro_push', 'pull_back', 'tilt')
# camera.move.focus (camera_frame.py): the waypoint each move frames its subject from, and when in the shot the camera
# is there (progress along the move). The solved pose anchors the path; an orbit preserves its sweep about that pose.
# A dive has no single framing pose and takes no focus.
FOCUSED = {'macro_push': (-1, 1.0), 'slide': (1, 0.5), 'crane': (1, 0.5),
           'turntable': (None, 0.5), 'orbit_reveal': (None, 0.5), 'pull_back': (0, 0.0), 'tilt': (1, 0.5)}


def refocus(kind, waypoints, position):
    """The move's waypoints with its framing pose at `position`: a push keeps its start and recomputes the middle; a
    slide moves whole (its span around the new pose)."""
    index, _ = FOCUSED[kind]
    if kind == 'macro_push':
        start = waypoints[0]
        return [start, _v(start, tuple(position), 0.5), tuple(position)]
    shift = _sub(tuple(position), waypoints[index])
    return [_add(w, shift) for w in waypoints]


def orbit_refocus(plan, position, aim, center, share=0.5):
    """Fit the real orbit to a solved midpoint pose; keep its sweep and composition offset."""
    delta = _sub(tuple(position), tuple(center))
    angle = math.degrees(math.atan2(delta[1], delta[0]))
    orbit = {**plan['orbit'], 'radius_m': math.hypot(delta[0], delta[1]), 'height_m': delta[2],
             'start_deg': angle - plan['sweep_deg'] * share}
    return orbit, _sub(tuple(aim), tuple(center))


def rotate_z(point, angle_deg):
    angle = math.radians(angle_deg)
    return (point[0] * math.cos(angle) - point[1] * math.sin(angle),
            point[0] * math.sin(angle) + point[1] * math.cos(angle), point[2])


# centre_v: the screen height (0 bottom .. 1 top) the framed point sits at. The build fills it from the platform UI
# (the middle of the rect no feed UI covers - a Reels frame keeps 14 % top and 35 % bottom clear, so about 0.6) and
# aspect from the output, unless the move gives them (camera_moves.compile_move).


def unknown_params(move):
    """Params a move was given that it never reads (a typo or a leftover from another move type)."""
    return sorted(set((move or {}).get('params', {})) - set(PARAMS.get((move or {}).get('type'), {})))


def _v(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def _sub(a, b): return tuple(a[i] - b[i] for i in range(3))
def _add(a, b): return tuple(a[i] + b[i] for i in range(3))
def _mul(a, s): return tuple(x * s for x in a)
def _len(a): return math.sqrt(sum(x * x for x in a))


def _norm(a):
    n = _len(a)
    return _mul(a, 1 / n) if n > 1e-9 else (0.0, 1.0, 0.0)


def _r3(p):
    return [round(float(x), 3) for x in p]


def centre(box):
    return tuple((box[0][i] + box[1][i]) / 2 for i in range(3))


def size(box):
    return tuple(box[1][i] - box[0][i] for i in range(3))


BACKTRACK_TOLERANCE_M = 0.05
ALPHA = 0.5   # centripetal: knot spacing |p_i+1 - p_i|^0.5, never loops or cusps inside a segment (Yuksel 2011)


def _knot(a, b):
    return max(_len(_sub(b, a)) ** ALPHA, 1e-6)


def _segment(p0, p1, p2, p3, t):
    """Barry-Goldman evaluation of the centripetal Catmull-Rom segment p1 -> p2 at t in [0, 1]."""
    t1 = _knot(p0, p1)
    t2 = t1 + _knot(p1, p2)
    t3 = t2 + _knot(p2, p3)
    u = t1 + (t2 - t1) * t

    def lerp(a, b, ta, tb):
        return _v(a, b, (u - ta) / (tb - ta))
    a1, a2, a3 = lerp(p0, p1, 0.0, t1), lerp(p1, p2, t1, t2), lerp(p2, p3, t2, t3)
    b1, b2 = lerp(a1, a2, 0.0, t2), lerp(a2, a3, t1, t3)
    return lerp(b1, b2, t1, t2)


def catmull_rom(points, step=STEP_M):
    """Centripetal Catmull-Rom through the waypoints, resampled every `step` metres (ordered).
    Uniform Catmull-Rom loops when a short segment sits between long ones (measured: samsung s01 dive, a 7.5 m
    mouth->inside chord between 55 m and 44 m legs ran backwards 0.3 m and bounced 1.5 m in z)."""
    pts = [tuple(map(float, p)) for p in points]
    if len(pts) < 2:
        raise ValueError('CAMERA_MOVE: a path needs at least two waypoints')
    ext = [_add(pts[0], _sub(pts[0], pts[1]))] + pts + [_add(pts[-1], _sub(pts[-1], pts[-2]))]
    dense = []
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        n = max(2, int(math.ceil(_len(_sub(p2, p1)) / step)))
        dense += [p1] + [_segment(p0, p1, p2, p3, k / n) for k in range(1, n)]
    dense.append(pts[-1])
    return dense


def backtrack_m(dense, waypoints):
    """Largest distance the path runs backwards along a waypoint chord (0 for a path that never turns back).
    Invariant checked by compile_move (MOVE_PATH_LOOP): a camera move never reverses between two waypoints."""
    worst, k = 0.0, 0
    for a, b in zip(waypoints, waypoints[1:]):
        chord = _sub(b, a)
        n = _len(chord)
        if n < 1e-9:
            continue
        axis = _mul(chord, 1 / n)
        best = -math.inf
        while k < len(dense) and _len(_sub(dense[k], b)) > 1e-9:
            s = sum(_sub(dense[k], a)[j] * axis[j] for j in range(3))
            best = max(best, s)
            worst = max(worst, best - s)
            k += 1
    return worst


def _ref(geo, ref, kind='point'):
    if isinstance(ref, (list, tuple)) and ref and all(isinstance(r, str) for r in ref):
        # several parts as one thing (a layer stack, a joint and its members): their boxes' union, its centre as a point
        box = (tuple(min(_ref(geo, r, 'box')[0][i] for r in ref) for i in range(3)),
               tuple(max(_ref(geo, r, 'box')[1][i] for r in ref) for i in range(3)))
        return box if kind == 'box' else centre(box)
    if isinstance(ref, (list, tuple)):
        return tuple(map(float, ref))
    if kind == 'box':
        if ref not in geo['boxes']:
            raise ValueError(f'CAMERA_MOVE: no box for {ref!r}')
        return geo['boxes'][ref]
    if ref in geo['points']:
        return geo['points'][ref]
    if ref in geo['boxes']:
        return centre(geo['boxes'][ref])
    raise ValueError(f'CAMERA_MOVE: unresolved reference {ref!r}')


def _dir(azimuth_deg, elevation_deg=0.0):
    """Unit vector pointing from the target toward the camera: azimuth 0 = camera on -Y looking +Y."""
    a, e = math.radians(azimuth_deg), math.radians(elevation_deg)
    return (math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e))


AIM_PARAM = {'waypoints': 'aim', 'push_in': 'target', 'dive_through': 'below', 'pass_between': 'target',
             'descend_levels': 'aim', 'crane': 'target', 'orbit_reveal': 'target', 'section_push': 'aim',
             'turntable': 'target', 'slide': 'target', 'macro_push': ('detail', 'target'),
             'pull_back': 'target', 'tilt': 'target', 'frame_to_frame': 'target'}   # a tuple: the first one given


def fit_distance(box, lens_mm, fill, aspect=9 / 16, sensor_mm=SENSOR_MM):
    """Camera distance at which `box` fills `fill` of the frame from any side: its horizontal diagonal against the
    frame width, its height against the frame height (sensor fitted to the long side, as Blender's AUTO fit).
    A starting distance: what a camera actually sees of a deep or partly hidden thing is solved on the scene itself
    (camera.move.frame, camera_frame.py)."""
    sx, sy, sz = size(box)
    tan_long = sensor_mm / (2 * lens_mm)
    tan_w, tan_h = (tan_long * aspect, tan_long) if aspect < 1 else (tan_long, tan_long / aspect)
    return max(math.hypot(sx, sy) / (2 * tan_w * fill), sz / (2 * tan_h * fill))


def _param(kind, p, name):
    """A move parameter: given, else its table default (a DERIVED or REQUIRED one is computed or read by the caller)."""
    default = PARAMS[kind][name]
    if default in (REQUIRED, DERIVED):
        raise KeyError(f'{kind}.{name} has no constant default')
    return p.get(name, default)
STEEP = {'dive_through': 85.0, 'descend_levels': 75.0}  # moves that look down into what they enter


def plan(move, geo):
    """-> {'kind': 'flythrough' | 'orbit', 'waypoints': [...], 'aim': point | None (= ahead), 'aim_ref': the scene
    reference aimed at (kept so the rig can guard its visibility), 'pitch_limit_deg', 'notes'}"""
    kind = move['type']
    if kind not in MOVES:
        raise ValueError(f'CAMERA_MOVE: unknown move {kind!r} (known: {MOVES})')
    result = _plan(kind, {**move.get('params', {}), '_lens_mm': move.get('lens_mm', 24.0), '_frames': move.get('frames') or []}, geo)
    p = move.get('params', {})
    names = AIM_PARAM[kind] if isinstance(AIM_PARAM[kind], tuple) else (AIM_PARAM[kind],)
    ref = p.get('aim') if kind == 'push_in' and p.get('aim') else next((p[n] for n in names if p.get(n)), None)
    result['aim_ref'] = ref if isinstance(ref, str) and ref != 'ahead' else None
    result['pitch_limit_deg'] = STEEP.get(kind)
    marks = MARKS.get(kind, {})
    if move.get('then'):   # carry on past the move's own path: one camera through the next space, one shot
        if result['kind'] != 'flythrough':
            raise ValueError(f'CAMERA_MOVE: then carries a path on; {kind} is an orbit')
        base = len(result['waypoints'])
        result['waypoints'] = result['waypoints'] + [_ref(geo, r) for r in move['then']]
        marks = {**marks, **{f'then{i}': base + i for i in range(len(move['then']))}}
    result['marks'] = {**{f'wp{i}': i for i in range(len(result['waypoints']))}, **marks}
    return result


MARKS = {'dive_through': {'mouth': 1, 'inside': 2}, 'pass_between': {'gap': 1}, 'section_push': {'front': 1, 'inside': 2}}  # named waypoints others can bind to


def mark_progress(dense, waypoints, marks, distance=None):
    """{mark: progress u at which the camera passes it} (u covers `distance` metres of the path, default all)."""
    lengths = [0.0]
    for a, b in zip(dense, dense[1:]):
        lengths.append(lengths[-1] + _len(_sub(b, a)))
    total = distance or lengths[-1] or 1.0
    out = {}
    for name, index in marks.items():
        w = waypoints[index]
        nearest = min(range(len(dense)), key=lambda i: _len(_sub(dense[i], w)))
        out[name] = min(1.0, lengths[nearest] / total)
    return out


def pass_frame(progress, u, frame_count):
    return next((f for f in range(frame_count) if progress(f / max(1, frame_count - 1)) >= u - 1e-9), frame_count - 1)


def cue_frames(dense, waypoints, marks, progress, frame_count, distance=None):
    """{mark: shot frame the camera passes it} for a path covered by progress u(t).
    Actions bind to these as cues 'cam-<mark>', so a reveal can finish before the camera arrives."""
    return {name: pass_frame(progress, u, frame_count) for name, u in mark_progress(dense, waypoints, marks, distance).items()}


def _plan(kind, p, geo):
    if kind == 'waypoints':
        pts = [_ref(geo, r) for r in p['points']]
        aim = None if _param(kind, p, 'aim') == 'ahead' else _ref(geo, p['aim'])
        return {'kind': 'flythrough', 'waypoints': pts, 'aim': aim, 'notes': {}}
    if kind == 'push_in':
        target = _ref(geo, p['target'])
        d = _dir(_param(kind, p, 'azimuth_deg'))
        h = _param(kind, p, 'height_m')
        far, near = _param(kind, p, 'from_m'), _param(kind, p, 'to_m')
        start = (target[0] + d[0] * far, target[1] + d[1] * far, h)
        end = (target[0] + d[0] * near, target[1] + d[1] * near, h)
        return {'kind': 'flythrough', 'waypoints': [start, _v(start, end, 0.5), end], 'aim': _ref(geo, p['aim']) if p.get('aim') else target, 'notes': {}}
    if kind == 'dive_through':
        box = _ref(geo, p['opening'], 'box')
        c, sz = centre(box), size(box)
        below = _ref(geo, p['below'])
        top = box[1][2]
        above = p.get('above_m', max(sz[0], sz[1]) * 0.8)
        back = p.get('back_m', sz[1] * 0.6)
        start = (c[0], c[1] - back, top + above)
        mouth = (c[0], c[1], top + 0.5)
        # inside sits below the slab, not at its mid-depth: a thin opening (0.4 m road) otherwise leaves a nearly flat
        # mouth->inside leg between two steep ones and the camera visibly bounces at the mouth
        inside = (c[0], c[1] + back * 0.15, box[0][2] - p.get('inside_depth_m', max(1.5, sz[2] * 0.5)))
        stop = _v(inside, below, _param(kind, p, 'approach'))
        return {'kind': 'flythrough', 'waypoints': [start, mouth, inside, stop], 'aim': below,
                'notes': {'opening_centre': c, 'opening_size': sz}}
    if kind == 'section_push':
        # The cutaway approach: from an aerial, come down level in front of a section cut through the ground (the
        # structure's -y face) and push into it. `front` frames the section: width = fill of the frame width,
        # its centre at screen height centre_v below a horizon at horizon_v (level camera; framing holds it).
        box = _ref(geo, p['section'], 'box')
        c, sz = centre(box), size(box)
        face = box[0][1]
        tan_y = _param(kind, p, 'sensor_mm') / (2 * p['_lens_mm'])
        tan_x = tan_y * _param(kind, p, 'aspect')
        distance = sz[0] / (2 * tan_x * _param(kind, p, 'fill'))
        horizon = _param(kind, p, 'horizon_v')
        z_front = c[2] + (_param(kind, p, 'centre_v') - horizon) * 2 * tan_y * distance
        front = (c[0], face - distance, z_front)
        start = (c[0], face - distance - _param(kind, p, 'back_m'), _param(kind, p, 'above_m'))
        into = _param(kind, p, 'into_m')
        inside = (c[0], face + into, p.get('inside_z', c[2]))
        aim = _ref(geo, p['aim']) if p.get('aim') else None
        return {'kind': 'flythrough', 'waypoints': [start, front, inside], 'aim': aim,
                'notes': {'section_face_y': face, 'front_distance_m': round(distance, 3), 'section_size': sz}}
    if kind == 'pass_between':
        a, b = _ref(geo, p['a'], 'box'), _ref(geo, p['b'], 'box')
        ca, cb = centre(a), centre(b)
        gap_mid = _v(ca, cb, 0.5)
        across = _norm(_sub(cb, ca))
        forward = _norm((-across[1], across[0], 0.0)) if abs(across[2]) < 0.9 else (0.0, 1.0, 0.0)
        target = _ref(geo, p['target']) if p.get('target') else _add(gap_mid, _mul(forward, 20))
        if sum((target[i] - gap_mid[i]) * forward[i] for i in range(3)) < 0:
            forward = _mul(forward, -1)
        h = p.get('height_m', gap_mid[2])
        approach, beyond = _param(kind, p, 'approach_m'), _param(kind, p, 'beyond_m')
        mid = (gap_mid[0], gap_mid[1], h)
        start = _add(mid, _mul(forward, -approach))
        end = _add(mid, _mul(forward, beyond))
        gap = max(0.0, _len(_sub(cb, ca)) - (max(size(a)[0], size(a)[1]) + max(size(b)[0], size(b)[1])) / 2)
        return {'kind': 'flythrough', 'waypoints': [start, mid, end], 'aim': target, 'notes': {'gap_m': round(gap, 3)}}
    if kind == 'descend_levels':
        box = _ref(geo, p['section'], 'box')
        c = centre(box)
        inset = _param(kind, p, 'inset_m')
        y = box[0][1] + inset
        z0, z1 = p.get('from_z', box[1][2] - 1.0), p.get('to_z', box[0][2] + 2.0)
        aim = _ref(geo, p['aim']) if p.get('aim') else (c[0], box[1][1], (z0 + z1) / 2)
        return {'kind': 'flythrough', 'waypoints': [(c[0], y - inset, z0), (c[0], y, (z0 + z1) / 2), (c[0], y + inset, z1)], 'aim': aim, 'notes': {}}
    if kind == 'crane':
        target = _ref(geo, p['target'])
        d = _dir(_param(kind, p, 'azimuth_deg'))
        dist = _param(kind, p, 'dist_m')
        base = (target[0] + d[0] * dist, target[1] + d[1] * dist)
        return {'kind': 'flythrough', 'waypoints': [(*base, _param(kind, p, 'from_h')), (*base, (_param(kind, p, 'from_h') + _param(kind, p, 'to_h')) / 2),
                                                    (*base, _param(kind, p, 'to_h'))], 'aim': target, 'notes': {}}
    if kind in OBJECT_MOVES:
        return _plan_object_move(kind, p, geo)
    if kind == 'frame_to_frame':
        return _plan_frames(p, geo)
    target = _ref(geo, p['target'])  # orbit_reveal
    return {'kind': 'orbit', 'waypoints': [], 'aim': target,
            'orbit': {'radius_m': _param(kind, p, 'radius_m'), 'height_m': _param(kind, p, 'height_m'), 'start_deg': _param(kind, p, 'start_deg'),
                      'deg_per_s': 1.0}, 'sweep_deg': _param(kind, p, 'sweep_deg'), 'notes': {}}


def frame_seed(box, frame, lens_mm, aspect):
    """(eye, aim) a frame target's solve starts from: back along its view from the framed box so the box is about
    `height_share` of the frame tall, aimed at its centre (camera_frame.py then solves the exact place)."""
    c = centre(box)
    d = _dir(frame['view'].get('azimuth_deg', 0.0), frame['view']['elevation_deg'])
    return _add(c, _mul(d, fit_distance(box, lens_mm, frame['height_share'], aspect))), c


def _plan_frames(p, geo):
    """frame_to_frame before the solve: a pose per frame target seeded from the framed box (the preview path)."""
    kind = 'frame_to_frame'
    framed = p['parts'] if p.get('parts') else p['target']
    box = _ref(geo, framed if isinstance(framed, str) else list(framed), 'box')
    path, follow, aspect, pace = _param(kind, p, 'path'), _param(kind, p, 'follow'), _param(kind, p, 'aspect'), _param(kind, p, 'pace')
    axis = _param(kind, p, 'axis')
    if path not in FRAME_PATHS or not 0.0 <= float(follow) <= 1.0 or pace not in ('subject', 'timing') or axis not in ('x', 'y', 'z'):
        raise ValueError(f'CAMERA_MOVE: frame_to_frame path {path!r} (one of {FRAME_PATHS}) / follow {follow!r} (0..1) / '
                         f'pace {pace!r} (subject or timing) / axis {axis!r} (x, y or z)')
    frames = sorted(p['_frames'], key=lambda f: f['at'])
    if len(frames) < 2:
        raise ValueError('CAMERA_MOVE: frame_to_frame needs move.frames with a first and a last target (frames apply)')
    seeds = [frame_seed(box, f, f.get('lens_mm') or p['_lens_mm'], aspect) for f in frames]
    return {'kind': 'flythrough', 'waypoints': [eye for eye, _ in seeds], 'aim': centre(box),
            'notes': {'path': path, 'follow': follow, 'frames': [f['at'] for f in frames]}}


VIEW_STEPS = {'azimuth_deg': (0.0, 12.0, -12.0, 24.0, -24.0, 36.0, -36.0), 'elevation_deg': (0.0, 10.0, -10.0, 20.0, -20.0)}
MAX_VIEWS = 35          # every step combination: the nearest that works wins, a far one is still better than none


def candidate_views(view, steps=VIEW_STEPS, limit=MAX_VIEWS):
    """Directions to try for a frame target, nearest the picture's first: the picture says roughly where the camera
    looks from; the scene decides whether the subject and what must be seen show from exactly there."""
    elevation, azimuth = view
    rows = []
    for da in steps['azimuth_deg']:
        for de in steps['elevation_deg']:
            e = max(-85.0, min(85.0, elevation + de))
            rows.append((math.hypot(da * math.cos(math.radians(e)), e - elevation), (round(e, 3), round(azimuth + da, 3)), (da, round(e - elevation, 3))))
    seen, out = set(), []
    for _, v, offset in sorted(rows, key=lambda r: r[0]):
        if v not in seen:
            seen.add(v)
            out.append({'view': v, 'offset': {'azimuth_deg': offset[0], 'elevation_deg': offset[1]}})
    return out[:limit]


# A picture's intent (camera_moves._solve_intent): what the camera keeps of it when it does not match its numbers.
# Size classes by the subject's height in frame heights, measured past the frame edge (a frame row's height_share, the
# camera report's achieved.height_share: 2.0 shows half of it) - the words the storyboard brief says them in
# (storyboard_brief.size_class). Bands chosen so the words match what a reel's sheets look like (0.10 a dot over the sea,
# 0.3 a whole vehicle with room, 0.7 filling the frame, 1.4 a cut body, 3 one leg); the open top is capped at SIZE_TOP
# for a band the camera can stand in.
SIZE_CLASSES = ((0.15, '원경'), (0.45, '전체'), (0.9, '꽉 참'), (2.0, '클로즈업'), (math.inf, '익스트림 클로즈업'))
SIZE_TOP = 4.0
# a size measured within this share of a class edge is still the picture's class (said only past it), and a whole
# picture's refine aims this far inside its band: the edges are reading aids, not compositions - a 0.46 picture shown at
# 0.42 across the 0.45 edge reads the same (2026-10-16); 10 % is about what one refine's SIZE_TOL lets the solve stop short
SIZE_EDGE_MARGIN = 0.1
# the smallest class has no low edge (0): a subject there may still not shrink past this share of the picture's own
# height - the share the build check allows a size to shrink by (frame_targets.HEIGHT_RATIO[0]); else a dot drawn 0.12
# of the frame could be built a speck of 0.01 and kept as 'the same class'
SIZE_SMALLEST_FLOOR = 0.75
LENS_CLASSES = {'wide': 24.0, 'normal': 35.0, 'tele': 70.0}   # what "wide / normal / tele" reads as, full-frame equivalent
LENS_LADDER = (70.0, 35.0, 24.0, 18.0, 14.0)   # intent candidates: the picture's lens and the next wider rung
LEVEL_DEG = 5.0          # a picture within this of level is level (frame_targets.LEVEL_DEG)
INTENT_ELEVATION_TOL = 15.0   # the camera keeps within this of the picture's elevation, on its side of level
INTENT_TURN_MAX = 45.0   # turned further than this round the subject from the picture's azimuth: said (FRAME_INTENT_DEPARTED)
STEEP_DEG = 85.0         # the steepest a camera looks down or up
# the whole way round: a picture is never read for its azimuth (frame_targets.target_of: the shot's own or held), so a
# part on the far side is reached by going round, nearest first (intent_score), not by a picture redrawn
INTENT_STEPS = {'azimuth_deg': (0.0, 15.0, -15.0, 30.0, -30.0, 60.0, -60.0, 90.0, -90.0, 120.0, -120.0, 150.0, -150.0, 180.0),
                'elevation_deg': (0.0, 12.0, -12.0, 24.0, -24.0)}


def size_class(height_share):
    """(name, [low, high)) of the size class a subject `height_share` frame heights tall reads as."""
    low = 0.0
    for top, name in SIZE_CLASSES:
        if height_share < top:
            return name, [low, min(top, SIZE_TOP)]
        low = top
    return SIZE_CLASSES[-1][1], [SIZE_CLASSES[-2][0], SIZE_TOP]


def size_kept(height_share, band, margin=SIZE_EDGE_MARGIN, picture_h=None):
    """Whether `height_share` is within the size class `band` [low, high), widened by `margin` of each edge; the top
    class (its high at SIZE_TOP, a cap for search, not a class edge) has no upper edge, and the smallest (low 0) is
    floored at SIZE_SMALLEST_FLOOR of the picture's own height (`picture_h`)."""
    low, high = band
    floor = picture_h * SIZE_SMALLEST_FLOOR if low <= 0.0 and picture_h else low * (1.0 - margin)
    return floor <= height_share and (high >= SIZE_TOP or height_share < high * (1.0 + margin))


def size_target(height_share, band, margin=SIZE_EDGE_MARGIN):
    """The height a whole picture's refine aims at: its own, pulled `margin` inside its class band when it sits near an
    edge, so a solve stopping within its tolerance stays in the class. The top class is not pulled down from above; a
    band too narrow for both margins aims at its middle."""
    low, high = band
    floor = low * (1.0 + margin)
    ceiling = math.inf if high >= SIZE_TOP else high * (1.0 - margin)
    if floor > ceiling:
        return (low + high) / 2.0
    return min(max(height_share, floor), ceiling)


def elevation_band(elevation_deg):
    """(name, [low, high]) the camera's elevation keeps for a picture seen from `elevation_deg` (+ above, looking
    down): within INTENT_ELEVATION_TOL of it, never into the other side's class - a picture looking down (above
    LEVEL_DEG) stays above -LEVEL_DEG, one looking up below +LEVEL_DEG, a level one within the tolerance either way.
    Continuous across the class edges: -5.6 and -4.9 deg keep nearly the same band."""
    low, high = max(-STEEP_DEG, elevation_deg - INTENT_ELEVATION_TOL), min(STEEP_DEG, elevation_deg + INTENT_ELEVATION_TOL)
    if abs(elevation_deg) < LEVEL_DEG:
        return 'level', [round(low, 3), round(high, 3)]
    if elevation_deg > 0:
        return 'down', [round(max(low, -LEVEL_DEG), 3), round(high, 3)]
    return 'up', [round(low, 3), round(min(high, LEVEL_DEG), 3)]


# verify: with every cheat applied, a moment may turn this much further round than it turned on its own (a third of the
# turn that is said at all: a re-solve on a changed scene moves the camera a few steps, a swing to the far side is new)
INTENT_TURN_SLACK = INTENT_TURN_MAX / 3.0


def losses(row):
    """What a solved row gave up, structured: [{kind, key, value, text}] - its departures (intent_losses, kept on the
    row as `departures`) and every other warning it said, by its code."""
    # a key read back from json (a thing and its axis) is a list: the same key as the tuple it was written from
    out = [{**d, 'key': tuple(d['key']) if isinstance(d.get('key'), list) else d.get('key')} for d in row.get('departures') or []]
    out += [{'kind': str(w).split(':', 1)[0], 'key': None, 'value': 1, 'text': str(w)} for w in row.get('warnings') or []
            if not str(w).startswith('FRAME_INTENT_DEPARTED')]
    return out


def lost_more(joint, own):
    """What `joint` (a row) gives up beyond `own` (the same moment solved on its own), in words ([] when nothing): per
    kind and key - a turn more than INTENT_TURN_SLACK further, a lens class wider, a size class further off, anything
    `own` did not give up at all. verify asks it of every moment when every cheat is applied together."""
    mine = {(d['kind'], d['key']): d['value'] for d in losses(own)}
    out = []
    for d in losses(joint):
        k = (d['kind'], d['key'])
        if k not in mine:
            out.append(d['text'])
        elif d['kind'] == 'turn' and d['value'] > mine[k] + INTENT_TURN_SLACK:
            out.append(d['text'])
        elif d['kind'] in ('lens', 'size') and d['value'] > mine[k]:
            out.append(d['text'])
    return out


def kept_intent(row):
    """What a solved frame row gave up of its picture's intent, in words ([] when it kept it): its departures
    (FRAME_INTENT_DEPARTED: turned past INTENT_TURN_MAX, a wider lens class, another size) and every other warning the
    solve said (a lean not reached, the camera in a volume)."""
    lost = list(row.get('departed') or [])
    lost += [w for w in row.get('warnings') or [] if not str(w).startswith('FRAME_INTENT_DEPARTED')]
    return lost


def wrap_deg(angle):
    """An angle in (-180, 180]."""
    out = (angle + 180.0) % 360.0 - 180.0
    return 180.0 if out == -180.0 else out


LENS_RANK = {name: i for i, name in enumerate(sorted(LENS_CLASSES, key=lambda k: -LENS_CLASSES[k]))}   # tele 0 ... wide


def _size_steps(height, intent):
    """How many size classes `height` is from the picture's (at least 1 when it is a departure)."""
    names = [name for _top, name in SIZE_CLASSES]
    return max(1, abs(names.index(size_class(height)[0]) - names.index(intent['size']['class'])))


def intent_losses(intent, offset, lens_mm, seen, said=()):
    """intent_departures as structured pairs: [{kind, key, value, text}] - turn (deg), lens (rank of its class: wider is
    more), size (classes off), whole (a cut picture shown whole), and picture_terms' `lost` (`said`): layout_lost /
    layout_flip per thing (and axis), owner, cut per edge, end_on. The text is for the sheet; verify compares values."""
    out = []
    turn = abs(wrap_deg(offset.get('azimuth_deg', 0.0)))
    if turn > INTENT_TURN_MAX:
        out.append({'kind': 'turn', 'key': None, 'value': round(turn, 2),
                    'text': f'turned {turn:.0f} deg round the subject (more than {INTENT_TURN_MAX:g})'})
    if lens_mm < intent['lens']['mm'] - 1e-6 and lens_class(lens_mm) != intent['lens']['class']:
        out.append({'kind': 'lens', 'key': None, 'value': LENS_RANK[lens_class(lens_mm)],
                    'text': f"{lens_class(lens_mm)} lens ({lens_mm:g} mm) for the picture's {intent['lens']['class']} ({intent['lens']['mm']:g} mm)"})
    if seen is not None:
        height = seen['height_share']
        if not intent.get('cut') and not size_kept(height, intent['size']['band'], picture_h=intent['size']['height_share']):
            out.append({'kind': 'size', 'key': None, 'value': _size_steps(height, intent),
                        'text': f"{size_class(height)[0]} ({height:.2f} of the frame) for the picture's {intent['size']['class']} "
                                f"({intent['size']['height_share']:.2f})"})
        elif intent.get('cut') and seen.get('whole_in_frame'):
            out.append({'kind': 'whole', 'key': None, 'value': 1,
                        'text': f"the whole subject in the frame ({height:.2f} of it) for the picture's cut {intent['size']['class']}"})
    texts = {d['text'] for d in out}
    return out + [d for d in said if isinstance(d, dict) and d['text'] not in texts]


def intent_departures(intent, offset, lens_mm, seen, said=()):
    """What a chosen pose gave up of its picture's intent, in words ([] when nothing): turned past INTENT_TURN_MAX round
    the subject, a lens class wider than the picture's (allowed - a wider lens stands closer - but said), the subject at
    another size than the picture's (seen: camera_frame.seen_height at the pose; None when not measured). A picture that
    frames its subject whole is compared by size class; a cut one (its height an extrapolated whole box, which a close
    camera's perspective shrinks) only by whether the pose still cuts it: one showing it whole in the frame is not the
    close-up the picture drew."""
    lost = [d for d in said if isinstance(d, dict)]
    texts = [d['text'] for d in intent_losses(intent, offset, lens_mm, seen, lost)]
    return texts + [s for s in said if isinstance(s, str) and s not in texts]


def lens_class(lens_mm):
    """The LENS_CLASSES name nearest `lens_mm` (by ratio)."""
    return min(LENS_CLASSES, key=lambda k: abs(math.log(lens_mm / LENS_CLASSES[k])))


def wider_lens(lens_mm):
    """The next LENS_LADDER rung wider than `lens_mm`, or None at the widest."""
    return next((mm for mm in LENS_LADDER if mm < lens_mm - 1e-6), None)


def intent_of(fr, lens_mm=None):
    """What a frame target (a move.frames row) keeps when the camera does not match its numbers: {size: {class, band,
    height_share}, elevation: {class, band, deg}, lens: {class, mm}, cut_edges, tilt_deg, must_see, show}. From the
    row alone - an older row (no cut_edges) gives the same intent, nothing is read again."""
    lens = float(fr.get('lens_mm') or lens_mm or LENS_CLASSES['wide'])
    size, band = size_class(float(fr['height_share']))
    elevation = float(fr['view']['elevation_deg'])
    height, heights = elevation_band(elevation)
    return {'size': {'class': size, 'band': band, 'height_share': float(fr['height_share'])},
            'elevation': {'class': height, 'band': heights, 'deg': elevation},
            'lens': {'class': lens_class(lens), 'mm': lens},
            'cut': cut_picture(fr), 'cut_edges': fr.get('cut_edges'), 'tilt_deg': fr.get('tilt_deg'),
            'must_see': list(fr.get('must_see') or []), 'show': fr.get('show')}


def intent_views(intent, view, steps=INTENT_STEPS):
    """candidate_views around the picture's view (its azimuth may be a lying subject's turned one) at INTENT_STEPS,
    those whose elevation stays in the intent's band, nearest first."""
    low, high = intent['elevation']['band']
    return [c for c in candidate_views(view, steps, limit=len(steps['azimuth_deg']) * len(steps['elevation_deg']))
            if low - 1e-6 <= c['view'][0] <= high + 1e-6]


# The free-angle search (camera_moves.wide_search): when no view within a picture's intent shows its must-see parts,
# every side, height, framing and lens is placed and probed, and the nearest poses that work are PROPOSED - never taken
# (a picture redrawn from there is the user's pick: frames proposals / frames pick). Why (2026-10-17): three moments of a
# reel had no view within their intent that showed their parts; a search by hand over this grid found poses for two.
WIDE_GRID = {
    'framings': {'whole': {'height_share': 0.6, 'cut': False, 'cost': 0.0},   # the subject framed whole
                 'close': {'height_share': 1.3, 'cut': True, 'cost': 1.0},    # a close-up aimed at the must-see parts
                 'tight': {'height_share': 2.5, 'cut': True, 'cost': 2.0}},   # a tight one on them
    'elevation_deg': tuple(float(e) for e in range(-75, 76, 15)),
    'azimuth_deg': tuple(float(a) for a in range(0, 360, 15)),
    'lens_mm': (14.0, 18.0, 24.0, 35.0, 50.0),
}
WIDE_MAX_PROBES = 2400   # placed poses probed at most, nearest the picture first (~0.02 s each, under a minute): of the
                         # 3960 the grid holds, the rest depart ~16.5 points or more (turned 120 deg and 45 deg higher at
                         # another lens) - another picture, not this one redrawn. 2026-10-17: the poses that solved a
                         # reel's three blocked moments departed 10-11.5
WIDE_JUDGE_MAX = 8       # of the poses the probe passed, the nearest the picture judged in full (the build's id render)
WIDE_KEEP = 3            # poses proposed at most, each a choice: another framing, or INTENT_KEEP_APART_DEG round
WIDE_KEEP_APART_DEG = 20.0
CUT_ALL = ['bottom', 'left', 'right', 'top']


def wide_departure(picture_view, lens0, framing, view, lens_mm):
    """How far a free-angle pose departs from its picture, in intent_score's points: 10 deg up, down or round is 1, a
    closer framing its WIDE_GRID cost, each halving or doubling of the focal length 1."""
    return round(abs(view[0] - picture_view[0]) / 10.0 + abs(wrap_deg(view[1] - picture_view[1])) / 10.0
                 + WIDE_GRID['framings'][framing]['cost'] + abs(math.log2(lens_mm / lens0)), 3)


def wide_poses(picture_view, lens0, aims=True, grid=WIDE_GRID):
    """Every pose of the grid as (departure, framing, view, lens_mm), nearest the picture first (the azimuths taken
    round from the picture's own); a close framing only with must-see parts to aim at (`aims`)."""
    out = []
    for framing, row in grid['framings'].items():
        if row['cut'] and not aims:
            continue
        for e in grid['elevation_deg']:
            for a in grid['azimuth_deg']:
                view = (float(e), round(wrap_deg(picture_view[1] + a), 3))
                for lens in grid['lens_mm']:
                    out.append((wide_departure(picture_view, lens0, framing, view, lens), framing, view, float(lens)))
    return sorted(out, key=lambda r: (r[0], r[1], r[2], r[3]))


def wide_frame(fr, framing, view, lens_mm):
    """The frame row a picture redrawn at a free-angle pose would carry: the picture's own must-see and opening, the
    framing's size, centred, cut on every side for a close-up, seen from `view` at `lens_mm` - no lean, layout, pick or
    cheat of the picture it replaces (they were drawn for another view)."""
    g = WIDE_GRID['framings'][framing]
    out = {k: v for k, v in fr.items() if k not in ('chosen', 'cheats', 'intent', 'width_share', 'edges', 'tilt_deg', 'picture',
                                                     'departs', 'side', '_cheated')}
    out.update(height_share=g['height_share'], centre=[0.5, 0.5], cut_edges=list(CUT_ALL) if g['cut'] else [],
               view={'elevation_deg': float(view[0]), 'azimuth_deg': float(view[1])}, lens_mm=float(lens_mm))
    return out


WIDE_FRAMING_WORDS = {'whole': '전체가 보이게', 'close': '클로즈업', 'tight': '타이트 클로즈업'}


def wide_words(picture_view, lens0, framing, view, lens_mm):
    """What a free-angle pose gives up of its picture, in Korean (the proposals sheet): height, turn, lens, framing."""
    out = []
    de = view[0] - picture_view[0]
    if abs(de) >= 1.0:
        out.append(f"{'위' if de > 0 else '아래'}로 {abs(de):.0f}° (그림 {picture_view[0]:+.0f}° → {view[0]:+.0f}°)")
    da = wrap_deg(view[1] - picture_view[1])
    if abs(da) >= 1.0:
        out.append(f"옆으로 {abs(da):.0f}° 돌아감 (방위 {picture_view[1]:.0f}° → {view[1]:.0f}°)")
    if abs(lens_mm - lens0) > 1e-6:
        out.append(f"렌즈 {lens_mm:g} mm (그림 {lens0:g} mm)")
    if framing != 'whole':
        out.append(WIDE_FRAMING_WORDS[framing])
    return out

# What a picture's intent holds beyond size, side of level and lens (picture_terms): weights per unit of each cost, in
# intent_score's points (10 degrees off the picture's view is 1). Why (2026-10-16, a review of every shot's placed
# views): the picture's other things left the frame or swapped sides, a close-up on a part lost the thing it belongs to,
# the widest lens stood so close the frame bent and cut the subject, and a long thing seen along its length read as a
# stripe - none of it cost anything, so a view a few degrees nearer the picture won.
INTENT_WEIGHTS = {
    # the layout costs are shares of the picture's named things (picture_terms divides by how many were compared): a
    # picture with five things round the subject weighs no more than one with one, and a must-see part's margin (worth
    # up to half a point) still tells two views apart
    'layout_flip': 2.0,    # all things on the other side of the subject (left/right, above/below) than drawn: more than a 15 deg step
    'layout_lost': 2.0,    # all things drawn in the frame and out of it now
    'layout_shift': 1.0,   # per frame height their place round the subject moved from where drawn (x in true proportions)
    'owner_short': 3.0,    # the subject's share in frame below OWNER_KEEP of what the picture shows of it, per that share
    'cut_edge': 1.0,       # per frame edge the subject runs past that the picture does not cut
    'lens_stop': 1.0,      # per halving of the focal length below the picture's: wider only when nothing less wide holds
    'end_on': 4.0,         # a long thing seen within END_ON_DEG of its length, drawn along it: it reads as a stripe or a dot
    'aspect': 1.0,         # per e-fold the subject's shown width-to-height differs from the picture's (neither cut at its sides)
}
INTENT_LAYOUT = {
    'side_min': 0.05,      # a thing's centre this far (share of the frame) from the subject's is on a side of it
    'owner_keep': 0.5,     # the subject keeps at least this share of the height or width it shows in the picture
    'end_on_deg': 45.0,    # a long axis nearer the line of sight than this shows its end - measured 2026-10-14: well-read
                           # builds 57-86 deg, a cylinder seen down its axis 2.4 deg (frame_targets' SUBJECT_END_ON reads it)
    'long_ratio': 2.0,     # a picture shows a thing along its length when its box is this much longer one way (true proportions)
    'aspect_cap': math.log(4.0),   # a shape 4 times wider or narrower than drawn is already another view: no further cost
    'sight_tol_deg': 10.0, # ... and a pose is end-on only when this much nearer its length than the picture's own view was
                           # (a picture drawn steeply up a tall thing looks near its length itself)
}
END_ON_DEG = INTENT_LAYOUT['end_on_deg']


def lens_stops(picture_mm, lens_mm):
    """How many halvings of the focal length `lens_mm` is wider than the picture's (0 when not wider)."""
    return round(max(0.0, math.log2(picture_mm / lens_mm)), 4) if lens_mm > 0 else 0.0


def _clip(b):
    return [max(0.0, b[0]), max(0.0, b[1]), min(1.0, b[2]), min(1.0, b[3])]


def _middle(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def _in_frame(b):
    """A thing is in the frame when its centre is: a sliver at the edge is not the thing the picture drew there."""
    return b is not None and 0.0 <= (b[0] + b[2]) / 2 <= 1.0 and 0.0 <= (b[1] + b[3]) / 2 <= 1.0


SIDES_WORDS = {(0, False): 'left of', (0, True): 'right of', (1, False): 'above', (1, True): 'below'}


def picture_terms(picture, shown, aspect):
    """What a placed view keeps of its picture's layout, as costs (INTENT_WEIGHTS keys) and words: {'cost', 'said'}.
    picture: {subject: {box (what shows of it, frame coords), cut_edges}, context: [{name, box}]} (frame_targets
    picture_layout, the reading's boxes); shown: {subject_box (what of it is in frame), cut_sides (frame edges it runs
    past), context {name: screen box or None (behind the camera)} - only the things the scene can name, sight_deg (long
    axis to the line of sight), picture_sight_deg (the same from the picture's own view), long (the subject has a long
    axis)}. Four rules, any scene: the other things keep their
    side of the subject and stay in the frame they were drawn in; the subject keeps OWNER_KEEP of what the picture shows
    of it (a close-up on a part shows whose part); no frame edge cuts it that the picture does not cut; a long thing drawn
    along its length is not seen down it. No picture layout: nothing."""
    if not picture:
        return {'cost': {}, 'said': [], 'lost': []}
    cost, said, lost = {}, [], []

    def add(key, value, words=None, kind=None, thing=None, amount=1):
        cost[key] = round(cost.get(key, 0) + value, 4)
        if words:
            said.append(words)
            lost.append({'kind': kind or key, 'key': thing, 'value': amount, 'text': words})
    drawn = (picture.get('subject') or {}).get('box')
    seen = shown.get('subject_box')
    if drawn and seen:
        pc, sc = _middle(_clip(drawn)), _middle(_clip(seen))
        compared = [t for t in picture.get('context') or [] if t['name'] in (shown.get('context') or {})]
        per = 1.0 / max(1, len(compared))   # each thing's share of the layout costs (INTENT_WEIGHTS)
        for thing in compared:
            name = thing['name']   # (one the scene names no object for is not compared)
            got, centre = shown['context'][name], _middle(thing['box'])
            if not _in_frame(got):
                if 0.0 <= centre[0] <= 1.0 and 0.0 <= centre[1] <= 1.0:
                    add('layout_lost', per, f'{name}: drawn in the frame, out of it here', thing=name)
                continue
            now = _middle(_clip(got))
            for axis in (0, 1):   # one line per thing and relation (left/right, above/below), keyed by the thing's name
                d0, d1 = centre[axis] - pc[axis], now[axis] - sc[axis]
                if abs(d0) > INTENT_LAYOUT['side_min'] and abs(d1) > INTENT_LAYOUT['side_min'] and (d0 > 0) != (d1 > 0):
                    add('layout_flip', per, f"{name}: drawn {SIDES_WORDS[(axis, d0 > 0)]} the subject, {SIDES_WORDS[(axis, d1 > 0)]} it here",
                        thing=(name, 'xy'[axis]))
            add('layout_shift', per * math.hypot(((centre[0] - pc[0]) - (now[0] - sc[0])) * aspect, (centre[1] - pc[1]) - (now[1] - sc[1])))
        d, s = _clip(drawn), _clip(seen)
        dh, dw, sh, sw = d[3] - d[1], d[2] - d[0], s[3] - s[1], s[2] - s[0]
        kept = max(sh / dh if dh > 1e-6 else 1.0, sw / dw if dw > 1e-6 else 1.0)
        if kept < INTENT_LAYOUT['owner_keep']:
            add('owner_short', (INTENT_LAYOUT['owner_keep'] - kept) / INTENT_LAYOUT['owner_keep'],
                f'the subject shows {sh:.2f} of the frame tall and {sw:.2f} wide here, the picture {dh:.2f} by {dw:.2f}', kind='owner')
        ratio = lambda b: ((b[2] - b[0]) * aspect) / max(1e-6, b[3] - b[1])  # noqa: E731 - true proportions
        r0, r1 = ratio(d), ratio(s)
        # the shape the subject is drawn in, any thing's both frames show its whole width of - a ship drawn broadside,
        # seen bow-on, is narrow and tall
        across = {'left', 'right'}   # a box cut at its sides has the frame's width, not the thing's
        if not across & set((picture.get('subject') or {}).get('cut_edges') or []) and not across & set(shown.get('cut_sides') or []) \
                and r0 > 0 and r1 > 0:
            add('aspect', min(INTENT_LAYOUT['aspect_cap'], abs(math.log(r1 / r0))))
        if shown.get('long'):
            along = max(r0, 1 / max(r0, 1e-6)) >= INTENT_LAYOUT['long_ratio']
            sight, drawn_sight = shown.get('sight_deg'), shown.get('picture_sight_deg')
            if along and sight is not None and sight < INTENT_LAYOUT['end_on_deg'] and \
                    (drawn_sight is None or sight < drawn_sight - INTENT_LAYOUT['sight_tol_deg']):
                add('end_on', 1, f"seen {shown['sight_deg']:.0f} deg off its length (under {INTENT_LAYOUT['end_on_deg']:g}): "
                                 'drawn along it, it reads as its end here')
    for side in sorted(set(shown.get('cut_sides') or []) - set((picture.get('subject') or {}).get('cut_edges') or [])):
        add('cut_edge', 1, f'the subject runs past the {side} edge here, which the picture does not cut', kind='cut', thing=side)
    return {'cost': cost, 'said': said, 'lost': lost}


def intent_score(offset, lens_changed, margin, adjusted=0, terms=None):
    """Lower is better: degrees off the picture's view (per 10; azimuth the short way round), a lens other than the
    picture's (1; with terms, INTENT_WEIGHTS lens_stop per halving of its focal length instead), each adjustment of its
    framing (aimed at its must-see parts, come closer: 0.25), less the must-see margin (min over parts of px / min_px,
    capped at 2: a part shown well is worth up to half a step), plus what it gives up of the picture's layout (terms:
    picture_terms' costs, weighted by INTENT_WEIGHTS)."""
    turn = math.hypot(wrap_deg(offset['azimuth_deg']), offset['elevation_deg']) / 10.0
    terms = terms or {}
    lens = INTENT_WEIGHTS['lens_stop'] * terms['lens_stop'] if 'lens_stop' in terms else (1.0 if lens_changed else 0.0)
    rest = sum(INTENT_WEIGHTS[k] * v for k, v in terms.items() if k != 'lens_stop')
    return round(turn + lens + 0.25 * adjusted - 0.5 * (min(margin, 2.0) - 1.0) + rest, 4)


def _framing():
    """camera_frame_core, beside this module in Blender or as studio.blender_ops on the host (intent_of runs on both)."""
    try:
        import camera_frame_core as framing
    except ImportError:
        from . import camera_frame_core as framing
    return framing


def cut_picture(fr):
    """The picture frames only part of its subject: an edge cut, or taller than the frame."""
    framing = _framing()
    return bool(fr.get('cut_edges')) or bool(fr.get('edges')) or float(fr['height_share']) > 1 - 2 * framing.MARGIN


def framed_pose(box, fr, view, lens_mm, aspect, shows=None):
    """(eye, aim) a picture's frame row put analytically at `view` and `lens_mm`: the whole box `height_share` frames
    tall - its extent along the camera's up, as a picture's height is read (its size class; frame_seed's fit, the larger
    of width and height, put a wide subject seen from below at a third of its height, 2026-10-15) - then shifted in the
    camera's plane so the box centre lands where the picture has it (centre). Given `shows` (the point of the parts a
    cut picture must show) it aims at them at that distance: a close-up on the legs frames the legs - its whole-box
    centre, read off a picture of a part, lies off the frame by as much as the reading is off (2026-10-15: every placed
    view of two close-ups missed their parts). A picture that shows its subject's whole width (no left or right cut)
    keeps that width too: far enough for both (2026-10-16: a lying hull placed by its height alone ran off both sides
    of a tall frame and read as a stripe)."""
    import camera_frame_core as framing
    forward, right, up = framing.basis(view[0], view[1])
    tan_w, tan_h = framing.tangents(lens_mm, aspect)
    corners = [(x, y, z) for x in (box[0][0], box[1][0]) for y in (box[0][1], box[1][1]) for z in (box[0][2], box[1][2])]

    def extent(axis):
        along = [sum(a * b for a, b in zip(v, axis)) for v in corners]
        return max(along) - min(along)
    distance = max(extent(up), 1e-6) / (2 * tan_h * float(fr['height_share']))
    # the edges the picture cuts: its cut_edges; an older row without them, the sides its `edges` (the uncut ones) leave
    if fr.get('cut_edges') is not None:
        cut = set(fr['cut_edges'])
    else:
        cut = {'left', 'top', 'right', 'bottom'} - set(fr['edges']) if fr.get('edges') else set()
    if fr.get('width_share') and not cut & {'left', 'right'}:
        distance = max(distance, extent(right) / (2 * tan_w * float(fr['width_share'])))
    aim = centre(box)
    eye = _sub(aim, _mul(forward, distance))
    if shows is not None:
        return _add(shows, _sub(eye, aim)), tuple(shows)
    cx, cy = (fr.get('centre') or [0.5, 0.5])
    depth = math.dist(eye, aim)
    shift = _add(_mul(right, (0.5 - cx) * 2 * tan_w * depth), _mul(up, (cy - 0.5) * 2 * tan_h * depth))
    return _add(eye, shift), _add(aim, shift)


def follow_anchors(subjects, follow):
    """Per-frame anchor the camera's offsets hang from: the subject's first place plus `follow` of its travel."""
    base = subjects[0]
    return [_add(base, _mul(_sub(s, base), follow)) for s in subjects]


MIN_TRAVEL = 0.05   # a subject moving less than this share of its size between poses is still: the move's timing drives


def roll_track(rolls, key_frames, count, progress=None):
    """Roll per frame through the pictures' rolls: on the same progress the camera's position rides (s = i on key i,
    subject_progress or the move's timing), else straight between the key frames; held before the first, after the last."""
    out = []
    for f in range(count):
        if progress is not None:
            s = progress[f]
        elif f <= key_frames[0]:
            s = 0.0
        elif f >= key_frames[-1]:
            s = float(len(key_frames) - 1)
        else:
            i = max(j for j in range(len(key_frames) - 1) if key_frames[j] <= f)
            s = i + (f - key_frames[i]) / (key_frames[i + 1] - key_frames[i])
        i = min(len(rolls) - 2, max(0, int(math.floor(s)))) if len(rolls) > 1 else 0
        u = min(1.0, max(0.0, s - i)) if len(rolls) > 1 else 0.0
        out.append(rolls[i] + (rolls[i + 1] - rolls[i]) * u if len(rolls) > 1 else rolls[0])
    return out


ROLL_RATE_MAX_DEG = 5.0   # deg/s the picture may turn: the user's reference profile turns slower in 95 % of its shots (5.4)


def limit_roll_rate(key_rolls, key_frames, count, progress, fps, max_deg_s=ROLL_RATE_MAX_DEG):
    """Key rolls whose turn between pictures stays under max_deg_s at its fastest frame (roll_track's own curve), each
    segment shrunk toward the roll it starts from. Returns (rolls, [segment index, wanted, kept]) - the limited segments.
    Why (2026-10-12): matching each picture's lean turned s20's camera 22 deg/s; the user's reference stays under ~5."""
    rolls, limited = list(key_rolls), []
    for i in range(1, len(rolls)):
        track = roll_track(rolls, key_frames, count, progress)
        a, b = key_frames[i - 1], key_frames[i]
        peak = max((abs(track[f + 1] - track[f]) * fps for f in range(a, b)), default=0.0)
        if peak > max_deg_s and peak > 0:
            wanted = rolls[i]
            rolls[i] = rolls[i - 1] + (rolls[i] - rolls[i - 1]) * max_deg_s / peak
            limited.append([i, round(wanted, 2), round(rolls[i], 2)])
    return rolls, limited


def subject_progress(subjects, key_frames, count, size):
    """Progress s(f) through the keys (s = i on key i) at the pace the subject travels: the camera hurries while the
    booster falls fast and settles as it slows. None when the subject barely moves between the poses."""
    def travelled(a, b):
        return sum(_len(_sub(subjects[f + 1], subjects[f])) for f in range(a, b))
    spans = [travelled(a, b) for a, b in zip(key_frames, key_frames[1:])]
    if not spans or min(spans) < MIN_TRAVEL * max(size, 1e-9):
        return None
    out = []
    for f in range(count):
        if f <= key_frames[0]:
            out.append(0.0)
            continue
        if f >= key_frames[-1]:
            out.append(float(len(key_frames) - 1))
            continue
        i = max(j for j in range(len(key_frames) - 1) if key_frames[j] <= f)
        out.append(i + travelled(key_frames[i], f) / spans[i])
    return out


def side_of(eye, centre_point):
    """Azimuth (deg) of an eye around a point, the camera_frame_core.basis convention (0 = camera on -Y)."""
    d = _sub(eye, centre_point)
    return math.degrees(math.atan2(d[0], -d[1]))


def eased_progress(key_frames, count, eased=True):
    """Progress s(f) through the keys (s = i exactly at key frame i): a monotone cubic through (frame_i, i) - every
    pose is reached on its own frame, the camera never stops between keys, and with `eased` it starts and settles
    softly. Held before the first key and after the last. Keys must be on distinct, increasing frames."""
    n = len(key_frames)
    if n < 2 or any(b <= a for a, b in zip(key_frames, key_frames[1:])):
        raise ValueError(f'FRAME_KEYS_COINCIDE: camera poses on frames {list(key_frames)} - each needs its own, later frame')
    slope = []
    for i in range(n):
        if 0 < i < n - 1:
            slope.append(2.0 / (key_frames[i + 1] - key_frames[i - 1]))
        else:
            span = key_frames[1] - key_frames[0] if i == 0 else key_frames[-1] - key_frames[-2]
            slope.append(0.0 if eased else 1.0 / span)
    out = []
    for f in range(count):
        if f <= key_frames[0]:
            out.append(0.0)
            continue
        if f >= key_frames[-1]:
            out.append(float(n - 1))
            continue
        i = max(j for j in range(n - 1) if key_frames[j] <= f)
        h = key_frames[i + 1] - key_frames[i]
        t = (f - key_frames[i]) / h
        out.append((2 * t ** 3 - 3 * t ** 2 + 1) * i + (t ** 3 - 2 * t ** 2 + t) * h * slope[i]
                   + (-2 * t ** 3 + 3 * t ** 2) * (i + 1) + (t ** 3 - t ** 2) * h * slope[i + 1])
    return out


def _through(values, s):
    """Uniform Catmull-Rom through values (tuples) at parameter s in [0, n-1]; reflected ends make two keys a line."""
    n = len(values)
    i = min(n - 2, max(0, int(math.floor(s))))
    t = s - i
    at = lambda k: values[k] if 0 <= k < n else (  # noqa: E731
        tuple(2 * a - b for a, b in zip(values[0], values[1])) if k < 0 else tuple(2 * a - b for a, b in zip(values[-1], values[-2])))
    p0, p1, p2, p3 = at(i - 1), at(i), at(i + 1), at(i + 2)
    return tuple(0.5 * (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t * t + (-a + 3 * b - 3 * c + d) * t ** 3)
                 for a, b, c, d in zip(p0, p1, p2, p3))


def _through_centripetal(values, s, alpha=0.5):
    """Centripetal Catmull-Rom (Barry-Goldman, knots spaced by distance ** alpha) through values at s in [0, n-1], the
    ends reflected as _through: no loop or overshoot past a key where neighbouring keys stand unevenly apart (a near
    pose then a far one; the uniform curve swings past and can dip under what the subject stands on)."""
    n = len(values)
    i = min(n - 2, max(0, int(math.floor(s))))
    t = s - i
    at = lambda k: values[k] if 0 <= k < n else (  # noqa: E731
        tuple(2 * a - b for a, b in zip(values[0], values[1])) if k < 0 else tuple(2 * a - b for a, b in zip(values[-1], values[-2])))
    p = [at(i - 1), at(i), at(i + 1), at(i + 2)]
    knots = [0.0]
    for a, b in zip(p, p[1:]):
        knots.append(knots[-1] + max(math.dist(a, b), 1e-9) ** alpha)
    t0, t1, t2, t3 = knots
    u = t1 + (t2 - t1) * t

    def lerp(a, b, ta, tb):
        w = (u - ta) / (tb - ta)
        return tuple(x + (y - x) * w for x, y in zip(a, b))
    a1, a2, a3 = lerp(p[0], p[1], t0, t1), lerp(p[1], p[2], t1, t2), lerp(p[2], p[3], t2, t3)
    b1, b2 = lerp(a1, a2, t0, t2), lerp(a2, a3, t1, t3)
    return lerp(b1, b2, t1, t2)


def _monotone(values, s):
    """A value through keys at s in [0, n-1] (s already eased: eased_progress or the move's timing), straight between
    each two: never past either key (a lens a spline swings wider than both its keys pumps)."""
    n = len(values)
    i = min(n - 2, max(0, int(math.floor(s))))
    t = min(1.0, max(0.0, s - i))
    return values[i] + (values[i + 1] - values[i]) * t


def frame_path(keys, count, path='straight', eased=True, centre_point=(0.0, 0.0, 0.0), progress=None, shape='uniform'):
    """Per-frame (eye, aim, lens) through solved poses keys = [{frame, eye, aim, lens}] (in the anchor's frame).
    straight: the eye goes through the poses on a smooth line; arc: it swings around centre_point, keeping its
    distance and turning its direction (an orbit-like move between two framings). `progress` (per frame, 0..n-1)
    replaces the eased progress - a move's own timing curve between two poses. shape 'centripetal' (an intent move):
    eye and aim on centripetal Catmull-Rom, the lens never past its keys (_monotone); 'uniform' as it always was."""
    if shape == 'centripetal':
        return _frame_path_centripetal(keys, count, path, eased, centre_point, progress)
    keys = sorted(keys, key=lambda k: k['frame'])
    s = progress if progress is not None else eased_progress([k['frame'] for k in keys], count, eased)
    eyes = [tuple(k['eye']) for k in keys]
    if path == 'arc':
        polar, previous = [], None
        for eye in eyes:
            d = _sub(eye, centre_point)
            r = _len(d)
            az, el = math.atan2(d[1], d[0]), math.asin(max(-1.0, min(1.0, d[2] / r))) if r > 0 else 0.0
            if previous is not None:   # the short way round
                az += 2 * math.pi * round((previous - az) / (2 * math.pi))
            previous = az
            polar.append((r, az, el))

        def eye_at(u):
            r, az, el = _through(polar, u)
            return _add(centre_point, (r * math.cos(el) * math.cos(az), r * math.cos(el) * math.sin(az), r * math.sin(el)))
    else:
        def eye_at(u):
            return _through(eyes, u)
    aims, lenses = [tuple(k['aim']) for k in keys], [(float(k['lens']),) for k in keys]
    return [{'eye': eye_at(u), 'aim': _through(aims, u), 'lens': _through(lenses, u)[0]} for u in s]


def _frame_path_centripetal(keys, count, path, eased, centre_point, progress):
    keys = sorted(keys, key=lambda k: k['frame'])
    s = progress if progress is not None else eased_progress([k['frame'] for k in keys], count, eased)
    eyes = [tuple(k['eye']) for k in keys]
    if path == 'arc':   # radius and height kept apart from the turn: the turn the short way round
        polar, previous = [], None
        for eye in eyes:
            d = _sub(eye, centre_point)
            az = math.atan2(d[1], d[0])
            if previous is not None:
                az += 2 * math.pi * round((previous - az) / (2 * math.pi))
            previous = az
            polar.append((math.hypot(d[0], d[1]), az, d[2]))

        def eye_at(u):
            r, az, z = _through_centripetal(polar, u)
            return _add(centre_point, (r * math.cos(az), r * math.sin(az), z))
    else:
        def eye_at(u):
            return _through_centripetal(eyes, u)
    aims, lenses = [tuple(k['aim']) for k in keys], [float(k['lens']) for k in keys]
    return [{'eye': eye_at(u), 'aim': _through_centripetal(aims, u), 'lens': _monotone(lenses, u)} for u in s]


# Joining a shot's intent poses (camera_moves._compile_frames): each moment keeps a few judged poses; the shot takes the
# combination with the lowest cost - each pose's own score plus, between moments, how fast the camera turns round the
# subject, how much nearer or farther it goes and how many lens classes it changes.
JOIN_WEIGHTS = {'turn_deg_per_s': 0.05, 'distance_log': 1.0, 'lens_step': 0.5}
JOIN_TRIES = 6                # joined paths checked (keep-out, support) before the shot is said to need a cut
ARC_TURN_DEG = 20.0           # an intent path swings round the subject when its poses' sides differ more than this ...
ARC_DISTANCE_RATIO = 1.6      # ... and they stand within this ratio of each other's distance; else a line


def join_cost(a, b, seconds, weights=JOIN_WEIGHTS):
    """Cost of going from pose a to pose b ({azimuth_deg, distance_m, lens_mm}) in `seconds` of the shot."""
    turn = abs(wrap_deg(b['azimuth_deg'] - a['azimuth_deg'])) / max(seconds, 0.1)
    far = abs(math.log(max(b['distance_m'], 1e-6) / max(a['distance_m'], 1e-6)))
    steps = abs(_rung(b['lens_mm']) - _rung(a['lens_mm']))
    return weights['turn_deg_per_s'] * turn + weights['distance_log'] * far + weights['lens_step'] * steps


def _rung(lens_mm):
    return min(range(len(LENS_LADDER)), key=lambda i: abs(math.log(lens_mm / LENS_LADDER[i])))


def joint_rank(moments, seconds, weights=JOIN_WEIGHTS, limit=JOIN_TRIES):
    """The cheapest combinations of one pose per moment, cheapest first: [(cost, [index per moment])], at most `limit`.
    moments: per moment, its poses [{score, azimuth_deg, distance_m, lens_mm}] (the per-moment winner first);
    seconds: the shot time between consecutive moments. A Viterbi over the moments with the `limit` best paths kept at
    every pose (k-best), so the next best joining is there when a joined path fails its checks."""
    best = [[(p['score'], [i])] for i, p in enumerate(moments[0])]
    for m in range(1, len(moments)):
        nxt = []
        for j, pose in enumerate(moments[m]):
            paths = sorted((cost + join_cost(moments[m - 1][path[-1]], pose, seconds[m - 1], weights) + pose['score'], path + [j])
                           for row in best for cost, path in row)
            nxt.append(paths[:limit])
        best = nxt
    return sorted((round(c, 6), path) for row in best for c, path in row)[:limit]


def intent_path(sides, distances):
    """'arc' when consecutive poses differ by more than ARC_TURN_DEG round the subject and stand within
    ARC_DISTANCE_RATIO of each other's distance (an orbit-like move); 'straight' otherwise - a push in with a small turn,
    or poses on one side."""
    for (a, b), (r1, r2) in zip(zip(sides, sides[1:]), zip(distances, distances[1:])):
        if abs(wrap_deg(b - a)) > ARC_TURN_DEG and max(r1, r2) / max(min(r1, r2), 1e-6) <= ARC_DISTANCE_RATIO:
            return 'arc'
    return 'straight'


def _plan_object_move(kind, p, geo):
    """Moves about one object, framed from its box: turntable (orbit it), slide (truck past it, aimed at it),
    macro_push (from the whole object in to a detail of it)."""
    box = _ref(geo, p['target'], 'box')
    c = centre(box)
    lens, aspect = p['_lens_mm'], _param(kind, p, 'aspect')
    far = _param(kind, p, 'distance_scale') * fit_distance(box, lens, _param(kind, p, 'fill'), aspect)
    elevation = _param(kind, p, 'elevation_deg')
    if kind == 'turntable':
        e = math.radians(elevation)
        return {'kind': 'orbit', 'waypoints': [], 'aim': c,
                'orbit': {'radius_m': p.get('radius_m', far * math.cos(e)), 'height_m': p.get('height_m', far * math.sin(e)),
                          'start_deg': _param(kind, p, 'start_deg'), 'deg_per_s': 1.0},
                'sweep_deg': _param(kind, p, 'sweep_deg'), 'notes': {'distance_m': round(far, 4)}}
    d = _dir(_param(kind, p, 'azimuth_deg'), elevation)
    if kind == 'pull_back':
        start = _add(c, _mul(d, far))
        span = _param(kind, p, 'span')
        if span <= 0:
            raise ValueError('CAMERA_MOVE: pull_back span must be positive')
        end = _add(start, _mul(d, far * span))
        return {'kind': 'flythrough', 'waypoints': [start, _v(start, end, 0.5), end], 'aim': c,
                'notes': {'retreat_m': far * span}}
    if kind == 'tilt':
        eye = _add(c, _mul(d, far))
        aims = [_ref(geo, p['aim_from']), _ref(geo, p['aim_to'])]
        return {'kind': 'flythrough', 'waypoints': [eye, eye, eye], 'aim': _v(*aims, 0.5),
                'tilt_aims': aims, 'notes': {'fixed_eye': True}}
    tan_h = 36.0 / (2 * lens) if aspect < 1 else 36.0 / (2 * lens) / aspect

    def lifted(point, distance):   # aim below the point so it shows at centre_v of the frame height
        return (point[0], point[1], point[2] - (_param(kind, p, 'centre_v') - 0.5) * 2 * tan_h * distance)
    if kind == 'slide':
        right = _norm((-d[1], d[0], 0.0))
        half = _param(kind, p, 'span') * max(size(box)[0], size(box)[1]) / 2
        eye = _add(c, _mul(d, far))
        return {'kind': 'flythrough', 'waypoints': [_add(eye, _mul(right, -half)), eye, _add(eye, _mul(right, half))], 'aim': lifted(c, far),
                'notes': {'distance_m': round(far, 4), 'span_m': round(2 * half, 4)}}
    detail = _ref(geo, p['detail']) if p.get('detail') else c        # macro_push
    detail_box = geo['boxes'].get(p['detail']) if isinstance(p.get('detail'), str) else None
    if detail_box is None and isinstance(p.get('detail'), (list, tuple)) and p['detail'] and all(isinstance(r, str) for r in p['detail']):
        detail_box = _ref(geo, p['detail'], 'box')   # several parts as one detail
    extent = p.get('detail_size_m') or (max(size(detail_box)) if detail_box else max(size(box)) * 0.3)
    near = p.get('to_m', _param(kind, p, 'distance_scale') * fit_distance(((0, 0, 0), (extent, 0, extent)), lens, _param(kind, p, 'detail_fill'), aspect))   # "closer" moves the whole push
    if near >= far:
        raise ValueError(f'CAMERA_MOVE: macro_push detail ({extent:.3f} m) is not smaller than its target; name a smaller detail or detail_size_m')
    start, end = _add(c, _mul(d, far)), _add(detail, _mul(d, near))
    return {'kind': 'flythrough', 'waypoints': [start, _v(start, end, 0.5), end], 'aim': lifted(detail, near),
            'notes': {'from_m': round(far, 4), 'to_m': round(near, 4), 'detail_size_m': round(extent, 4)}}


def realism_with_style(camera, style):
    """camera.realism with the motion style's motion-blur target filled in (an explicit realism value wins)."""
    realism = dict(camera.get('realism') or {})
    target = ((style or {}).get('defaults') or {}).get('motion_blur', {}).get('target_blur_px')
    if target is not None and 'target_blur_px' not in realism:
        realism['target_blur_px'] = target
    return realism


def flown_distance(length, timing, aimed):
    """How far along the path the rig flies over the shot: the user's timing.distance_m, else the whole path when the
    camera aims at something, else the path less its look-ahead stretch (camera_moves sets the rig the same way)."""
    return timing.get('distance_m') or (length if aimed else max(0.0, length - min(10.0, length / 4)))


def cues_for(dense, waypoints, marks, timing, move, frame_count, fps, aimed, timing_curve):
    """Where the camera passes each mark on this path: (timing, progress, mark_u, cues {'cam-<mark>': frame}).
    `aimed`: the camera aims at something (the whole path is travelled); otherwise the last look-ahead stretch is
    not. Dwell (time spent at a cue) is resolved on this path, so every cue follows the dwelled curve."""
    length = path_length(dense)
    # the distance the rig flies: the user's timing.distance_m wins (camera_moves keeps it on the rig), else the path
    # (less the look-ahead stretch when nothing is aimed at). 2026-10-12 audit: cues assumed the whole path while the
    # rig flew distance_m - a reveal bound to a mark fired half a shot early.
    travel = flown_distance(length, timing, aimed)
    mark_u = mark_progress(dense, waypoints, marks, travel)
    if move.get('dwell'):
        timing = {**timing, 'dwell': resolve_dwell(move['dwell'], mark_u, frame_count, fps)}
    progress = timing_curve(timing)
    return timing, progress, mark_u, {f'cam-{k}': pass_frame(progress, u, frame_count) for k, u in mark_u.items()}


CUE_ITERATIONS = 3   # repair <-> cue rounds before the move is refused as unstable


def resolve_dwell(dwell, mark_u, frame_count, fps=30):
    """move.dwell [{cue, seconds?, frames?}] -> timing dwell [{u, frac}] (u = where the cue's mark lies on the move)."""
    out = []
    for d in dwell:
        mark = d['cue'][4:] if d['cue'].startswith('cam-') else d['cue']
        if mark not in mark_u:
            raise ValueError(f"CAMERA_MOVE: dwell cue {d['cue']!r} is not a mark of this move (known: {sorted('cam-' + k for k in mark_u)})")
        frames = d['frames'] if d.get('frames') else round(d['seconds'] * fps)
        out.append({'u': round(mark_u[mark], 6), 'frac': round(frames / max(1, frame_count - 1), 6),
                    **({'drift': d['drift']} if 'drift' in d else {})})
    return out


def dwell_frames(timing, dwell, frame_count, rig_core):
    """[{cue, u, start_frame, end_frame}]: where each resolved dwell sits in the shot (for the report)."""
    base = rig_core._profile_curve(timing)
    knots = rig_core.dwell_knots(base, timing['dwell'])
    rows = []
    for d, resolved in zip(dwell, timing['dwell']):
        t_c = rig_core._inverse(base, resolved['u'])
        start = next((k for k in knots if abs(k[1] - t_c) < 1e-6), None)
        i = knots.index(start) if start else None
        end = knots[i + 1] if i is not None and i + 1 < len(knots) else None
        rows.append({'cue': d['cue'], 'u': resolved['u'], 'start_frame': round(start[0] * (frame_count - 1)) if start else None,
                     'end_frame': round(end[0] * (frame_count - 1)) if end else None})
    return rows


def compile_framing(framing, cues, frame_count):
    """move.framing -> rig.framing: the hold ends at a camera cue (+ offset), resolved to a shot frame."""
    release = None
    if framing.get('hold_until_cue'):
        if framing['hold_until_cue'] not in cues:
            raise ValueError(f"CAMERA_MOVE: framing.hold_until_cue {framing['hold_until_cue']!r} is not a cue of this move "
                             f'(known: {sorted(cues)})')
        release = max(0, min(frame_count - 1, cues[framing['hold_until_cue']] + framing.get('offset_frames', 0)))
    return {'horizon_v': framing['horizon_v'], 'release_frame': release, 'blend_frames': framing.get('blend_frames', 13)}


def min_distance_to_box(points, box):
    """Smallest distance from any path point to an axis-aligned box (0 inside)."""
    best = math.inf
    for q in points:
        d = [max(box[0][i] - q[i], 0.0, q[i] - box[1][i]) for i in range(3)]
        best = min(best, _len(tuple(d)))
    return best


def whip_aim(eye, targets, whip_deg, frames):
    """Aim points that start whip_deg of yaw off the target and swing onto it over `frames` (ease-out), then
    follow the target: the head of the shot reads as a whip pan arriving from the previous shot."""
    out = []
    for f, t in enumerate(targets):
        x = min(1.0, f / max(1, frames))
        angle = math.radians(whip_deg) * (1 - x) ** 2
        d = _sub(t, eye)
        c, s = math.cos(angle), math.sin(angle)
        out.append((eye[0] + d[0] * c - d[1] * s, eye[1] + d[0] * s + d[1] * c, t[2]))
    return out


def path_length(points):
    return sum(_len(_sub(b, a)) for a, b in zip(points, points[1:]))
