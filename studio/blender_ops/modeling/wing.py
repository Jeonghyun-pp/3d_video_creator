"""Wing / blade / fin / hydrofoil: a lofted airfoil with planform laws.

params = {
  span: tip-to-tip if mirror else root-to-tip, projected on the span axis (m),
  root_chord, tip_chord (m),
  sweep_deg: sweep of the quarter-chord line (positive = tips aft, toward -chord),
  dihedral_deg: quarter-chord line rise (sections stay parallel to the root
                plane, so bbox on span axis == span exactly),
  airfoil: 'NACA2415' | 'NACA23015' | '0012' | {points: [[x, y], ...]} (chord-normalised,
           any loop order; open trailing edges are closed at the mean TE point),
  tip_airfoil: optional, blended linearly with span fraction,
  incidence_deg: root nose-up angle (0),
  washout_deg: tip nose-down twist, linear in span fraction (0),
  twist_deg: optional [[span_fraction, deg nose-up], ...] piecewise-linear extra twist
             (propeller / fan blades),
  mirror: true, sections: 12 (per half), chord_points: 40 (cosine spacing per surface),
  span_axis: 'x', chord_axis: 'y' (leading edge toward +), thickness_axis: 'z'
             (each may be prefixed '-' to flip),
  tip: 'square' | 'round', elliptic: false, smooth: true, sharp_angle_deg: SHARP_ANGLE_DEG (31)
}
Twist rotates each section about its quarter-chord point. Origin = root quarter chord.
"""
import math

from .primitives import mesh_object, skin

FIVE_DIGIT = {1: (0.05, 0.0580, 361.4), 2: (0.10, 0.1260, 51.64), 3: (0.15, 0.2025, 15.957),
              4: (0.20, 0.2900, 6.643), 5: (0.25, 0.3910, 3.230)}
ROUND_TIP_END_DEG = 85.0
ROUND_TIP_STEPS = 4


def _thickness(x, t):
    return 5 * t * (0.2969 * math.sqrt(x) - 0.1260 * x - 0.3516 * x ** 2 + 0.2843 * x ** 3 - 0.1036 * x ** 4)


def _camber_fn(code):
    """Return f(x) -> (yc, dyc/dx) and thickness ratio for a NACA 4/5-digit code."""
    if len(code) == 4:
        m, p, t = int(code[0]) / 100, int(code[1]) / 10, int(code[2:]) / 100

        def camber(x):
            if m == 0 or p == 0:
                return 0.0, 0.0
            if x < p:
                return m / p ** 2 * (2 * p * x - x * x), 2 * m / p ** 2 * (p - x)
            return m / (1 - p) ** 2 * (1 - 2 * p + 2 * p * x - x * x), 2 * m / (1 - p) ** 2 * (p - x)
        return camber, t
    if len(code) == 5:
        lift, pos, reflex, t = int(code[0]), int(code[1]), int(code[2]), int(code[3:]) / 100
        if reflex or pos not in FIVE_DIGIT:
            raise ValueError(f'NACA {code}: only non-reflexed 5-digit mean lines (2x0xx) supported')
        _, m, k1 = FIVE_DIGIT[pos]
        scale = lift / 2.0  # table is for design CL 0.3 (first digit 2)

        def camber(x):
            if x < m:
                return scale * k1 / 6 * (x ** 3 - 3 * m * x ** 2 + m * m * (3 - m) * x), \
                    scale * k1 / 6 * (3 * x * x - 6 * m * x + m * m * (3 - m))
            return scale * k1 * m ** 3 / 6 * (1 - x), -scale * k1 * m ** 3 / 6
        return camber, t
    raise ValueError(f'NACA code must have 4 or 5 digits, got {code!r}')


def _cosine_x(n):
    return [0.5 * (1 - math.cos(math.pi * i / (n - 1))) for i in range(n)]


def _surfaces(airfoil, n):
    """(upper, lower): lists of n (x, z) points from LE to TE, sharing LE and TE."""
    xs = _cosine_x(n)
    if isinstance(airfoil, str):
        code = airfoil.upper().replace('NACA', '').strip()
        if not code.isdigit():
            raise ValueError(f'bad airfoil {airfoil!r}')
        camber, t = _camber_fn(code)
        up, lo = [], []
        for x in xs:
            yc, dy = camber(x)
            yt, th = _thickness(x, t), math.atan(dy)
            up.append((x - yt * math.sin(th), yc + yt * math.cos(th)))
            lo.append((x + yt * math.sin(th), yc - yt * math.cos(th)))
        up[-1] = lo[-1] = (1.0, camber(1.0)[0])
        up[0] = lo[0] = (0.0, 0.0)
        return up, lo
    pts = [(float(p[0]), float(p[1])) for p in airfoil['points']]
    le = min(range(len(pts)), key=lambda i: (pts[i][0], pts[i][1]))
    pts = pts[le:] + pts[:le]
    te = max(range(len(pts)), key=lambda i: pts[i][0])
    chains = [sorted(chain, key=lambda p: p[0]) for chain in (pts[:te + 1], pts[te:] + [pts[0]])]

    def interp(chain, x):
        for k in range(1, len(chain)):
            if chain[k][0] >= x:
                x0, z0 = chain[k - 1]
                x1, z1 = chain[k]
                return z0 if x1 == x0 else z0 + (z1 - z0) * (x - x0) / (x1 - x0)
        return chain[-1][1]
    x0, x1 = chains[0][0][0], max(chains[0][-1][0], chains[1][-1][0])
    sampled = [[(x, interp(c, x0 + x * (x1 - x0))) for x in xs] for c in chains]
    if sum(p[1] for p in sampled[0]) < sum(p[1] for p in sampled[1]):
        sampled.reverse()
    up, lo = sampled
    le_z, te_z = 0.5 * (up[0][1] + lo[0][1]), 0.5 * (up[-1][1] + lo[-1][1])
    up[0] = lo[0] = (0.0, le_z)
    up[-1] = lo[-1] = (1.0, te_z)
    return up, lo


