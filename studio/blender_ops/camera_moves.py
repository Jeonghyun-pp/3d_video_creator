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
from frame_probe_core import PROBE_HEIGHT_PX, px_verdict
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
# [SUPPORT_MARGIN_MIN_M, SUPPORT_MARGIN_M] (2026-10-14: a 24 mm step went under the support it framed a subject on, unrefused;
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
POSE_REFUSALS = ('FRAME_BELOW_SUPPORT', 'CAMERA_IN_ATMOSPHERE', 'CAMERA_IN_SUBJECT')   # where no pose of a frame may stand: when every pose the
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


def _lenses_at(log, view):
    """The lenses a pose was refused at from this view (the log rows the intent sweeps keep; `view` as candidate_views gives it)."""
    return sorted({r['lens'] for r in log if r.get('view') == [float(v) for v in view]})


def _pose_code(log):
    """The code a frame no view solved fails with: the one refusal (POSE_REFUSALS) every pose the solve reached met."""
    posed = {r['code'] for r in log if r['posed']}
    return next(iter(posed)) if len(posed) == 1 and posed <= set(POSE_REFUSALS) else 'FRAME_UNSOLVED'


OFF_SCREEN_SHARE = 0.5   # a must-see part with less than this share of itself inside the frame is off frame, not hidden
CLOSER_MAX = core.CLOSER_MAX   # a small part: the framing comes up to this much closer (camera_moves_core)



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


def frame_context(job, move):
    """What every frame target of a frame_to_frame move is solved with (solve_frame): the target and the refs it frames,
    the output aspect, the keep-out guards, the trees built once per frame (cache), the subject's own object names and
    the must-see sizes. Checks the move's path, follow and frames (core.plan)."""
    shot = job['shot']
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
    min_px = show_core.detail_min_px(shot, (job.get('output_size') or (1080, 1920))[1])
    return {'p': p, 'target': target, 'aspect': aspect, 'refs': refs, 'framed': framed, 'guards': guards, 'keep_ids': keep_ids,
            'keep_m': keep_m, 'cache': cache, 'mine': mine, 'min_px': min_px}


INTENT_TOP = 3           # placed views (_solve_intent) that passed the probe, listed on the row for the shot's joining
INTENT_JUDGE_MAX = 8     # ... and judged in full, best score first until one passes, at most this many per sweep
INTENT_KEEP = 4          # poses each moment of a shot keeps, judged in full, for joining the shot (core.joint_rank) ...
PROBE_MARGIN_PX = 0.0    # id-pass pixels (output height / frame_probe_core.PROBE_HEIGHT_PX) a must-see part keeps above its
                         # min_px at the camera: none - a chosen pose is measured by the build's own id render
                         # (camera_frame.parts_px: the same function, grid, scene and frame), so both read the same pixels.
                         # 2026-10-17: rasterised points read 3-17 px off that render, more than half a cell could cover
PICK_VIEW_TOL_DEG = 2.0   # a recorded pick whose eye looks at its aim more than this off its recorded view is stale (moved since)
INTENT_KEEP_APART_DEG = 20.0   # ... each at least this far round the subject from the others: a choice, not a copy
PROBE_PX_SLACK = 0.8     # the probe measures a part on a quarter of shown_px's points: a pose reaching this share of a
                         # must-see part's min_px goes on to the full measure, which decides
INTENT_PLACE_TOL = 0.06  # an intent's refine places the subject within this of the picture's centre ...
INTENT_STEPS_MAX = 6     # ... in at most this many solve steps, on INTENT_SAMPLES points
INTENT_SAMPLES = 1000
INTENT_LOCAL_SEEDS = 12  # near misses (must-see partly shown, closest first, one per view) searched around more finely:
                         # a small part shows from a narrow cone the grid can step past, and how near a miss came is a
                         # weak guide to where that cone is - so many seeds, each cheap (probes ~0.02 s; 2026-10-15: a
                         # thruster's cone lay round the 7th-12th nearest miss)
AIMS = core.PLACEMENT_AIMS[1:]   # ('parts', 'between'): where a cut picture's camera aims when its own framing misses its must-see parts: at
                              # them, and half way from them to the subject's centre - a close-up keeps whose part it is
LOCAL_STEPS = {'azimuth_deg': (0.0, 5.0, -5.0, 10.0, -10.0), 'elevation_deg': (0.0, 6.0, -6.0)}   # ... by these steps


INSIDE_RAYS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)) + tuple((x * 0.57735, y * 0.57735, z * 0.57735) for x in (1, -1) for y in (1, -1) for z in (1, -1))   # the 6 axes and 8 corners
INSIDE_HITS = 12   # of INSIDE_RAYS cast from a camera, this many meeting the subject (whichever way its faces point): it is
                   # enclosed by it - inside a solid, or in a hollow of it (a skirt, a hull). Why: outside, at most the rays
                   # into its side meet it - a face 5, a corner of two walls 8, of three 10; inside a hollow open at one
                   # end 13 (2026-10-17: a free-angle search placed close-ups inside a rocket's skirt that read every part;
                   # review: a face count trusted normals that single-sided or flipped skins break)


def _inside_subject(frame, position, mine, cache=None):
    """CAMERA_IN_SUBJECT: why a camera at `position` is enclosed by the framed subject (`mine`: its object names) at
    Blender frame `frame` - INSIDE_HITS of INSIDE_RAYS meet it, faces either way - else None. One BVH of the subject per
    frame (`cache`). A moment whose show opens the subject (interior: the camera inside by design; a cut, explode or
    see-through) is never asked (_refusal)."""
    cache = {} if cache is None else cache
    key = ('inside', frame, frozenset(mine))
    if key not in cache:
        scene = bpy.context.scene
        if scene.frame_current != frame:
            scene.frame_set(frame)
        objs = [o for o in scene.objects if o.name in mine and o.type == 'MESH' and not o.hide_render]
        cache[key] = _merged_tree(objs, bpy.context.evaluated_depsgraph_get()) if objs else None
    tree = cache[key]
    if tree is None:
        return None
    hits = sum(tree.ray_cast(Vector(position), Vector(d).normalized())[0] is not None for d in INSIDE_RAYS)
    return f'CAMERA_IN_SUBJECT: the camera stands inside the subject ({hits} of {len(INSIDE_RAYS)} rays meet it)' \
        if hits >= INSIDE_HITS else None


def _past(ctx):
    """FRAME_CHECK_STOPPED once the caller's deadline (ctx['abort_at'], frames check's moment and shot time) is past: a
    search it bounds stops there, never decides."""
    import time
    if ctx.get('abort_at') is not None and time.monotonic() > ctx['abort_at']:
        raise _failed("FRAME_CHECK_STOPPED: the check's time was spent before this solve ended - nothing is decided",
                      {'code': 'FRAME_CHECK_STOPPED'})


def _refusal(frame, position, box, middle, floor, keep_ids, keep_m, skip, cache, fr=None):
    """Why a pose may not stand there (the code and its words), else None: under the support, in a keep-out volume,
    inside the subject itself unless the moment's show opens it (`fr`'s show how: show_core.opens), in an atmosphere
    volume - the last two unless the project softens them - the pose refusals, in their order."""
    import gate_policy
    low = _below_support(floor, position, middle, _support_margin(box))
    if low:
        return 'FRAME_BELOW_SUPPORT', low
    clash = _keep_out(keep_ids, frame + 1, position, keep_m, middle, skip, cache)
    if clash:
        return 'KEEP_OUT', clash
    if not show_core.opens(((fr or {}).get('show') or {}).get('how', 'exterior')):
        inside = _inside_subject(frame + 1, position, skip, cache)
        if inside and gate_policy.is_error('CAMERA_IN_SUBJECT'):
            return 'CAMERA_IN_SUBJECT', inside
    fog = _in_atmosphere(frame + 1, position, core.centre(box), cache)
    if fog and gate_policy.is_error('CAMERA_IN_ATMOSPHERE'):
        return 'CAMERA_IN_ATMOSPHERE', fog
    return None


