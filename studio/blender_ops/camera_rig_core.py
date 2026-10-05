"""Pure camera-rig math: no bpy/mathutils, so it is unit-tested on the host.

Conventions match Blender: world Z up, quaternions (w, x, y, z), the camera looks
down its local -Z with local +Y up. Shot frames are 0-based. The rig turns a
declared shot.camera.rig plus per-frame subject samples into per-frame camera
location/rotation/lens; the Blender layer (camera_rig.py) samples and keys it.
"""
from __future__ import annotations

import math
import random

UP = (0.0, 0.0, 1.0)
G = 9.81
DEFAULT_LENS = 35.0
DEFAULT_PITCH_LIMIT = 60.0


# --- vectors -----------------------------------------------------------------
def add(a, b): return (a[0]+b[0], a[1]+b[1], a[2]+b[2])
def sub(a, b): return (a[0]-b[0], a[1]-b[1], a[2]-b[2])
def mul(a, s): return (a[0]*s, a[1]*s, a[2]*s)
def dot(a, b): return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]
def cross(a, b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
def length(a): return math.sqrt(dot(a, a))
def lerp(a, b, t): return add(a, mul(sub(b, a), t))


def normalize(a, fallback=None):
    n = length(a)
    if n < 1e-9:
        return fallback
    return (a[0]/n, a[1]/n, a[2]/n)


# --- quaternions -------------------------------------------------------------
def q_mul(a, b):
    w1, x1, y1, z1 = a; w2, x2, y2, z2 = b
    return (w1*w2 - x1*x2 - y1*y2 - z1*z2, w1*x2 + x1*w2 + y1*z2 - z1*y2,
            w1*y2 - x1*z2 + y1*w2 + z1*x2, w1*z2 + x1*y2 - y1*x2 + z1*w2)


def q_norm(q):
    n = math.sqrt(sum(c*c for c in q))
    return tuple(c/n for c in q)


def q_axis_angle(axis, angle):
    axis = normalize(axis)
    s = math.sin(angle/2)
    return (math.cos(angle/2), axis[0]*s, axis[1]*s, axis[2]*s)


def q_rotate(q, v):
    w, x, y, z = q
    p = q_mul(q_mul(q, (0.0, *v)), (w, -x, -y, -z))
    return p[1:]


def q_from_matrix(cols):
    """Rotation matrix given as its three column vectors (local X, Y, Z in world)."""
    (m00, m10, m20), (m01, m11, m21), (m02, m12, m22) = cols
    trace = m00 + m11 + m22
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        q = (0.25*s, (m21-m12)/s, (m02-m20)/s, (m10-m01)/s)
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2
        q = ((m21-m12)/s, 0.25*s, (m01+m10)/s, (m02+m20)/s)
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2
        q = ((m02-m20)/s, (m01+m10)/s, 0.25*s, (m12+m21)/s)
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2
        q = ((m10-m01)/s, (m02+m20)/s, (m12+m21)/s, 0.25*s)
    return q_norm(q)


def q_between(a, b):
    """Shortest-arc rotation taking unit vector a onto unit vector b."""
    d = dot(a, b)
    if d < -1 + 1e-9:
        axis = normalize(cross(a, (1.0, 0.0, 0.0)), None) or normalize(cross(a, (0.0, 1.0, 0.0)))
        return q_axis_angle(axis, math.pi)
    c = cross(a, b)
    return q_norm((1.0 + d, *c))


def slerp(a, b, t):
    d = sum(x*y for x, y in zip(a, b))
    if d < 0:
        b = tuple(-c for c in b); d = -d
    if d > 0.9995:
        return q_norm(tuple(x + (y-x)*t for x, y in zip(a, b)))
    theta = math.acos(d)
    s = math.sin(theta)
    wa, wb = math.sin((1-t)*theta)/s, math.sin(t*theta)/s
    return tuple(wa*x + wb*y for x, y in zip(a, b))


def quat_continuity(previous, q):
    """Keep consecutive keys in one hemisphere so linear key interpolation never spins."""
    if previous is not None and sum(x*y for x, y in zip(previous, q)) < 0:
        return tuple(-c for c in q)
    return q


def look_rotation(eye, target, roll=0.0, previous_right=None):
    """Blender to_track_quat('-Z', 'Y') followed by a roll about the view axis."""
    forward = normalize(sub(target, eye))
    if forward is None:
        raise ValueError('CAMERA_RIG: camera location equals its aim point')
    right = normalize(cross(forward, UP), None) or previous_right or (1.0, 0.0, 0.0)
    back = mul(forward, -1)
    up = cross(back, right)
    q = q_from_matrix((right, up, back))
    return q_mul(q, q_axis_angle((0, 0, 1), roll)) if roll else q


def view_tangents(lens_mm, sensor_mm, sensor_fit, width, height):
    """tan(half FOV) horizontally and vertically, following Blender's sensor fit rules."""
    t = sensor_mm / (2.0 * lens_mm)
    horizontal = sensor_fit == 'HORIZONTAL' or (sensor_fit == 'AUTO' and width >= height)
    return (t, t * height / width) if horizontal else (t * width / height, t)


def screen_ray(x, y, tan_x, tan_y):
    """Camera-space direction of normalized screen point (x, y), top-left origin."""
    return normalize(((x - 0.5) * 2 * tan_x, (0.5 - y) * 2 * tan_y, -1.0))


def screen_anchor_rotation(eye, subject, roll, x, y, tan_x, tan_y, previous_right=None):
    """Rotation that puts subject exactly at screen (x, y)."""
    track = look_rotation(eye, subject, roll, previous_right)
    return q_mul(track, q_between(screen_ray(x, y, tan_x, tan_y), (0.0, 0.0, -1.0)))


def project(q, eye, point, tan_x, tan_y):
    """Normalized top-left screen coordinates and depth of a world point."""
    w, x, y, z = q
    local = q_rotate((w, -x, -y, -z), sub(point, eye))
    depth = -local[2]
    if depth <= 1e-9:
        return None
    return (0.5 + local[0] / depth / (2 * tan_x), 0.5 - local[1] / depth / (2 * tan_y), depth)


def horizon_v(pitch_rad, tan_y):
    """Screen height (0 = top) of the horizon for a level-rolled camera pitched by pitch_rad (negative = down).
    Same value after look_camera's two-point correction, which trades the pitch for an equal lens shift."""
    return 0.5 + math.tan(pitch_rad) / (2 * tan_y)


def framing_pitch(v, tan_y):
    """Inverse of horizon_v: the pitch that puts the horizon at screen height v."""
    return math.atan((v - 0.5) * 2 * tan_y)


def framed_aims(eyes, aims, framing, tan_ys):
    """framing {horizon_v, release_frame, blend_frames}: until release_frame each aim keeps its yaw but takes the
    pitch that holds the horizon at horizon_v; over blend_frames after it the pitch eases (smoothstep) to the
    aim's own. Without a release frame the hold lasts the whole shot."""
    release, blend = framing.get('release_frame'), max(1, framing.get('blend_frames', 13))
    out = []
    for f, (eye, aim) in enumerate(zip(eyes, aims)):
        d = sub(aim, eye)
        horizontal = math.hypot(d[0], d[1])
        if horizontal < 1e-6:
            out.append(aim); continue
        own = math.atan2(d[2], horizontal)
        held = framing_pitch(framing['horizon_v'], tan_ys[f])
        w = 0.0 if release is None or f <= release else smoothstep((f - release) / blend)
        pitch = held + (own - held) * w
        out.append(add(eye, (d[0], d[1], math.tan(pitch) * horizontal)))
    return out


def pitch_deg(q):
    forward = q_rotate(q, (0.0, 0.0, -1.0))
    return math.degrees(math.asin(max(-1.0, min(1.0, forward[2]))))


# --- shaping -----------------------------------------------------------------
def smoothstep(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def flat_bump(t, mid, width, power=4):
    return math.exp(-abs((t - mid) / width) ** power)


def monotone_cubic(points):
    """Fritsch-Carlson monotone cubic through (t, u) points with increasing t: returns f(t)."""
    pts = sorted((float(t), float(u)) for t, u in points)
    if len(pts) < 2:
        raise ValueError('CAMERA_RIG: timing needs at least two points')
    ts, us = [p[0] for p in pts], [p[1] for p in pts]
    if any(b <= a for a, b in zip(ts, ts[1:])) or any(b < a for a, b in zip(us, us[1:])):
        raise ValueError('CAMERA_RIG: timing points must have increasing t and non-decreasing u')
    n = len(pts)
    d = [(us[i + 1] - us[i]) / (ts[i + 1] - ts[i]) for i in range(n - 1)]
    m = [d[0]] + [0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2 for i in range(1, n - 1)] + [d[-1]]
    for i in range(n - 1):
        if d[i] == 0:
            m[i] = m[i + 1] = 0.0
            continue
        a, b = m[i] / d[i], m[i + 1] / d[i]
        h = a * a + b * b
        if h > 9:
            k = 3 / math.sqrt(h)
            m[i], m[i + 1] = k * a * d[i], k * b * d[i]

    def f(t):
        if t <= ts[0]:
            return us[0]
        if t >= ts[-1]:
            return us[-1]
        i = max(j for j in range(n - 1) if ts[j] <= t)
        hseg = ts[i + 1] - ts[i]
        x = (t - ts[i]) / hseg
        h00, h10 = 2 * x ** 3 - 3 * x ** 2 + 1, x ** 3 - 2 * x ** 2 + x
        h01, h11 = -2 * x ** 3 + 3 * x ** 2, x ** 3 - x ** 2
        return h00 * us[i] + h10 * hseg * m[i] + h01 * us[i + 1] + h11 * hseg * m[i + 1]
    return f


TIMING_DEFAULTS = {'burst_frac': 0.25, 'burst_share': 0.65, 'hold_frac': 0.25, 'drift': 0.01}
HEAD_SHARE = 0.06  # progress made during a slow head (a drift, not a hold: a dead-still head reads as a freeze)


DWELL_DRIFT = 0.004   # share of the move still covered while the camera lingers (a drift, not a freeze)


def _inverse(curve, u):
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if curve(mid) < u else (lo, mid)
    return (lo + hi) / 2


def dwell_knots(base, dwells):
    """[(new_t, base_t)] knots of the time warp: each dwell {u, frac, drift?} gets `frac` of the shot during which the
    base curve advances only `drift` of the move past u; the moving stretches share the rest in proportion."""
    marks = sorted((_inverse(base, d['u']), d) for d in dwells)
    total = sum(d['frac'] for _, d in marks)
    if not 0 < total < 0.9:
        raise ValueError(f'CAMERA_RIG: dwell fractions must sum to (0, 0.9), got {total}')
    spans, cursor = [], 0.0                              # base-time spans: ('move', a, b) / ('dwell', a, b, frac)
    for t_c, d in marks:
        end = _inverse(base, min(1.0, d['u'] + d.get('drift', DWELL_DRIFT)))
        if t_c < cursor - 1e-9:
            raise ValueError('CAMERA_RIG: dwells overlap')
        spans += [('move', cursor, t_c), ('dwell', t_c, end, d['frac'])]
        cursor = end
    spans.append(('move', cursor, 1.0))
    moving = sum(b - a for kind, a, b, *_ in spans if kind == 'move') or 1.0
    knots, t = [(0.0, 0.0)], 0.0
    for kind, a, b, *rest in spans:
        t += rest[0] if kind == 'dwell' else (1 - total) * (b - a) / moving
        if b - a > 1e-9 or kind == 'dwell':
            knots.append((min(1.0, t), b))
    knots[-1] = (1.0, 1.0)
    out = [knots[0]]
    for k in knots[1:]:                                  # strictly increasing in both
        if k[0] > out[-1][0] + 1e-9 and k[1] >= out[-1][1]:
            out.append(k)
    return out


def timing_curve(spec):
    """Progress along a move, u(t) for t in [0, 1] -> [0, 1], monotone.

    `dwell` [{u, frac, drift?}] (resolved from move.dwell cues by compile_move) makes the camera linger at progress u
    for `frac` of the shot - the 'stop in front of the section and let it read' beat - by warping time with a
    monotone cubic, so it eases into and out of the hold. Without dwell the curve is exactly the profile's.
"""
    base = _profile_curve(spec)
    if not spec.get('dwell'):
        return base
    warp = monotone_cubic(dwell_knots(base, spec['dwell']))
    return lambda t: base(max(0.0, min(1.0, warp(t))))


def _profile_curve(spec):
    """The profile alone (see timing_curve).

    One curve, several parameterisations: 'burst_settle' rushes to burst_share of the move by burst_frac
    of the shot, decelerates, and creeps only `drift` over the last hold_frac (the 'dive in, then let it
    read' rhythm); 'ease_in_out' and 'linear' are the classic shapes; 'points' gives the curve directly."""
    profile = spec.get('profile', 'burst_settle')
    if profile == 'linear':
        return lambda t: max(0.0, min(1.0, t))
    if profile == 'ease_in_out':
        return smoothstep
    if profile == 'points':
        return monotone_cubic(spec['points'])
    if profile != 'burst_settle':
        raise ValueError(f'CAMERA_RIG: unknown timing profile {profile!r}')
    p = {**TIMING_DEFAULTS, **{k: v for k, v in spec.items() if k in TIMING_DEFAULTS}}
    b, share, hold, drift = p['burst_frac'], p['burst_share'], p['hold_frac'], p['drift']
    # Optional slow head: creep head_share of the move over head_frac (something has to happen first - a
    # road opening, a title reading), then the burst runs over the rest. Absent = the classic rhythm.
    h, hs = spec.get('head_frac', 0.0), spec.get('head_share', HEAD_SHARE) if spec.get('head_frac') else 0.0
    if not (0 < b and h + b < 1 - hold <= 1 and 0 < share < 1 - drift <= 1 and hold >= 0 and 0 <= h and 0 <= hs < share):
        raise ValueError(f'CAMERA_RIG: burst_settle needs 0 < burst_frac, head_frac + burst_frac < 1 - hold_frac and 0 < burst_share < 1 - drift ({p})')
    rest = 1 - hs
    points = [(0.0, 0.0)] + ([(h, hs)] if h > 0 else []) + [(h + b * 0.4, hs + share * 0.62 * rest), (h + b, hs + share * rest)] + \
        ([(1 - hold, 1 - drift)] if hold > 0 else []) + [(1.0, 1.0)]
    return monotone_cubic(points)



def sample_keys(keys, frame, field, default):
    """Value of a keyed field at frame: held before/after, eased between keys."""
    if not keys:
        return default
    if frame <= keys[0]['frame']:
        return keys[0][field]
    for a, b in zip(keys, keys[1:]):
        if frame <= b['frame']:
            u = (frame - a['frame']) / (b['frame'] - a['frame'])
            u = u if b.get('ease') == 'linear' else smoothstep(u)
            va, vb = a[field], b[field]
            if isinstance(va, (list, tuple)):
                return tuple(p + (q - p) * u for p, q in zip(va, vb))
            return va + (vb - va) * u
    return keys[-1][field]


def _line_fit(points, at):
    """Least-squares line through samples: (value at index `at`, per-sample velocity)."""
    n = len(points)
    mean_i = (n - 1) / 2
    var = sum((i - mean_i) ** 2 for i in range(n)) or 1.0
    mean_p = mul(tuple(map(sum, zip(*points))), 1.0 / n)
    velocity = mul(tuple(map(sum, zip(*[mul(sub(p, mean_p), i - mean_i) for i, p in enumerate(points)]))), 1.0 / var)
    return add(mean_p, mul(velocity, at - mean_i)), velocity


def zero_phase_smooth(points, tau_s, fps):
    """Forward+backward first-order filter: smooths jitter without lagging the subject.

    The ends are padded by linear extrapolation so constant velocity passes through.
    """
    if not tau_s or len(points) < 3:
        return [tuple(p) for p in points]
    alpha = 1 - math.exp(-1.0 / (tau_s * fps))
    pad = int(math.ceil(12 * tau_s * fps))
    window = max(2, min(len(points), 2 * int(math.ceil(tau_s * fps)) + 1))
    head_p, head_v = _line_fit(points[:window], 0)
    tail_p, tail_v = _line_fit(points[-window:], window - 1)
    padded = [add(head_p, mul(head_v, -k)) for k in range(pad, 0, -1)] + [tuple(p) for p in points] + \
             [add(tail_p, mul(tail_v, k)) for k in range(1, pad + 1)]
    for sequence in (range(len(padded)), range(len(padded) - 1, -1, -1)):
        state = None
        for i in sequence:
            state = padded[i] if state is None else lerp(state, padded[i], alpha)
            padded[i] = state
    return padded[pad:pad + len(points)]


# --- paths -------------------------------------------------------------------
def arc_length_table(points):
    table = [0.0]
    for a, b in zip(points, points[1:]):
        table.append(table[-1] + length(sub(b, a)))
    return table


def eval_by_arclength(points, table, s):
    s = max(0.0, min(table[-1], s))
    lo, hi = 0, len(table) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if table[mid] <= s:
            lo = mid
        else:
            hi = mid
    span = table[hi] - table[lo]
    return lerp(points[lo], points[hi], 0.0 if span <= 0 else (s - table[lo]) / span)


def headings(points, fallback=(0.0, 1.0, 0.0)):
    """Horizontal travel direction per sample (central differences, held when stopped)."""
    result, previous = [], None
    for i in range(len(points)):
        a, b = points[max(0, i - 1)], points[min(len(points) - 1, i + 1)]
        d = sub(b, a)
        h = normalize((d[0], d[1], 0.0), None)
        previous = h or previous
        result.append(previous)
    first = next((h for h in result if h), fallback)
    return [h or first for h in result]


def turn_rates(heading_list, fps):
    """Signed yaw rate (rad/s, +CCW seen from above) of a heading sequence."""
    rates = []
    for i in range(len(heading_list)):
        a, b = heading_list[max(0, i - 1)], heading_list[min(len(heading_list) - 1, i + 1)]
        span = (min(len(heading_list) - 1, i + 1) - max(0, i - 1)) / fps
        angle = math.atan2(a[0]*b[1] - a[1]*b[0], a[0]*b[0] + a[1]*b[1])
        rates.append(angle / span if span else 0.0)
    return rates


def coordinated_bank(speed, yaw_rate, cap_deg=75.0):
    """Coordinated-turn bank angle tan(phi) = v*omega/g (FAA PHAK ch.5), capped."""
    cap = math.radians(cap_deg)
    return max(-cap, min(cap, math.atan(speed * yaw_rate / G)))


def orbit_eye(center, radius, height, angle):
    return (center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle), center[2] + height)


# --- bake --------------------------------------------------------------------
def _shake(rng_seed, amp_deg, freq_hz):
    rng = random.Random(rng_seed)
    phases = [rng.uniform(0, 2 * math.pi) for _ in range(4)]
    amp = math.radians(amp_deg)

    def at(t):
        w = 2 * math.pi * freq_hz * t
        return (amp * (0.6 * math.sin(w + phases[0]) + 0.4 * math.sin(2.3 * w + phases[1])),
                amp * (0.6 * math.sin(1.1 * w + phases[2]) + 0.4 * math.sin(2.9 * w + phases[3])))
    return at


def bake(rig, fps, frame_count, subject=None, target=None, path=None, view=None, default_lens=DEFAULT_LENS, overrides=None):
    """Deterministic per-frame camera for a rig.

    subject/target: lists of world positions per frame (None when unused).
    path: polyline points for flythrough. view: (sensor_mm, sensor_fit, width, height).
    overrides: optional per-frame dicts from a procedural camera_state(t, ctx):
      offset_m, blend, lift_m, lens_mm, roll_deg.
    Returns frames [{location, rotation, lens, roll_deg, aim}] and derived subject data.
    """
    kind = rig['type']
    overrides = overrides or [{}] * frame_count
    timing = rig.get('timing')
    progress = timing_curve(timing) if timing else None
    u_at = (lambda f: progress(f / max(1, frame_count - 1))) if progress else None
    if kind == 'flythrough':
        table = arc_length_table(path)
        start = rig.get('start_offset_m', 0.0)
        ahead = rig.get('look_ahead_m', 10.0)
        if progress:  # timed: the whole move covers distance_m (default: the path past start, minus look-ahead)
            distance = timing.get('distance_m', max(0.0, table[-1] - start - ahead))
            travel = [start + distance * u_at(f) for f in range(frame_count)]
        else:
            travel = [start + rig['speed_mps'] * f / fps for f in range(frame_count)]
        subject = [eval_by_arclength(path, table, s) for s in travel]
        target_path = [eval_by_arclength(path, table, s + ahead) for s in travel]
        if target is None:
            target = target_path
    if subject is None or len(subject) != frame_count:
        raise ValueError('CAMERA_RIG: subject samples missing')
    heading = headings(subject)
    if kind == 'follow':
        heading = [heading[0]] * frame_count
    speeds = [length(sub(subject[min(frame_count - 1, i + 1)], subject[max(0, i - 1)])) * fps /
              max(1, min(frame_count - 1, i + 1) - max(0, i - 1)) for i in range(frame_count)]
    bank = [coordinated_bank(v, w) for v, w in zip(speeds, turn_rates(headings(subject), fps))]
    roll_cfg = rig.get('roll') or {'follow_bank': 0.0, 'max_deg': 0.0}
    smoothing = rig.get('smoothing') or {}
    default_offset = (0.0, 0.0, 0.0) if kind == 'flythrough' else (0.0, 2.0, 8.0)
    lift_keys = [k for k in rig.get('aim_keys') or [] if 'lift_m' in k]
    eyes, aims, rolls, lenses = [], [], [], []
    for f in range(frame_count):
        o = overrides[f]
        t = f / fps
        kf = u_at(f) * (frame_count - 1) if progress and timing.get('scope') == 'all' else f  # keys ride the same rush
        p = subject[f]
        if kind == 'orbit':
            spec = rig['orbit']
            angle = spec['start_deg'] + (rig['sweep_deg'] * u_at(f) if progress and 'sweep_deg' in rig else spec['deg_per_s'] * t)
            eye = orbit_eye(p, spec['radius_m'], spec['height_m'], math.radians(angle))
        else:
            ox, oy, oz = o.get('offset_m') or sample_keys(rig.get('offset_keys'), kf, 'offset_m', default_offset)
            forward = heading[f]
            right = normalize(cross(forward, UP))
            eye = add(add(add(p, mul(right, ox)), mul(UP, oy)), mul(forward, -oz))
        blend = o.get('blend', sample_keys(rig.get('aim_keys'), kf, 'blend', 1.0 if kind == 'flythrough' else 0.0))
        lift = o.get('lift_m', sample_keys(lift_keys, kf, 'lift_m', 0.0))
        aim = add(lerp(p, target[f], blend) if target else p, (0.0, 0.0, lift))
        roll = math.radians(o['roll_deg']) if 'roll_deg' in o else \
            max(-math.radians(roll_cfg['max_deg']), min(math.radians(roll_cfg['max_deg']), bank[f] * roll_cfg['follow_bank']))
        eyes.append(eye); aims.append(aim); rolls.append(roll)
        lenses.append(o.get('lens_mm', sample_keys(rig.get('lens_keys'), kf, 'lens_mm', default_lens)))
    eyes = zero_phase_smooth(eyes, smoothing.get('position_s', 0.0), fps)
    aims = zero_phase_smooth(aims, smoothing.get('aim_s', 0.0), fps)
    if rig.get('framing'):
        aims = framed_aims(eyes, aims, rig['framing'], [view_tangents(lens, *view)[1] for lens in lenses])
    limit = math.radians(rig.get('pitch_limit_deg', DEFAULT_PITCH_LIMIT))
    anchor = rig.get('screen_anchor')
    shake = _shake(rig['shake']['seed'], rig['shake']['amp_deg'], rig['shake']['freq_hz']) if rig.get('shake') else None
    frames, previous, previous_right = [], None, None
    for f in range(frame_count):
        eye, aim, roll, lens = eyes[f], aims[f], rolls[f], lenses[f]
        d = sub(aim, eye)
        horizontal = math.hypot(d[0], d[1])
        if abs(math.atan2(d[2], horizontal)) > limit:
            if horizontal < 1e-6:
                h = heading[f]; d = (h[0], h[1], d[2]); horizontal = 1.0
            aim = add(eye, (d[0], d[1], math.copysign(math.tan(limit) * horizontal, d[2])))
        q = look_rotation(eye, aim, roll, previous_right)
        if anchor and anchor['weight'] > 0:
            tan_x, tan_y = view_tangents(lens, *view)
            composed = screen_anchor_rotation(eye, subject[f], roll, anchor['x'], anchor['y'], tan_x, tan_y, previous_right)
            q = slerp(q, composed, anchor['weight'])
        if shake:
            sp, sy = shake(f / fps)
            q = q_mul(q, q_mul(q_axis_angle((1, 0, 0), sp), q_axis_angle((0, 1, 0), sy)))
        q = quat_continuity(previous, q_norm(q))
        previous = q
        previous_right = q_rotate(q, (1.0, 0.0, 0.0))
        frames.append({'location': eye, 'rotation': q, 'lens': lens, 'roll_deg': math.degrees(roll), 'aim': aim})
    return {'frames': frames, 'subject': subject, 'target': target, 'heading': heading, 'speed_mps': speeds,
            'bank_deg': [math.degrees(b) for b in bank]}
