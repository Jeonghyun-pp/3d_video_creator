"""Rounded corners for 2D outlines - pure Python (no bpy), shared by every section a builder reads.

``round_corners`` replaces a polygon corner by a circular arc tangent to both of its edges (the way a cast or molded
corner, a rounded tube or a filleted flange is drawn). One rule for every outline: loft / sweep sections
(``rounded_rect``, ``points`` + ``fillet_r``), extruded profiles (``{points, fillet_r}``) and revolve profiles
(``fillet_m``), so a radius means the same thing everywhere.
"""
from __future__ import annotations

import math

ARC_SEGMENTS = 8   # arc steps per rounded corner when the caller gives none


def _radii(points, radius):
    if isinstance(radius, (int, float)):
        return [float(radius)] * len(points)
    radii = [float(r) for r in radius]
    if len(radii) != len(points):
        raise ValueError(f'fillet radii: {len(radii)} values for {len(points)} corners (give one number or one per corner)')
    return radii


def round_corners(points, radius, segments=ARC_SEGMENTS, closed=True):
    """[(u, v)] with each corner i (radius[i] > 0) replaced by ``segments`` + 1 arc points.

    ``radius`` is one number for every corner or one per point. An open outline (``closed=False``) keeps its two end
    points. Straight-through corners are left as they are. Radii whose tangent lengths do not fit on an edge are
    refused (they would fold the outline) - the error names the edge.
    """
    pts = [(float(u), float(v)) for u, v in points]
    radii = _radii(pts, radius)
    if any(r < 0 for r in radii):
        raise ValueError('fillet radii must be >= 0')
    n = len(pts)
    segments = max(1, int(segments))
    corners = {}
    for i in range(n):
        if radii[i] <= 0 or (not closed and i in (0, n - 1)):
            continue
        p, a, b = pts[i], pts[i - 1], pts[(i + 1) % n]
        la, lb = math.dist(p, a), math.dist(p, b)
        if la < 1e-15 or lb < 1e-15:
            raise ValueError(f'fillet corner {i} has a zero-length edge')
        u1 = ((a[0] - p[0]) / la, (a[1] - p[1]) / la)
        u2 = ((b[0] - p[0]) / lb, (b[1] - p[1]) / lb)
        angle = math.acos(max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1])))   # interior angle between the edges
        if angle > math.pi - 1e-9:
            continue   # straight through: nothing to round
        if angle < 1e-9:
            raise ValueError(f'fillet corner {i} folds back on itself')
        d = radii[i] / math.tan(angle / 2)
        corners[i] = (u1, u2, d, la, lb)
    for i in range(n):   # each edge must hold the tangent lengths of both of its corners
        j = (i + 1) % n
        if not closed and j == 0:
            continue
        used = (corners[i][2] if i in corners else 0.0) + (corners[j][2] if j in corners else 0.0)
        length = math.dist(pts[i], pts[j])
        if used > length * (1 + 1e-9):
            raise ValueError(f'fillet radii too large for edge {i}-{j}: tangent lengths {used:.6g} m > edge {length:.6g} m')
    out = []
    for i, p in enumerate(pts):
        if i not in corners:
            out.append(p)
            continue
        u1, u2, d, _, _ = corners[i]
        t1 = (p[0] + u1[0] * d, p[1] + u1[1] * d)
        t2 = (p[0] + u2[0] * d, p[1] + u2[1] * d)
        bis = (u1[0] + u2[0], u1[1] + u2[1])
        bl = math.hypot(*bis)
        h = math.hypot(d, radii[i])   # corner to arc centre
        c = (p[0] + bis[0] / bl * h, p[1] + bis[1] / bl * h)
        a0 = math.atan2(t1[1] - c[1], t1[0] - c[0])
        a1 = math.atan2(t2[1] - c[1], t2[0] - c[0])
        sweep = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi   # the short way round (< 180 deg)
        for k in range(segments + 1):
            a = a0 + sweep * k / segments
            out.append((c[0] + radii[i] * math.cos(a), c[1] + radii[i] * math.sin(a)))
    out = [q for k, q in enumerate(out) if k == 0 or math.dist(q, out[k - 1]) > 1e-12]
    if closed and len(out) > 1 and math.dist(out[0], out[-1]) <= 1e-12:
        out.pop()
    return out


def rounded_rect(a, b, r, segments=ARC_SEGMENTS):
    """Rectangle of half-sizes a, b centred on the origin with corner radius r (r = min(a, b): a stadium)."""
    a, b, r = float(a), float(b), float(r)
    if a <= 0 or b <= 0 or not 0 <= r <= min(a, b) * (1 + 1e-9):
        raise ValueError(f'rounded_rect needs a, b > 0 and 0 <= r <= min(a, b) (got a={a} b={b} r={r})')
    return round_corners([(a, -b), (a, b), (-a, b), (-a, -b)], min(r, a, b), segments)