def _solve_intent(job, fr, ctx, frame, box, picture_view, must_see, axis, long_name, want, lying, lens0, floor, log, how, own_view):
    """solve_frame keeping the picture's intent (core.intent_of): its size class, its side of level
    and its lens class, from views placed rather than solved. Every view within the intent's height band
    (core.intent_views) at the picture's lens and the next wider one is put where the picture's numbers put it
    (core.framed_pose), refused where a pose may not stand (_refusal), probed with few rays (camera_frame.probe) and
    scored (core.intent_score); the INTENT_TOP best are refined - a subject framed whole is solved to its size band in a
    few steps, a cut one keeps its placed pose - and judged in full (blocked_share, shown_px, the refusals again). When
    none passes: the rest of the lens ladder at every view, then a finer neighbourhood around the near misses (LOCAL_STEPS)
    - probes only, the best judged once.
    Why (2026-10-15): the exact search solved up to 560 poses of a failing moment, 150-1300 s; layout artists place a
    camera from a picture's intent and look. -> (solved, chosen view, roll, needed roll, rejected, extra row fields);
    raises _failed with the frame codes (SHOW_NEEDS_OPENING / SHOW_NOT_OPENING for an enclosed must-see part,
    the one refusal every pose met, else FRAME_UNSOLVED) and what stood in front."""
    import time
    import camera_frame
    started = time.monotonic()
    target, aspect, refs = ctx['target'], ctx['aspect'], ctx['refs']
    keep_ids, keep_m, cache, mine, min_px = ctx['keep_ids'], ctx['keep_m'], ctx['cache'], ctx['mine'], ctx['min_px']
    size = job.get('output_size') or (1080, 1920)
    intent = core.intent_of(fr, lens0)
    middle = (box[0][2] + box[1][2]) / 2
    wider = core.wider_lens(lens0)
    lenses = [lens0] + ([wider] if wider else [])
    passed, rejected, thin_first = [], [], None
    short_best = []   # the best judged pose whose must-see parts read only a little short (MUST_SEE_SHORT): used, said,
                      # when no pose shows them fully
    views = core.intent_views(intent, picture_view)
    boxes = [b for b in ((resolve_ref(i) or {}).get('box') for i in must_see) if b]
    shows = core.centre((tuple(min(b[0][k] for b in boxes) for k in range(3)), tuple(max(b[1][k] for b in boxes) for k in range(3)))) \
        if boxes else None   # what a cut picture must show: a close-up aims at it (core.framed_pose)
    # the picture's layout (ctx['layout'], frame_targets.picture_layout): the things round the subject, by the scene ids
    # the shot names them with - where each stands in the world, once; one the scene cannot find is left out
    layout = ctx.get('layout')
    things = {}
    for thing in (layout or {}).get('context') or []:
        found = [b for b in ((resolve_ref(i) or {}).get('box') for i in thing.get('ids') or []) if b]
        if found:
            things[thing['name']] = (tuple(min(b[0][k] for b in found) for k in range(3)), tuple(max(b[1][k] for b in found) for k in range(3)))
    long_thing = long_name is not None and axis is not None
    drawn_forward = frame_core.basis(picture_view[0], picture_view[1])[0]
    drawn_sight = math.degrees(math.acos(min(1.0, abs(sum(a * b for a, b in zip(axis, drawn_forward)) /
                                                     max(1e-9, math.sqrt(sum(a * a for a in axis))))))) if long_thing else None

    def shown_of(eye, view, lens, roll, seen):
        """Where the subject and the picture's other things stand from this pose (core.picture_terms' `shown`)."""
        _f, right, up = frame_core.basis(view[0], view[1], roll_deg=roll)
        sight = frame_core.seen_lean(axis, eye, core.centre(box), right, up)['sight_deg'] if long_thing else None
        return {'subject_box': (seen or {}).get('seen_box'), 'cut_sides': (seen or {}).get('cut_sides') or [],
                'context': {n: frame_core.screen_box(b, eye, view, lens, aspect, roll) for n, b in things.items()},
                'sight_deg': sight, 'picture_sight_deg': drawn_sight, 'long': long_thing}

    def turn(view):
        needed = frame_core.roll_for_tilt((0, 0, 0), frame_core.basis(*view)[0], axis, want) \
            if want is not None and axis is not None and not lying else None
        return (needed if needed is not None and abs(needed) <= ROLL_MAX_DEG else 0.0), needed

    def refuse(candidate, lens, code, why, owners=None):
        log.append({'code': code, 'posed': True, 'lens': lens, 'view': [float(v) for v in candidate['view']]})
        rejected.append({'view': list(candidate['view']), 'lens': lens, 'code': code, 'why': why[:200],
                         **({'owners': owners} if owners else {})})   # what stood in front (frames check: what a cheat may clear)
    cut = core.cut_picture(fr)
    closer = min(CLOSER_MAX, intent['size']['band'][1] / fr['height_share'])   # come closer, within the size class
    near, placed = {}, [0]   # near misses: view -> a pose the probe found clear of the focus but a must-see part thin
    prepared = camera_frame.probe_set(refs, [target], must_see, frame + 1,   # the moment's points, drawn once
                                      share=camera_frame.PROBE_SCORE if ctx.get('probe_only') else 1.0)
    low, high = intent['elevation']['band']

    def place_and_probe(candidate, lens, place, scale, record=True, pose=None, roll_deg=None, judge_thin=False):
        """One pose: placed (core.framed_pose; 'parts': aimed at a cut picture's must-see parts; scale: closer), refused
        where it may not stand, probed. -> (kind, data): ('refused' | 'nothing' | 'blocked', None), ('thin', {thin, off}) or
        ('pass', a candidate for judge). With record, a refusal is kept (rejected, log) and a thin pose as a near miss."""
        _past(ctx)
        roll, _needed = turn(candidate['view'])
        if roll_deg is not None:   # a recorded pose keeps its own roll
            roll = float(roll_deg)
        placing = {**fr, 'height_share': fr['height_share'] * scale}
        # 'parts': aimed at the must-see parts; 'between': half way from them to the subject's centre, so a close-up on a
        # part keeps some of what it belongs to (core.picture_terms' owner rule chooses)
        point = shows if place == 'parts' else tuple((a + b) / 2 for a, b in zip(shows, core.centre(box))) if place == 'between' else None
        eye, aim = (tuple(pose[0]), tuple(pose[1])) if pose else core.framed_pose(box, placing, candidate['view'], lens, aspect, point)
        say = (lambda code, why, owners=None: refuse(candidate, lens, code, why, owners)) if record else (lambda *a, **k: None)
        stop = _refusal(frame, eye, box, middle, floor, keep_ids, keep_m, mine, cache, fr)
        if stop:
            say(*stop)
            return 'refused', None
        seen = camera_frame.probe(refs, [target], must_see, eye, candidate['view'], lens, size, frame + 1, roll_deg=roll,
                                  prepared=prepared, blocked_max=BLOCKED_MAX, count_fronts=not ctx.get('probe_only'))
        if seen['blocked_share'] is None:
            say('FRAME_UNSOLVED', 'nothing of the focus in frame')
            return 'nothing', None
        if seen['blocked_share'] > BLOCKED_MAX:
            say('FOCUS_BLOCKED', f"{round(seen['blocked_share'] * 100)} % of the focus behind {list(seen['blockers'])[:3]} at {lens} mm",
                list(seen['blockers']))
            return 'blocked', None
        margin = min([seen['must_see_px'][i] / min_px[i] for i in must_see if min_px.get(i)] or [2.0])
        adjusted = (place != 'picture') + (scale != 1.0)
        terms = core.picture_terms(layout, shown_of(eye, candidate['view'], lens, roll, seen), aspect)
        score = core.intent_score(candidate['offset'], lens != lens0, margin, adjusted,
                                  terms={**terms['cost'], 'lens_stop': core.lens_stops(lens0, lens)})
        thin = {i: px for i, px in seen['must_see_px'].items() if px < PROBE_PX_SLACK * min_px.get(i, 0)}
        if thin:
            off = sorted(i for i in thin if seen['in_frame'].get(i, 0.0) < OFF_SCREEN_SHARE)
            if record and not judge_thin:   # a pick judged in full anyway: refused there, if at all
                nonlocal thin_first
                thin_first = thin_first or (candidate, lens, eye, roll, thin, seen)
                key = tuple(candidate['view'])   # one per view: the pose its part showed largest at
                if key not in near or (margin, -score) > (near[key]['margin'], -near[key]['score']):
                    near[key] = {'candidate': candidate, 'lens': lens, 'place': place, 'scale': scale, 'score': score, 'margin': margin}
                front = _probe_fronts(seen, thin)
                say('MUST_SEE_THIN', f'must-see {sorted(thin)} at {thin} px (need {[min_px.get(i, 0) for i in sorted(thin)]})'
                    + (f' - {off} off the frame' if off else '') + (f' - behind {front}' if front else ''),
                    sorted({o for i in thin for o in (seen.get('part_blockers') or {}).get(i, {})}))
            if not judge_thin:
                return 'thin', {'thin': thin, 'off': off}
        return 'pass', {'candidate': candidate, 'lens': lens, 'eye': eye, 'aim': aim, 'roll': roll, 'probe': seen,
                        'placement': {'aim': place, 'closer': round(scale, 3)}, 'height_share': placing['height_share'], 'score': score,
                        'terms': terms['cost'], **({'exact': True} if pose else {})}

    def sweep(lens_list):
        """Place and probe every view at these lenses; what passes goes to `passed`. The picture's own framing first;
        only when a must-see part misses does it adjust: aimed at the parts a cut picture must show when they (or the
        whole focus) are off the frame, closer within the size class when they are small."""
        only = ctx.get('probe_views')   # frames check: the views a cheat can change (where what it moves stood in front)
        for candidate in views:
            if only is not None and tuple(candidate['view']) not in only:
                continue
            for lens in lens_list:
                placed[0] += 1
                tries = [('picture', 1.0)]
                while tries:
                    place, scale = tries.pop(0)
                    if place == 'aim':   # aimed at the must-see parts: straight at them and half way back to the subject
                        tried = [(p_, *place_and_probe(candidate, lens, p_, scale)) for p_ in AIMS]   # both probed: the
                        ok = [t for t in tried if t[1] == 'pass']                                       # lower score kept
                        if ok:            # (the owner rule, core.picture_terms, is what tells them apart)
                            passed.append(min(ok, key=lambda t: t[2]['score'])[2])
                            if ctx.get('probe_only'):
                                return
                            break
                        place, kind, data = tried[0]
                    else:
                        kind, data = place_and_probe(candidate, lens, place, scale)
                    aim_parts = cut and shows is not None and place == 'picture'
                    if kind == 'nothing' and aim_parts:
                        tries.append(('aim', scale))
                    elif kind == 'thin':
                        if data['off'] and aim_parts:
                            tries.append(('aim', scale))
                        elif not data['off'] and scale == 1.0 and closer > 1.01 and any(px > 0 for px in data['thin'].values()):
                            tries.append((place, closer))
                    elif kind == 'pass':
                        passed.append(data)
                        if ctx.get('probe_only'):   # a cheat is scored by whether a view passes: the nearest first
                            return
                        break

    judged = [0]

    def judge(found):
        """Refine and judge `found` in full (_intent_pose), best score first, until one passes - at most INTENT_JUDGE_MAX:
        [the one] or []. A pose whose must-see part the full measure finds a little small is tried once more one step
        closer within the size class: within PROBE_PX_SLACK of its min px it goes
        straight to the full measure again (the probe is the coarser of the two, and the full measure has just read the
        part there), smaller but shown it is probed first."""
        nonlocal thin_first
        queue, spent = sorted(found, key=lambda c: c['score'])[:INTENT_JUDGE_MAX], 0
        while queue and spent < 2 * INTENT_JUDGE_MAX:   # each judged pose may bring one closer one
            c = queue.pop(0)
            spent += 1
            judged[0] += 1
            _past(ctx)
            solved, why = _intent_pose(job, fr, ctx, frame, box, middle, floor, intent, c, size, prepared)
            if solved is None:
                code, words, thin, *short = why
                refuse(c['candidate'], c['lens'], code, words)
                if short and (not short_best or short[0]['short_reach'] > short_best[0]['solved']['short_reach']):
                    short_best[:] = [{**c, 'solved': short[0]}]   # the smallest shortfall
                if thin:
                    thin_first = thin_first or (c['candidate'], c['lens'], c['eye'], c['roll'], thin, c['probe'])
                    if c['placement']['closer'] == 1.0 and closer > 1.01 and all(px > 0 for px in thin.values()):
                        if all(px >= PROBE_PX_SLACK * min_px.get(i, 0) for i, px in thin.items()):
                            queue.insert(0, {**c, **_dolly(c, shows, closer), 'height_share': c['height_share'] * closer,
                                             'placement': {**c['placement'], 'closer': round(closer, 3)}, 'score': c['score'] + 0.25})
                        else:
                            kind, data = place_and_probe(c['candidate'], c['lens'], c['placement']['aim'], closer, record=False)
                            if kind == 'pass':
                                queue.insert(0, data)
                continue
            return [{**c, 'solved': solved}]
        return []
    # seeded poses first - a recorded pick (fr['chosen'], frames pick: pinned) and the poses a probe-only trial of the same
    # cheat passed (ctx['seeds'], frames check): placed, probed and judged as any; the whole search only when none passes
    seeds = ([{**fr['chosen'], 'pinned': True}] if fr.get('chosen') and not ctx.get('probe_only') else []) + list(ctx.get('seeds') or [])
    refined, engaged, pinned_failed = [], [], None
    said = []   # what became of a pick on its way to a pose: said on the row (FRAME_INTENT_DEPARTED), never silent
    centre_now = core.centre(box)

    def why_since(mark):
        """The words of the last refusal recorded after `mark` (len(rejected) then), else ''."""
        return rejected[-1]['why'] if len(rejected) > mark else ''

    def again(sd):
        """A recorded pose's view, lens and placement without where it stood: placed round the subject as it stands now."""
        return {k: v for k, v in sd.items() if k not in ('eye', 'aim', 'roll_deg', 'subject_at', 'subject_pose')}

    def seed_poses(queue_seeds):
        """Each seed placed (or kept where it stood) and probed: the ones that pass, a pick first."""
        nonlocal pinned_failed
        seeded = []
        while queue_seeds:
            sd = queue_seeds.pop(0)
            view = (float(sd['view'][0]), float(sd['view'][1]))
            try:
                placement = core.placement_of(sd)
            except ValueError as error:   # a hand-written row: said, the view placed as its picture is
                said.append(f'the pick {list(view)} at {sd["lens_mm"]} mm: {error} - placed as its picture is')
                placement = dict(core.PLACEMENT_DEFAULT)
            # a pick that recorded where the camera stood is that pose exactly: placing it again from the view would use
            # the moment's size as it reads now, not as it was picked (2026-10-17: a pose painted over moved and lost its
            # part). It is carried with its subject (subject_at: where the subject's centre stood when it was recorded -
            # core.follow_subject), and must still look along its view (else it was recorded before something turned):
            # stale, it is placed again from its view as the next pick, said
            exact = None
            if sd.get('eye') and sd.get('aim'):
                # recorded as it looked: its eye along its own view (else recorded before something turned) ...
                looks = frame_core.view_of(sd['eye'], sd['aim'])
                if abs(looks[0] - view[0]) <= PICK_VIEW_TOL_DEG and abs(core.wrap_deg(looks[1] - view[1])) <= PICK_VIEW_TOL_DEG:
                    # ... then carried with its subject: the root's whole move when both ends recorded it, else its centre's
                    try:
                        eye, aim = core.follow_subject(sd['eye'], sd['aim'], sd.get('subject_at'), centre_now,
                                                       sd.get('subject_pose'), ctx.get('subject_pose'))
                        # its roll turned with it: the part it framed stays upright as it was picked (core.follow_roll)
                        roll_kept = core.follow_roll(sd['eye'], sd['aim'], sd.get('roll_deg'), sd.get('subject_pose'),
                                                     ctx.get('subject_pose'))
                    except ValueError as error:   # a hand-written matrix: said, carried by the centre
                        said.append(f'the pick {list(view)}: {error} - carried by its centre')
                        eye, aim = core.follow_subject(sd['eye'], sd['aim'], sd.get('subject_at'), centre_now)
                        roll_kept = float(sd.get('roll_deg') or 0.0)
                    if abs(roll_kept) > ROLL_MAX_DEG:   # the subject turned so far since that riding along cants the
                        # picture past the roll a lean may take (turn()): placed again from its view, said
                        said.append(f'the pick {list(view)} rides its subject to a {roll_kept:.0f} deg roll (limit '
                                    f'{ROLL_MAX_DEG:.0f}), re-placed from its view')
                        queue_seeds.insert(0, again(sd))
                        continue
                    exact = (eye, aim)
                    looks = frame_core.view_of(eye, aim)
                    view = (round(looks[0], 3), round(looks[1], 3))   # the view the pose looks along now (a lean turns it)
                else:
                    said.append(f"recorded pose stale (it looks along {[round(v, 1) for v in looks]}, not {list(view)}), re-placed from its view")
                    queue_seeds.insert(0, again(sd))
                    continue
            cand = {'view': view, 'offset': {'azimuth_deg': round(core.wrap_deg(view[1] - picture_view[1]), 3),
                                             'elevation_deg': round(view[0] - picture_view[0], 3)}}
            # a pick is judged by the full measure even where the probe reads its part thin: the probe is the coarser of
            # the two (2026-10-17: a picked thruster probed 16 px and measured 40 in full), and a pick is one pose, not a sweep
            mark = len(rejected)   # what this pose was refused for, not an earlier one's words (review 2026-10-17)
            kind, data = place_and_probe(cand, float(sd['lens_mm']), placement['aim'], placement['closer'], pose=exact,
                                         roll_deg=roll_kept if exact else None, judge_thin=bool(sd.get('pinned')))
            if kind == 'pass':
                seeded.append({**data, 'score': data['score'] - (100.0 if sd.get('pinned') else 0.0),   # a pick goes first
                               **({'seed': sd} if sd.get('pinned') else {})})
            elif sd.get('pinned') and exact:
                # where the pick stood shows nothing of what it showed (the scene changed since it was recorded, or it
                # was recorded on another): its view, lens and placement placed round the subject as it stands now
                said.append(f"the recorded pose of the pick {list(view)} at {sd['lens_mm']} mm "
                            f"{'subject-relative' if sd.get('subject_at') else 'in world coordinates'}"
                            f"{' (picked on ' + sd['scene'] + ')' if sd.get('scene') else ''} fails here ({kind}"
                            f"{': ' + why_since(mark)[:120] if why_since(mark) else ''}), re-placed from its view")
                queue_seeds.insert(0, again(sd))
            elif sd.get('pinned'):
                pinned_failed = f"the picked view {list(view)} at {sd['lens_mm']} mm no longer passes ({kind}" \
                                f"{': ' + why_since(mark)[:160] if why_since(mark) else ''})"
        return seeded

    if ctx.get('wide_only'):   # frames check's proposals for a moment that already failed: not searched again (review 2026-10-17)
        raise _failed(f"FRAME_UNSOLVED: at t={fr['at']} the free-angle poses of a moment no intent view solved (proposals)",
                      {'code': 'FRAME_UNSOLVED', 'at': fr['at'], 'intent': intent,
                       'wide': wide_search(job, fr, ctx, frame, box, middle, floor, picture_view, must_see, lens0, shows) if must_see else {}})
    seeded = seed_poses(list(seeds))
    judged_from = len(rejected)
    if seeded:
        refined = judge(seeded)
        engaged = ['seeded'] if refined else []
    held = bool(refined and refined[0]['score'] <= -50)
    pick = seeds[0] if seeds and seeds[0].get('pinned') else None
    exact_tried = next((c for c in seeded if c.get('seed') and c.get('exact')), None)
    if pick and not held and exact_tried is not None:
        # the pose where the pick stood passed the probe but not the full checks: its view placed again as it stands now
        said.append(f"the recorded pose of the pick {list(exact_tried['candidate']['view'])} at {exact_tried['lens']} mm failed "
                    f"the full checks ({(why_since(judged_from) or 'judged')[:120]}), re-placed from its view")
        judged_from = len(rejected)
        replaced = seed_poses([again(exact_tried['seed'])])
        if replaced:   # a cheat seed that passed stands when the pick placed again does not (review 2026-10-17)
            refined = judge(replaced) or refined
            engaged = ['seeded'] if refined else []
            held = bool(refined and refined[0]['score'] <= -50)
    if pick and not pinned_failed and not held:
        pinned_failed = f"the picked view {list(pick['view'])} at {pick['lens_mm']} mm failed the full checks" \
                        f"{' (' + why_since(judged_from)[:160] + ')' if why_since(judged_from) else ''}"
    if said:
        pinned_failed = '; '.join([*said, *([pinned_failed] if pinned_failed else [])])
    # a shot joined from several moments needs other poses to fall back on when the pinned ones cannot be joined (a path
    # through the subject): the sweep still runs beside an agent's pin; a user's pick stays the only pose of its moment
    agent_pin = seeds and seeds[0].get('pinned') and seeds[0].get('by') == 'agent'   # anything else is the user's
    if not refined or (agent_pin and ctx.get('keep', 1) > 1 and not ctx.get('probe_only')):
        sweep(lenses)
    if ctx.get('probe_only'):   # frames check scores a cheat by what the probes find, before any full measure: the
        if not passed:          # first view that passes (views nearest the picture first), else the rest of the ladder
            sweep([mm for mm in FLOOR_LENSES if mm < min(lenses) - 1e-6])
        best = min(passed, key=lambda c: c['score']) if passed else None
        return None, None, None, None, rejected, {
            'probe_only': True, 'placed': placed[0], 'passed': len(passed), 'seconds': round(time.monotonic() - started, 2),
            'best': {'score': best['score'], 'view': list(best['candidate']['view']), 'lens_mm': best['lens'],
                     'offset': best['candidate']['offset'], 'placement': best['placement']} if best else None,
            'passing': [{'view': list(c['candidate']['view']), 'lens_mm': c['lens'], 'placement': c['placement']}
                        for c in sorted(passed, key=lambda c: c['score'])]}
    if not refined:
        refined = judge(passed)
        engaged = []
    # no view at the picture's lens or the next wider keeps it: the rest of the ladder (FLOOR_LENSES), every view, at
    # probe cost - a wider lens stands closer, in front of what hid the focus, and shows a part larger (the exact
    # search's widen sweep, 2026-10-14)
    ladder = [mm for mm in FLOOR_LENSES if mm < min(lenses) - 1e-6]
    if not refined and ladder:
        before = len(passed)
        sweep(ladder)
        refined = judge(passed[before:])
        engaged = ['widen'] if refined else []
    # the grid steps past a part that shows only within a few degrees (2026-10-15: a thruster read at 32.2 px from one
    # view the steps went either side of): around the near misses whose must-see parts came closest to showing (some of
    # them shown), a finer
    # neighbourhood (LOCAL_STEPS) at their lens, as placed and one step closer within the size class - probes only; the
    # best that passes is judged once
    if not refined and near:
        found, tried = [], set()
        # a view its part did not show from at all is not near: no finer step there shows it
        for c in sorted((c for c in near.values() if c['margin'] > 0), key=lambda c: (-c['margin'], c['score']))[:INTENT_LOCAL_SEEDS]:
            e0, a0 = c['candidate']['view']
            for de in LOCAL_STEPS['elevation_deg']:
                for da in LOCAL_STEPS['azimuth_deg']:
                    view = (round(max(-85.0, min(85.0, e0 + de)), 3), round(a0 + da, 3))
                    if not low - 1e-6 <= view[0] <= high + 1e-6:
                        continue
                    candidate = {'view': view, 'offset': {'azimuth_deg': round(core.wrap_deg(view[1] - picture_view[1]), 3),
                                                          'elevation_deg': round(view[0] - picture_view[0], 3)}}
                    for scale in dict.fromkeys([c['scale'], *([closer] if closer > 1.01 else [])]):
                        if (view, c['lens'], c['place'], scale) in tried:
                            continue
                        tried.add((view, c['lens'], c['place'], scale))
                        placed[0] += 1
                        kind, data = place_and_probe(candidate, c['lens'], c['place'], scale, record=False)
                        if kind == 'pass':
                            found.append(data)
        if found:
            refined = judge(found)
            engaged = ['local'] if refined else []
    if not refined and short_best:
        import gate_policy
        if not gate_policy.is_error('MUST_SEE_SHORT'):   # a part a little thin reads (user, 2026-10-17): kept, said
            refined, engaged = short_best, ['short']
    kept = {(tuple(c['candidate']['view']), c['lens']): c['solved'] for c in refined}
    summary = [{'view': list(c['candidate']['view']), 'offset': c['candidate']['offset'], 'lens_mm': c['lens'], 'score': c['score'],
                'probe_blocked': c['probe']['blocked_share'], 'probe_px': c['probe']['must_see_px'], 'terms': c.get('terms') or {},
                'blockers': dict(list(c['probe']['blockers'].items())[:4]), 'placement': c['placement'],
                **({'position': kept[key]['position'], 'aim': kept[key]['aim'], 'kept': True} if key in kept else {'kept': False})}
               for c in _unique([*sorted(passed, key=lambda c: c['score'])[:INTENT_TOP], *refined])
               for key in [(tuple(c['candidate']['view']), c['lens'])]]   # the next step joins a shot from these; the poses
                                                                          # judged in full are always listed (the chosen first)
    extra = {'intent': intent, 'candidates': summary,
             'probe': {'placed': placed[0], 'passed': len(passed), 'judged': judged[0], 'listed': len(summary),
                       'seconds': round(time.monotonic() - started, 2)}}
    if refined:
        best = refined[0]
        # a shot joined from several moments (ctx['keep'], _compile_frames) keeps a few more poses that pass in full, the
        # next best by score, judged now: the shot may take one that turns less between its moments (core.joint_rank)
        kept = [best]
        chosen_key = (tuple(best['candidate']['view']), best['lens'])
        for c in sorted(passed, key=lambda c: c['score']):
            if len(kept) >= ctx.get('keep', 1) or judged[0] >= INTENT_JUDGE_MAX * 3:
                break
            if (tuple(c['candidate']['view']), c['lens']) == chosen_key or any(
                    abs(core.wrap_deg(c['candidate']['view'][1] - k['candidate']['view'][1])) < INTENT_KEEP_APART_DEG for k in kept):
                continue
            judged[0] += 1
            solved_c, _why = _intent_pose(job, fr, ctx, frame, box, middle, floor, intent, c, size, prepared)
            if solved_c is not None:
                kept.append({**c, 'solved': solved_c})
        extra['probe']['judged'] = judged[0]
        out = []
        for i, c in enumerate(kept):
            # a recorded pose built at the roll it was probed and judged at (carried with its subject: core.follow_roll),
            # not the one the picture's lean would ask from its view (review 2026-10-18: passes and build disagreed)
            roll, needed = (c['roll'], c['roll']) if c.get('exact') else turn(c['candidate']['view'])
            solved = {**c['solved'], 'engaged': ['intent', *(engaged if i == 0 else []), *c['solved'].get('engaged', [])],
                      'placement': c['placement']}
            # what the pose gives up of the picture's intent and layout, where it ended (a refine moves it): said, never
            # silent (FRAME_INTENT_DEPARTED)
            final = core.picture_terms(layout, shown_of(solved['position'], c['candidate']['view'], c['lens'], roll,
                                                        solved.get('probe_at_pose') or c['probe']), aspect)
            solved['picture_terms'] = final['cost']
            departures = core.intent_losses(intent, c['candidate']['offset'], c['lens'], solved.get('seen'), final['lost'])
            if pinned_failed and i == 0:   # a recorded pick that no longer holds: said, the engine's own choice stands
                held = c['score'] <= -50   # the pick (re-placed from its view) is still the pose
                departures.append({'kind': 'pick', 'key': None, 'value': 1,
                                   'text': pinned_failed + ('' if held else ' - the engine chose this one (frames pick again, or redraw)')})
            out.append((solved, c['candidate'], roll, needed, {**extra, 'departed': [d['text'] for d in departures],
                                                               'departures': departures}, c['score']))
        solved, chosen, roll, needed, first, _score = out[0]
        if len(out) > 1:
            first['alternatives'] = [(s, ch, r, n, {k: v for k, v in e.items() if k != 'alternatives'}, sc) for s, ch, r, n, e, sc in out]
        return solved, chosen, roll, needed, rejected, first
    if ctx.get('wide') and must_see:   # frames check asks: what a picture redrawn from elsewhere would show (proposed, never taken)
        extra['wide'] = wide_search(job, fr, ctx, frame, box, middle, floor, picture_view, must_see, lens0, shows)
    _intent_failed(job, fr, ctx, frame, box, picture_view, must_see, how, own_view, log, rejected, thin_first, extra)


