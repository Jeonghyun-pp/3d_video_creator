"""Build-time scene actions tied to the camera: the `reveal` action and camera-cue binding.

A reveal opens geometry progressively: a boolean cutter (hidden, its faces become the cap) is keyed in
location / rotation / scale at fractions `t` of the action interval, so a hole can grow from nothing while
the camera approaches. Any action may bind its interval to camera cues ('cam-<mark>': the frame the camera
passes a named point of its move, computed by camera_moves.compile_move) exactly like speech cues, so a road
finishes opening before the camera dives through it, however the move is later re-timed.

Not imported by render workers; scene_tools stays untouched (it is part of the render fingerprint).
"""
from __future__ import annotations

import bmesh
import bpy
from mathutils import Vector

from mesh_data import unique_data
from scene_roles import TAG as ROLE_TAG, counts
from scene_tools import apply_actions, interpolation, object_by_id, targets

PREFIX = 'StudioReveal_'
CAM = 'cam-'


def camera_bound(action):
    binding = action.get('time_binding') or {}
    return any(str(binding.get(k, '')).startswith(CAM) for k in ('start_cue_id', 'end_cue_id'))


def resolve(shot, cues):
    """Set start/end of camera-bound actions from camera cues (in place). Returns [{action_id, start, end}]."""
    rows = []
    for action in shot['actions']:
        if not camera_bound(action):
            continue
        b = action['time_binding']
        try:
            start = cues[b['start_cue_id']] + b['start_offset_frames']
            end = cues[b['end_cue_id']] + b['end_offset_frames']
        except KeyError as error:
            raise ValueError(f'REVEAL: camera cue {error} does not exist (known: {sorted(cues)})') from error
        if not 0 <= start < end <= shot['duration_frames']:
            raise ValueError(f"REVEAL: {action['action_id']} resolves to [{start}, {end}) outside the shot or empty; "
                             'move the binding offsets or the camera arrival (move.arrive)')
        action['start_frame'], action['end_frame'] = start, end
        rows.append({'action_id': action['action_id'], 'start_frame': start, 'end_frame': end})
    return rows


def _evaluated(obj):
    """bmesh of what the object renders (a geometry-node host has no polygons of its own)."""
    mesh = bmesh.new()
    if obj.data.polygons:
        mesh.from_mesh(obj.data)
    else:
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        data = evaluated.to_mesh()
        mesh.from_mesh(data)
        evaluated.to_mesh_clear()
    return mesh


def _manifold(obj):
    """Closed mesh test on what renders. An empty mesh is not closed (it used to pass with nothing checked)."""
    mesh = _evaluated(obj)
    try:
        return bool(mesh.edges) and all(edge.is_manifold for edge in mesh.edges)
    finally:
        mesh.free()


def signed_volume(obj):
    """World-space signed volume: negative means the faces wind inward (or a mirroring transform flips them)."""
    mesh = _evaluated(obj)
    try:
        volume = mesh.calc_volume(signed=True)
    finally:
        mesh.free()
    scale = obj.matrix_world.to_3x3().determinant()
    return volume * scale


def _moving(obj):
    """Keyed transform (a driving car); the reveal's own visibility keys do not count."""
    action = obj.animation_data.action if obj.animation_data else None
    return bool(action) and any(not c.data_path.startswith('modifiers[') for c in _curves(action))


def _world_box(obj):
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    return [min(c[i] for c in corners) for i in range(3)], [max(c[i] for c in corners) for i in range(3)]


def _overlaps(a, b):
    return all(a[0][i] <= b[1][i] and b[0][i] <= a[1][i] for i in range(3))


def _reset():
    for obj in bpy.context.scene.objects:
        for modifier in list(obj.modifiers):
            if modifier.name.startswith(PREFIX):
                obj.modifiers.remove(modifier)
        if obj.get('studio_reveal_cutter'):
            obj.animation_data_clear()
        elif obj.animation_data and obj.animation_data.action:  # the visibility keys of an earlier apply
            action = obj.animation_data.action
            curves = _curves(action)
            for curve in [c for c in curves if c.data_path.startswith(f'modifiers["{PREFIX}')]:
                curves.remove(curve)
            if not len(_curves(action)):
                obj.animation_data.action = None


def _curves(action):
    """F-curve collection of an action (layered actions keep them in a channelbag; legacy ones on the action)."""
    if hasattr(action, 'layers') and action.layers:
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    return bag.fcurves
    return action.fcurves


def _key_visibility(obj, modifier, on_frame):
    """The cut exists from on_frame only: off (constant) before it, so no hole opens ahead of the action."""
    for frame, state in ((1, False), (on_frame, True)):
        modifier.show_viewport = modifier.show_render = state
        for attr in ('show_viewport', 'show_render'):
            obj.keyframe_insert(f'modifiers["{modifier.name}"].{attr}', frame=frame)
    for curve in _curves(obj.animation_data.action):
        if curve.data_path.startswith(f'modifiers["{modifier.name}"]'):
            for point in curve.keyframe_points:
                point.interpolation = 'CONSTANT'


