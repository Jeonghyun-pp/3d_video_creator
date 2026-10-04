"""Geometry measurements shared by look QA (look_perfection) and subject fidelity (assembly claims).

Pure measurement, no judgement: callers compare the numbers with their tolerances.
Distances are vertex->surface minima (an upper bound of the true gap, exact for touching/overlapping
triangles which return 0); penetration depth is the deepest sampled point of one closed mesh inside
another (ray parity in two directions, both must agree).
"""
from __future__ import annotations

import math

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

RAY_DIRS = (Vector((0.5377, 0.6143, 0.5773)).normalized(), Vector((-0.4111, 0.2903, -0.8642)).normalized())
CLAIM_TYPES = ('contact', 'no_interference', 'clearance', 'no_floating', 'through', 'cover')


def eval_mesh(obj, dg, matrix=None):
    """Evaluated vertices (world, or matrix @ world) and polygons of one object."""
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    if me is None:
        return [], []
    mw = (matrix @ ev.matrix_world) if matrix is not None else ev.matrix_world.copy()
    verts = [mw @ v.co for v in me.vertices]
    polys = [tuple(p.vertices) for p in me.polygons]
    ev.to_mesh_clear()
    return verts, polys


def bvh(obj, dg):
    verts, polys = eval_mesh(obj, dg)
    return (BVHTree.FromPolygons(verts, polys, epsilon=0.0) if polys else None), verts


def aabb(verts):
    xs, ys, zs = zip(*verts)
    return Vector((min(xs), min(ys), min(zs))), Vector((max(xs), max(ys), max(zs)))


def aabb_overlap(a, b, pad):
    return all(a[0][i] - pad <= b[1][i] and b[0][i] - pad <= a[1][i] for i in range(3))


def sample(verts, limit=4000):
    if len(verts) <= limit:
        return verts
    step = len(verts) / limit
    return [verts[int(i * step)] for i in range(limit)]


def one_way(verts, tree, search):
    best = math.inf
    for v in sample(verts):
        hit = tree.find_nearest(v, search)
        if hit[0] is not None and hit[3] < best:
            best = hit[3]
    return best


def pair_distance(a, b, dg, search=1.0, cache=None):
    """0.0 if evaluated triangles touch/intersect, else vertex->surface min both ways (upper bound)."""
    ta, va = (cache or {}).get(a.name) or bvh(a, dg)
    tb, vb = (cache or {}).get(b.name) or bvh(b, dg)
    if ta is None or tb is None:
        return math.inf
    if ta.overlap(tb):
        return 0.0
    return min(one_way(va, tb, search), one_way(vb, ta, search))


def inside(tree, p, far=1e3):
    """Ray parity in two directions; both must agree (closed meshes only)."""
    votes = 0
    for d in RAY_DIRS:
        o, n = p.copy(), 0
        for _ in range(64):
            loc = tree.ray_cast(o, d, far)[0]
            if loc is None:
                break
            n += 1
            o = loc + d * 1e-6
        votes += n % 2
    return votes == 2


def closed(polys):
    edges = {}
    for p in polys:
        for i in range(len(p)):
            e = tuple(sorted((p[i], p[(i + 1) % len(p)])))
            edges[e] = edges.get(e, 0) + 1
    return bool(edges) and all(c == 2 for c in edges.values())


def depth(points, tree, is_closed):
    if not is_closed:
        return None
    deepest = 0.0
    for v in sample(points, 2000):
        if inside(tree, v):
            d = tree.find_nearest(v)[3]
            deepest = max(deepest, d or 0.0)
    return deepest


# ---- part groups (subject fidelity) -------------------------------------------------------------

class Group:
    """All mesh objects of one subject part merged into one BVH, in the subject root frame."""

    def __init__(self, part_id, objs, dg, matrix):
        self.part_id = part_id
        verts, polys, probes, every_closed = [], [], [], bool(objs)
        for obj in objs:
            v, p = eval_mesh(obj, dg, matrix)
            every_closed = every_closed and closed(p)
            base = len(verts)
            verts += v
            polys += [tuple(i + base for i in poly) for poly in p]
            probes += v + [sum((v[i] for i in poly), Vector()) / len(poly) for poly in p]
        self.verts, self.polys, self.probes, self.closed = verts, polys, probes, every_closed
        self.tree = BVHTree.FromPolygons(verts, polys, epsilon=0.0) if polys else None
        self.box = aabb(verts) if verts else None