def wide_search(job, fr, ctx, frame, box, middle, floor, picture_view, must_see, lens0, shows):
    """The free-angle search, run when no view within the picture's intent showed its must-see parts and the caller asks
    (ctx['wide']: frames check): every pose of core.WIDE_GRID - framing, height, side, lens - placed (core.framed_pose; a
    close-up aimed at the must-see parts), refused where a pose may not stand (_refusal, the camera inside the subject
    among them), probed, nearest the picture first up to core.WIDE_MAX_PROBES; of those whose parts the probe reads, the
    nearest judged in full (_intent_pose: the build's own id render) up to core.WIDE_JUDGE_MAX, core.WIDE_KEEP kept, each
    a choice; each kept pose solved again as the picture redrawn from there would be (core.wide_frame) - its status as
    frames check would give it. Never adopted: the moment still fails; these are proposals (frames proposals).
    -> {poses: [{framing, view, lens_mm, placement, eye, aim, roll_deg, subject_at, frame, must_see_px, departure,
    departed, resolve}], probed, passed, judged, refused, seconds}."""
    import time
    import camera_frame
    import gate_policy
    started = time.monotonic()
    target, aspect, refs, min_px = ctx['target'], ctx['aspect'], ctx['refs'], ctx['min_px']
    size = job.get('output_size') or (1080, 1920)
    prepared = camera_frame.probe_set(refs, [target], must_see, frame + 1)
    passed, refused, probed, stopped = [], {}, 0, None
    over = lambda: ctx.get('abort_at') is not None and time.monotonic() > ctx['abort_at']   # noqa: E731
    for departure, framing, view, lens in core.wide_poses(picture_view, lens0, aims=shows is not None)[:core.WIDE_MAX_PROBES]:
        if over():   # the check's time spent: what was found so far is proposed, said
            stopped = 'probe'
            break
        probed += 1
        cut = core.WIDE_GRID['framings'][framing]['cut']
        frv = core.wide_frame(fr, framing, view, lens)
        eye, aim = core.framed_pose(box, frv, view, lens, aspect, shows if cut else None)
        stop = _refusal(frame, eye, box, middle, floor, ctx['keep_ids'], ctx['keep_m'], ctx['mine'], ctx['cache'], fr)
        if stop:
            refused[stop[0]] = refused.get(stop[0], 0) + 1
            continue
        seen = camera_frame.probe(refs, [target], must_see, eye, view, lens, size, frame + 1, prepared=prepared,
                                  blocked_max=BLOCKED_MAX, count_fronts=False)
        if seen['blocked_share'] is None or seen['blocked_share'] > BLOCKED_MAX:
            continue
        if any(seen['must_see_px'].get(i, 0.0) < PROBE_PX_SLACK * min_px.get(i, 0) for i in must_see):
            continue
        passed.append({'departure': departure, 'framing': framing, 'frame_row': frv, 'height_share': frv['height_share'],
                       'candidate': {'view': view, 'offset': {'azimuth_deg': round(core.wrap_deg(view[1] - picture_view[1]), 3),
                                                              'elevation_deg': round(view[0] - picture_view[0], 3)}},
                       'lens': lens, 'roll': 0.0, 'eye': eye, 'aim': aim, 'exact': True,
                       'placement': {'aim': 'parts' if cut else 'picture', 'closer': 1.0}})
    kept, judged = [], 0
    for c in passed:
        if len(kept) >= core.WIDE_KEEP or judged >= core.WIDE_JUDGE_MAX:
            break
        if over():
            stopped = stopped or 'judge'
            break
        if any(k['framing'] == c['framing'] and abs(core.wrap_deg(k['candidate']['view'][1] - c['candidate']['view'][1]))
               < core.WIDE_KEEP_APART_DEG and abs(k['candidate']['view'][0] - c['candidate']['view'][0]) < core.WIDE_KEEP_APART_DEG
               for k in kept):
            continue   # a choice, not a copy of one kept
        judged += 1
        frv = c['frame_row']
        solved, why = _intent_pose(job, frv, ctx, frame, box, middle, floor, core.intent_of(frv, c['lens']), c, size, prepared)
        if solved is None and why[0] == 'MUST_SEE_SHORT' and len(why) > 3 and not gate_policy.is_error('MUST_SEE_SHORT'):
            solved = why[3]   # a little short reads (the project's policy): kept, said
        if solved is not None:
            kept.append({**c, 'solved': solved})
    subject_at = [round(v, 4) for v in core.centre(box)]
    poses = []
    for c in kept:
        view, lens = c['candidate']['view'], c['lens']
        poses.append({'framing': c['framing'], 'view': [float(v) for v in view], 'lens_mm': lens, 'placement': c['placement'],
                      'eye': [round(v, 5) for v in c['solved']['position']], 'aim': [round(v, 5) for v in c['solved']['aim']],
                      'roll_deg': 0.0, 'subject_at': subject_at, **({'subject_pose': ctx['subject_pose']} if ctx.get('subject_pose') else {}),
                      'frame': frame, 'height_share': c['height_share'],
                      'must_see_px': c['solved'].get('must_see_px'), 'departure': c['departure'],
                      **({'must_see_short': c['solved']['must_see_short']} if c['solved'].get('must_see_short') else {}),
                      'departed': core.wide_words(picture_view, lens0, c['framing'], view, lens)})
    for row, c in zip(poses, kept):   # the picture redrawn from there, solved as frames check would solve it
        if over():
            row['resolve'] = {'status': 'unchecked', 'message': "the check's time was spent first"}
            stopped = stopped or 'resolve'
            continue
        try:
            _key, again, warnings = solve_frame(job, {'params': ctx['p']}, c['frame_row'],
                                                {**ctx, 'wide': False, 'wide_only': False, 'cache': {}, 'keep': 1})
            lost = core.kept_intent({**again, 'warnings': warnings})
            row['resolve'] = {'status': 'moved' if lost else 'as_intended', 'lost': lost, 'view': again['view'],
                              'lens_mm': again['lens_mm'], 'must_see_px': again.get('must_see_px')}
        except ValueError as error:
            code = _code(str(error), 'FRAME_UNSOLVED')
            row['resolve'] = {'status': 'unchecked' if code == 'FRAME_CHECK_STOPPED' else 'fail', 'code': code, 'message': str(error)[:300]}
            stopped = stopped or ('resolve' if code == 'FRAME_CHECK_STOPPED' else None)
    bpy.context.scene.frame_set(frame + 1)   # the failing moment's own frame, for its diagnosis after
    return {'poses': poses, 'probed': probed, 'passed': len(passed), 'judged': judged, 'refused': refused,
            'grid': len(core.wide_poses(picture_view, lens0, aims=shows is not None)), 'seconds': round(time.monotonic() - started, 1),
            **({'stopped': stopped} if stopped else {})}


