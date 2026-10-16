"""Registering a picture painted over a clay render back onto it (numpy only; Blender's bundled python runs it):
edges by a small Canny, F1 of edges against a reference line image within a tolerance, and the one global
similarity (scale about the frame centre + shift) that lays the painting's edges on the reference lines best.

Why (2026-10-16 pilot, studio/picture3d.py): an image model painting over a grey 3D render keeps the render's parts
and states, but hands the frame back re-framed - moved 5-12 % and rescaled 0.9-1.25 - so a painting is laid back onto
the render it was painted over before anything reads it pixel by pixel. One similarity, not a free warp: a painting
that only fits by bending has changed the structure, and must be painted again (REGISTER_LIMIT, picture3d).
"""
from __future__ import annotations

import numpy as np

TOL_PX = 3                 # an edge within this many pixels of a reference line matches it (at the 720 px measure)
SCALES = (0.80, 1.35, 0.025)   # the similarity search: scales tried, as (lowest, highest, step) - the pilot saw 0.9-1.25, a
                               # trial 1.27; a fit at either end (the refine may step past it) is not trusted (at_search_edge)
SHIFT_RANGE = 0.18             # ... and shifts up to this share of the frame each way: past picture3d.REGISTER_LIMIT (0.10),
                               # so a painting moved too far is found and refused, not mis-fitted at the search's edge
POOL = 8                       # the coarse search runs on edges pooled by this factor (one step = POOL px), then 1 px


def shift(a, dy, dx, fill=0):
    h, w = a.shape
    out = np.full_like(a, fill)
    ys, yd = slice(max(dy, 0), h + min(dy, 0)), slice(max(-dy, 0), h + min(-dy, 0))
    xs, xd = slice(max(dx, 0), w + min(dx, 0)), slice(max(-dx, 0), w + min(-dx, 0))
    out[ys, xs] = a[yd, xd]
    return out


def dilate(b, r):
    out = b.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dy or dx:
                out |= shift(b, dy, dx, False)
    return out


def blur(a, sigma=1.4):
    h, w = a.shape
    r = int(3 * sigma)
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    p = np.pad(a, r, mode='edge')
    a = sum(k[i] * p[r:-r, i:i + w] for i in range(2 * r + 1))
    p = np.pad(a, ((r, r), (0, 0)), mode='edge')
    return sum(k[i] * p[i:i + h, :] for i in range(2 * r + 1))


def canny(gray, hi_pct=85.0, lo_ratio=0.5):
    """Edges of a grey image (floats 0-1): blur, Sobel, non-maximum suppression, hysteresis from the hi_pct percentile."""
    g = blur(gray)
    p = np.pad(g, 1, mode='edge')
    gx = (p[:-2, 2:] + 2 * p[1:-1, 2:] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[1:-1, :-2] + p[2:, :-2])
    gy = (p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[:-2, 1:-1] + p[:-2, 2:])
    mag = np.hypot(gx, gy)
    ang = (np.rad2deg(np.arctan2(gy, gx)) + 180) % 180
    q = np.zeros(mag.shape, int)
    q[(ang >= 22.5) & (ang < 67.5)] = 1
    q[(ang >= 67.5) & (ang < 112.5)] = 2
    q[(ang >= 112.5) & (ang < 157.5)] = 3
    neighbours = {0: ((0, 1), (0, -1)), 1: ((1, 1), (-1, -1)), 2: ((1, 0), (-1, 0)), 3: ((1, -1), (-1, 1))}
    keep = np.zeros(mag.shape, bool)
    for d, ((ay, ax), (by, bx)) in neighbours.items():
        keep |= (q == d) & (mag >= shift(mag, ay, ax)) & (mag >= shift(mag, by, bx))
    nms = np.where(keep, mag, 0)
    cand = nms[nms > 1e-4]
    if cand.size == 0:
        return np.zeros(mag.shape, bool)
    hi = max(np.percentile(cand, hi_pct), 0.02)
    strong, weak = nms >= hi, nms >= hi * lo_ratio
    edge = strong.copy()
    for _ in range(500):
        grown = dilate(edge, 1) & weak
        if (grown == edge).all():
            break
        edge = grown
    edge[:2, :] = edge[-2:, :] = False
    edge[:, :2] = edge[:, -2:] = False
    return edge


def f1(pred, ref, tol=TOL_PX):
    """{precision, recall, f1, edge_px} of predicted edges against reference lines, within `tol` px; None values when
    either is empty."""
    if not pred.any() or not ref.any():
        return {'precision': None, 'recall': None, 'f1': None, 'edge_px': int(pred.sum())}
    p = float((pred & dilate(ref, tol)).sum() / pred.sum())
    r = float((ref & dilate(pred, tol)).sum() / ref.sum())
    return {'precision': round(p, 4), 'recall': round(r, 4), 'f1': round(2 * p * r / (p + r), 4) if p + r else 0.0,
            'edge_px': int(pred.sum())}