def part_groups(parts, root_inverse):
    """{part_id: Group} from {part_id: [mesh objects]}; array/mirror children share their part's group."""
    dg = bpy.context.evaluated_depsgraph_get()
    return {pid: Group(pid, objs, dg, root_inverse) for pid, objs in sorted(parts.items())}


def distance(a, b, search=1e3):
    """0 when the surfaces touch or one part is embedded in the other (closed mesh), else the min gap."""
    if a.tree is None or b.tree is None:
        return math.inf
    if a.tree.overlap(b.tree):
        return 0.0
    if (b.closed and a.verts and inside(b.tree, a.verts[0])) or (a.closed and b.verts and inside(a.tree, b.verts[0])):
        return 0.0
    return min(one_way(a.verts, b.tree, search), one_way(b.verts, a.tree, search))


def penetration(a, b):
    """Deepest point of either group inside the other (m); None when neither mesh is closed."""
    if a.tree is None or b.tree is None:
        return None
    if not aabb_overlap(a.box, b.box, 0.0):
        return 0.0
    if not a.tree.overlap(b.tree):
        contained = (b.closed and inside(b.tree, a.verts[0])) or (a.closed and inside(a.tree, b.verts[0]))
        if not contained:
            return 0.0
    known = [d for d in (depth(a.probes, b.tree, b.closed), depth(b.probes, a.tree, a.closed)) if d is not None]
    return max(known) if known else None


def _axis(name):
    return 'xyz'.index(str(name).lstrip('+-').lower())


def measure_claim(claim, groups):
    """Raw numbers for one assembly claim; keys depend on the claim type (all lengths in metres)."""
    kind = claim['type']
    out = {'type': kind, 'a': claim.get('a'), 'b': claim.get('b')}
    if kind == 'no_floating':
        names = [claim['a']] if claim.get('a') else sorted(groups)
        nearest = {}
        for name in names:
            others = [(distance(groups[name], g), other) for other, g in groups.items() if other != name]
            gap, other = min(others) if others else (math.inf, None)
            nearest[name] = {'gap_m': None if math.isinf(gap) else round(gap, 6), 'nearest': other}
        out['parts'] = nearest
        return out
    a, b = groups[claim['a']], groups[claim['b']]
    if kind in ('contact', 'clearance', 'no_interference'):
        gap = distance(a, b)
        out['distance_m'] = None if math.isinf(gap) else round(gap, 6)
        if kind != 'clearance':
            pen = penetration(a, b) if gap == 0.0 else 0.0
            out['penetration_m'] = None if pen is None else round(pen, 6)
        return out
    if kind == 'through':
        i = _axis(claim['axis'])
        others = [k for k in range(3) if k != i]
        out['a_range'] = [round(a.box[0][i], 6), round(a.box[1][i], 6)]
        out['b_range'] = [round(b.box[0][i], 6), round(b.box[1][i], 6)]
        out['protrude_m'] = [round(b.box[0][i] - a.box[0][i], 6), round(a.box[1][i] - b.box[1][i], 6)]
        centre = (a.box[0] + a.box[1]) / 2
        out['centred_in_b'] = all(b.box[0][k] <= centre[k] <= b.box[1][k] for k in others)
        pen = penetration(a, b)
        out['penetration_m'] = None if pen is None else round(pen, 6)
        return out
    if kind == 'cover':
        if b.tree is None or not a.verts:
            out['inside'], out['cover_m'] = False, None
            return out
        pts = sample(a.verts, 3000)
        out['inside'] = b.closed and all(inside(b.tree, p) for p in sample(pts, 200))
        out['cover_m'] = round(min(b.tree.find_nearest(p)[3] for p in pts), 6)
        return out
    raise ValueError(f'unknown assembly claim type {kind!r}')
