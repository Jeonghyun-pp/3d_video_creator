"""Blender layer of shot.camera.move: resolve what the move is about, build its path, compile a rig.

Runs at build time before camera_rig.bake_camera_rig, which then bakes, guards and reports the compiled rig
exactly as if it had been written by hand. The shot snapshot keeps the move; the compiled rig is recorded in
camera_move_report.json. Not imported by render workers (render fingerprints unaffected).
"""
from __future__ import annotations

import math
import bpy
from mathutils import Vector
import numpy as np
from mathutils.bvhtree import BVHTree

import camera_moves_core as core
from scene_roles import counts
import scene_geometry
import camera_rig_core as rig_core
import camera_frame_core as frame_core
import show_core
from scene_index import resolve_ref

PATH_PREFIX = 'studio_camera_path.'
AIM_PREFIX = 'studio_camera_aim.'
WHIP_S = 0.25


def _refs(params):
    """Every string in a move's params is a scene reference (top level or inside a list), except the 'ahead'
    keyword: a new move or parameter needs no registration here."""
    out = []
    for value in params.values():
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str) and item != 'ahead':
                out.append(item)
    return out


def resolve(params, frame=1):
    """Scene references of a move -> {points, boxes} at the requested Blender frame (scene_index.resolve_ref: a group
    is its copies' union box and that box's centre)."""
    bpy.context.scene.frame_set(frame)
    geo = {'points': {}, 'boxes': {}}
    for ref in _refs(params):
        found = resolve_ref(ref)
        if found is None:
            raise ValueError(f'CAMERA_MOVE: reference not found in scene: {ref}')
        geo['points'][ref] = tuple(found['point'])
        if found['box']:
            geo['boxes'][ref] = found['box']
    return geo


def _changes_over_time(obj):
    """True when obj's geometry can differ between frames: keyed (itself or a parent, incl. keyed visibility)
    or cut/deformed by a modifier whose object is (an animated reveal cutter)."""
    o = obj
    while o is not None:
        if o.animation_data and o.animation_data.action:
            return True
        o = o.parent
    if scene_geometry.is_time_dependent(obj):  # rigid bodies, simulation zones: moving without keys
        return True
    return any(getattr(m, 'object', None) is not None and _changes_over_time(m.object) for m in obj.modifiers)


def _merged_tree(objs, depsgraph):
    """One BVH over the evaluated geometry of `objs` - instances a Geometry Nodes host scatters included
    (scene_geometry) - built with numpy, no per-vertex Python."""
    names = {o.name for o in objs}
    vertices, triangles = scene_geometry.merged(depsgraph, keep=lambda host: host.name in names)
    return BVHTree.FromPolygons(vertices.tolist(), triangles.tolist()) if vertices is not None else None