def airfoil_ring(airfoil, n):
    """Closed ring TE -> upper -> LE -> lower (2n-2 points) and the opposite-surface index map."""
    up, lo = _surfaces(airfoil, n)
    ring = list(reversed(up)) + lo[1:-1]
    m = len(ring)
    partner = [0] * m
    for i in range(n):  # upper[i] sits at ring index n-1-i; lower[i] at n-1+i (i in 1..n-2)
        u = n - 1 - i
        partner[u] = u if i in (0, n - 1) else n - 1 + i
        if 0 < i < n - 1:
            partner[n - 1 + i] = u
    return ring, partner


def _axis(spec):
    spec = str(spec).lower()
    sign = -1.0 if spec.startswith('-') else 1.0
    return 'xyz'.index(spec.lstrip('+-')), sign


def _interp_table(table, x):
    table = sorted((float(a), float(b)) for a, b in table)
    if x <= table[0][0]:
        return table[0][1]
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return table[-1][1]


def wing(name, params):
    span = float(params['span'])
    mirror = bool(params.get('mirror', True))
    half = span / 2 if mirror else span
    root_c = float(params['root_chord'])
    tip_c = float(params.get('tip_chord', root_c))
    if span <= 0 or root_c <= 0 or tip_c < 0:
        raise ValueError(f'{name}: span/chords must be positive')
    sweep = math.tan(math.radians(float(params.get('sweep_deg', 0))))
    dihedral = math.tan(math.radians(float(params.get('dihedral_deg', 0))))
    n_chord = int(params.get('chord_points', 40))
    n_sec = max(1, int(params.get('sections', 12)))
    elliptic = bool(params.get('elliptic', False))
    root_ring, partner = airfoil_ring(params.get('airfoil', 'NACA0012'), n_chord)
    tip_ring = airfoil_ring(params['tip_airfoil'], n_chord)[0] if params.get('tip_airfoil') else root_ring
    axes = [_axis(params.get(k, d)) for k, d in (('span_axis', 'x'), ('chord_axis', 'y'), ('thickness_axis', 'z'))]
    if sorted(a[0] for a in axes) != [0, 1, 2]:
        raise ValueError(f'{name}: span/chord/thickness axes must be a permutation of x, y, z')

    def chord(eta):
        c = root_c + (tip_c - root_c) * eta
        return max(root_c * math.sqrt(max(0.0, 1 - eta * eta)), tip_c) if elliptic else c

    def twist(eta):
        deg = float(params.get('incidence_deg', 0)) - float(params.get('washout_deg', 0)) * eta
        if params.get('twist_deg'):
            deg += _interp_table(params['twist_deg'], eta)
        return math.radians(deg)

    round_tip = params.get('tip', 'square') == 'round'
    tip_t = max(abs(tip_ring[i][1] - tip_ring[partner[i]][1]) for i in range(len(tip_ring))) * chord(1.0)
    r_tip = 0.5 * tip_t if round_tip else 0.0
    main = half - r_tip * math.sin(math.radians(ROUND_TIP_END_DEG))

    def section(eta, x, squash=1.0):
        """eta: span fraction (planform laws); x: canonical span coordinate; squash: thickness scale."""
        c, a = chord(eta), twist(eta)
        y0, z0 = -eta * main * sweep, eta * main * dihedral
        ring = [tuple(root_ring[i][k] + (tip_ring[i][k] - root_ring[i][k]) * eta for k in (0, 1))
                for i in range(len(root_ring))]
        out = []
        for i, (xc, zc) in enumerate(ring):
            zm = 0.5 * (zc + ring[partner[i]][1])
            zc = zm + (zc - zm) * squash
            dy, dz = (0.25 - xc) * c, zc * c
            canon = (x, y0 + dy * math.cos(a) - dz * math.sin(a), z0 + dy * math.sin(a) + dz * math.cos(a))
            p = [0.0, 0.0, 0.0]
            for (idx, sign), value in zip(axes, canon):
                p[idx] = sign * value
            out.append(tuple(p))
        return out

    etas = [k / n_sec for k in range(n_sec + 1)]
    if elliptic:
        etas = [math.sin(0.5 * math.pi * e) for e in etas]
    half_rings = [(e, e * main, 1.0) for e in etas]
    if round_tip:
        for j in range(1, ROUND_TIP_STEPS + 1):
            phi = math.radians(ROUND_TIP_END_DEG) * j / ROUND_TIP_STEPS
            half_rings.append((1.0, main + r_tip * math.sin(phi), math.cos(phi)))
    if mirror:
        rows = [(e, -x, s) for e, x, s in reversed(half_rings[1:])] + half_rings
    else:
        rows = half_rings
    rings = [section(e, x, s) for e, x, s in rows]
    verts, faces = skin(rings, True, True)
    return mesh_object(name, verts, faces, params)