def _dolly(c, shows, closer):
    """{eye, aim} of a judged pose come `closer` times nearer its must-see parts (`shows`, their point) along the line
    from them to the camera, the camera turned no way: the parts stay where they are on the screen and grow by
    `closer` (a frame re-placed round the subject's box instead pushed a thruster near its top out of the frame,
    2026-10-15). Without a must-see point it comes nearer what it aims at."""
    eye, aim = c['eye'], c['aim']
    toward = shows if shows is not None else aim
    near = [t + (e - t) / closer for e, t in zip(eye, toward)]
    return {'eye': tuple(near), 'aim': tuple(a + (n - e) for a, n, e in zip(aim, near, eye))}


def _probe_fronts(seen, parts):
    """What the probe found in front of these parts, most first."""
    share = {}
    for i in parts:
        for owner, s in (seen.get('part_blockers') or {}).get(i, {}).items():
            share[owner] = max(share.get(owner, 0.0), s)
    return sorted(share, key=lambda o: -share[o])[:4]


def _unique(candidates):
    """Candidates once each by view, lens and where they stand (a pose judged in full may also be among those probed)."""
    seen, out = set(), []
    for c in candidates:
        key = (tuple(c['candidate']['view']), c['lens'], tuple(round(v, 4) for v in c['eye']))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _intent_pose(job, fr, ctx, frame, box, middle, floor, intent, c, size, prepared=None):
    """A placed view that passed the probe, refined and judged in full: (solved, None) or (None, (code, why, the
    must-see parts found thin)). A subject the picture frames whole is solved to its size class's band (camera_frame_core
    tol, INTENT_STEPS_MAX steps); a cut one keeps the placed pose (its
    numbers place the part of it the picture shows); either is then refused or judged in full (refusals, blocked share, must-see px), and the
    size it shows the subject at measured (camera_frame.seen_height: `seen`, and `achieved` when the solve gave none)."""
    import camera_frame
    target, aspect, refs, min_px = ctx['target'], ctx['aspect'], ctx['refs'], ctx['min_px']
    view, lens, roll = c['candidate']['view'], c['lens'], c['roll']
    # a recorded pose (a pick's eye and aim) is kept where it stood: the size it shows is said, never re-solved
    whole = not core.cut_picture(fr) and c['placement']['aim'] == 'picture' and not c.get('exact')
    solved = {'position': [round(v, 5) for v in c['eye']], 'aim': [round(v, 5) for v in c['aim']], 'achieved': None,
              'steps': 0, 'mode': 'intent', 'engaged': ['placed']}
    placed_seen = camera_frame.seen_height(prepared, c['eye'], view, lens, aspect, roll_deg=roll) if whole and prepared else None
    if placed_seen and intent['size']['band'][0] <= placed_seen['height_share'] < intent['size']['band'][1]:
        whole = False   # placed within its size class already (its width fit too): a height-only refine would undo the width
    placed_pose = solved
    if whole:
        low, high = intent['size']['band']
        h = core.size_target(c['height_share'], [low, high])   # inside its band, not on its edge (SIZE_EDGE_MARGIN)
        tol = {'size': max(frame_core.SIZE_TOL, min(h - low, high - h) / h), 'place': INTENT_PLACE_TOL, 'steps': INTENT_STEPS_MAX}
        lowest = (lambda position: (floor(position, max(position[2], middle)), _support_margin(box))) if floor is not None else None
        try:
            refined = camera_frame.solve_focus(job, {'height_share': h, 'centre': fr['centre']}, refs, c['eye'], view, frame + 1, lens,
                                               roll_deg=roll, floor=lowest, tol=tol, samples=INTENT_SAMPLES)
            solved = {**refined, 'mode': 'intent', 'engaged': ['refined', *(refined.get('engaged') or [])]}
            # a refine to the size class that runs the subject past a frame edge the placed pose kept it inside (a
            # picture's height and width read for another shape than the scene's) loses more of the picture than the
            # size: the placed pose stands, its size said
            # (by projection alone, both where the refine ended and where the pose was placed - after any dolly)
            if prepared and set(camera_frame.cut_sides(prepared['focus'], solved['position'], view, lens, aspect, roll)) \
                    - set(camera_frame.cut_sides(prepared['focus'], c['eye'], view, lens, aspect, roll)) - set(fr.get('cut_edges') or []):
                solved = placed_pose
        except ValueError:   # the band was not reached in the steps: the placed pose stands, judged as it is
            pass
    stop = _refusal(frame, solved['position'], box, middle, floor, ctx['keep_ids'], ctx['keep_m'], ctx['mine'], ctx['cache'], fr)
    if stop:
        return None, (*stop, {})
    fog = _in_atmosphere(frame + 1, solved['position'], core.centre(box), ctx['cache'])
    if fog:   # softened by the project's policy (_refusal refuses it otherwise): said on the row
        solved['in_atmosphere'] = fog
    if not show_core.opens((fr.get('show') or {}).get('how', 'exterior')):
        inside = _inside_subject(frame + 1, solved['position'], ctx['mine'], ctx['cache'])
        if inside:   # softened by the project's policy, the same way
            solved['in_subject'] = inside
    blocked = camera_frame.blocked_share(refs, [target], solved['position'], solved['aim'], lens, aspect, frame + 1, roll_deg=roll)
    if blocked is None:
        return None, ('FRAME_UNSOLVED', f'nothing of the focus in frame at {lens} mm', {})
    if blocked > BLOCKED_MAX:
        return None, ('FOCUS_BLOCKED', f'{round(blocked * 100)} % of the focus behind something else at {lens} mm', {})
    # the build's own measure (an id render at this pose: camera_frame.parts_px), so the camera and the build read the same
    keys = [k['id'] for k in (job.get('probe') or {}).get('key_parts') or [] if k.get('id')]
    shown = camera_frame.parts_px(fr.get('must_see') or [], solved['position'], solved['aim'], lens, size, frame + 1, roll_deg=roll,
                                  subjects=[target], keys=keys, cache=prepared)
    margin = PROBE_MARGIN_PX * size[1] / PROBE_HEIGHT_PX
    verdicts = {i: px_verdict(px, min_px.get(i, 0) + margin) for i, px in shown.items()}
    thin = {i: px for i, px in shown.items() if verdicts[i] != 'ok'}
    if thin and not all(verdicts[i] == 'short' for i in thin):
        return None, ('MUST_SEE_THIN', f'must-see {sorted(thin)} at {thin} px (need {[round(min_px.get(i, 0) + margin, 1) for i in sorted(thin)]})', thin)
    seen = camera_frame.seen_height(prepared, solved['position'], view, lens, aspect, roll_deg=roll) if prepared else None
    # the probe again where the pose ended (a refine moves it): how far the probe reads from the full measure
    again = camera_frame.probe(refs, [target], list(fr.get('must_see') or []), solved['position'], view, lens, size, frame + 1,
                               roll_deg=roll, prepared=prepared) if prepared else None
    full = {**solved, 'lens_mm': lens, 'blocked_share': blocked, 'must_see_px': shown, 'lens_rejected': [],
            **({'seen': seen} if seen else {}), 'achieved': solved.get('achieved') or seen,
            **({'probe_at_pose': {k: again.get(k) for k in ('blocked_share', 'must_see_px', 'seen_box', 'cut_sides', 'focus_in_frame')}}
               if again else {})}
    if thin:   # a little short (frame_probe_core.SHORTFALL_SHARE): kept aside whole, used and said only if no pose passes
        reach = min(px / (min_px.get(i, 0) + margin) for i, px in thin.items())   # the share of its minimum the worst part reaches
        words = f'must-see {sorted(thin)} at {thin} px (need {[round(min_px.get(i, 0) + margin, 1) for i in sorted(thin)]}) - a little short'
        return None, ('MUST_SEE_SHORT', words, thin, {**full, 'must_see_short': thin, 'short_reach': round(reach, 4)})
    return full, None


