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


def resolve(params, frame=1):
    """Scene references of a move -> {points, boxes} at the requested Blender frame."""
    bpy.context.scene.frame_set(frame)
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


FLOOR_LENSES = (24.0, 18.0, 14.0)   # a pose under a declared surface keeps its direction and goes wider and closer
BLOCKED_MAX = 0.3       # more of the focus than this behind something else (a plume, a deck): the view is rejected / warned
PATH_STEP = 10          # frames between checks along the joined path


def _keep_out(ids, frame, position, min_m):
    """How a camera at `position` meets the surfaces and volumes the move keeps it out of (move.guards.clearance_ids:
    the sea, a deck, a plume stand-in): 'inside / below' one (the nearest face looks away from it), or nearer than
    min_m; None when clear."""
    objs = [m for ident in ids for m in (_meshes(object_by_id(ident)) if object_by_id(ident) is not None else [])]
    if not objs:
        return None
    bpy.context.scene.frame_set(frame)
    tree = _merged_tree(objs, bpy.context.evaluated_depsgraph_get())
    if tree is None:
        return None
    location, normal, _index, distance = tree.find_nearest(Vector(position))
    if location is None:
        return None
    if normal is not None and (Vector(position) - location).dot(normal) < 0:
        return f'inside or below {ids} by {distance:.2f} m'
    if distance < min_m:
        return f'{distance:.2f} m from {ids} (clearance {min_m} m)'
    return None


def _pose(job, fr, view, refs, own, box, aspect, frame, first_lens, keep_ids, keep_m, must_see, min_px):
    """One candidate view of a frame target: solved at the widening lens ladder, then judged - out of the keep-out
    volumes, the focus not hidden behind something else, every must-see part as thick as it must be. (solved, None)
    or (None, why)."""
    import camera_frame
    why = None
    for lens in [first_lens] + [mm for mm in FLOOR_LENSES if mm < first_lens]:
        start, _ = core.frame_seed(box, {**fr, 'view': {'elevation_deg': view[0], 'azimuth_deg': view[1]}}, lens, aspect)
        try:
            solved = camera_frame.solve_focus(job, {'height_share': fr['height_share'], 'centre': fr['centre'],
                                                    **({'edges': fr['edges']} if fr.get('edges') else {})}, refs, start, view,
                                              frame + 1, lens, must_see)
        except ValueError as error:
            why = str(error)[:160]
            continue
        clash = _keep_out(keep_ids, frame + 1, solved['position'], keep_m) if keep_ids else None
        if clash:
            why = clash
            continue
        blocked = camera_frame.blocked_share(refs, own, solved['position'], solved['aim'], lens, aspect, frame + 1)
        if blocked is not None and blocked > BLOCKED_MAX:
            return None, f'{round(blocked * 100)} % of the focus behind something else at {lens} mm'
        shown = {i: camera_frame.shown_px(i, solved['position'], view, lens, job.get('output_size') or (1080, 1920), frame + 1) for i in must_see}
        solved['must_see_px'] = shown
        thin = {i: px for i, px in shown.items() if px < min_px.get(i, 0)}
        if thin:
            return None, f'must-see {sorted(thin)} at {thin} px (need {[min_px.get(i, 0) for i in sorted(thin)]})'
        return {**solved, 'lens_mm': lens, 'blocked_share': blocked}, None
    return None, why


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
    min_px = {d['id']: d.get('min_px', 0) for d in (shot.get('screen') or {}).get('details', [])}
    keys, rows = [], []
    for fr in sorted(move['frames'], key=lambda f: f['at']):
        frame = round(fr['at'] * (count - 1))
        geo = resolve({'target': target, **({'parts': refs} if p.get('parts') else {})}, frame=frame + 1)
        box = core._ref(geo, framed, 'box')
        picture_view = (fr['view']['elevation_deg'], fr['view'].get('azimuth_deg', 0.0))
        must_see = list(fr.get('must_see') or [])
        rejected, solved, chosen = [], None, None
        for candidate in core.candidate_views(picture_view):
            solved, why = _pose(job, fr, candidate['view'], refs, [target], box, aspect, frame, fr.get('lens_mm') or move.get('lens_mm', 24.0),
                                keep_ids, keep_m, must_see, min_px)
            if solved:
                chosen = candidate
                break
            rejected.append({'view': list(candidate['view']), 'why': why})
        if not solved:
            raise ValueError(f"FRAME_UNSOLVED: at t={fr['at']} no view within {core.VIEW_STEPS['azimuth_deg'][-2]} deg of the picture's "
                             f"{picture_view} works: " + '; '.join(f"{r['view']}: {r['why']}" for r in rejected[:6])
                             + ' - raise or move the subject in the scene, take a must-see part off this moment, or draw another picture')
        keys.append({'frame': frame, 'eye': tuple(solved['position']), 'aim': tuple(solved['aim']), 'lens': solved['lens_mm']})
        rows.append({'at': fr['at'], 'frame': frame, 'lens_mm': solved['lens_mm'], 'picture_view': list(picture_view),
                     'view': list(chosen['view']), 'offset_from_picture': chosen['offset'], 'rejected': rejected,
                     'blocked_share': solved.get('blocked_share'), 'must_see': must_see, 'must_see_px': solved.get('must_see_px'),
                     **{k: solved[k] for k in ('position', 'aim', 'achieved', 'steps', 'limited_by', 'target') if k in solved},
                     **({'detail_px': solved['detail_px']} if solved.get('detail_px') else {}), **({'picture': fr['picture']} if fr.get('picture') else {})})
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
    # the joined path: never inside what the camera keeps out of; how much of the focus hides behind something else
    checks, blocked_runs = [], []
    for f in list(range(0, count, PATH_STEP)) + [count - 1]:
        eye, aim = core._add(anchors[f], poses[f]['eye']), core._add(anchors[f], poses[f]['aim'])
        clash = _keep_out(keep_ids, f + 1, eye, keep_m) if keep_ids else None
        if clash:
            raise ValueError(f'CAMERA_MOVE: between the pictures the camera passes {clash} at frame {f}; choose follow or path '
                             '(params) so it goes around, or another picture')
        blocked = camera_frame.blocked_share(refs, [target], eye, aim, poses[f]['lens'], aspect, f + 1, count=400)
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
    if guards:
        rig['guards'] = guards
    blur = defaults.get('motion_blur', {})
    shutter = move['motion_blur_shutter'] if 'motion_blur_shutter' in move else blur.get('shutter')
    if shutter:
        rig['motion_blur_shutter'] = shutter
    if on_cues:
        on_cues({})
    report = {'schema_version': 1, 'move': move, 'style': (style or {}).get('name'), 'frames': rows, 'follow': follow, 'path': path,
              'pacing': pacing, 'timing': timing, 'path_checks': checks, 'warnings': warnings, 'notes': {}, 'repairs': [],
              'camera_cues': {}, 'rig': rig, 'focus': rows[-1]}   # the last pose stands for the move where one focus is read
    return rig, report



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
        opening = object_by_id(params['opening']) if isinstance(params.get('opening'), str) else None
        skip = {o.name for o in [opening, *opening.children_recursive]} if opening else set()  # a hole marker is not a wall
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
            frames = [next((f for f in range(count) if progress(f / max(1, count - 1)) * length >= s - 1e-9), count - 1) for s in lengths]
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
