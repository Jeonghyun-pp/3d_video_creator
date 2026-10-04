"""Loft: skin a list of cross-sections placed along one axis.

params = {
  stations: [{s: position along axis (m),
              section: {type: ellipse|superellipse|rect|points,
                        a: half-width, b: half-height, n: superellipse exponent (2.5),
                        points: [[u, v], ...] closed polygon (section plane),
                        center: [du, dv] offset of the section in its plane}}],
  axis: 'y' (default) | 'x' | 'z',
  segments: 32,            # vertices per section
  cap_start: true, cap_end: true, smooth: true, sharp_angle_deg: 30
}

Section plane (u, v) -> world: axis y: (u, v) = (X, Z); axis x: (Y, Z); axis z: (X, Y).
Every section is densely sampled, rotated to start at the point hit by the +u
ray from its centre, made counter-clockwise, then resampled by arc length to
``segments`` points. Same start + same direction + same count => no twist.
A section with a = b = 0 (or all points equal) collapses to a pole.
"""
import math

from .primitives import mesh_object, skin

DENSE = 720
PLANE = {'x': (1, 2, 0), 'y': (0, 2, 1), 'z': (0, 1, 2)}  # (u index, v index, axis index)


def _dense(section):
    kind = section.get('type', 'ellipse')
    if kind == 'points':
        pts = [(float(p[0]), float(p[1])) for p in section['points']]
        if len(pts) < 3:
            raise ValueError('points section needs >= 3 points')
        return pts
    a, b = float(section.get('a', 0)), float(section.get('b', section.get('a', 0)))
    if kind == 'ellipse':
        e = 2.0
    elif kind == 'superellipse':
        e = float(section.get('n', 2.5))
    elif kind == 'rect':
        return [(a, 0.0), (a, b), (-a, b), (-a, -b), (a, -b)]
    else:
        raise ValueError(f'unknown section type {kind!r}')
    out = []
    for i in range(DENSE):
        t = 2 * math.pi * i / DENSE
        c, s = math.cos(t), math.sin(t)
        out.append((a * math.copysign(abs(c) ** (2 / e), c), b * math.copysign(abs(s) ** (2 / e), s)))
    return out


def _poly_area(pts):
    return 0.5 * sum(pts[i - 1][0] * pts[i][1] - pts[i][0] * pts[i - 1][1] for i in range(len(pts)))


def resample_closed(pts, count):
    """CCW, start on the +u ray from the vertex centroid, arc-length resampled."""
    pts = list(pts)
    if _poly_area(pts) < 0:
        pts.reverse()
    cu = sum(p[0] for p in pts) / len(pts)
    cv = sum(p[1] for p in pts) / len(pts)
    # start point: intersection of ray (cu,cv)+(t,0), t>0 with the polygon; take nearest crossing
    start = None
    for i in range(len(pts)):
        p, q = pts[i], pts[(i + 1) % len(pts)]
        dv = q[1] - p[1]
        if dv == 0:
            continue
        w = (cv - p[1]) / dv
        if 0 <= w < 1:
            u = p[0] + w * (q[0] - p[0])
            if u > cu and (start is None or u < start[0]):
                start = (u, i, w)
    if start is None:
        raise ValueError('cannot find section start point (degenerate polygon?)')
    _, i0, w0 = start
    p, q = pts[i0], pts[(i0 + 1) % len(pts)]
    first = (p[0] + w0 * (q[0] - p[0]), p[1] + w0 * (q[1] - p[1]))
    ring = [first] + [pts[(i0 + 1 + k) % len(pts)] for k in range(len(pts))] + [first]
    lengths = [0.0]
    for k in range(1, len(ring)):
        lengths.append(lengths[-1] + math.dist(ring[k], ring[k - 1]))
    total = lengths[-1]
    out, seg = [], 0
    for j in range(count):
        target = total * j / count
        while lengths[seg + 1] < target:
            seg += 1
        span = lengths[seg + 1] - lengths[seg]
        w = 0.0 if span == 0 else (target - lengths[seg]) / span
        out.append((ring[seg][0] + w * (ring[seg + 1][0] - ring[seg][0]),
                    ring[seg][1] + w * (ring[seg + 1][1] - ring[seg][1])))
    return out


def section_ring(section, count):
    """2D ring of ``count`` points (or a single point for a collapsed section), offset by center."""
    du, dv = (float(c) for c in section.get('center', (0.0, 0.0)))
    pts = _dense(section)
    if max(abs(p[0]) for p in pts) + max(abs(p[1]) for p in pts) < 1e-12 or \
            max(math.dist(pts[0], p) for p in pts) < 1e-12:
        return [(du + pts[0][0], dv + pts[0][1])]
    return [(u + du, v + dv) for u, v in resample_closed(pts, count)]


def loft(name, params):
    axis = str(params.get('axis', 'y')).lower()
    if axis not in PLANE:
        raise ValueError(f'{name}: axis must be x, y or z')
    iu, iv, ia = PLANE[axis]
    count = int(params.get('segments', 32))
    stations = sorted(params['stations'], key=lambda st: float(st['s']))
    if len(stations) < 2:
        raise ValueError(f'{name}: loft needs >= 2 stations')
    rings = []
    for st in stations:
        ring = []
        for u, v in section_ring(st['section'], count):
            p = [0.0, 0.0, 0.0]
            p[iu], p[iv], p[ia] = u, v, float(st['s'])
            ring.append(tuple(p))
        rings.append(ring)
    verts, faces = skin(rings, params.get('cap_start', True), params.get('cap_end', True))
    return mesh_object(name, verts, faces, params)