def _intent_failed(job, fr, ctx, frame, box, picture_view, must_see, how, own_view, log, rejected, thin_first, extra):
    """No placed view kept the intent: why, with the frame codes. A must-see part thin from the nearest pose
    that reached the check is diagnosed there once (camera_frame.diagnose_part): enclosed is the shot list's opening
    (SHOW_NEEDS_OPENING / SHOW_NOT_OPENING); otherwise the one refusal every pose met, else FRAME_UNSOLVED, naming what
    stood in front and the ways that clear it."""
    import camera_frame
    size = job.get('output_size') or (1080, 1920)
    blocking = []
    if thin_first:
        candidate, lens, eye, roll, thin, seen = thin_first
        diagnosis = {i: camera_frame.diagnose_part(i, [ctx['target']], eye, candidate['view'], lens, size, frame + 1, roll_deg=roll,
                                                   shown=[m for m in must_see if m != i], exposable=_exposable(job, i)) for i in thin}
        hidden = {i: d for i, d in diagnosis.items() if d['hidden_share'] >= camera_frame.HIDDEN_SHARE and d['own_shell']}
        if hidden:
            why = {'kind': 'hidden', 'parts': hidden}
            code = 'SHOW_NOT_OPENING' if show_core.opens(how) else 'SHOW_NEEDS_OPENING'
            advice = (f"the {how} opening does not uncover them - its cut misses or something else is in front"
                      if show_core.opens(how) else
                      'this moment shows the outside: make it a section, cutaway, x-ray, transparency or explode in the '
                      'shot list (show), draw its picture that way, or take the part off this moment')
            advice += (' - or let frames check open it: at a moment that explains parts it tries what wraps them see-through '
                       '(see_enclosure), then taken apart for the moment (explode_apart), recorded as cheats with --apply')
            raise _failed(f"{code}: at t={fr['at']} {sorted(hidden)} are hidden behind {_blocking(hidden)} from the view "
                          f"{list(candidate['view'])}: {advice}",
                          {'code': code, 'at': fr['at'], 'view': list(picture_view), 'diagnosis': _diagnosis(why),
                           'rejected': rejected, 'lenses_at_picture': _lenses_at(log, own_view), **extra})
        blocking = _blocking(diagnosis) or _probe_fronts(seen, thin)
    code = _pose_code(log)
    parts = sorted(thin_first[4]) if thin_first else []
    clear = (f" - {blocking} stand in front of {parts or must_see}: make them see-through (transparency, xray) or open them at "
             "this moment (shot list show), or draw the picture from a side they leave free - frames check tries the see-through "
             "and taking-apart cheats itself;" if blocking else ' -')
    counts = {}
    for r in rejected:
        counts[r['code']] = counts.get(r['code'], 0) + 1
    raise _failed(f"{code}: at t={fr['at']} no placed view keeping the picture's intent ({extra['intent']['size']['class']}, "
                  f"{extra['intent']['elevation']['class']}, {extra['intent']['lens']['class']}) from {list(picture_view)} works "
                  f"({extra['probe']['placed']} placed: {counts}): " + '; '.join(f"{r['view']} {r['lens']} mm: {r['why']}" for r in
                                                                  rejected[:4])
                  + clear + ' raise or move the subject in the scene, take a must-see part off this moment, or draw another picture',
                  {'code': code, 'at': fr['at'], 'view': list(picture_view), 'blocking': blocking, 'parts': parts,
                   'rejected': rejected, 'lenses_tried': sorted({r['lens'] for r in log}),
                   'lenses_at_picture': _lenses_at(log, own_view), **extra})