def apply(shot):
    """Apply every `reveal` action (idempotent: earlier reveal modifiers and cutter keys are removed first)."""
    _reset()
    rows = []
    for action in shot['actions']:
        if action['type'] != 'reveal':
            continue
        p, start, end = action['params'], action['start_frame'], action['end_frame']
        cutter = object_by_id(p['cutter_object_id'])
        cap = bpy.data.materials.get(p['cap_material_id'])
        if cutter is None or cutter.type != 'MESH' or cap is None:
            raise ValueError(f"REVEAL: {action['action_id']} needs a mesh cutter and an existing cap material")
        cutter.hide_render = True
        cutter.display_type = 'WIRE'
        cutter['studio_reveal_cutter'] = True
        cutter[ROLE_TAG] = 'helper'
        unique_data(cutter).data.materials.clear(); cutter.data.materials.append(cap)
        first_frame = min(round(start + key['t'] * (end - start)) + 1 for key in p['cutter_keys'])
        for key in p['cutter_keys']:
            frame = round(start + key['t'] * (end - start)) + 1
            for channel, attr in (('location', 'location'), ('rotation_euler', 'rotation_euler'), ('scale', 'scale')):
                if channel in key:
                    setattr(cutter, attr, key[channel])
                    cutter.keyframe_insert(attr, frame=frame)
        interpolation(cutter, action['easing'])
        objects = targets(action)
        skipped = []
        if p.get('also_cut_overlapping'):  # everything static lying in the opened volume (markings, kerbs...) goes too
            bpy.context.scene.frame_set(end + 1)
            box = _world_box(cutter)
            for obj in bpy.context.scene.objects:
                if (obj in objects or obj is cutter or obj.type != 'MESH' or obj.hide_render or not counts(obj, 'reveal_overlap')
                        or not _overlaps(_world_box(obj), box)):
                    continue
                if _moving(obj):  # a car driving across would be sliced
                    skipped.append(obj.name); continue
                if not _manifold(obj):
                    skipped.append(obj.name); continue
                objects.append(obj)
        # MANIFOLD (Blender 4.5+) is many times faster per evaluated frame - the cutter is animated, so every
        # frame re-cuts - and transfers the cap material like EXACT (measured 2026-10-05: 0.18 vs 1.26 ms on a
        # cube pair, cap faces present). It needs closed meshes on both sides; targets are checked below.
        solver = 'MANIFOLD' if _manifold(cutter) else 'EXACT'
        volumes = {}
        for obj in [cutter, *objects]:  # invariant: every closed mesh in the cut winds outward
            if obj.type != 'MESH' or not _manifold(obj):
                raise ValueError(f'REVEAL: target {obj.name} must be a closed (manifold) mesh')
            volumes[obj.name] = round(signed_volume(obj), 3)
            if volumes[obj.name] <= 0:
                raise ValueError(f'REVEAL: {obj.name} is inside-out (signed volume {volumes[obj.name]}); its faces must wind outward '
                                 '(recalculate normals where the mesh is built) - a boolean on it leaves seams and stray faces')
        for obj in objects:
            if obj.data.polygons and cap.name not in [m.name for m in obj.data.materials if m]:
                unique_data(obj).data.materials.append(cap)   # a node-built host gets the cap through TRANSFER alone
            modifier = obj.modifiers.new(PREFIX + action['action_id'], 'BOOLEAN')
            modifier.operation, modifier.solver, modifier.object = 'DIFFERENCE', solver, cutter
            if hasattr(modifier, 'material_mode'):
                modifier.material_mode = 'TRANSFER'
            _key_visibility(obj, modifier, first_frame)
        rows.append({'action_id': action['action_id'], 'solver': solver, 'targets': [o.name for o in objects], 'skipped_overlapping': skipped,
                     'cutter': cutter.name, 'frames': [start, end], 'keys': len(p['cutter_keys']), 'cut_from_frame': first_frame,
                     'signed_volumes': volumes})
    bpy.context.scene.frame_set(1)
    return rows


def apply_camera_bound(shot, cues):
    """Resolve camera-bound actions and (re)apply actions so they use the resolved frames. Reveals always
    apply; other camera-bound actions go through the standard apply_actions (idempotent)."""
    resolved = resolve(shot, cues)
    if any(a['type'] != 'reveal' and camera_bound(a) for a in shot['actions']):
        apply_actions(shot)
    reveals = apply(shot)
    simulations = []
    if any(a['type'] == 'simulate' for a in shot['actions']):  # after the reveal: debris falls through the cut
        import simulate
        simulations = simulate.apply(shot)
    return {'resolved': resolved, 'reveals': reveals, 'simulations': simulations}