_GRIDS = {}


def warp(b, s, dx, dy):
    """b scaled by s about the frame centre, then moved dx, dy px (nearest neighbour; what falls outside is empty)."""
    h, w = b.shape
    if (h, w) not in _GRIDS:
        _GRIDS[(h, w)] = np.mgrid[0:h, 0:w]
    ys, xs = _GRIDS[(h, w)]
    sy = np.round((ys - dy - h / 2) / s + h / 2).astype(int)
    sx = np.round((xs - dx - w / 2) / s + w / 2).astype(int)
    ok = (sy >= 0) & (sy < h) & (sx >= 0) & (sx < w)
    out = np.zeros_like(b)
    out[ok] = b[sy[ok], sx[ok]]
    return out


def pool(b, k=POOL):
    h, w = b.shape
    return b[:h - h % k, :w - w % k].reshape(h // k, k, w // k, k).any(axis=(1, 3))


def _score(m, rs, rd):
    n = m.sum()
    if not n:
        return 0.0
    prec = (m & rd).sum() / n
    ed = dilate(m, 1)
    rec = (rs & ed).sum() / max(1, rs.sum())
    return float(2 * prec * rec / (prec + rec)) if prec + rec else 0.0


def register(edges, ref, region):
    """The best similarity laying `edges` (the painting's) onto `ref` (the render's lines) inside `region` (the scene's
    things, not a backdrop - a painted sky's texture must not pull the fit): coarse on pooled edges, then refined at
    1 px. -> {scale, dx_px, dy_px, shift_share, worst_share, unpainted_share, at_search_edge, f1, subject_f1}."""
    h, w = edges.shape
    es, gs = pool(edges), pool(region)
    rs = pool(ref) & gs
    rd = dilate(rs, 1)
    hp, wp = es.shape
    best = (-1.0, 1.0, 0, 0)
    ry, rx = int(SHIFT_RANGE * hp), int(SHIFT_RANGE * wp)
    for s in np.arange(SCALES[0], SCALES[1] + 1e-9, SCALES[2]):
        for dy in range(-ry, ry + 1):
            for dx in range(-rx, rx + 1):
                f = _score(warp(es, s, dx, dy) & gs, rs, rd)
                if f > best[0]:
                    best = (f, float(s), dx * POOL, dy * POOL)
    _f, s, dx, dy = best
    # then the scale between the coarse steps and the shift within a coarse step, at half size, then 1 px at full size
    half_e, half_g = pool(edges, 2), pool(region, 2)
    half_rs = pool(ref, 2) & half_g
    half_rd = dilate(half_rs, 1)
    best = (-1.0, s, dx, dy)
    for s2 in np.arange(s - SCALES[2], s + SCALES[2] + 1e-9, SCALES[2] / 5):
        for ddy in range(-POOL, POOL + 1, 2):
            for ddx in range(-POOL, POOL + 1, 2):
                f = _score(warp(half_e, s2, (dx + ddx) / 2, (dy + ddy) / 2) & half_g, half_rs, half_rd)
                if f > best[0]:
                    best = (f, float(s2), dx + ddx, dy + ddy)
    _f, s, dx, dy = best
    full_rs = ref & region
    full_rd = dilate(full_rs, 1)
    best = (-1.0, s, dx, dy)
    for ddy in range(-2, 3):
        for ddx in range(-2, 3):
            f = _score(warp(edges, s, dx + ddx, dy + ddy) & region, full_rs, full_rd)
            if f > best[0]:
                best = (f, s, dx + ddx, dy + ddy)
    _f, s, dx, dy = best
    moved = warp(edges, s, dx, dy)
    shift_share = [round(dx / w, 4), round(dy / h, 4)]
    edge = s <= SCALES[0] or s >= SCALES[1] \
        or abs(dx) >= (SHIFT_RANGE * w - POOL) or abs(dy) >= (SHIFT_RANGE * h - POOL)
    return {'scale': round(s, 4), 'dx_px': int(dx), 'dy_px': int(dy), 'shift_share': shift_share,
            'worst_share': round(max(abs(shift_share[0]), abs(shift_share[1]), abs(s - 1.0)), 4),
            'unpainted_share': unpainted(s, shift_share), 'at_search_edge': bool(edge),
            'f1': f1(moved, ref)['f1'], 'subject_f1': f1(moved & region, ref & region)['f1']}


def unpainted(s, shift_share):
    """The largest share of the frame, on any side, that a painting laid back by scale s (about the centre) and the
    shift leaves without painting: an enlarged painting only crops, a shrunk or moved one leaves a strip to fill."""
    sides = [(1 - s) / 2 + shift_share[0], (1 - s) / 2 - shift_share[0], (1 - s) / 2 + shift_share[1], (1 - s) / 2 - shift_share[1]]
    return round(max(0.0, *sides), 4)