def _finish(shot, fr, frame, solved, chosen, roll, needed, rejected, extra, picture_view, axis, want, lying, turned, spans,
            must_see, log, own_view):
    """solve_frame's pose made a key for the path, a report row and its warnings."""
    tilt_warnings = []
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
    key = {'frame': frame, 'eye': eye, 'aim': aim, 'lens': solved['lens_mm'], 'roll': roll, 'axis': axis}
    if extra.get('departed'):   # the intent kept, not every part of it: said like a lean not reached
        tilt_warnings.append(f"FRAME_INTENT_DEPARTED: {shot['shot_id']} t={fr['at']}: " + '; '.join(extra['departed']))
    if solved.get('in_atmosphere'):   # softened by the project's policy: said, not refused
        tilt_warnings.append(f"CAMERA_IN_ATMOSPHERE (warning by policy): {shot['shot_id']} t={fr['at']}: {solved['in_atmosphere']}")
    if solved.get('in_subject'):
        tilt_warnings.append(f"CAMERA_IN_SUBJECT (warning by policy): {shot['shot_id']} t={fr['at']}: {solved['in_subject']}")
    if solved.get('must_see_short'):   # no pose showed them fully; this one shows them a little thin (frame_probe_core.px_verdict)
        tilt_warnings.append(f"MUST_SEE_SHORT (warning by policy): {shot['shot_id']} t={fr['at']}: must-see {solved['must_see_short']} px, "
                             'a little under their minimum - come a little closer, or open what is in front, if they must read fully')
    row = {'at': fr['at'], 'frame': frame, 'lens_mm': solved['lens_mm'], 'picture_view': list(picture_view),
                 'view': list(chosen['view']), 'offset_from_picture': chosen['offset'], 'rejected': rejected,
                 **({'moved_because': rejected[0]['why']} if rejected and any(chosen['offset'].values()) else {}),
                 'blocked_share': solved.get('blocked_share'), 'must_see': must_see, 'must_see_px': solved.get('must_see_px'),
                 'lens_rejected': solved.get('lens_rejected') or [], 'lenses_at_picture': _lenses_at(log, own_view),
                 'azimuth_from': 'tilt' if turned is not None else 'picture',
                 **{k: solved[k] for k in ('position', 'aim', 'achieved', 'steps', 'limited_by', 'target', 'mode', 'engaged', 'placement',
                                           'seen', 'probe_at_pose', 'picture_terms') if k in solved},
                 **({'detail_px': solved['detail_px']} if solved.get('detail_px') else {}), **({'picture': fr['picture']} if fr.get('picture') else {}),
                 'roll_deg': round(roll, 2), 'picture_tilt_deg': want,
                 # the target's own extents at this moment (_spans, longest first): what a picture's box can be of it
                 'spans_m': [round(e, 3) for e in spans],
                 **({'in_atmosphere': solved['in_atmosphere']} if solved.get('in_atmosphere') else {}),
                 **({'must_see_short': solved['must_see_short']} if solved.get('must_see_short') else {}),
                 # the declared cheats this moment was solved with (cheat_scene.py: a lens or view cheat changed the target)
                 **({'cheats': fr['cheats'], **({'cheated': fr['_cheated']} if fr.get('_cheated') else {})} if fr.get('cheats') else {}),
                 **extra,
                 'tilt_deg': None if axis is None or frame_core.screen_tilt_deg(eye, aim, axis, roll) is None
                 else round(frame_core.screen_tilt_deg(eye, aim, axis, roll), 2)}
    return key, row, tilt_warnings