def _scene_trees(skip, cache=None):
    """BVH trees of everything that blocks the camera (scene_roles 'raycast'): static geometry merged once
    (cached across frames when `cache` is given), geometry that changes over time merged per call."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    objs = [o for o in bpy.context.scene.objects if o.type == 'MESH' and not o.hide_render and o.name not in skip
            and counts(o, 'raycast') and not o.name.startswith((PATH_PREFIX, AIM_PREFIX))]
    moving = [o for o in objs if _changes_over_time(o)]
    key = ('static', frozenset(skip))
    if cache is None or key not in cache:
        tree = _merged_tree([o for o in objs if not _changes_over_time(o)], depsgraph)
        if cache is None:
            return [t for t in (tree, _merged_tree(moving, depsgraph)) if t is not None]
        cache[key] = tree
    return [t for t in (cache[key], _merged_tree(moving, depsgraph)) if t is not None]


def _trees_at(frame, skip, cache):
    """Scene BVH trees as the geometry stands at shot frame `frame` (a reveal may still be closed or open)."""
    key = (frame, frozenset(skip))  # the same frame measured with and without the opening skipped are different trees
    if key not in cache:
        bpy.context.scene.frame_set(frame + 1)
        cache[key] = _scene_trees(skip, cache)
    return cache[key]


def _nearest(trees, point):
    best = None
    for tree in trees:
        location, normal, _index, distance = tree.find_nearest(point)
        if location is not None and (best is None or distance < best[2]):
            best = (location, normal, distance)
    return best


def repair(waypoints, clearance, trees, rounds=4):
    """Push waypoints out of geometry to `clearance` along the nearest surface normal. Endpoints that sit
    inside a solid are pushed out of it too. `trees`: one tree list for all waypoints, or a function
    index -> trees (geometry as it stands when the camera passes that waypoint). Returns (points, [{index, moved_m}])."""
    points = [Vector(p) for p in waypoints]
    trees_for = trees if callable(trees) else (lambda _i: trees)
    moved = {}
    for _ in range(rounds):
        changed = False
        for i, p in enumerate(points):
            hit = _nearest(trees_for(i), p)
            if hit is None:
                continue
            location, normal, distance = hit
            outside = (p - location).dot(normal) >= 0
            if outside and distance >= clearance - 1e-4:
                continue
            target = location + normal.normalized() * clearance
            moved[i] = moved.get(i, 0.0) + (target - p).length
            points[i] = target
            changed = True
        if not changed:
            break
    return [tuple(p) for p in points], [{'index': i, 'moved_m': round(m, 3)} for i, m in sorted(moved.items())]


def _polyline(name, points):
    """Ordered-vertex mesh (camera_rig._path_points reads vertex order), hidden from render."""
    old = bpy.data.objects.get(name)
    if old is not None:
        bpy.data.objects.remove(old, do_unlink=True)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(p) for p in points], [(i, i + 1) for i in range(len(points) - 1)], [])
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.hide_render = True
    obj.hide_viewport = True
    obj['studio_id'] = name
    obj['studio_scene_role'] = 'helper'
    return obj


def _aim_empty(name, points):
    """Look target as an empty keyed on every frame (a fixed point, or a whip swinging onto the target)."""
    old = bpy.data.objects.get(name)
    if old is not None:
        bpy.data.objects.remove(old, do_unlink=True)
    obj = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(obj)
    obj['studio_id'] = name
    obj['studio_scene_role'] = 'helper'
    obj.location = points[0]
    if len(points) > 1:
        for f, p in enumerate(points):
            obj.location = p
            obj.keyframe_insert('location', frame=f + 1)
        from scene_tools import curves
        for curve in curves(obj.animation_data.action):
            for point in curve.keyframe_points:
                point.interpolation = 'LINEAR'
    return obj


def _sample_points(ref, count):
    scene = bpy.context.scene
    out = []
    for f in range(count):
        scene.frame_set(f + 1)
        found = resolve_ref(ref)
        if found is None:
            raise ValueError(f'CAMERA_MOVE: look target not found: {ref}')
        out.append(tuple(found['point']))
    scene.frame_set(1)
    return out


FLOOR_LENSES = core.FLOOR_LENSES   # a pose under a declared surface keeps its direction and goes wider and closer
BLOCKED_MAX = 0.3       # more of the focus than this behind something else (a plume, a deck): the view is rejected / warned
PATH_STEP = 10          # frames between checks along the joined path
ROLL_MAX_DEG = 30.0     # a picture's lean matched by rolling the camera up to this; beyond, the scene's pose differs too much
AXES = {'x': (1.0, 0.0, 0.0), 'y': (0.0, 1.0, 0.0), 'z': (0.0, 0.0, 1.0)}   # the subject's long axis in its own frame


def _axis(target, name):
    """The subject's long axis in the world: its own frame's `name` axis; a group's when every copy stands the same way;
    else (a point, copies turned differently, nothing) None - no one lean to match."""
    found = resolve_ref(target)
    roots = found['roots'] if found and found['objects'] else []   # a point (an anchor) has no axis of its own
    axes = [tuple((r.matrix_world.to_3x3() @ Vector(AXES[name])).normalized()) for r in roots]
    same = axes and all(max(abs(a - b) for a, b in zip(axes[0], other)) < 1e-6 for other in axes)
    return axes[0] if same else None


LONG_AXIS_RATIO = 2.0   # a subject this many times longer along one of its own axes than along the next is long: its
                        # pictures are read along that axis (a lean, where the frame crosses it); a compact one keeps 'z'
LYING_Z = 0.5           # a long axis whose world z is below this lies down: its picture's lean is where the camera stands


def _spans(target):
    """[(extent m, AXES key)] of the subject in its own frame, longest first (its tree's mesh boxes in its first root's
    frame), or None when it names no mesh."""
    found = resolve_ref(target)
    if not found or not found['objects'] or not found['roots']:
        return None
    meshes = [o for o in found['objects'] if o.type == 'MESH']
    if not meshes:
        return None
    to_local = found['roots'][0].matrix_world.inverted()
    corners = [to_local @ (o.matrix_world @ Vector(c)) for o in meshes for c in o.bound_box]
    return sorted(((max(c[i] for c in corners) - min(c[i] for c in corners), name) for i, name in enumerate(AXES)), reverse=True)


def _long_axis(target):
    """The AXES key the subject is longest along in its own frame (_spans), or None when it is not LONG_AXIS_RATIO
    times longer than along the next (2026-10-14: a lying hull's lean was read on z)."""
    spans = _spans(target)
    if not spans:
        return None
    return spans[0][1] if spans[0][0] >= LONG_AXIS_RATIO * max(spans[1][0], 1e-9) else None


# A frame_to_frame camera stays above the support under it by SUPPORT_MARGIN_SHARE of the framed subject's height, within
# [SUPPORT_MARGIN_MIN_M, SUPPORT_MARGIN_M] (2026-10-14: the 24 mm step of the rocket's landing went under the deck unrefused;
# the share keeps a product on a bench or a car seen from 0.3 m possible). Separate from guards.min_clearance_m: that is
# the clearance from a plume or the sea, not from the floor (review, 2026-10-14).
SUPPORT_MARGIN_M = 0.5
SUPPORT_MARGIN_SHARE = 0.25
SUPPORT_MARGIN_MIN_M = 0.05
# The solver's own steps keep to the same rule (camera_frame_core._Course: a step under the support is halved, then
# refused - A4). Not here yet (recorded risk): reveal cutters that open a hole in a support are applied after the camera
# is solved, so a frame_to_frame camera diving through a reveal hole in a support is refused, not supported.


def _support_margin(box):
    """How far above the support under it the camera stays when it frames `box`: SUPPORT_MARGIN_SHARE of its height,
    within [SUPPORT_MARGIN_MIN_M, SUPPORT_MARGIN_M]."""
    return min(SUPPORT_MARGIN_M, max(SUPPORT_MARGIN_MIN_M, SUPPORT_MARGIN_SHARE * (box[1][2] - box[0][2])))


def _floor_objects():
    """The visible meshes whose scene role counts 'floor' (scene_roles 'support': a deck, a pad, a table, the ground)."""
    return [o for o in bpy.context.scene.objects if o.type == 'MESH' and not o.hide_render and counts(o, 'floor')]


def _support(frame, skip, cache=None):
    """floor(position, from_z) over the supports as they stand at shot frame `frame`, the subject's own objects (names
    in `skip`) left out - a deck the shot is about may be seen from below (camera_frame.support_floor); None when the
    scene has no support. Built once per frame when `cache` (a dict for one compile) is given."""
    import camera_frame
    key = ('floor', frame, frozenset(skip))
    if cache is None or key not in cache:
        bpy.context.scene.frame_set(frame + 1)
        found = camera_frame.support_floor(bpy.context.evaluated_depsgraph_get(), bpy.context.scene, frozenset(skip))
        if cache is None:
            return found
        cache[key] = found
    return cache[key]


def _below_support(floor, position, middle_z, margin):
    """Why a camera at `position` stands too low over what the subject stands on - nearer its top than `margin` or
    under it - or None. The column is searched from the subject's middle or the camera, whichever is higher (a support
    above the subject's middle is its ceiling). Never moves the camera: a pose under the support is refused, not lifted onto it."""
    if floor is None:
        return None
    top = floor(position, max(position[2], middle_z))
    if top is None or position[2] >= top + margin:
        return None
    return (f'FRAME_BELOW_SUPPORT: the camera at z {position[2]:.2f} m is {position[2] - top:+.2f} m from the top of the '
            f'support under it (z {top:.2f} m); it stays {margin:g} m above')


def _nearest_clash(tree, position, names, min_m):
    location, normal, _index, distance = tree.find_nearest(Vector(position))
    if location is None:
        return None
    if normal is not None and (Vector(position) - location).dot(normal) < 0:
        return f'inside or below {names} by {distance:.2f} m'
    if distance < min_m:
        return f'{distance:.2f} m from {names} (clearance {min_m} m)'
    return None


def _keep_out(ids, frame, position, min_m, middle_z=None, skip=frozenset(), cache=None):
    """How a camera at `position` meets what the move keeps it out of: the surfaces and volumes it declares
    (move.guards.clearance_ids: the sea, a deck, a plume stand-in) - inside / below one (the nearest face looks away
    from it) or nearer than min_m - and every support (scene_roles 'floor', declared or not) - inside one; how far
    above a support it stays is _below_support's margin, not min_m. The supports follow _support's rules: the subject's
    own (names in `skip`) left out, and one whose top is above the subject's middle (`middle_z`) is a ceiling (a street
    over a station hall). None when clear. Trees built once per frame when `cache` is given."""
    cache = {} if cache is None else cache
    key = ('keep_out', frame, tuple(ids), frozenset(skip), None if middle_z is None else round(middle_z, 3))
    if key not in cache:
        declared = [o for ident in ids for o in ((resolve_ref(ident) or {}).get('objects') or []) if o.type == 'MESH']
        supports = [o for o in _floor_objects() if o.name not in skip and o not in declared]
        if not declared and not supports:
            return None
        bpy.context.scene.frame_set(frame)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        supports = [o for o in supports if middle_z is None or max((o.matrix_world @ Vector(c)).z for c in o.bound_box) <= middle_z]
        cache[key] = ((_merged_tree(declared, depsgraph) if declared else None, list(ids)),
                      (_merged_tree(supports, depsgraph) if supports else None, sorted({o.get('studio_id') or o.name for o in supports})))
    (declared_tree, declared_names), (support_tree, support_names) = cache[key]
    clash = _nearest_clash(declared_tree, position, declared_names, min_m) if declared_tree is not None else None
    if clash is None and support_tree is not None:
        clash = _nearest_clash(support_tree, position, support_names, 0.0)
    return clash


ATMOSPHERE_NEAR_M = frame_core.ATMOSPHERE_NEAR_M   # a camera nearer an atmosphere volume than this stands in it
POSE_REFUSALS = ('FRAME_BELOW_SUPPORT', 'CAMERA_IN_ATMOSPHERE')   # where no pose of a frame may stand: when every pose the
                          # solve reached was refused for one of them, the frame fails with that code, not FRAME_UNSOLVED


def _volumes(frame, focus, cache):
    """camera_frame.volumes at Blender frame `frame` for the focus point `focus` (the air the subject stands in left
    out), built once per frame and focus when `cache` is given."""
    import camera_frame
    key = ('atmosphere', frame, tuple(round(v, 3) for v in focus))
    if key not in cache:
        bpy.context.scene.frame_set(frame)
        cache[key] = camera_frame.volumes(bpy.context.evaluated_depsgraph_get(), Vector(focus))
    return cache[key]


def _atmosphere_objects():
    import camera_frame
    return camera_frame.atmosphere_objects()


def _in_atmosphere(frame, position, focus, cache=None):
    """CAMERA_IN_ATMOSPHERE: why a camera at `position` stands in a fog, smoke or beam volume (scene_roles
    'atmosphere') at Blender frame `frame` - enclosed by it (camera_frame.holds: a cloud's region, a closed surface by
    crossing parity, whatever way its faces point) or nearer it than ATMOSPHERE_NEAR_M - else None. A volume that also
    holds the focus point `focus` is the air the subject stands in (a hall's fog, a dust cloud round it) and stays
    allowed. The volumes still block no ray; a pose in one is refused like one under the support, never pushed out."""
    import camera_frame
    cache = {} if cache is None else cache
    if not cache.setdefault(('atmosphere_any', frame), bool(_atmosphere_objects())):
        return None
    for name, volume in sorted(_volumes(frame, focus, cache).items()):
        if camera_frame.holds(volume, position):
            return f'CAMERA_IN_ATMOSPHERE: the camera is inside {name}'
        gap = camera_frame.distance(volume, position)
        if gap < ATMOSPHERE_NEAR_M:
            return f'CAMERA_IN_ATMOSPHERE: the camera is {gap:.2f} m from {name} (it keeps {ATMOSPHERE_NEAR_M} m)'
    return None


def _framed_spans(refs):
    """The framed thing's own extents, longest first: the target's in its own frame (_spans), or - several parts
    framed - their union box's."""
    if len(refs) == 1:
        return [e for e, _name in _spans(refs[0]) or []]
    boxes = [found['box'] for ref in refs if (found := resolve_ref(ref)) and found['box']]
    if not boxes:
        return []
    return sorted((max(b[1][i] for b in boxes) - min(b[0][i] for b in boxes) for i in range(3)), reverse=True)


def _pose_code(log):
    """The code a frame no view solved fails with: the one refusal (POSE_REFUSALS) every pose the solve reached met."""
    posed = {r['code'] for r in log if r['posed']}
    return next(iter(posed)) if len(posed) == 1 and posed <= set(POSE_REFUSALS) else 'FRAME_UNSOLVED'


OFF_SCREEN_SHARE = 0.5   # a must-see part with less than this share of itself inside the frame is off frame, not hidden
CLOSER_MAX = core.CLOSER_MAX   # a small part: the framing comes up to this much closer (camera_moves_core)
SIZE_MARGIN = 1.15       # ... aiming this far past the thickness it needs: the frame probe measures it on a 256 px id pass,
                         # about a fifth thinner than these ray samples for a small part (frame_to_frame_smoke, 2026-10-13)


def _pose(job, fr, view, refs, own, box, aspect, frame, first_lens, keep_ids, keep_m, must_see, min_px, roll_deg=0.0, diagnose=False,
          floor=None, skip=frozenset(), log=None, cache=None, axis_dir=None):
    """One candidate view of a frame target: solved at the widening lens ladder, then judged - out of the keep-out
    volumes, the focus not hidden behind something else, every must-see part as thick as it must be. (solved, None)
    or (None, why). With diagnose, a must-see failure says why: (None, {why, kind: hidden | off_screen | small, parts:
    {id: camera_frame.diagnose_part}, lens_rejected}). Every lens the ladder gave up on is kept as lens_rejected
    [{lens, code, why}] - on the solved pose and in the failure's why (2026-10-14: s21/s23 reported only the last lens's
    reason; the 24 mm step that went under the deck was lost). A pose nearer the top of the support under it than
    _support_margin(box) (floor: camera_moves._support) is refused, FRAME_BELOW_SUPPORT - never lifted. `log` collects every
    lens rejection of the frame's whole view search, posed when the solve reached a pose (the final error's code).
    The solve gets what the picture cuts (core.frame_segment), the subject's long axis (axis_dir: stations along it) and
    the floor rule (no step under the support; camera_frame_core.solve), A4 2026-10-14."""
    import camera_frame
    why, lens_rejected = None, []

    def reject(lens, code, reason, posed=True):
        lens_rejected.append({'lens': lens, 'code': code, 'why': reason})
        if log is not None:
            log.append({'code': code, 'posed': posed})
        return reason

    def ladder():   # one reason as it was written; several, each with its lens
        return lens_rejected[0]['why'] if len(lens_rejected) == 1 else '; '.join(f"{r['lens']} mm: {r['why']}" for r in lens_rejected)
    middle = (box[0][2] + box[1][2]) / 2
    # what the picture cuts (core.frame_segment) and the floor rule the solve's steps keep to (A4, 2026-10-14)
    focus = {'height_share': fr['height_share'], 'centre': fr['centre'], **({'edges': fr['edges']} if fr.get('edges') else {}),
             **core.frame_segment(fr)}
    lowest = (lambda position: (floor(position, max(position[2], middle)), _support_margin(box))) if floor is not None else None
    extra = {k: v for k, v in (('axis_dir', axis_dir), ('floor', lowest)) if v is not None}

    def failed(error):   # a solve that reached a pose under the support was posed (the frame's final code says so)
        code = getattr(error, 'code', None) or _code(str(error), 'FRAME_UNSOLVED')
        return reject(lens, code, str(error)[:160], posed=code == 'FRAME_BELOW_SUPPORT')
    picture = focus.get('cut_edges') is not None
    for lens in [first_lens] + [mm for mm in FLOOR_LENSES if mm < first_lens]:
        seeded = {**fr, 'view': {'elevation_deg': view[0], 'azimuth_deg': view[1]}}
        seeds = {'plain': core.frame_seed(box, seeded, lens, aspect)[0]}
        solved, first_errors, last = None, {}, None
        # every seed's first pass before any rescue (camera_frame_core.PASSES): a pose a seed reaches as it always did
        # is never taken from a rescue of another seed. A cropped body's inferred centre can seed above the visible feet
        # and trap the edge solve: the edge seed is tried when the plain one fails.
        for passes in (('first',), ('rescue',)) if picture else (('first',),):
            for kind in ('plain', 'edge'):
                if kind == 'edge' and 'edge' not in seeds:
                    seeds['edge'] = core.frame_edge_seed(box, seeded, lens, aspect)
                if seeds[kind] is None:
                    continue
                if passes == ('rescue',) and not getattr(first_errors.get(kind), 'rescuable', False):
                    continue
                try:
                    solved = camera_frame.solve_focus(job, focus, refs, seeds[kind], view, frame + 1, lens, must_see, roll_deg=roll_deg,
                                                      passes=passes, **extra)
                    break
                except ValueError as error:
                    last = error
                    if passes == ('first',):
                        first_errors[kind] = error
            if solved is not None:
                break
        if solved is None:
            why = failed(last)
            continue
        low = _below_support(floor, solved['position'], middle, _support_margin(box))
        if low:
            why = reject(lens, 'FRAME_BELOW_SUPPORT', low)
            continue
        clash = _keep_out(keep_ids, frame + 1, solved['position'], keep_m, middle, skip, cache)
        if clash:
            why = reject(lens, 'KEEP_OUT', clash)
            continue
        fog = _in_atmosphere(frame + 1, solved['position'], core.centre(box), cache)
        if fog:   # A5 (2026-10-14): a pose in a plume or fog shows the volume; refused unless the project softens it
            import gate_policy
            if gate_policy.is_error('CAMERA_IN_ATMOSPHERE'):
                why = reject(lens, 'CAMERA_IN_ATMOSPHERE', fog)
                continue
            solved['in_atmosphere'] = fog
        blocked = camera_frame.blocked_share(refs, own, solved['position'], solved['aim'], lens, aspect, frame + 1, roll_deg=roll_deg)
        if blocked is not None and blocked > BLOCKED_MAX:
            reject(lens, 'FOCUS_BLOCKED', f'{round(blocked * 100)} % of the focus behind something else at {lens} mm')
            return None, ladder()
        size = job.get('output_size') or (1080, 1920)
        shown = {i: camera_frame.shown_px(i, solved['position'], view, lens, size, frame + 1, roll_deg=roll_deg) for i in must_see}
        solved['must_see_px'] = shown
        thin = {i: px for i, px in shown.items() if px < min_px.get(i, 0)}
        if thin:
            reject(lens, 'MUST_SEE_THIN', f'must-see {sorted(thin)} at {thin} px (need {[min_px.get(i, 0) for i in sorted(thin)]})')
            why = ladder()
            if not diagnose:
                return None, why
            # why each one is thin: hidden (an opening's job), off frame (recentre), or small (come closer)
            diagnosis = {i: camera_frame.diagnose_part(i, own, solved['position'], view, lens, size, frame + 1, roll_deg=roll_deg,
                                                       shown=[m for m in must_see if m != i], exposable=_exposable(job, i)) for i in thin}
            # hidden first, however small it would be open: an opening shows it, then closer makes it big enough
            covered = {i: d for i, d in diagnosis.items() if d['hidden_share'] >= camera_frame.HIDDEN_SHARE}
            # enclosed (a housing around it, camera_frame.diagnose_part): the shot list's opening shows it; behind a
            # sibling or another thing (a leg, the next engine, a pillar): the camera may go round it, and says so
            hidden = {i: d for i, d in covered.items() if d['own_shell']}
            blocked = {i: d for i, d in covered.items() if not d['own_shell']}
            off = {i: d for i, d in diagnosis.items() if i not in covered and d['on_screen'] < OFF_SCREEN_SHARE}
            kind = 'hidden' if hidden else 'blocked' if blocked else 'off_screen' if off else 'small'
            parts = hidden or blocked or off or diagnosis
            if kind == 'blocked':
                why += ' - behind ' + ', '.join(_blocking(blocked))
            return None, {'why': why, 'kind': kind, 'parts': parts, 'lens_mm': lens, 'lens_rejected': lens_rejected}
        return {**solved, 'lens_mm': lens, 'blocked_share': blocked, 'lens_rejected': lens_rejected}, None
    return None, ladder() if lens_rejected else why


def _blocking(parts):
    """The parts (subject/part, or a scene id) standing in front of the diagnosed parts, most in front first
    (camera_frame.diagnose_part occluder_parts; the raw object names when a diagnosis has none)."""
    share = {}
    for d in parts.values():
        for ref, s in (d.get('occluder_parts') or d.get('occluders') or {}).items():
            share[ref] = max(share.get(ref, 0.0), s)
    return sorted(share, key=lambda r: (-share[r], r))


def _diagnosis(why):
    """A must-see failure as data (camera_move_report rows and failure report; read by the next step that may make
    what blocks see-through): {kind, parts: {ref: {hidden_share, on_screen, enclosed, blocked_by: [part refs]}}}."""
    return {'kind': why['kind'], 'parts': {ref: {'hidden_share': d.get('hidden_share'), 'on_screen': d.get('on_screen'),
                                                 'enclosed': d.get('own_shell'), 'blocked_by': _blocking({ref: d})}
                                           for ref, d in sorted(why['parts'].items())}}


def _failed(message, report):
    """A ValueError carrying a structured report (generate.py writes it into camera_move_report.json)."""
    error = ValueError(message)
    error.report = report
    return error


def _exposable(job, ref):
    """The part's exposable_share from its subject's exposure survey, as the host passed it (job['exposure']:
    {subject: {part: share}}, blender.generator_inputs), or None - then hidden is judged against all of it."""
    subject, _, part = str(ref).partition('/')
    return ((job.get('exposure') or {}).get(subject) or {}).get(part) if part else None


def _code(message, default):
    """The error code a message starts with ('FRAME_UNSOLVED: ...'), else `default`."""
    head = message.split(':', 1)[0]
    return head if head and head.replace('_', '').isalpha() and head.isupper() else default


def _compile_frames(job, move, style, on_cues):
    """frame_to_frame: for each frame target, the view nearest its picture that keeps the camera out of what it must
    not enter, the focus unhidden and the must-see parts readable (core.candidate_views; camera_frame.solve_focus per
    view on the scene at that moment); the poses joined around or toward the subject (core.frame_path), hung from an
    anchor that shares `follow` of the subject's travel, paced by the subject's own travel when it moves; the joined
    path checked every PATH_STEP frames. Baked as a follow rig keyed on every frame."""
    import camera_frame
    shot = job['shot']
    count = shot['duration_frames']
    defaults = (style or {}).get('defaults', {})
    p = move.get('params', {})
    target = p.get('target')
    if not isinstance(target, str):
        raise ValueError('CAMERA_MOVE: frame_to_frame target must be a scene group reference (the subject the pictures frame)')
    width, height = job.get('output_size') or (1080, 1920)
    aspect = width / height
    refs = list(p['parts']) if p.get('parts') else [target]
    framed = refs if len(refs) > 1 else refs[0]
    core.plan({**move, 'params': {'aspect': aspect, **p}}, resolve({'target': target, **({'parts': refs} if p.get('parts') else {})}))  # checks path/follow/frames
    guards = dict(move.get('guards', {}))
    keep_ids, keep_m = guards.get('clearance_ids') or [], guards.get('min_clearance_m', move.get('clearance_m', 0.0))
    cache = {}   # support and keep-out trees, once per frame
    mine = {o.name for ref in refs + [target] for o in ((resolve_ref(ref) or {}).get('objects') or [])}
    min_px = {d['id']: d.get('min_px', 0) for d in (shot.get('screen') or {}).get('details', [])}
    if on_cues:   # frame-bound openings (show_scene) exist before the poses are solved: the camera sees the open subject
        on_cues({})
    keys, rows, tilt_warnings = [], [], []
    for fr in sorted(move['frames'], key=lambda f: f['at']):
        frame = round(fr['at'] * (count - 1))
        geo = resolve({'target': target, **({'parts': refs} if p.get('parts') else {})}, frame=frame + 1)
        box = core._ref(geo, framed, 'box')
        spans = _framed_spans(refs)   # the scene stands at this moment (resolve)
        picture_view = (fr['view']['elevation_deg'], fr['view'].get('azimuth_deg', 0.0))
        must_see = list(fr.get('must_see') or [])
        long_name = p.get('axis') or _long_axis(target)   # declared, else the subject's own longest axis
        axis = _axis(target, long_name or 'z')
        want = fr.get('tilt_deg')
        # a lying long subject (a hull, a barge, a fallen mast): the picture's lean says where the camera stands around
        # it, not how far it rolls (2026-10-14: a -73 deg lean of a lying hull asked a 73 deg roll and was dropped)
        lying = want is not None and axis is not None and long_name is not None and abs(axis[2]) < LYING_Z
        turned = frame_core.azimuth_for_tilt(picture_view[0], axis, want, picture_view[1]) if lying else None
        if turned is not None:
            picture_view = (picture_view[0], turned)
        rejected, solved, chosen, fixed = [], None, None, {}
        diagnosed = False   # the first view that reaches the must-see check says why a part is thin (2026-10-14: only the
                            # picture's own view did; when it failed earlier, a hidden part read as FRAME_UNSOLVED)
        how = (fr.get('show') or {}).get('how', 'exterior')
        lens0 = fr.get('lens_mm') or move.get('lens_mm', 24.0)
        floor, log = _support(frame, mine, cache), []
        for candidate in core.candidate_views(picture_view):
            # Solve the picture in the rolled camera; adding roll afterwards moves off-centre subjects.
            needed = frame_core.roll_for_tilt((0, 0, 0), frame_core.basis(*candidate['view'])[0], axis, want) \
                if want is not None and axis is not None and not lying else None
            roll = needed if needed is not None and abs(needed) <= ROLL_MAX_DEG else 0.0
            solved, why = _pose(job, fr, candidate['view'], refs, [target], box, aspect, frame, lens0,
                                keep_ids, keep_m, must_see, min_px, roll_deg=roll, diagnose=not diagnosed,
                                floor=floor, skip=mine, log=log, cache=cache, axis_dir=axis if long_name else None)
            if not solved and isinstance(why, dict):
                diagnosed = True
                # The picture's own view, a must-see part thin (2026-10-13: the camera turned 10-20 deg to find an
                # angle it showed at): hidden is the opening's job, never the angle's; small comes closer; off frame
                # moves the framing - then, only then, other angles.
                if why['kind'] == 'hidden':
                    blockers = _blocking(why['parts'])
                    code = 'SHOW_NOT_OPENING' if show_core.opens(how) else 'SHOW_NEEDS_OPENING'
                    advice = (f"the {how} opening does not uncover them - its cut misses or something else is in front"
                              if show_core.opens(how) else
                              'this moment shows the outside: make it a section, cutaway, x-ray, transparency or explode in the '
                              'shot list (show), draw its picture that way, or take the part off this moment')
                    raise _failed(f"{code}: at t={fr['at']} {sorted(why['parts'])} are hidden behind {blockers} from the picture's "
                                  f"view {list(picture_view)}: {advice}",
                                  {'code': code, 'at': fr['at'], 'view': list(picture_view), 'diagnosis': _diagnosis(why)})
                if why['kind'] == 'blocked':   # behind another thing: other views are tried below, the reason kept
                    rejected.append({'view': list(candidate['view']), 'why': why['why'], 'diagnosis': _diagnosis(why)})
                    continue
                retry = dict(fr)
                if why['kind'] == 'small':
                    worst = min(d['px_if_open'] / max(1, min_px.get(i, 0)) for i, d in why['parts'].items())
                    scale = min(CLOSER_MAX, SIZE_MARGIN / max(worst, 1e-3))
                    retry['height_share'] = fr['height_share'] * scale
                    fixed = {'size_scale': round(scale, 3), 'because': f"{sorted(why['parts'])} too small at the picture's size"}
                else:
                    shift = [0.0, 0.0]
                    for d in why['parts'].values():
                        c = d.get('screen_centre') or [0.5, 0.5]
                        for axis_i in (0, 1):
                            shift[axis_i] += (min(0.92, max(0.08, c[axis_i])) - c[axis_i]) / len(why['parts'])
                    retry['centre'] = [fr['centre'][0] + shift[0], fr['centre'][1] + shift[1]]
                    fixed = {'place_shift': [round(v, 4) for v in shift], 'because': f"{sorted(why['parts'])} off the frame"}
                solved, why2 = _pose(job, retry, candidate['view'], refs, [target], box, aspect, frame, lens0,
                                     keep_ids, keep_m, must_see, min_px, roll_deg=roll, floor=floor, skip=mine, log=log, cache=cache,
                                     axis_dir=axis if long_name else None)
                if solved:
                    chosen = candidate
                    break
                fixed = {}
                why = why['why'] + f"; {why['kind']} fix: {why2}"
            if solved:
                chosen = candidate
                break
            rejected.append({'view': list(candidate['view']), 'why': why if isinstance(why, str) else why.get('why')})
        if not solved:
            # every pose the solve reached, at any lens of any view, was refused for one reason (under the support, in a
            # volume): say that, not 'unsolved' (the views' reasons follow either way)
            code = _pose_code(log)
            # parts that stood in front (a neighbour, a leg, the next engine): name them, and the ways that clear them
            blocking = sorted({b for r in rejected for d in ((r.get('diagnosis') or {}).get('parts') or {}).values() for b in d['blocked_by']})
            hidden = sorted({p for r in rejected for p in ((r.get('diagnosis') or {}).get('parts') or {})})
            clear = (f" - {blocking} stand in front of {hidden}: make them see-through (transparency, xray) or open them at this "
                     "moment (shot list show), or draw the picture from a side they leave free (from above, or round them);"
                     if blocking else ' -')
            raise _failed(f"{code}: at t={fr['at']} no view within {core.VIEW_STEPS['azimuth_deg'][-2]} deg of the picture's "
                          f"{picture_view} works: " + '; '.join(f"{r['view']}: {r['why']}" for r in rejected[:6])
                          + clear + ' raise or move the subject in the scene, take a must-see part off this moment, or draw another picture',
                          {'code': code, 'at': fr['at'], 'view': list(picture_view), 'blocking': blocking, 'parts': hidden, 'rejected': rejected})
        eye, aim = tuple(solved['position']), tuple(solved['aim'])
        if lying and turned is None:
            tilt_warnings.append(f"FRAME_TILT_UNREACHED: {shot['shot_id']} t={fr['at']}: the picture leans the lying subject {want:.0f} deg; "
                                 f"no side of it shows that lean from {picture_view[0]:.0f} deg up - pose the subject nearer the picture "
                                 'at this moment (the author script), then redraw the picture')
        elif want is not None and axis is not None and not lying:
            if needed is None or abs(needed) > ROLL_MAX_DEG:
                tilt_warnings.append(f"FRAME_TILT_UNREACHED: {shot['shot_id']} t={fr['at']}: the picture leans the subject {want:.0f} deg; "
                                     f"rolling the camera would take {needed if needed is None else round(needed)} deg (limit {ROLL_MAX_DEG:.0f}) - "
                                     'pose the subject nearer the picture at this moment (the author script), then redraw the picture')
        keys.append({'frame': frame, 'eye': eye, 'aim': aim, 'lens': solved['lens_mm'], 'roll': roll, 'axis': axis})
        if solved.get('in_atmosphere'):   # softened by the project's policy: said, not refused
            tilt_warnings.append(f"CAMERA_IN_ATMOSPHERE (warning by policy): {shot['shot_id']} t={fr['at']}: {solved['in_atmosphere']}")
        rows.append({'at': fr['at'], 'frame': frame, 'lens_mm': solved['lens_mm'], 'picture_view': list(picture_view),
                     'view': list(chosen['view']), 'offset_from_picture': chosen['offset'], 'rejected': rejected,
                     **fixed, **({'moved_because': rejected[0]['why']} if rejected and any(chosen['offset'].values()) else {}),
                     'blocked_share': solved.get('blocked_share'), 'must_see': must_see, 'must_see_px': solved.get('must_see_px'),
                     'lens_rejected': solved.get('lens_rejected') or [],
                     'azimuth_from': 'tilt' if turned is not None else 'picture',
                     **{k: solved[k] for k in ('position', 'aim', 'achieved', 'steps', 'limited_by', 'target', 'mode', 'engaged', 'pass') if k in solved},
                     **({'detail_px': solved['detail_px']} if solved.get('detail_px') else {}), **({'picture': fr['picture']} if fr.get('picture') else {}),
                     'roll_deg': round(roll, 2), 'picture_tilt_deg': want,
                     # the target's own extents at this moment (_spans, longest first): what a picture's box can be of it
                     'spans_m': [round(e, 3) for e in spans],
                     **({'in_atmosphere': solved['in_atmosphere']} if solved.get('in_atmosphere') else {}),
                     'tilt_deg': None if axis is None or frame_core.screen_tilt_deg(eye, aim, axis, roll) is None
                     else round(frame_core.screen_tilt_deg(eye, aim, axis, roll), 2)})
    subjects = _sample_points(target, count)
    follow = float(p.get('follow', core.PARAMS['frame_to_frame']['follow']))
    anchors = core.follow_anchors(subjects, follow)
    centre_point = tuple(sum(subjects[k['frame']][i] for k in keys) / len(keys) for i in range(3))
    relative = [{**k, 'eye': core._sub(k['eye'], anchors[k['frame']]), 'aim': core._sub(k['aim'], anchors[k['frame']])} for k in keys]
    path = p.get('path', 'auto')
    if path == 'auto':   # poses from different sides swing around the subject; from one side, a line
        sides = [core.side_of(k['eye'], subjects[k['frame']]) for k in keys]
        turn = max(abs((b - a + 180) % 360 - 180) for a, b in zip(sides, sides[1:]))
        path = 'arc' if turn > core.ARC_FROM_DEG else 'straight'
    centre_rel = core._sub(centre_point, anchors[keys[0]['frame']]) if path == 'arc' else (0.0, 0.0, 0.0)
    timing = {**(defaults.get('timing') or {'profile': 'ease_in_out'}), **(move.get('timing') or {})}
    box0 = core._ref(resolve({'target': target}), target, 'box')
    progress = core.subject_progress(subjects, [k['frame'] for k in keys], count, max(core.size(box0))) \
        if p.get('pace', 'subject') == 'subject' else None
    pacing = 'subject' if progress else timing.get('profile')
    if progress is None and len(keys) == 2:   # two poses: the move's own timing carries the camera between them
        curve, (f0, f1) = rig_core.timing_curve(timing), (keys[0]['frame'], keys[1]['frame'])
        progress = [0.0 if f <= f0 else 1.0 if f >= f1 else curve((f - f0) / (f1 - f0)) for f in range(count)]
    poses = core.frame_path(relative, count, path, timing.get('profile') != 'linear', centre_rel, progress)
    fps = job.get('fps') or 30
    kept, limited = core.limit_roll_rate([k['roll'] for k in keys], [k['frame'] for k in keys], count, progress, fps)
    for i, wanted, now_roll in limited:   # the lean is matched only as fast as it is comfortable to watch
        keys[i]['roll'] = now_roll
        rows[i]['roll_deg'] = round(now_roll, 2)
        if keys[i]['axis'] is not None:
            lean = frame_core.screen_tilt_deg(keys[i]['eye'], keys[i]['aim'], keys[i]['axis'], now_roll)
            rows[i]['tilt_deg'] = None if lean is None else round(lean, 2)
        tilt_warnings.append(f"FRAME_ROLL_LIMITED: {shot['shot_id']} t={rows[i]['at']}: matching the picture's lean would turn the camera "
                             f"{wanted - keys[i - 1]['roll']:+.0f} deg between pictures, faster than {core.ROLL_RATE_MAX_DEG:.0f} deg/s; it turns "
                             f"{now_roll - keys[i - 1]['roll']:+.0f} deg - pose the subject nearer the picture's lean at this moment (the author script)")
    rolls = core.roll_track([k['roll'] for k in keys], [k['frame'] for k in keys], count, progress)
    # the joined path: never inside what the camera keeps out of; how much of the focus hides behind something else
    checks, blocked_runs, fogged_path = [], [], []
    for f in list(range(0, count, PATH_STEP)) + [count - 1]:
        eye, aim = core._add(anchors[f], poses[f]['eye']), core._add(anchors[f], poses[f]['aim'])
        floor = _support(f, mine, cache)
        held = core._ref(resolve({'target': target}, frame=f + 1), target, 'box') if floor is not None or _floor_objects() else None
        middle = core.centre(held)[2] if held else None
        clash = _keep_out(keep_ids, f + 1, eye, keep_m, middle, mine, cache)
        if clash:
            raise ValueError(f'CAMERA_MOVE: between the pictures the camera passes {clash} at frame {f}; choose follow or path '
                             '(params) so it goes around, or another picture')
        fog = _in_atmosphere(f + 1, eye, core.centre(held or core._ref(resolve({'target': target}, frame=f + 1), target, 'box')), cache) \
            if _atmosphere_objects() else None
        if fog:
            fogged_path.append(f)   # between the pictures: said, not refused - the pictures' poses are what is refused
        low = _below_support(floor, eye, middle, _support_margin(held)) if floor is not None else None
        if low:
            raise ValueError(f'{low} - between the pictures, at frame {f}; choose follow or path (params) so it stays above, '
                             'or another picture')
        blocked = camera_frame.blocked_share(refs, [target], eye, aim, poses[f]['lens'], aspect, f + 1, count=400,
                                             roll_deg=rolls[f])
        checks.append({'frame': f, 'blocked_share': blocked})
        if blocked is not None and blocked > BLOCKED_MAX:
            blocked_runs.append(f)
    warnings = [f"FRAME_PATH_BLOCKED: {shot['shot_id']} frames {blocked_runs[0]}-{blocked_runs[-1]}: up to "
                f"{round(max(c['blocked_share'] or 0 for c in checks if c['frame'] in blocked_runs) * 100)} % of the focus behind "
                'something else between the pictures - change follow or path, or move what stands in the way'] if blocked_runs else []
    forward = rig_core.headings(anchors)[0]
    right = rig_core.normalize(rig_core.cross(forward, rig_core.UP))
    offsets = [[round(rig_core.dot(pose['eye'], right), 6), round(pose['eye'][2], 6), round(-rig_core.dot(pose['eye'], forward), 6)] for pose in poses]
    anchor_ref, aim_name = AIM_PREFIX + shot['shot_id'] + '.anchor', AIM_PREFIX + shot['shot_id']
    _aim_empty(anchor_ref, anchors)
    _aim_empty(aim_name, [core._add(anchors[f], pose['aim']) for f, pose in enumerate(poses)])
    rig = {'type': 'follow', 'subject': anchor_ref, 'look_target': aim_name, 'guard_target': target,
           'offset_keys': [{'frame': f, 'offset_m': o, 'ease': 'linear'} for f, o in enumerate(offsets)],
           'aim_keys': [{'frame': 0, 'blend': 1.0}],
           'lens_keys': [{'frame': f, 'lens_mm': round(pose['lens'], 3), 'ease': 'linear'} for f, pose in enumerate(poses)],
           'pitch_limit_deg': 89.0}   # the pictures chose how steep it looks; the solve already keeps it out of declared volumes
    if any(abs(k['roll']) > 1e-6 for k in keys):
        rig['roll_keys'] = [{'frame': f, 'roll_deg': round(r, 4), 'ease': 'linear'} for f, r in enumerate(rolls)]
    warnings += tilt_warnings
    if fogged_path:
        warnings.append(f"ATMOSPHERE_COVERS: {shot['shot_id']} frames {fogged_path[0]}-{fogged_path[-1]}: between the pictures the "
                        f'camera passes through an atmosphere volume (or within {ATMOSPHERE_NEAR_M} m of one) - choose follow or path '
                        '(params) so it goes around')
    if guards:
        rig['guards'] = guards
    blur = defaults.get('motion_blur', {})
    shutter = move['motion_blur_shutter'] if 'motion_blur_shutter' in move else blur.get('shutter')
    if shutter:
        rig['motion_blur_shutter'] = shutter
    report = {'schema_version': 1, 'move': move, 'style': (style or {}).get('name'), 'frames': rows, 'follow': follow, 'path': path,
              'pacing': pacing, 'timing': timing, 'path_checks': checks, 'warnings': warnings, 'notes': {}, 'repairs': [],
              'camera_cues': {}, 'rig': rig, 'focus': rows[-1],   # the last pose stands for the move where one focus is read
              # how far the subject travels over the shot: a moving one is judged where the camera was solved to frame it
              'subject_travel_m': round(max(math.dist(subjects[0], s) for s in subjects), 3)}
    return rig, report



def _seen_boxes(job, row, refs, camera):
    """The framed thing's points at this moment projected two ways, nothing hidden in either (like with like):
    {'box': through the scene camera as built, 'posed_box': from the pose the move solved (position, aim, lens, roll)} -
    where they differ, the rig or the look moved the camera off its pose (MOVING_SUBJECT_OFF)."""
    import camera_frame
    from bpy_extras.object_utils import world_to_camera_view
    scene = bpy.context.scene
    try:
        points = camera_frame.sample(refs, bpy.context.evaluated_depsgraph_get(), count=400)
    except ValueError:
        return {}
    width, height = job.get('output_size') or (1080, 1920)
    seen = [world_to_camera_view(scene, camera, Vector(p)) for p in points]
    built = [(v.x, 1.0 - v.y) for v in seen if v.z > 0]
    basis = frame_core.basis(*frame_core.view_of(row['position'], row['aim']), roll_deg=row.get('roll_deg') or 0.0)
    posed, _behind = frame_core.project(points, row['position'], basis, frame_core.tangents(row['lens_mm'], width / height))
    box = lambda xy: [round(min(x for x, _ in xy), 4), round(min(y for _, y in xy), 4), round(max(x for x, _ in xy), 4),  # noqa: E731
                      round(max(y for _, y in xy), 4)] if xy else None
    return {'box': box(built), 'posed_box': box([(x, y) for x, y, _z in posed])}


def measure_built(job, report):
    """Each frame picture's moment as the finished scene shows it, written into its report row as 'built': the
    subject's long axis seen by the scene camera at that frame (camera_frame_core.seen_lean: its lean on screen and its
    angle to the line of sight) and what the atmosphere volumes cover (camera_frame.atmosphere_cover). Measured after
    the rig is baked and the look applied (a shake, a lens shift), so the judgement reads the camera that renders, not the
    pose the move asked for (2026-10-14: a cylinder seen down its axis was judged by the lean the report claimed)."""
    import camera_frame
    shot = job['shot']
    move = shot['camera'].get('move') or {}
    if move.get('type') != 'frame_to_frame' or not report:
        return report
    p = move.get('params', {})
    target = p.get('target')
    refs = list(p['parts']) if p.get('parts') else [target]
    scene = bpy.context.scene
    camera = scene.camera
    for row in report.get('frames') or []:
        scene.frame_set(row['frame'] + 1)
        found = resolve_ref(target)
        built = {}
        if camera is not None and found and found['box']:
            centre = tuple((Vector(found['box'][0]) + Vector(found['box'][1])) / 2)
            axis = _axis(target, p.get('axis') or _long_axis(target) or 'z')
            if axis is not None:
                m = camera.matrix_world
                built.update(frame_core.seen_lean(axis, tuple(m.translation), centre, tuple(m.col[0].xyz.normalized()),
                                                  tuple(m.col[1].xyz.normalized())))
        if camera is not None and row.get('position') and row.get('aim'):
            built.update(_seen_boxes(job, row, refs, camera))
        if camera is not None:
            cover = camera_frame.atmosphere_cover(refs, row['frame'] + 1)
            if cover is not None:
                built['atmosphere'] = cover
        row['built'] = built
    scene.frame_set(1)
    return report


def compile_move(job, style=None, on_cues=None):
    """shot.camera.move -> (rig dict for bake_camera_rig, report). `style` is the motion style json (or None).
    on_cues(cues) is called once the camera's pass frames are known ({'cam-<mark>': frame}) and before any
    clearance repair, so cue-bound scene actions (a road opening) exist when geometry is measured; it is called again
    with the settled cues when a repair moved them (it must replace its own earlier output)."""
    shot = job['shot']
    move = shot['camera']['move']
    count, fps = shot['duration_frames'], job['fps']
    defaults = (style or {}).get('defaults', {})
    params = move.get('params', {})
    if move['type'] in ('tilt', 'pull_back') and not isinstance(params.get('target'), str):
        raise ValueError(f"CAMERA_MOVE: {move['type']} target must be a scene group reference for its visibility guard; "
                         'put the parts to frame in focus.parts')
    if move['type'] == 'frame_to_frame':
        unread = [k for k in ('look_target', 'framing', 'then', 'whip_in_deg', 'arrive', 'dwell', 'focus', 'lens_end_mm') if move.get(k)]
        if unread:
            raise ValueError(f'CAMERA_MOVE: frame_to_frame takes its poses, aim and lens from its frame targets; omit {unread}')
        return _compile_frames(job, move, style, on_cues)
    if move['type'] == 'tilt':
        unread = [k for k in ('look_target', 'framing', 'then', 'whip_in_deg', 'arrive', 'dwell') if move.get(k)]
        if unread:
            raise ValueError(f'CAMERA_MOVE: fixed-eye tilt owns its aim and path; omit {unread}')
    geo = resolve({**params, '_then': move.get('then', [])})   # `then` may name scene references too
    if move['type'] in core.OBJECT_MOVES:   # framing defaults from this output and its platform UI, unless the move gives them
        width, height = job.get('output_size') or (1080, 1920)
        ui = job.get('ui_rect') or [0.0, 0.0, 1.0, 1.0]
        derived = {'aspect': width / height, **({'centre_v': round(1 - (ui[1] + ui[3]) / 2, 4)} if 'centre_v' in core.PARAMS[move['type']] else {})}
        move = {**move, 'params': {**derived, **params}}
    plan = core.plan(move, geo)
    focus_report = None
    orbit_aim_offset = None
    focus_frame = None
    focus_progress = 0.5
    if move.get('focus'):   # solve where the camera stands so the focus shows as asked (camera_frame.py)
        if move['type'] not in core.FOCUSED:
            raise ValueError(f"FRAME_UNSUPPORTED: {move['type']} frames from no single pose; focus works with {sorted(core.FOCUSED)}")
        refs = move['focus'].get('parts') or (shot.get('screen') or {}).get('subject')
        if not refs:
            raise ValueError('FRAME_UNSOLVED: no focus to frame - set screen.subject or camera.move.focus.parts')
        p = move['params']
        index, share = core.FOCUSED[move['type']]
        focus_frame = round(share * (count - 1))
        if move['type'] in ('crane', 'turntable', 'orbit_reveal', 'tilt'):
            timing = {**(defaults.get('timing') or {'profile': 'ease_in_out'}), **(move.get('timing') or {})}
            focus_frame = core.pass_frame(rig_core.timing_curve(timing), share, count)
            focus_progress = rig_core.timing_curve(timing)(focus_frame / max(1, count - 1))
        if plan['kind'] == 'orbit':
            center = _sample_points(p['target'], count)[focus_frame]
            orbit = plan['orbit']
            angle = orbit['start_deg'] + plan['sweep_deg'] * focus_progress
            start = rig_core.orbit_eye(center, orbit['radius_m'], orbit['height_m'], math.radians(angle))
            view = (math.degrees(math.atan2(orbit['height_m'], orbit['radius_m'])), angle + 90.0)
        elif move['type'] == 'crane':
            start = core._v(plan['waypoints'][0], plan['waypoints'][-1], focus_progress)
            delta = core._sub(start, plan['aim'])
            view = (math.degrees(math.atan2(delta[2], math.hypot(delta[0], delta[1]))), p.get('azimuth_deg', 0.0))
        else:
            # Moving subjects must seed the solve from the same pose its rays sample, not their opening position.
            focus_geo = resolve({**p, '_then': move.get('then', [])}, frame=focus_frame + 1)
            start = core.plan(move, focus_geo)['waypoints'][index]
            view = (p.get('elevation_deg', core.PARAMS[move['type']]['elevation_deg']), p.get('azimuth_deg', core.PARAMS[move['type']]['azimuth_deg']))
        lens = (move.get('lens_end_mm') if share >= 1.0 else None) or move.get('lens_mm', 24.0)
        if move['type'] in ('crane', 'turntable', 'orbit_reveal', 'tilt') and move.get('lens_end_mm'):
            lens += (move['lens_end_mm'] - lens) * focus_progress
        import camera_frame
        details = [d['id'] for d in (shot.get('screen') or {}).get('details', [])]
        focus_report = camera_frame.solve_focus(job, move['focus'], list(refs), start, view, focus_frame + 1, lens, details)
        focus_report['frame'] = focus_frame
        if plan['kind'] == 'orbit':
            plan['orbit'], orbit_aim_offset = core.orbit_refocus(plan, focus_report['position'], focus_report['aim'], center, focus_progress)
        elif move['type'] == 'crane':
            delta = core._sub(tuple(focus_report['position']), start)
            plan['waypoints'] = [core._add(point, delta) for point in plan['waypoints']]
        else:
            plan['waypoints'] = core.refocus(move['type'], plan['waypoints'], focus_report['position'])
        plan['aim'], plan['aim_ref'] = tuple(focus_report['aim']), None
    timing = {**(defaults.get('timing') or {'profile': 'ease_in_out'}), **(move.get('timing') or {})}
    blur = defaults.get('motion_blur', {})
    shutter = move['motion_blur_shutter'] if 'motion_blur_shutter' in move else blur.get('shutter')
    report = {'schema_version': 1, 'move': move, 'style': (style or {}).get('name'), 'resolved': {
        'points': {k: core._r3(v) for k, v in geo['points'].items()},
        'boxes': {k: [core._r3(v[0]), core._r3(v[1])] for k, v in geo['boxes'].items()}}, 'notes': plan['notes'], 'repairs': [],
        **({'focus': focus_report} if focus_report else {})}
    rig = {'guards': dict(move.get('guards', {}))}
    progress = rig_core.timing_curve(timing)
    # object moves aim at their planned point (it carries centre_v and a solved focus), guarding the object itself
    look = move.get('look_target') or (None if move['type'] in core.OBJECT_MOVES else plan['aim_ref'])
    whip_or_aim = bool(move.get('whip_in_deg') or look or plan['aim'] is not None)
    if plan['kind'] == 'orbit':
        if move.get('dwell'):
            raise ValueError('CAMERA_MOVE: dwell binds to camera cues; an orbit has none')
        if move.get('framing'):
            raise ValueError('CAMERA_MOVE: framing applies to flythrough moves (an orbit keeps its target centred)')
        if not isinstance(params.get('target'), str):
            raise ValueError('CAMERA_MOVE: orbit_reveal needs a scene reference as target')
        rig.update({'type': 'orbit', 'subject': params['target'], 'orbit': plan['orbit'], 'sweep_deg': plan['sweep_deg'], 'timing': timing})
        if orbit_aim_offset is not None:
            centers = _sample_points(params['target'], count)
            aim_name = AIM_PREFIX + shot['shot_id']
            aims = [core._add(c, core.rotate_z(orbit_aim_offset, plan['sweep_deg'] * (progress(f / max(1, count - 1)) - focus_progress)))
                    for f, c in enumerate(centers)]
            _aim_empty(aim_name, aims)
            rig['look_target'], rig['guard_target'] = aim_name, params['target']
            rig['aim_keys'] = [{'frame': 0, 'blend': 1.0}]
        cues = {}
        if on_cues:
            on_cues(cues)
    else:
        base_timing, planned = timing, plan['waypoints']
        timing, progress, mark_u, cues = core.cues_for(core.catmull_rom(planned), planned, plan['marks'], base_timing, move,
                                                       count, fps, whip_or_aim, rig_core.timing_curve)
        report['camera_cues_pre_repair'] = dict(cues)
        clearance = move.get('clearance_m', 0.0)
        opening = resolve_ref(params['opening']) if isinstance(params.get('opening'), str) else None
        skip = {o.name for o in opening['objects']} if opening else set()  # a hole marker is not a wall
        waypoints = planned
        # Cue-bound actions (a road opening, falling debris) happen at the camera's pass frames, and the clearance
        # repair measures the scene as it stands at those frames; a repair that moves waypoints moves the pass frames.
        # Iterate to the fixed point so reveals, exposure keys, graphics and framing follow the path the camera flies.
        for _round in range(core.CUE_ITERATIONS):
            if on_cues:
                on_cues(cues)
            if clearance <= 0:
                break
            cache = {}
            pass_frame = {i: cues.get(f'cam-wp{i}', 0) for i in range(len(planned))}
            waypoints, report['repairs'] = repair(planned, clearance, lambda i: _trees_at(pass_frame[i], skip, cache))
            timing, progress, mark_u, settled = core.cues_for(core.catmull_rom(waypoints), waypoints, plan['marks'], base_timing,
                                                              move, count, fps, whip_or_aim, rig_core.timing_curve)
            if settled == cues:
                break
            cues = settled
        else:
            raise ValueError(f'CAMERA_MOVE: cue drift - clearance repairs keep moving the pass frames after {core.CUE_ITERATIONS} rounds '
                             f'(last {cues}); give the camera more room near the moving geometry')
        if move.get('dwell'):   # linger at cues: every cue above already follows the dwelled curve
            report['dwell'] = core.dwell_frames(timing, move['dwell'], count, rig_core)
        report['mark_progress'] = {f'cam-{k}': round(u, 5) for k, u in mark_u.items()}
        late = [{'cue': a['cue'], 'frame': cues.get(a['cue']), 'not_before_frame': round(a['not_before_s'] * fps)}
                for a in move.get('arrive', []) if a['cue'] not in cues or cues[a['cue']] < round(a['not_before_s'] * fps)]
        if late:  # advisory at build: `camera fit` turns arrive into a hard penalty and re-times the head
            report['arrive_violations'] = late
        dense = core.catmull_rom(waypoints)
        back = core.backtrack_m(dense, waypoints)
        if back > core.BACKTRACK_TOLERANCE_M:  # invariant: the camera never reverses between two waypoints
            raise ValueError(f'MOVE_PATH_LOOP: the path runs {back:.2f} m backwards between waypoints {[core._r3(p) for p in waypoints]}; '
                             'move or drop the waypoint that sits too close to its neighbours')
        name = PATH_PREFIX + shot['shot_id']
        _polyline(name, dense)
        length = core.path_length(dense)
        rig.update({'type': 'flythrough', 'path': name, 'look_ahead_m': 0.0, 'timing': {'distance_m': length, **timing}})
        report.update({'waypoints': [core._r3(p) for p in waypoints], 'path_points': len(dense), 'path_length_m': round(length, 3)})
        if clearance > 0:
            lengths = [0.0]
            for a, b in zip(dense, dense[1:]):
                lengths.append(lengths[-1] + (Vector(b) - Vector(a)).length)
            flown = core.flown_distance(length, timing, whip_or_aim)   # where the rig is at progress u (the cues' own rule)
            frames = [next((f for f in range(count) if progress(f / max(1, count - 1)) * flown >= s - 1e-9), count - 1) for s in lengths]
            hits = [_nearest(_trees_at(frames[i] // 10 * 10, set(), cache), Vector(p)) for i, p in enumerate(dense)]
            report['path_min_clearance_m'] = round(min(h[2] for h in hits if h), 3) if any(hits) else None
        bpy.context.scene.frame_set(1)
        whip = move.get('whip_in_deg')
        if move['type'] == 'tilt':
            if whip:
                raise ValueError('CAMERA_MOVE: tilt supplies its own aim traversal; omit whip_in_deg')
            ends = [_sample_points(p, count) if isinstance(p, str) else [core._ref(geo, p)] * count
                    for p in (params['aim_from'], params['aim_to'])]
            mid = core._v(ends[0][focus_frame or 0], ends[1][focus_frame or 0], focus_progress)
            offset = core._sub(plan['aim'], mid) if focus_report else (0.0, 0.0, 0.0)
            aims = []
            for f, (a, b) in enumerate(zip(*ends)):
                u = progress(f / max(1, count - 1))
                # Named endpoints remain exact anchors; composition applies only around the solved hero.
                w = u / max(focus_progress, 1e-9) if u <= focus_progress else (1 - u) / max(1 - focus_progress, 1e-9)
                weight = w * w * (3 - 2 * w)
                aims.append(core._add(core._v(a, b, u), core._mul(offset, weight)))
            aim_name = AIM_PREFIX + shot['shot_id']
            _aim_empty(aim_name, aims)
            rig['look_target'], rig['guard_target'] = aim_name, params['target']
            report['tilt_aims'] = [core._r3(aims[0]), core._r3(aims[-1])]
        elif whip:
            targets = _sample_points(look, count) if look else [plan['aim'] or dense[-1]] * count
            aim_name = AIM_PREFIX + shot['shot_id']
            _aim_empty(aim_name, core.whip_aim(dense[0], targets, whip, max(1, round(WHIP_S * fps))))
            rig['look_target'] = aim_name
            if look:   # aim at the empty, guard the object it swings onto
                rig['guard_target'] = look
            report['look_target_guard'] = f'on: guards {look}' if look else 'off: whip aims at a point, not an object'
        elif look:
            rig['look_target'] = look
        elif plan['aim'] is not None:
            aim_name = AIM_PREFIX + shot['shot_id']
            _aim_empty(aim_name, [plan['aim']])
            rig['look_target'] = aim_name
            target = plan['aim_ref'] if isinstance(plan['aim_ref'], str) else (move.get('params') or {}).get('target')
            if isinstance(target, str):   # the empty sits on the target's point; the guard looks at the target itself
                rig['guard_target'] = target
                report['look_target_guard'] = f'on: guards {target}'
        else:
            rig['look_ahead_m'] = min(10.0, length / 4)
            if 'distance_m' not in (move.get('timing') or {}):   # the user's distance wins (it would otherwise be a silent no-op)
                rig['timing']['distance_m'] = max(0.0, length - rig['look_ahead_m'])
        if plan['pitch_limit_deg']:
            rig['pitch_limit_deg'] = plan['pitch_limit_deg']
        if move.get('framing'):
            rig['framing'] = core.compile_framing(move['framing'], cues, count)
            report['framing'] = rig['framing']
    if move['type'] == 'macro_push' and focus_report and isinstance(params.get('target'), str):
        subjects = _sample_points(params['target'], count)
        if any(core._len(core._sub(p, subjects[0])) > 1e-5 for p in subjects[1:]):
            # A focused push travels relative to its moving subject; a static end aim loses a descending engine.
            start = core._sub(plan['waypoints'][0], subjects[0])
            end = core._sub(tuple(focus_report['position']), subjects[-1])
            if 'distance_m' in (move.get('timing') or {}):
                distance = core._len(core._sub(end, start))
                end = core._add(start, core._mul(core._sub(end, start), move['timing']['distance_m'] / max(distance, 1e-9)))
            forward = rig_core.headings(subjects)[0]
            right = rig_core.normalize(rig_core.cross(forward, rig_core.UP))
            offsets = [[rig_core.dot(d, right), d[2], -rig_core.dot(d, forward)] for d in (start, end)]
            composed = core._sub(tuple(focus_report['aim']), subjects[-1])
            targets = [core._add(p, composed) for p in subjects]
            if not move.get('look_target'):
                aim_name = AIM_PREFIX + shot['shot_id']
                if move.get('whip_in_deg'):
                    targets = core.whip_aim(plan['waypoints'][0], targets, move['whip_in_deg'], max(1, round(WHIP_S * fps)))
                _aim_empty(aim_name, targets)
                rig['look_target'] = aim_name
            rig.update({'type': 'follow', 'subject': params['target'],
                        'offset_keys': [{'frame': 0, 'offset_m': offsets[0]},
                                        {'frame': count - 1, 'offset_m': offsets[1], 'ease': 'linear'}],
                        'aim_keys': [{'frame': 0, 'blend': 1.0}],
                        'guard_target': rig.get('guard_target') or move.get('look_target') or params['target'],
                        'timing': {k: v for k, v in {**rig['timing'], 'scope': 'all'}.items() if k != 'distance_m'}})
            for key in ('path', 'look_ahead_m'):
                rig.pop(key, None)
            report['tracking'] = {'subject': params['target'], 'relative_push_m': round(core._len(core._sub(end, start)), 4),
                                  'subject_translation_m': round(core._len(core._sub(subjects[-1], subjects[0])), 4)}
    if move.get('lens_mm'):
        rig['lens_keys'] = [{'frame': 0, 'lens_mm': move['lens_mm']}] + (
            [{'frame': count - 1, 'lens_mm': move['lens_end_mm']}] if move.get('lens_end_mm') else [])
        rig['timing'].setdefault('scope', 'all')  # a lens change rides the same rush as the travel
    if shutter:
        rig['motion_blur_shutter'] = shutter
    if not rig['guards']:
        del rig['guards']
    report['camera_cues'] = cues
    report['rig'] = rig
    return rig, report
