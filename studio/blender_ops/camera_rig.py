"""Blender layer of shot.camera.rig: sample subjects, bake per-frame keys, verify guards.

Runs only at build time (build_scene.py). Render workers replay the baked keys, so this
module is never imported by the frozen renderer and does not affect render fingerprints.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import runpy

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from bpy_extras.object_utils import world_to_camera_view

import camera_rig_core as core
from scene_tools import anchor_for, curves, object_by_id

GUARD_DEFAULTS = {'subject_margin': 0.03, 'look_target_visible': True, 'max_hidden_s': 0.5, 'near_field_m': 10.0}


def _r(value, digits=6):
    if isinstance(value, (list, tuple, Vector)):
        return [_r(v, digits) for v in value]
    return None if value is None else round(float(value), digits)


def _meshes(obj):
    return [o for o in [obj, *obj.children_recursive] if o.type == 'MESH']


def _sample(identifier, count):
    """World position of an anchor on every frame (frame f -> Blender frame f+1)."""
    scene = bpy.context.scene
    points, owner = [], None
    for f in range(count):
        scene.frame_set(f + 1)
        obj, point = anchor_for(identifier)
        if obj is None:
            raise ValueError(f'CAMERA_RIG: anchor not found: {identifier}')
        owner = obj
        points.append(tuple(point))
    return owner, points


def _path_points(identifier):
    obj = object_by_id(identifier)
    if obj is None:
        raise ValueError(f'CAMERA_RIG: path object not found: {identifier}')
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        points = [tuple(obj.matrix_world @ v.co) for v in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()
    if len(points) < 2:
        raise ValueError(f'CAMERA_RIG: path has fewer than two points: {identifier}')
    return points


def _procedural(job, rig, count, fps):
    script = (Path(job['project_dir']) / rig['script']).resolve()
    if not script.is_relative_to(Path(job['project_dir']).resolve()) or not script.is_file():
        raise ValueError(f"CAMERA_RIG: procedural script missing: {rig['script']}")
    state = runpy.run_path(str(script), run_name='camera_rig_script')['camera_state']
    rows = []
    for f in range(count):
        result = state(f / fps, {'frame': f, 'fps': fps, 'frame_count': count}) or {}
        unknown = set(result) - {'offset_m', 'blend', 'lift_m', 'lens_mm', 'roll_deg'}
        if unknown:
            raise ValueError(f'CAMERA_RIG: camera_state returned unknown keys {sorted(unknown)}')
        rows.append(result)
    return rows, hashlib.sha256(script.read_bytes()).hexdigest()


def _camera(scene, warnings):
    camera = scene.camera
    if not camera:
        data = bpy.data.cameras.new('StudioCamera')
        camera = bpy.data.objects.new('StudioCamera', data)
        scene.collection.objects.link(camera)
        scene.camera = camera
    if camera.parent or camera.constraints:
        raise ValueError('CAMERA_RIG: rig camera must have no parent or constraints (they would override baked keys)')
    if (camera.animation_data and camera.animation_data.action) or (camera.data.animation_data and camera.data.animation_data.action):
        warnings.append('author_camera_overridden')
    camera.animation_data_clear(); camera.data.animation_data_clear()
    camera.data.type = 'PERSP'
    return camera


def _key(camera, frames):
    camera.rotation_mode = 'QUATERNION'
    for f, row in enumerate(frames):
        camera.location = row['location']
        camera.rotation_quaternion = row['rotation']
        camera.data.lens = row['lens']
        camera.keyframe_insert('location', frame=f + 1)
        camera.keyframe_insert('rotation_quaternion', frame=f + 1)
        camera.data.keyframe_insert('lens', frame=f + 1)
    # Per-frame keys: LINEAR so Bezier handles never overshoot between frames (Blender 5 layered actions too).
    for owner in (camera, camera.data):
        for curve in curves(owner.animation_data.action if owner.animation_data else None):
            for point in curve.keyframe_points:
                point.interpolation = 'LINEAR'


def _box(scene, camera, objects):
    xs, ys = [], []
    for obj in objects:
        for corner in obj.bound_box:
            ndc = world_to_camera_view(scene, camera, obj.matrix_world @ Vector(corner))
            if ndc.z > 0:
                xs.append(ndc.x); ys.append(1 - ndc.y)
    return [min(xs), min(ys), max(xs), max(ys)] if xs else None


def _visible(scene, depsgraph, camera, objects):
    """True when any of the target's bbox centre/corners (pulled 10 % inward) is unoccluded."""
    origin = camera.matrix_world.translation
    names = {o.name for o in objects}
    for obj in objects:
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        center = sum(corners, Vector()) / 8
        for point in [center] + [center.lerp(c, .9) for c in corners[::2]]:
            ndc = world_to_camera_view(scene, camera, point)
            if not (ndc.z > 0 and 0 <= ndc.x <= 1 and 0 <= ndc.y <= 1):
                continue
            ray = point - origin
            hit, _, _, _, hit_obj, _ = scene.ray_cast(depsgraph, origin, ray.normalized(), distance=ray.length + 1e-3)
            if not hit or hit_obj.name in names or (hit_obj.original and hit_obj.original.name in names):
                return True
    return False