def subject_pose(target, blender_frame):
    """The framed subject's root world matrix at Blender frame `blender_frame`, 16 numbers row by row: what a recorded
    pose's eye and aim are carried by (core.follow_subject), read under the same scene state as the solve. The root:
    the subject's object with no parent of the same subject (scene objects tagged studio_subject_id with the target's
    subject, else what the target resolves to); several roots, the largest - a subject moved, leaned or scaled whole
    moves them together. None when nothing renders."""
    scene = bpy.context.scene
    if scene.frame_current != blender_frame:
        scene.frame_set(blender_frame)
    subject = str(target).split('/')[0]
    objs = [o for o in scene.objects if o.get('studio_subject_id') == subject] or list((resolve_ref(target) or {}).get('objects') or [])
    names = {o.name for o in objs}
    roots = [o for o in objs if o.parent is None or o.parent.name not in names]
    if not roots:
        return None

    def size(o):
        corners = [o.matrix_world @ Vector(c) for c in o.bound_box]
        return max((a - b).length for a in corners for b in corners)
    root = max(sorted(roots, key=lambda o: o.name), key=size)
    return [round(float(root.matrix_world[i][j]), 6) for i in range(4) for j in range(4)]


def subject_centre(move, frame):
    """Where a frame_to_frame move's framed subject (its parts, else its target) has its centre at the shot frame
    `frame` (0-based): what a recorded pose's subject_at is (solve_frame), read again on the scene being shot."""
    p = move.get('params') or {}
    refs = list(p['parts']) if p.get('parts') else [p['target']]
    geo = resolve({'target': p['target'], **({'parts': refs} if p.get('parts') else {})}, frame=frame + 1)
    return [round(v, 4) for v in core.centre(core._ref(geo, refs if len(refs) > 1 else refs[0], 'box'))]


def picture_pose(move, fr, frame, aspect):
    """(eye, aim) where a frame row's own numbers put the camera at shot frame `frame` (core.framed_pose at its view and
    lens; a cut picture aimed at its must-see parts): the pose its picture was drawn from, whatever stands in the way -
    what frames proposals shows as the intended pose."""
    p = move.get('params') or {}
    refs = list(p['parts']) if p.get('parts') else [p['target']]
    geo = resolve({'target': p['target'], **({'parts': refs} if p.get('parts') else {})}, frame=frame + 1)
    box = core._ref(geo, refs if len(refs) > 1 else refs[0], 'box')
    boxes = [b for b in ((resolve_ref(i) or {}).get('box') for i in fr.get('must_see') or []) if b]
    shows = core.centre((tuple(min(b[0][k] for b in boxes) for k in range(3)), tuple(max(b[1][k] for b in boxes) for k in range(3)))) \
        if boxes and core.cut_picture(fr) else None
    view = (fr['view']['elevation_deg'], fr['view'].get('azimuth_deg', 0.0))
    return core.framed_pose(box, fr, view, float(fr.get('lens_mm') or move.get('lens_mm', 24.0)), aspect, shows)


def solve_frame(job, move, fr, ctx):
    """One frame target of a frame_to_frame move solved on the scene as it stands at its moment (ctx: frame_context):
    the view nearest its picture that keeps the camera out of what it must not enter, the focus unhidden and the
    must-see parts readable. -> (key for the path, report row, warnings); raises a ValueError with a report (_failed)
    when no view works. The build solves every target this way (_compile_frames); frames check (frames_check.py) solves
    one again after a cheat, in the same session."""
    shot = job['shot']
    count = shot['duration_frames']
    p, target, refs, framed = ctx['p'], ctx['target'], ctx['refs'], ctx['framed']
    cache, mine = ctx['cache'], ctx['mine']
    frame = round(fr['at'] * (count - 1))
    geo = resolve({'target': target, **({'parts': refs} if p.get('parts') else {})}, frame=frame + 1)
    box = core._ref(geo, framed, 'box')
    pose_then = subject_pose(target, frame + 1)   # what the pose is recorded against (with subject_at, for older readers)
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
    rejected, solved, chosen, extra = [], None, None, {}
    how = (fr.get('show') or {}).get('how', 'exterior')
    lens0 = fr.get('lens_mm') or move.get('lens_mm', 24.0)
    floor, log = _support(frame, mine, cache), []
    own_view = core.candidate_views(picture_view)[0]['view']   # the picture's own view as the views are tried (rounded, clamped)
    try:
        # where this moment's picture has its subject and the things round it (frame_targets.picture_layout)
        layout = (job.get('picture_layout') or {}).get((fr.get('picture') or {}).get('concept_id'))
        solved, chosen, roll, needed, rejected, extra = _solve_intent(job, fr, {**ctx, 'layout': layout, 'subject_pose': pose_then},
                                                                      frame, box, picture_view,
                                                                      must_see, axis, long_name, want, lying, lens0, floor, log,
                                                                      how, own_view)
    finally:
        bpy.context.scene.frame_set(1)   # the moment's probes held its frame (camera_frame.probe_set)
    if extra.get('probe_only'):   # ctx['probe_only']: what the probes found, no pose (frames check)
        return None, extra, []
    alternatives = extra.pop('alternatives', None)
    key, row, tilt_warnings = _finish(shot, fr, frame, solved, chosen, roll, needed, rejected, extra, picture_view, axis, want,
                                      lying, turned, spans, must_see, log, own_view)
    # where the framed subject's centre stood at the moment: what the pose's eye and aim are recorded against (a pick
    # carries it; core.follow_subject moves the pose with the subject on a scene authored again since)
    subject_at = [round(v, 4) for v in core.centre(box)]
    row['subject_at'] = subject_at
    if pose_then is not None:   # the root's whole transform: a lean, a turn or a scale carried too
        row['subject_pose'] = pose_then
    if alternatives:   # an intent shot's other judged poses for this moment, each a whole key and row (_compile_frames joins)
        row['alternatives'] = []
        for alt_solved, alt_chosen, alt_roll, alt_needed, alt_extra, score in alternatives:
            k, r, w = _finish(shot, fr, frame, alt_solved, alt_chosen, alt_roll, alt_needed, rejected, alt_extra, picture_view,
                              axis, want, lying, turned, spans, must_see, log, own_view)
            row['alternatives'].append({'key': k, 'row': {**r, 'subject_at': subject_at, **({'subject_pose': pose_then} if pose_then else {})},
                                        'warnings': w, 'score': score})
    return key, row, tilt_warnings


