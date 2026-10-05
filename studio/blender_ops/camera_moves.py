"""Blender layer of shot.camera.move: resolve what the move is about, build its path, compile a rig.

Runs at build time before camera_rig.bake_camera_rig, which then bakes, guards and reports the compiled rig
exactly as if it had been written by hand. The shot snapshot keeps the move; the compiled rig is recorded in
camera_move_report.json. Not imported by render workers (render fingerprints unaffected).
"""
from __future__ import annotations

import bpy
from mathutils import Vector
import numpy as np
from mathutils.bvhtree import BVHTree

import camera_moves_core as core
from scene_roles import counts
import scene_geometry
import camera_rig_core as rig_core
from scene_tools import anchor_for, object_by_id

PATH_PREFIX = 'studio_camera_path.'
AIM_PREFIX = 'studio_camera_aim.'
WHIP_S = 0.25


def _meshes(obj):
    return [o for o in [obj, *obj.children_recursive] if o.type == 'MESH']


def _world_box(obj):
    """Axis-aligned world box of an object and its mesh children (an empty: its display cube)."""
    corners = []
    for o in _meshes(obj):
        corners += [o.matrix_world @ Vector(c) for c in o.bound_box]
    if not corners and obj.type == 'EMPTY':
        r = obj.empty_display_size
        corners = [obj.matrix_world @ Vector((x * r, y * r, z * r)) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    if not corners:
        return None
    return (tuple(min(c[i] for c in corners) for i in range(3)), tuple(max(c[i] for c in corners) for i in range(3)))


def _refs(params):
    """Every string in a move's params is a scene reference (top level or inside a list), except the 'ahead'
    keyword: a new move or parameter needs no registration here."""
    out = []
    for value in params.values():
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str) and item != 'ahead':
                out.append(item)
    return out


def resolve(params):
    """Scene references of a move -> {points, boxes} at frame 1 (what the camera is about when the shot opens)."""
    bpy.context.scene.frame_set(1)
    geo = {'points': {}, 'boxes': {}}
    for ref in _refs(params):
        obj, point = anchor_for(ref)
        if obj is None:
            raise ValueError(f'CAMERA_MOVE: reference not found in scene: {ref}')
        geo['points'][ref] = tuple(point)
        named = object_by_id(ref)
        box = _world_box(named) if named is not None else None
        if box:
            geo['boxes'][ref] = box
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
        obj, point = anchor_for(ref)
        if obj is None:
            raise ValueError(f'CAMERA_MOVE: look target not found: {ref}')
        out.append(tuple(point))
    scene.frame_set(1)
    return out


