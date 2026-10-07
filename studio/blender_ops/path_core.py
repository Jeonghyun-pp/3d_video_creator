"""Smooth paths through given points - pure Python (no bpy), for sweeps (runners, hoses, cables, handles).

``catmull_rom`` is the centripetal Catmull-Rom spline (alpha 0.5): it passes through every point, never forms cusps or
self-loops inside a segment, and does not overshoot as the uniform variant does on uneven spacing. ``scale_at`` reads a
piecewise-linear profile scale along the normalised path length.
"""
from __future__ import annotations

import math


def _knot(t, a, b):
    return t + max(math.dist(a, b), 1e-12) ** 0.5


def catmull_rom(points, samples=8, closed=False):
    """Points along the spline: ``samples`` per segment, every input point kept (exactly) in the output."""
    pts = [tuple(float(c) for c in p) for p in points]
    n = len(pts)
    if n < 2:
        raise ValueError('a smooth path needs >= 2 points')
    if any(math.dist(pts[i], pts[i + 1]) < 1e-12 for i in range(n - 1)):
        raise ValueError('a smooth path has coincident consecutive points')
    samples = max(1, int(samples))
    if closed:
        ext = [pts[-1]] + pts + [pts[0], pts[1]]
        segments = n
    else:   # mirrored end points: the spline leaves the ends along the first / last segment
        first = tuple(2 * a - b for a, b in zip(pts[0], pts[1]))
        last = tuple(2 * a - b for a, b in zip(pts[-1], pts[-2]))
        ext = [first] + pts + [last]
        segments = n - 1
    out = []
    for i in range(segments):
        p0, p1, p2, p3 = ext[i], ext[i + 1], ext[i + 2], ext[i + 3]
        t0 = 0.0
        t1 = _knot(t0, p0, p1); t2 = _knot(t1, p1, p2); t3 = _knot(t2, p2, p3)
        for k in range(samples):
            t = t1 + (t2 - t1) * k / samples
            a1 = [((t1 - t) * x + (t - t0) * y) / (t1 - t0) for x, y in zip(p0, p1)]
            a2 = [((t2 - t) * x + (t - t1) * y) / (t2 - t1) for x, y in zip(p1, p2)]
            a3 = [((t3 - t) * x + (t - t2) * y) / (t3 - t2) for x, y in zip(p2, p3)]
            b1 = [((t2 - t) * x + (t - t0) * y) / (t2 - t0) for x, y in zip(a1, a2)]
            b2 = [((t3 - t) * x + (t - t1) * y) / (t3 - t1) for x, y in zip(a2, a3)]
            out.append(p1 if k == 0 else tuple(((t2 - t) * x + (t - t1) * y) / (t2 - t1) for x, y in zip(b1, b2)))
    if not closed:
        out.append(pts[-1])
    return out


def scale_at(scale, u):
    """Profile scale at normalised length u (0..1): a number, or [[u, s], ...] interpolated linearly (held at the ends)."""
    if isinstance(scale, (int, float)):
        return float(scale)
    keys = sorted((float(a), float(b)) for a, b in scale)
    if not keys:
        return 1.0
    if u <= keys[0][0]:
        return keys[0][1]
    for (ua, sa), (ub, sb) in zip(keys, keys[1:]):
        if u <= ub:
            return sa + (sb - sa) * (u - ua) / (ub - ua) if ub > ua else sb
    return keys[-1][1]