def _through_path(ctx, anchors, poses, count, path):
    """The rig's passes_through_geometry guard (camera_rig._crossing; the move's guards, the guard's own default
    no_pass_through) on a joined intent path before it is baked, against the scene at each frame: the eye's steps over
    PATH_STEP frames first (a cheap first look), then every frame's step for a path that passed it. Raises _failed with
    code CAMERA_PASS_THROUGH (frame, object, path) where a step goes through a solid thing - _compile_frames then joins
    the moments' poses another way (2026-10-15: a line between two placed poses went through the subject's own
    interstage and the rig refused the whole shot)."""
    import camera_rig
    if not {**camera_rig.GUARD_DEFAULTS, **(ctx.get('guards') or {})}.get('no_pass_through', True):
        return
    scene = bpy.context.scene
    try:
        for step in (PATH_STEP, 1):
            frames = sorted(set(range(0, count, step)) | {count - 1})
            for f0, f in zip(frames, frames[1:]):
                scene.frame_set(f + 1)
                through = camera_rig._crossing(scene, bpy.context.evaluated_depsgraph_get(),
                                               Vector(core._add(anchors[f0], poses[f0]['eye'])), Vector(core._add(anchors[f], poses[f]['eye'])))
                if through:
                    raise _failed(f'CAMERA_MOVE: between the pictures the camera passes through {through} at frame {f}; choose follow '
                                  'or path (params) so it goes around, or another picture',
                                  {'code': 'CAMERA_PASS_THROUGH', 'frame': f, 'object': through, 'path': path})
    finally:
        scene.frame_set(1)


def _join_frames(job, ctx, keys, rows, tilt_warnings, subjects, anchors, defaults, move, path_as=None):
    """_compile_frames' joining of its keys: the path through them (arc or straight, decided by turn and distance,
    core.intent_path, on centripetal splines, core.frame_path shape), the lean's roll limited, and the joined path
    checked: through nothing solid on every frame (_through_path), every PATH_STEP frames out of keep-out volumes and
    above the support - raises where it fails. ->
    (path, poses, checks, warnings, rolls, pacing, timing, fogged_path); keys and rows take the limited roll."""
    import camera_frame
    shot = job['shot']
    count = shot['duration_frames']
    p, target, aspect, refs = ctx['p'], ctx['target'], ctx['aspect'], ctx['refs']
    keep_ids, keep_m, cache, mine = ctx['keep_ids'], ctx['keep_m'], ctx['cache'], ctx['mine']
    centre_point = tuple(sum(subjects[k['frame']][i] for k in keys) / len(keys) for i in range(3))
    relative = [{**k, 'eye': core._sub(k['eye'], anchors[k['frame']]), 'aim': core._sub(k['aim'], anchors[k['frame']])} for k in keys]
    path = path_as or p.get('path', 'auto')   # path_as: the joining tries a swing round the subject
    if path == 'auto':   # poses from different sides swing around the subject; from one side, a line
        sides = [core.side_of(k['eye'], subjects[k['frame']]) for k in keys]
        # a turn with the distance kept is an orbit; a push in that also turns a little is a line
        path = core.intent_path(sides, [math.dist(k['eye'], subjects[k['frame']]) for k in keys])
    centre_rel = core._sub(centre_point, anchors[keys[0]['frame']]) if path == 'arc' else (0.0, 0.0, 0.0)
    timing = {**(defaults.get('timing') or {'profile': 'ease_in_out'}), **(move.get('timing') or {})}
    box0 = core._ref(resolve({'target': target}), target, 'box')
    progress = core.subject_progress(subjects, [k['frame'] for k in keys], count, max(core.size(box0))) \
        if p.get('pace', 'subject') == 'subject' else None
    pacing = 'subject' if progress else timing.get('profile')
    if progress is None and len(keys) == 2:   # two poses: the move's own timing carries the camera between them
        curve, (f0, f1) = rig_core.timing_curve(timing), (keys[0]['frame'], keys[1]['frame'])
        progress = [0.0 if f <= f0 else 1.0 if f >= f1 else curve((f - f0) / (f1 - f0)) for f in range(count)]
    poses = core.frame_path(relative, count, path, timing.get('profile') != 'linear', centre_rel, progress,
                            shape='centripetal')
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
    _through_path(ctx, anchors, poses, count, path)
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
    return path, poses, checks, warnings, rolls, pacing, timing, fogged_path


def _compile_frames(job, move, style, on_cues):
    """frame_to_frame: for each frame target, the view nearest its picture that keeps the camera out of what it must
    not enter, the focus unhidden and the must-see parts readable (core.candidate_views; camera_frame.solve_focus per
    view on the scene at that moment: solve_frame); the poses joined around or toward the subject (core.frame_path), hung
    from an anchor that shares `follow` of the subject's travel, paced by the subject's own travel when it moves; the
    joined path checked. Baked as a follow rig keyed on every frame. Each moment keeps a few judged poses; the shot is
    joined from the cheapest combination whose path passes (core.joint_rank)."""
    shot = job['shot']
    count = shot['duration_frames']
    defaults = (style or {}).get('defaults', {})
    ctx = frame_context(job, move)
    p, target, guards = ctx['p'], ctx['target'], ctx['guards']
    if on_cues:   # frame-bound openings (show_scene) exist before the poses are solved: the camera sees the open subject
        on_cues({})
    if len(move['frames']) > 1:
        ctx['keep'] = INTENT_KEEP   # each moment keeps a few judged poses; the shot is joined from them
    keys, rows, tilt_warnings, warned_each = [], [], [], []
    frames_sorted = sorted(move['frames'], key=lambda f: f['at'])
    for fr in frames_sorted:
        key, row, warned = solve_frame(job, move, fr, ctx)
        keys.append(key); rows.append(row); tilt_warnings += warned; warned_each.append(warned)
    subjects = _sample_points(target, count)
    follow = float(p.get('follow', core.PARAMS['frame_to_frame']['follow']))
    anchors = core.follow_anchors(subjects, follow)
    combos, options = [None], None
    if any(r.get('alternatives') for r in rows):
        # the shot's poses chosen together (core.joint_rank): each moment's own score, and between moments how fast the
        # camera turns round the subject, how much nearer or farther it goes, how many lens classes it changes
        options = [r.pop('alternatives', None) or [{'key': k, 'row': r, 'warnings': w, 'score': 0.0}]
                   for k, r, w in zip(keys, rows, warned_each)]
        fps_s = job.get('fps') or 30
        seconds = [(b[0]['row']['at'] - a[0]['row']['at']) * (count - 1) / fps_s for a, b in zip(options, options[1:])]
        combos = core.joint_rank([[{'score': o['score'], 'azimuth_deg': o['row']['view'][1], 'lens_mm': o['key']['lens'],
                                    'distance_m': math.dist(o['key']['eye'], o['key']['aim'])} for o in moment] for moment in options],
                                 seconds)
    tried = []
    for attempt, combo in enumerate(combos):
        if combo is not None:
            import copy
            picks = [moment[i] for moment, i in zip(options, combo[1])]
            keys, rows = [copy.deepcopy(o['key']) for o in picks], [copy.deepcopy(o['row']) for o in picks]
            tilt_warnings = [w for o in picks for w in o['warnings']]
            for row, i, fr_, moment in zip(rows, combo[1], frames_sorted, options):
                row['joined'] = {'cost': combo[0], 'rank': attempt, 'pose': i}
                # an agent's pin given up for a pose that joins: said - only when the moment's first pose is that pin
                # (a pin that failed on its own is said already, by its pick departure)
                pin_first = not any(d.get('kind') == 'pick' for d in moment[0]['row'].get('departures') or [])
                if i and (fr_.get('chosen') or {}).get('by') == 'agent' and pin_first:
                    text = f"the agent's pinned pose could not be joined into the shot; kept pose {i} is used instead"
                    row['departed'] = [*(row.get('departed') or []), text]
                    row['departures'] = [*(row.get('departures') or []), {'kind': 'pick', 'key': None, 'value': 1, 'text': text}]
                    tilt_warnings.append(f"FRAME_INTENT_DEPARTED: {shot['shot_id']} t={row['at']}: {text}")
        try:
            try:
                path, poses, checks, warnings, rolls, pacing, timing, fogged_path = _join_frames(job, ctx, keys, rows, tilt_warnings,
                                                                                                 subjects, anchors, defaults, move)
            except ValueError as error:
                # a line between poses on two sides of the subject goes through it: the same poses swing round it
                crossing = getattr(error, 'report', None) or {}
                if p.get('path', 'auto') != 'auto' or crossing.get('code') != 'CAMERA_PASS_THROUGH' \
                        or crossing.get('path') == 'arc':
                    raise
                tried.append(str(error)[:200])
                path, poses, checks, warnings, rolls, pacing, timing, fogged_path = _join_frames(job, ctx, keys, rows, tilt_warnings,
                                                                                                 subjects, anchors, defaults, move,
                                                                                                 path_as='arc')
            if combo is not None and tried:   # what the joining tried before this one held
                for row in rows:
                    row['joined']['tried'] = list(tried)
            break
        except ValueError as error:
            if combo is None and (getattr(error, 'report', None) or {}).get('code') != 'CAMERA_PASS_THROUGH':
                raise
            tried.append(str(error)[:200])
            if attempt == len(combos) - 1:
                import re
                frame_at = (getattr(error, 'report', None) or {}).get('frame')
                if frame_at is None:   # keep-out and support refusals say their frame in words
                    found = re.search(r'at frame (\d+)', str(error))
                    frame_at = int(found.group(1)) if found else None
                before = [r['at'] for r in rows if frame_at is not None and r['frame'] <= frame_at]
                where = f' after t={before[-1]}' if before else ''
                raise ValueError(f"{error} - none of the {len(combos)} cheapest joinings of the moments' poses passes (core.joint_rank): "
                                 f'split the shot{where} (a cut between the pictures), or change follow or path (params)') from None
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
