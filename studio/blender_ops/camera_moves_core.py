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
ARC_FROM_DEG = 5.0      # path auto: poses whose sides differ by more than this swing around the subject
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


def frame_edge_seed(box, frame, lens_mm, aspect):
    """Fallback beside a pictured edge, rather than the hidden centre of a cropped long subject."""
    import camera_frame_core as framing
    edges = frame.get('edges') or {}
    if not (('left' in edges and 'right' in edges) or ('top' in edges and 'bottom' in edges)):
        return None
    forward, right, up = framing.basis(frame['view']['elevation_deg'], frame['view'].get('azimuth_deg', 0.0))
    tan_w, tan_h = framing.tangents(lens_mm, aspect)
    corners = [(x, y, z) for x in (box[0][0], box[1][0]) for y in (box[0][1], box[1][1]) for z in (box[0][2], box[1][2])]
    rs = [sum(a * b for a, b in zip(v, right)) for v in corners]
    us = [sum(a * b for a, b in zip(v, up)) for v in corners]
    if 'top' in edges and 'bottom' in edges:
        depth = (max(us) - min(us)) / (2 * tan_h * (edges['bottom'] - edges['top']))
    else:
        depth = (max(rs) - min(rs)) / (2 * tan_w * (edges['right'] - edges['left']))
    def offset(values, lo, hi, tangent):
        if lo in edges and hi in edges:
            return (min(values) + max(values)) / 2 + (0.5 - (edges[lo] + edges[hi]) / 2) * 2 * tangent * depth
        if lo in edges:
            return min(values) + (0.5 - edges[lo]) * 2 * tangent * depth
        if hi in edges:
            return max(values) + (0.5 - edges[hi]) * 2 * tangent * depth
        return (min(values) + max(values)) / 2
    # Frame y runs downwards; its top/bottom therefore correspond to max/min world-up.
    right_at = offset(rs, 'left', 'right', tan_w)
    up_at = -offset([-u for u in us], 'top', 'bottom', tan_h)
    c = centre(box)
    dr = right_at - sum(a * b for a, b in zip(c, right))
    du = up_at - sum(a * b for a, b in zip(c, up))
    return tuple(c[i] - forward[i] * depth + right[i] * dr + up[i] * du for i in range(3))


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
    softly. Held before the first key and after the last."""
    n = len(key_frames)
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


def frame_path(keys, count, path='straight', eased=True, centre_point=(0.0, 0.0, 0.0), progress=None):
    """Per-frame (eye, aim, lens) through solved poses keys = [{frame, eye, aim, lens}] (in the anchor's frame).
    straight: the eye goes through the poses on a smooth line; arc: it swings around centre_point, keeping its
    distance and turning its direction (an orbit-like move between two framings). `progress` (per frame, 0..n-1)
    replaces the eased progress - a move's own timing curve between two poses."""
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


def cues_for(dense, waypoints, marks, timing, move, frame_count, fps, aimed, timing_curve):
    """Where the camera passes each mark on this path: (timing, progress, mark_u, cues {'cam-<mark>': frame}).
    `aimed`: the camera aims at something (the whole path is travelled); otherwise the last look-ahead stretch is
    not. Dwell (time spent at a cue) is resolved on this path, so every cue follows the dwelled curve."""
    length = path_length(dense)
    travel = length if aimed else max(0.0, length - min(10.0, length / 4))
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
