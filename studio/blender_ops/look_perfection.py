"""Remove-CG-perfection look passes (bevel, contact QA, snap, micro jitter) with a mechanical guard.

Ported from projects/harness_validation/photoreal_research/photoreal_impl/02_bevel_jitter_contact/
remove_perfection.py (verified on Otis: auto bevel kept mechanical contact at 0, contact QA found
floating parts, sub-degree jitter, snapping to zero gap beats leaving a gap).

Invariant (why the guard exists): a cosmetic pass must never change a measured mechanical
relation. `scene['studio_guard_pairs']` declares [id_a, id_b] studio_id pairs that must keep their
evaluated distance. Every mutation is checked against the pairs containing the mutated object and
reverted for that object if any distance moves by more than GUARD_TOL. Objects with custom prop
`studio_mechanical` (or with a mechanical descendant) are never moved by snap or jitter.

Everything is reversible: modifiers are named `StudioLook_*`, and the rest transform / shading of
any touched object is stored in obj['studio_look_rest'] (JSON) for `revert_perfection`.
`apply_perfection` always reverts first, so re-applying the same spec gives identical results.
All distances are metres, world space. Reports are deterministic (sorted, rounded, no timestamps).
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from fnmatch import fnmatch

import bmesh
import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from geom_checks import (RAY_DIRS as _RAY_DIRS, aabb as _aabb, aabb_overlap as _aabb_overlap, bvh as _bvh,  # noqa: F401
                         closed as _closed, depth as _depth, eval_mesh as _eval_mesh, inside as _inside,
                         one_way as _one_way, pair_distance as _pair_distance, sample as _sample)

from scene_tools import curves, object_by_id

# --- tunables (defaults with rationale; override via spec) -------------------------------------
BEVEL_RATIO = 0.04       # width as fraction of smallest world dimension (highlight band of a few %)
BEVEL_MIN_W = 0.0015     # 1.5 mm: physical lower bound for a broken edge on fabricated parts
BEVEL_MAX_W = 0.012      # 12 mm: above this it reads as a design fillet, not an edge break
BEVEL_MAX_RATIO = 0.25   # never more than 1/4 of the thinnest dimension (prevents collapse)
BEVEL_ANGLE_DEG = 30.0   # only edges sharper than this are "hard" (classic autosmooth angle)
BEVEL_MAX_POLYS = 20000  # above this the mesh is treated as already detailed / scanned
ENV_MAX_DIM = 50.0       # floors / backdrops / sky cards: bevel invisible, cost not
CABLE_ASPECT = 30.0      # longest / middle dimension above this and ...
CABLE_MAX_THICK = 0.03   # ... middle dimension below 3 cm = cable / wire / thin strip
NONUNIFORM_TOL = 0.01
GUARD_TOL = 1e-6         # guard pair distance may not move more than this (gate threshold)
CONTACT_TOL = 0.0005     # gap <= 0.5 mm counts as touching
JITTER_ROT_DEG = (0.1, 0.4)    # verified: <= 0.4 deg reads as "placed by hand", not "broken"
JITTER_LOC_M = (0.001, 0.003)
JITTER_MAX_ARC_M = 0.0015      # cap travel of the farthest point (large objects get sub-degree)

PREFIX = 'StudioLook_'
MOD_BEVEL = PREFIX + 'Bevel'
MOD_WN = PREFIX + 'WeightedNormal'
PROP_MECH = 'studio_mechanical'
PROP_NO_BEVEL = 'studio_no_bevel'
PROP_JITTER = 'studio_jitter'
PROP_REST = 'studio_look_rest'
TRANSFORM_KEYS = ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale')


# --- identity / determinism ---------------------------------------------------------------------
def _sid(obj):
    return str(obj.get('studio_id') or obj.name)


def _rng(obj, salt):
    """Per-object RNG from sha256(salt:studio_id): process-, order- and run-independent."""
    digest = hashlib.sha256(f'{salt}:{_sid(obj)}'.encode()).digest()
    return random.Random(int.from_bytes(digest[:8], 'big'))


def _objects(scene, types=('MESH',)):
    return sorted((o for o in scene.objects if o.type in types and not o.hide_render), key=_sid)


def _r(x, n=6):
    return None if x is None or math.isinf(x) else round(x, n)


# --- rest state (undo) --------------------------------------------------------------------------
def _rest(obj):
    return json.loads(obj[PROP_REST]) if PROP_REST in obj else {}


def _save_rest(obj, key, value):
    rest = _rest(obj)
    if key not in rest:  # first capture wins: it is the pre-look state
        rest[key] = value
        obj[PROP_REST] = json.dumps(rest, sort_keys=True)


def _save_transform(obj):
    _save_rest(obj, 'transform', {'rotation_mode': obj.rotation_mode,
                                  **{k: list(getattr(obj, k)) for k in TRANSFORM_KEYS}})


def _restore_transform(obj, t):
    obj.rotation_mode = t['rotation_mode']
    for k in TRANSFORM_KEYS:
        setattr(obj, k, t[k])


def _restore_smooth(obj, bits):
    if obj.type == 'MESH' and len(bits) == len(obj.data.polygons):
        obj.data.polygons.foreach_set('use_smooth', [c == '1' for c in bits])
        obj.data.update()


def _remove_look_modifiers(obj):
    names = [m.name for m in obj.modifiers if m.name.startswith(PREFIX)]
    for n in names:
        obj.modifiers.remove(obj.modifiers[n])
    return len(names)


def revert_perfection(scene):
    """Remove StudioLook_* modifiers and restore stored transforms / shading. Idempotent."""
    reverted, removed = [], 0
    for obj in sorted(scene.objects, key=_sid):
        n = _remove_look_modifiers(obj)
        rest = _rest(obj)
        if 'transform' in rest:
            _restore_transform(obj, rest['transform'])
        if 'smooth' in rest:
            _restore_smooth(obj, rest['smooth'])
        if PROP_REST in obj:
            del obj[PROP_REST]
        if n or rest:
            reverted.append(_sid(obj))
        removed += n
    bpy.context.view_layer.update()
    return {'reverted': reverted, 'modifiers_removed': removed}


# --- evaluated geometry -------------------------------------------------------------------------
def _dg():
    return bpy.context.evaluated_depsgraph_get()


def _is_moving(obj):
    if obj.constraints:
        return 'constraints'
    ad = obj.animation_data
    if ad and ad.drivers:
        return 'drivers'
    if ad and any(fc.data_path.split('.')[-1] in TRANSFORM_KEYS + ('delta_location', 'delta_rotation_euler')
                  for fc in curves(ad.action)):
        return 'transform_animated'
    if obj.rigid_body:
        return 'rigid_body'
    return None


def _mechanical(obj):
    """Mechanical parts and anything carrying one (moving a parent moves its children)."""
    return any(bool(o.get(PROP_MECH)) for o in (obj, *obj.children_recursive))


# --- guard --------------------------------------------------------------------------------------
def _guard_pairs(scene, gate):
    raw = scene.get('studio_guard_pairs')
    if raw is None:
        return []
    try:
        rows = json.loads(raw) if isinstance(raw, str) else [list(r) for r in raw]
    except (TypeError, ValueError) as exc:
        gate.append(f'studio_guard_pairs unreadable: {exc}')
        return []
    pairs = []
    for row in rows:
        if not (isinstance(row, (list, tuple)) and len(row) == 2):
            gate.append(f'guard pair malformed: {row!r}')
            continue
        a, b = (object_by_id(str(x)) for x in row)
        if a is None or b is None:  # fail closed: an unguardable pair is not a passing pair
            gate.append(f'guard pair unresolved: {row[0]}|{row[1]}')
            continue
        pairs.append((str(row[0]), str(row[1]), a, b))
    return pairs


def _measure(pairs, frames):
    scene = bpy.context.scene
    keep = scene.frame_current
    out = {}
    for f in frames:
        if f != scene.frame_current:
            scene.frame_set(f)
        dg = _dg()
        for ia, ib, a, b in pairs:
            out[(ia, ib, f)] = _pair_distance(a, b, dg)
    if scene.frame_current != keep:
        scene.frame_set(keep)
    return out


def _same(d0, d1):
    return (math.isinf(d0) and math.isinf(d1)) or abs(d1 - d0) <= GUARD_TOL


def _guard_ok(obj, pairs, baseline, frames):
    """Re-measure only the pairs this object belongs to (only they can change by touching it)."""
    mine = [p for p in pairs if obj in (p[2], p[3])]
    if not mine:
        return True
    bpy.context.view_layer.update()
    after = _measure(mine, frames)
    return all(_same(baseline[k], d) for k, d in after.items())


# --- 1. bevel -----------------------------------------------------------------------------------
def _hard_edges(me, angle):
    bm = bmesh.new()
    bm.from_mesh(me)
    hard = sum(1 for e in bm.edges if e.is_manifold and e.calc_face_angle(0.0) > angle)
    bm.free()
    return hard


def _classify(obj, angle, max_polys):
    if obj.get(PROP_NO_BEVEL):
        return 'opt_out_prop'
    kinds = {m.type for m in obj.modifiers}
    if 'BEVEL' in kinds:
        return 'already_bevelled'
    if kinds & {'SUBSURF', 'MULTIRES'}:
        return 'subdivided'
    polys = len(obj.data.polygons)
    if polys == 0:
        return 'no_faces'
    if polys > max_polys:
        return 'high_poly'
    dims = sorted(obj.dimensions)
    if dims[2] > ENV_MAX_DIM:
        return 'environment_scale'
    if dims[1] > 0 and dims[2] / dims[1] > CABLE_ASPECT and dims[1] < CABLE_MAX_THICK:
        return 'thin_cable_or_strip'
    if _hard_edges(obj.data, angle) == 0:
        return 'no_hard_edges'
    s = obj.matrix_world.to_scale()
    if min(s) <= 0 or max(abs(x) for x in s) / max(min(abs(x) for x in s), 1e-12) - 1 > NONUNIFORM_TOL:
        return 'nonuniform_or_negative_scale'  # bevel width is local; applying scale is destructive
    return None


def _bevel(scene, spec, pairs, baseline, frames, reverted):
    angle = math.radians(spec.get('angle_deg', BEVEL_ANGLE_DEG))
    ratio, max_ratio = spec.get('ratio', BEVEL_RATIO), spec.get('max_ratio', BEVEL_MAX_RATIO)
    min_w, max_w = spec.get('min_m', BEVEL_MIN_W), spec.get('max_m', BEVEL_MAX_W)
    applied, skipped = [], {}
    for obj in _objects(scene):
        reason = _classify(obj, angle, spec.get('max_polys', BEVEL_MAX_POLYS))
        dmin = min((x for x in obj.dimensions if x > 1e-6), default=0.0)
        if reason is None:
            w = spec['width_m'] if spec.get('width_m') else min(max(ratio * dmin, min_w), max_w)
            w = min(w, max_ratio * dmin)
            if w < 0.5 * min_w:
                reason = 'too_thin'
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        _save_rest(obj, 'smooth', ''.join('1' if p.use_smooth else '0' for p in obj.data.polygons))
        obj.data.shade_smooth()  # harden/weighted normals keep flats flat
        scale = sum(abs(x) for x in obj.matrix_world.to_scale()) / 3.0
        b = obj.modifiers.new(MOD_BEVEL, 'BEVEL')
        b.width = w / scale
        b.segments = int(spec.get('segments') or (2 if w < 0.003 else 3))
        b.limit_method = 'ANGLE'
        b.angle_limit = angle
        b.use_clamp_overlap = True
        b.harden_normals = True
        b.miter_outer = 'MITER_ARC'
        wn = obj.modifiers.new(MOD_WN, 'WEIGHTED_NORMAL')
        wn.mode, wn.weight, wn.keep_sharp, wn.use_face_influence = 'FACE_AREA', 50, True, True
        if not _guard_ok(obj, pairs, baseline, frames):
            _remove_look_modifiers(obj)
            _restore_smooth(obj, _rest(obj)['smooth'])
            reverted.add(_sid(obj))
            skipped['reverted_by_guard'] = skipped.get('reverted_by_guard', 0) + 1
            continue
        applied.append({'id': _sid(obj), 'width_mm': round(w * 1000, 3)})
    bpy.context.view_layer.update()
    return {'applied': len(applied), 'skipped': sum(skipped.values()),
            'skip_reasons': dict(sorted(skipped.items())), 'objects': applied}


# --- 2. contact QA ------------------------------------------------------------------------------
def _coplanar_same_facing(faces, tree, dist_tol=2e-5, cos_tol=0.9995):
    """Face centres of A on B's surface facing the same way -> z-fighting risk.
    (Opposite-facing coplanar faces are ordinary resting contact.)"""
    for c, n in faces:
        if n is None:
            continue
        loc, nb, _, _ = tree.find_nearest(c, dist_tol)
        if loc is not None and nb.dot(n) > cos_tol:
            return True
    return False


def _contact(scene, spec):
    tol, search = spec.get('tol_m', CONTACT_TOL), spec.get('search_m', 0.05)
    pen_tol, ignore = spec.get('pen_tol_m', 0.001), spec.get('ignore', [])
    dg = _dg()
    data = {}
    for o in _objects(scene, ('MESH', 'CURVE')):
        if any(fnmatch(_sid(o), p) or fnmatch(o.name, p) for p in ignore):
            continue
        verts, polys = _eval_mesh(o, dg)
        if not polys:
            continue
        faces = []
        for p in polys:
            c = sum((verts[i] for i in p), Vector()) / len(p)
            n = (verts[p[1]] - verts[p[0]]).cross(verts[p[2]] - verts[p[0]])
            faces.append((c, n.normalized() if n.length > 1e-12 else None))
        data[o.name] = {'obj': o, 'tree': BVHTree.FromPolygons(verts, polys, epsilon=0.0), 'verts': verts,
                        'probes': verts + [c for c, _ in faces], 'faces': faces,
                        'closed': _closed(polys), 'box': _aabb(verts)}
    names = list(data)
    per = {n: {'gap': math.inf, 'nearest': None, 'pen': [], 'coplanar': []} for n in names}
    touching = 0
    for i, a in enumerate(names):
        A = data[a]
        for b in names[i + 1:]:
            B = data[b]
            if not _aabb_overlap(A['box'], B['box'], search):
                continue
            ov = A['tree'].overlap(B['tree'])
            gap = 0.0 if ov else min(_one_way(A['verts'], B['tree'], search), _one_way(B['verts'], A['tree'], search))
            if gap <= tol:
                known = [d for d in (_depth(A['probes'], B['tree'], B['closed']),
                                     _depth(B['probes'], A['tree'], A['closed'])) if d is not None]
                if _coplanar_same_facing(A['faces'], B['tree']) or _coplanar_same_facing(B['faces'], A['tree']):
                    per[a]['coplanar'].append(b); per[b]['coplanar'].append(a)
                if known and max(known) > pen_tol:
                    per[a]['pen'].append(b); per[b]['pen'].append(a)
                else:
                    touching += 1
            for x, y in ((a, b), (b, a)):
                if gap < per[x]['gap']:
                    per[x]['gap'], per[x]['nearest'] = gap, y
    sid = {n: _sid(data[n]['obj']) for n in names}
    floating = [{'id': sid[n], 'gap_mm': round(r['gap'] * 1000, 3), 'nearest': sid[r['nearest']]}
                for n, r in per.items() if not math.isinf(r['gap']) and r['gap'] > tol]
    return {'objects': len(names), 'touching': touching,
            'floating': [f['id'] for f in floating], 'floating_detail': floating,
            'isolated': sorted(sid[n] for n, r in per.items() if math.isinf(r['gap'])),
            'interpenetration': sorted(sid[n] for n, r in per.items() if r['pen']),
            'coplanar': sorted(sid[n] for n, r in per.items() if r['coplanar'])}


# --- 3. snap ------------------------------------------------------------------------------------
def _snap(contact, spec, pairs, baseline, frames, reverted):
    """Close reported floating gaps <= max_gap_m by translating along the shortest
    vertex->neighbour-surface vector (any direction), leaving target_gap_m (verified: 0 beats a slit)."""
    max_gap, target = spec.get('max_gap_m', 0.01), spec.get('target_gap_m', 0.0)
    only = spec.get('only')
    moved, skipped = [], {}

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    for f in contact['floating_detail']:
        obj, nb = object_by_id(f['id']), object_by_id(f['nearest'])
        if obj is None or nb is None or f['gap_mm'] / 1000 > max_gap:
            skip('gap_above_max'); continue
        if only and not any(fnmatch(f['id'], p) for p in only):
            skip('not_selected'); continue
        if _mechanical(obj):
            skip('mechanical'); continue
        if _is_moving(obj):
            skip('moving'); continue
        if obj.dimensions.length > nb.dimensions.length:  # the part settles onto its support, never the reverse
            skip('larger_than_neighbor'); continue
        dg = _dg()
        tree, _ = _bvh(nb, dg)
        verts, polys = _eval_mesh(obj, dg)
        pts = verts + [sum((verts[i] for i in p), Vector()) / len(p) for p in polys]
        best = None
        for v in pts:
            loc, _, _, d = tree.find_nearest(v, max_gap * 2)
            if loc is not None and (best is None or d < best[0]):
                best = (d, loc - v)
        if best is None or best[0] <= 1e-9:
            skip('no_target'); continue
        _save_transform(obj)
        mw = obj.matrix_world.copy()
        mw.translation += best[1] * ((best[0] - target) / best[0])
        obj.matrix_world = mw
        if not _guard_ok(obj, pairs, baseline, frames):
            _restore_transform(obj, _rest(obj)['transform'])
            reverted.add(f['id']); skip('reverted_by_guard'); continue
        bpy.context.view_layer.update()
        moved.append({'id': f['id'], 'onto': f['nearest'], 'moved_mm': round((best[0] - target) * 1000, 3),
                      'gap_after_mm': round(_pair_distance(obj, nb, _dg(), max_gap * 2) * 1000, 3)})
    bpy.context.view_layer.update()
    return {'moved': moved, 'skip_reasons': dict(sorted(skipped.items()))}


# --- 4. jitter (default deny) -------------------------------------------------------------------
def _support(name, cache, tol):
    """Nearest touching neighbour surface: (point, normal, dist, name) or None."""
    verts = cache[name][1]
    best = None
    for other, (tree, _) in sorted(cache.items()):
        if other == name or tree is None:
            continue
        for v in _sample(verts, 600):
            hit = tree.find_nearest(v, tol)
            if hit[0] is not None and (best is None or hit[3] < best[2]):
                best = (hit[0], hit[1], hit[3], other)
    return best


def _jitter(scene, spec, pairs, baseline, frames, reverted):
    """Only objects allowed by spec['allow'] globs (on studio_id / name) or custom prop
    studio_jitter move; mechanical, animated, constrained or mechanical-touching objects never do.
    Resting objects rotate about the contact normal through the contact point and slide
    tangentially; free objects rotate about their centre. New interpenetration or lost contact
    reverts the object."""
    lo_deg, hi_deg = spec.get('rot_deg', JITTER_ROT_DEG)
    loc_m = spec.get('loc_m', JITTER_LOC_M)
    max_arc, tol, salt = spec.get('max_arc_m', JITTER_MAX_ARC_M), spec.get('contact_tol_m', CONTACT_TOL), spec.get('seed', 0)
    allow = spec.get('allow', [])
    dg = _dg()
    meshes = _objects(scene)
    cache = {o.name: _bvh(o, dg) for o in meshes}
    mech = [o for o in meshes if o.get(PROP_MECH)]
    moved, skipped = [], {}

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    for o in meshes:
        if not (o.get(PROP_JITTER) or any(fnmatch(_sid(o), p) or fnmatch(o.name, p) for p in allow)):
            continue  # default deny, not reported
        if _mechanical(o):
            skip('mechanical'); continue
        why = _is_moving(o)
        if why:
            skip(why); continue
        if cache[o.name][0] is None:
            skip('no_faces'); continue
        if any(_pair_distance(o, p, dg, tol * 4, cache) <= tol for p in mech):
            skip('touches_mechanical'); continue
        rng = _rng(o, salt)
        ang = math.radians(rng.uniform(lo_deg, hi_deg)) * rng.choice((-1, 1))
        dist = rng.uniform(*loc_m)
        lo, hi = _aabb(cache[o.name][1])
        half = max((hi - lo).length / 2, 1e-6)
        ang = math.copysign(min(abs(ang), max_arc / half), ang)
        sup = _support(o.name, cache, tol)
        if sup:
            pivot, axis = Vector(sup[0]), Vector(sup[1]).normalized()
            t = axis.orthogonal().normalized()
            t.rotate(Matrix.Rotation(rng.uniform(0, 2 * math.pi), 3, axis))
            mode = 'resting'
        else:
            pivot = (lo + hi) / 2
            axis = Vector((rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 1))).normalized()
            t = Vector((rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 1))).normalized()
            mode = 'free'
        delta = (Matrix.Translation(t * dist) @ Matrix.Translation(pivot)
                 @ Matrix.Rotation(ang, 4, axis) @ Matrix.Translation(-pivot))
        near = sorted(n for n, (tr, vs) in cache.items() if n != o.name and tr is not None
                      and _aabb_overlap((lo, hi), _aabb(vs), 0.01))
        clear_before = [n for n in near if not cache[o.name][0].overlap(cache[n][0])]
        touching_before = [n for n in near
                           if _pair_distance(o, bpy.data.objects[n], dg, tol * 4, cache) <= tol]
        _save_transform(o)
        old = cache[o.name]
        o.matrix_world = delta @ o.matrix_world
        bpy.context.view_layer.update()
        dg = _dg()
        cache[o.name] = _bvh(o, dg)
        newly = [n for n in clear_before if cache[o.name][0].overlap(cache[n][0])]
        lost = [n for n in touching_before
                if _pair_distance(o, bpy.data.objects[n], dg, tol * 4, cache) > tol]
        guard_fail = not (newly or lost) and not _guard_ok(o, pairs, baseline, frames)
        if newly or lost or guard_fail:
            _restore_transform(o, _rest(o)['transform'])
            rest = _rest(o); rest.pop('transform')
            if rest:
                o[PROP_REST] = json.dumps(rest, sort_keys=True)
            else:
                del o[PROP_REST]
            bpy.context.view_layer.update()
            dg = _dg()
            cache[o.name] = old
            if guard_fail:
                reverted.add(_sid(o))
            skip('reverted_by_guard' if guard_fail else 'would_interpenetrate' if newly else 'would_lose_contact')
            continue
        moved.append({'id': _sid(o), 'rot_deg': round(math.degrees(ang), 4), 'move_mm': round(dist * 1000, 4),
                      'mode': mode})
    return {'moved': moved, 'skip_reasons': dict(sorted(skipped.items())), 'seed': salt}


# --- entry point --------------------------------------------------------------------------------
def apply_perfection(scene, spec):
    """spec = {'bevel': {...}|None, 'contact': {...}|None (always reported), 'snap': {...}|None,
    'jitter': {...}|None, 'guard_frames': [int]|None}. Reverts any earlier look first, so the
    result depends only on the rest scene and the spec."""
    spec = spec or {}
    revert_perfection(scene)
    warnings, gate, reverted = [], [], set()
    pairs = _guard_pairs(scene, gate)
    frames = list(spec.get('guard_frames') or [scene.frame_current])
    baseline = _measure(pairs, frames)
    for (ia, ib, f), d in sorted(baseline.items()):
        if d > GUARD_TOL:
            warnings.append(f'guard pair {ia}|{ib} not in contact before passes at frame {f}: {_r(d)} m')
    passes = {}
    if spec.get('bevel') is not None:
        passes['bevel'] = _bevel(scene, spec['bevel'], pairs, baseline, frames, reverted)
    contact = _contact(scene, spec.get('contact') or {})
    passes['contact'] = contact
    if spec.get('snap') is not None:
        passes['snap'] = _snap(contact, spec['snap'], pairs, baseline, frames, reverted)
    if spec.get('jitter') is not None:
        passes['jitter'] = _jitter(scene, spec['jitter'], pairs, baseline, frames, reverted)
    bpy.context.view_layer.update()
    final = _measure(pairs, frames)
    for k in sorted(baseline):
        if not _same(baseline[k], final[k]):
            gate.append(f'guard pair {k[0]}|{k[1]} distance changed at frame {k[2]}: '
                        f'{_r(baseline[k])} -> {_r(final[k])} m')
    return {'passes': passes, 'reverted_by_guard': sorted(reverted),
            'guard': {f'{a}|{b}@{f}': _r(final[(a, b, f)], 9) for a, b, f in sorted(final)},
            'warnings': warnings, 'gate_failures': gate}