def compile_move(job, style=None, on_cues=None):
    """shot.camera.move -> (rig dict for bake_camera_rig, report). `style` is the motion style json (or None).
    on_cues(cues) is called once the camera's pass frames are known ({'cam-<mark>': frame}) and before any
    clearance repair, so cue-bound scene actions (a road opening) exist when geometry is measured."""
    shot = job['shot']
    move = shot['camera']['move']
    count, fps = shot['duration_frames'], job['fps']
    defaults = (style or {}).get('defaults', {})
    params = move.get('params', {})
    geo = resolve(params)
    plan = core.plan(move, geo)
    timing = {**(defaults.get('timing') or {'profile': 'ease_in_out'}), **(move.get('timing') or {})}
    blur = defaults.get('motion_blur', {})
    shutter = move['motion_blur_shutter'] if 'motion_blur_shutter' in move else blur.get('shutter')
    report = {'schema_version': 1, 'move': move, 'style': (style or {}).get('name'), 'resolved': {
        'points': {k: core._r3(v) for k, v in geo['points'].items()},
        'boxes': {k: [core._r3(v[0]), core._r3(v[1])] for k, v in geo['boxes'].items()}}, 'notes': plan['notes'], 'repairs': []}
    rig = {'guards': dict(move.get('guards', {}))}
    progress = rig_core.timing_curve(timing)
    look = move.get('look_target') or plan['aim_ref']
    whip_or_aim = bool(move.get('whip_in_deg') or look or plan['aim'] is not None)
    if plan['kind'] == 'orbit':
        if move.get('framing'):
            raise ValueError('CAMERA_MOVE: framing applies to flythrough moves (an orbit keeps its target centred)')
        if not isinstance(params.get('target'), str):
            raise ValueError('CAMERA_MOVE: orbit_reveal needs a scene reference as target')
        rig.update({'type': 'orbit', 'subject': params['target'], 'orbit': plan['orbit'], 'sweep_deg': plan['sweep_deg'], 'timing': timing})
        cues = {}
        if on_cues:
            on_cues(cues)
    else:
        waypoints = plan['waypoints']
        first = core.catmull_rom(waypoints)
        first_len = core.path_length(first)
        travel = first_len if whip_or_aim else max(0.0, first_len - min(10.0, first_len / 4))
        mark_u = core.mark_progress(first, waypoints, plan['marks'], travel)
        cues = {f'cam-{k}': core.pass_frame(progress, u, count) for k, u in mark_u.items()}
        report['mark_progress'] = {f'cam-{k}': round(u, 5) for k, u in mark_u.items()}
        late = [{'cue': a['cue'], 'frame': cues.get(a['cue']), 'not_before_frame': round(a['not_before_s'] * fps)}
                for a in move.get('arrive', []) if a['cue'] not in cues or cues[a['cue']] < round(a['not_before_s'] * fps)]
        if late:  # advisory at build: `camera fit` turns arrive into a hard penalty and re-times the head
            report['arrive_violations'] = late
        if on_cues:
            on_cues(cues)
        clearance = move.get('clearance_m', 0.0)
        opening = object_by_id(params['opening']) if isinstance(params.get('opening'), str) else None
        skip = {o.name for o in [opening, *opening.children_recursive]} if opening else set()  # a hole marker is not a wall
        cache = {}
        pass_frame = {i: cues.get(f'cam-wp{i}', 0) for i in range(len(waypoints))}
        if clearance > 0:  # each waypoint is measured against the scene as it stands when the camera gets there
            waypoints, report['repairs'] = repair(waypoints, clearance, lambda i: _trees_at(pass_frame[i], skip, cache))
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
            frames = [next((f for f in range(count) if progress(f / max(1, count - 1)) * length >= s - 1e-9), count - 1) for s in lengths]
            hits = [_nearest(_trees_at(frames[i] // 10 * 10, set(), cache), Vector(p)) for i, p in enumerate(dense)]
            report['path_min_clearance_m'] = round(min(h[2] for h in hits if h), 3) if any(hits) else None
        bpy.context.scene.frame_set(1)
        whip = move.get('whip_in_deg')
        if whip:
            targets = _sample_points(look, count) if look else [plan['aim'] or dense[-1]] * count
            aim_name = AIM_PREFIX + shot['shot_id']
            _aim_empty(aim_name, core.whip_aim(dense[0], targets, whip, max(1, round(WHIP_S * fps))))
            rig['look_target'] = aim_name
            report['look_target_guard'] = 'off: whip aims at a moving empty, not the target object'
        elif look:
            rig['look_target'] = look
        elif plan['aim'] is not None:
            aim_name = AIM_PREFIX + shot['shot_id']
            _aim_empty(aim_name, [plan['aim']])
            rig['look_target'] = aim_name
        else:
            rig['look_ahead_m'] = min(10.0, length / 4)
            rig['timing']['distance_m'] = max(0.0, length - rig['look_ahead_m'])
        if plan['pitch_limit_deg']:
            rig['pitch_limit_deg'] = plan['pitch_limit_deg']
        if move.get('framing'):
            rig['framing'] = core.compile_framing(move['framing'], cues, count)
            report['framing'] = rig['framing']
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