def _trees(identifiers):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    trees = []
    for identifier in identifiers:
        root = object_by_id(identifier)
        if root is None:
            raise ValueError(f'CAMERA_RIG: clearance object not found: {identifier}')
        for obj in _meshes(root):
            evaluated = obj.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh()
            try:
                vertices = [obj.matrix_world @ v.co for v in mesh.vertices]
                polygons = [tuple(p.vertices) for p in mesh.polygons]
            finally:
                evaluated.to_mesh_clear()
            if polygons:
                trees.append(BVHTree.FromPolygons(vertices, polygons))
    return trees


def bake_camera_rig(job):
    shot = job['shot']
    rig = shot['camera']['rig']
    scene = bpy.context.scene
    fps, count = job['fps'], shot['duration_frames']
    guards = {**GUARD_DEFAULTS, **rig.get('guards', {})}
    warnings = []
    subject_obj = target_obj = None
    subject = target = path = None
    if rig['type'] == 'flythrough':
        path = _path_points(rig['path'])
    else:
        subject_obj, subject = _sample(rig['subject'], count)
        if rig.get('look_target'):
            target_obj, target = _sample(rig['look_target'], count)
    overrides, script_sha = (_procedural(job, rig, count, fps) if rig['type'] == 'procedural' else (None, None))
    camera = _camera(scene, warnings)
    view = (camera.data.sensor_width, camera.data.sensor_fit, scene.render.resolution_x, scene.render.resolution_y)
    result = core.bake(rig, fps, count, subject=subject, target=target, path=path, view=view,
                       default_lens=camera.data.lens, overrides=overrides)
    _key(camera, result['frames'])
    if rig.get('shake'):
        scene['studio_camera_rig_shake'] = True  # look_camera must not add a second shake
    if rig.get('motion_blur_shutter'):
        scene.render.use_motion_blur = True
        scene.render.motion_blur_shutter = rig['motion_blur_shutter']
    # Verification pass on the real keyed camera, independent of the math that produced it.
    trees = _trees(guards.get('clearance_ids', []))
    subject_meshes = _meshes(subject_obj) if subject_obj else []
    target_meshes = _meshes(target_obj) if target_obj else []
    heading = result['heading']
    samples, failures = [], []
    hidden_run = max_hidden_run = 0
    previous = {}
    for f in range(count):
        scene.frame_set(f + 1)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        eye = camera.matrix_world.translation.copy()
        forward = camera.matrix_world.to_quaternion() @ Vector((0, 0, -1))
        pitch = math.degrees(math.asin(max(-1, min(1, forward.z))))
        row = {'frame': f, 'camera': _r(eye), 'lens_mm': _r(camera.data.lens, 4), 'pitch_deg': _r(pitch, 3),
               'roll_deg': _r(result['frames'][f]['roll_deg'], 3), 'subject_bank_deg': _r(result['bank_deg'][f], 3)}
        if subject_meshes:
            box = _box(scene, camera, subject_meshes)
            row['subject_box'] = _r(box, 5)
            m = guards['subject_margin']
            if box is None or min(box[0], box[1]) < m or max(box[2], box[3]) > 1 - m:
                failures.append({'guard': 'subject_margin', 'frame': f, 'box': row['subject_box']})
        if target_meshes:
            box = _box(scene, camera, target_meshes)
            on_screen = box is not None and box[0] < 1 and box[2] > 0 and box[1] < 1 and box[3] > 0
            visible = on_screen and _visible(scene, depsgraph, camera, target_meshes)
            row.update({'target_box': _r(box, 5), 'target_visible': visible})
            hidden_run = 0 if visible else hidden_run + 1
            max_hidden_run = max(max_hidden_run, hidden_run)
        if trees:
            nearest = [hit[3] for hit in (t.find_nearest(eye) for t in trees) if hit[0] is not None]
            if nearest:
                row['clearance_m'] = _r(min(nearest), 3)
        for name, obj in (('subject', subject_obj), ('target', target_obj)):
            if obj is not None:
                position = obj.matrix_world.translation.copy()
                if name in previous:
                    row[f'{name}_path_speed_mps'] = _r((position - previous[name]).dot(Vector(heading[f])) * fps, 3)
                previous[name] = position
        samples.append(row)
    limit = rig.get('pitch_limit_deg', core.DEFAULT_PITCH_LIMIT)
    for row in samples:
        if abs(row['pitch_deg']) > limit + 2:
            failures.append({'guard': 'pitch_limit', 'frame': row['frame'], 'pitch_deg': row['pitch_deg']})
    if target_meshes and guards['look_target_visible'] and max_hidden_run > guards['max_hidden_s'] * fps:
        failures.append({'guard': 'look_target_hidden', 'max_hidden_frames': max_hidden_run})
    clearances = [row['clearance_m'] for row in samples if 'clearance_m' in row]
    if 'min_clearance_m' in guards and clearances and min(clearances) < guards['min_clearance_m']:
        failures.append({'guard': 'min_clearance', 'min_clearance_m': min(clearances),
                         'frames': [row['frame'] for row in samples if row.get('clearance_m', 1e9) < guards['min_clearance_m']][:20]})
    if 'min_subject_path_speed_mps' in guards:
        for key in ('subject_path_speed_mps', 'target_path_speed_mps'):
            slow = [row['frame'] for row in samples if key in row and row[key] < guards['min_subject_path_speed_mps']]
            if slow:
                failures.append({'guard': 'min_path_speed', 'series': key, 'frames': slow[:20]})
    in_margin = [row for row in samples if 'subject_box' in row]
    summary = {
        'frames': count,
        'subject_in_margin_ratio': _r(sum(1 for row in in_margin if not any(x['guard'] == 'subject_margin' and x.get('frame') == row['frame'] for x in failures)) / len(in_margin), 4) if in_margin else None,
        'target_visible_ratio': _r(sum(1 for row in samples if row.get('target_visible')) / count, 4) if target_meshes else None,
        'max_target_hidden_frames': max_hidden_run if target_meshes else None,
        'pitch_range_deg': [min(r['pitch_deg'] for r in samples), max(r['pitch_deg'] for r in samples)],
        'lens_range_mm': [min(r['lens_mm'] for r in samples), max(r['lens_mm'] for r in samples)],
        'max_abs_roll_deg': max(abs(r['roll_deg']) for r in samples),
        'min_clearance_m': min(clearances) if clearances else None,
        'near_field_ratio': _r(sum(1 for c in clearances if c <= guards['near_field_m']) / len(clearances), 4) if clearances else None,
        'subject_speed_mps': [_r(min(result['speed_mps']), 3), _r(max(result['speed_mps']), 3)],
    }
    report = {'schema_version': 1, 'rig_type': rig['type'],
              'rig_hash': hashlib.sha256(json.dumps(rig, sort_keys=True).encode()).hexdigest(),
              'core_sha256': hashlib.sha256(Path(core.__file__).read_bytes()).hexdigest(),
              'script_sha256': script_sha, 'guards': guards, 'warnings': warnings, 'summary': summary,
              'gate_failures': failures, 'samples': samples}
    return report
