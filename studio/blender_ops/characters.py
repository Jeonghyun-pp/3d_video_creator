"""Characters (runs inside Blender): rigged library people placed in a declarative scene, walking a path or holding an
action, at their real height (shot.scene.characters; host side studio/characters.py).

Why (archcut3, 2026-10-08): the inspectors were spec boxes swung at the hip - toy figures with straight legs that no
generation model could turn into people. A rigged CC0 character gives the size, pose, gait and position right; the
look (face, clothes) is what generation may restyle.

Walking without foot slide: a library action walks in place, its stance foot moving backward at the speed measured
when the character was registered (ground_speed, native units per action frame). The root moves along the path at
speed_mps; the action plays at scale = (ground_speed * size) / (speed_mps / fps) scene frames per action frame, so the
planted foot stays put. Every copy is its own armature (one shared pose would march a crowd in step), with its own
phase.

Registration (measure): the native height, the forward axis and each locomotion action's ground speed and cycle,
from the stance foot (lowest few percent of the height).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import bpy
from mathutils import Vector

FOOT_BONES = (('LeftFoot', 'RightFoot'), ('foot.L', 'foot.R'), ('mixamorig:LeftFoot', 'mixamorig:RightFoot'))
STANCE_SHARE = 0.012     # a foot within 1.2 % of the height above its lowest point is planted (3 % caught the swing foot
                         # skimming the ground, 2026-10-08: half the real ground speed)


def _import(fbx):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=str(fbx))
    new = [o for o in bpy.data.objects if o not in before]
    armature = next(o for o in new if o.type == 'ARMATURE')
    meshes = [o for o in new if o.type == 'MESH']
    return armature, meshes, new


def _use(armature, action):
    ad = armature.animation_data or armature.animation_data_create()
    ad.action = action
    if getattr(action, 'slots', None) and hasattr(ad, 'action_slot'):
        ad.action_slot = action.slots[0]


def _action(name):
    return next((a for a in bpy.data.actions if a.name == name or a.name.endswith('|' + name)), None)


def _feet(armature):
    for pair in FOOT_BONES:
        if all(b in armature.pose.bones for b in pair):
            return pair
    raise ValueError(f'CHARACTER: no foot bones ({FOOT_BONES}) in {armature.name}')


def _height(meshes):
    dg = bpy.context.evaluated_depsgraph_get()
    zs = [(m.matrix_world @ v.co).z for m in meshes for v in m.evaluated_get(dg).data.vertices]
    return max(zs) - min(zs), min(zs)


def measure(fbx, actions, locomotion):
    """character.json body: native height, forward axis, per action its frames and (locomotion) ground speed."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    armature, meshes, _ = _import(fbx)
    scene = bpy.context.scene
    out = {'actions': {}, 'bones': len(armature.data.bones)}
    armature.data.pose_position = 'REST'   # the standing height: the rest pose, not a stride
    bpy.context.view_layer.update()
    out['native_height'] = round(_height(meshes)[0], 4)
    armature.data.pose_position = 'POSE'
    heights, forward = [], Vector((0, 0, 0))
    for key, name in actions.items():
        action = _action(name)
        if action is None:
            raise ValueError(f'CHARACTER: no action {name!r} (has {sorted(a.name for a in bpy.data.actions)})')
        _use(armature, action)
        f0, f1 = (int(round(v)) for v in action.frame_range)
        row = {'action': action.name, 'frames': [f0, f1], 'cycle_frames': f1 - f0}
        scene.frame_set(f0)
        height = out['native_height']
        if key in locomotion:
            feet = _feet(armature)
            track = {foot: [] for foot in feet}
            for f in range(f0, f1 + 1):
                scene.frame_set(f)
                for foot in feet:
                    track[foot].append(armature.matrix_world @ armature.pose.bones[foot].head)
            low = min(p.z for ps in track.values() for p in ps)
            steps = []
            for ps in track.values():
                for a, b in zip(ps, ps[1:]):
                    if a.z < low + STANCE_SHARE * height and b.z < low + STANCE_SHARE * height:
                        steps.append(Vector((b.x - a.x, b.y - a.y, 0)))
            if not steps:
                raise ValueError(f'CHARACTER: {name} never plants a foot; is it a locomotion cycle?')
            mean = sum(steps, Vector()) / len(steps)
            along = sorted(v.dot(mean.normalized()) for v in steps)
            row['ground_speed'] = round(along[len(along) // 2], 5)   # native units per action frame (median planted step)
            row['planted_steps'] = len(steps)
            forward -= mean   # the planted foot moves backward
        out['actions'][key] = row
    if forward.length:
        f = forward.normalized()
        out['forward'] = [round(f.x, 4), round(f.y, 4)]
    return out


def _path_at(points, s):
    """(point, tangent) at arc length s along a polyline (clamped)."""
    for a, b in zip(points, points[1:]):
        seg = (b - a).length
        if s <= seg or b is points[-1]:
            t = 0.0 if seg == 0 else min(1.0, max(0.0, s / seg))
            return a.lerp(b, t), (b - a).normalized() if seg else Vector((0, 1, 0))
        s -= seg
    return points[-1], (points[-1] - points[-2]).normalized()


def _yaw(forward, direction):
    """Rotation about Z that turns the character's forward axis to a direction."""
    return math.atan2(direction.y, direction.x) - math.atan2(forward[1], forward[0])


def _texture(meshes, image_path):
    image = bpy.data.images.load(str(image_path), check_existing=True)
    image.colorspace_settings.name = 'sRGB'
    for mesh in meshes:
        for slot in mesh.material_slots:
            mat = slot.material
            if mat is None or not mat.use_nodes:
                continue
            nodes = mat.node_tree.nodes
            bsdf = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
            tex = next((n for n in nodes if n.type == 'TEX_IMAGE'), None) or nodes.new('ShaderNodeTexImage')
            tex.image = image
            tex.interpolation = 'Closest'   # a palette texture: one flat colour per UV island
            if bsdf is not None:
                mat.node_tree.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
                bsdf.inputs['Roughness'].default_value = 0.8


def build(rows, frame_count, fps):
    """Place every declared character (rows resolved by studio/characters.py). Returns report rows."""
    report = []
    for row in rows:
        manifest = row['manifest']
        armature, meshes, new = _import(Path(row['asset_dir']) / manifest['fbx'])
        action_row = manifest['actions'][row['action']]
        action = _action(action_row['action'])
        size = row['height_m'] / manifest['native_height']
        root = bpy.data.objects.new(row['id'], None)
        bpy.context.scene.collection.objects.link(root)
        root['studio_id'] = root['studio_layout_id'] = root['studio_subject_id'] = row['id']
        root['studio_character'] = json.dumps({'asset': row['asset'], 'action': row['action']})
        armature.parent = root
        armature.matrix_parent_inverse.identity()
        root.scale = (size, size, size)
        armature.name = f"{row['id']}.rig"
        armature['studio_scene_role'] = 'helper'
        for i, mesh in enumerate(meshes):
            mesh.name = f"{row['id']}/figure" + (f'.{i}' if i else '')
            mesh['studio_id'], mesh['studio_subject_id'], mesh['studio_part_id'] = mesh.name, row['id'], 'figure'
            mesh['studio_scene_role'] = 'object'
        if row.get('texture'):
            _texture(meshes, Path(row['asset_dir']) / row['texture'])
        forward = manifest.get('forward', [0.0, -1.0])
        start = int(row.get('start_frame', 0))
        moving = 'path' in row
        ad = armature.animation_data or armature.animation_data_create()
        ad.action = None
        track = ad.nla_tracks.new()
        if moving:
            prefs = bpy.context.preferences.edit
            saved_interp, prefs.keyframe_new_interpolation_type = prefs.keyframe_new_interpolation_type, 'LINEAR'   # a steady walk
            points = [Vector(p) for p in row['path']]
            per_frame = row['speed_mps'] / fps
            scale = action_row['ground_speed'] * size / per_frame
            for f in range(frame_count):
                s = max(0, f - start) * per_frame
                p, t = _path_at(points, s)
                root.location = p
                root.rotation_euler = (0.0, 0.0, _yaw(forward, t))
                root.keyframe_insert('location', frame=f + 1)
                root.keyframe_insert('rotation_euler', frame=f + 1)
            prefs.keyframe_new_interpolation_type = saved_interp
        else:
            scale = 1.0
            root.location = Vector(row['at'])
            root.rotation_euler = (0.0, 0.0, math.radians(row.get('facing_deg', 0.0)) - math.atan2(forward[1], forward[0]) + math.pi / 2)
        cycle = action_row['cycle_frames'] * scale
        offset = (row.get('phase', 0.0) % 1.0) * cycle
        strip = track.strips.new(row['action'], int(round(start + 1 - offset - cycle)), action)
        if getattr(action, 'slots', None) and hasattr(strip, 'action_slot'):
            strip.action_slot = action.slots[0]
        strip.scale = scale
        strip.repeat = max(1.0, (frame_count + 2 * cycle) / max(cycle, 1e-6))
        if row.get('appear_frame') is not None:   # walks in: hidden before (keyed visibility: an object, not a helper)
            for mesh in meshes:
                mesh.hide_render = True
                mesh.keyframe_insert('hide_render', frame=1)
                mesh.hide_render = False
                mesh.keyframe_insert('hide_render', frame=int(row['appear_frame']) + 1)
        for obj in new:   # imported actions keep only what the strips use
            obj.select_set(False)
        report.append({'id': row['id'], 'asset': row['asset'], 'action': row['action'], 'scale': round(size, 4),
                       'playback_scale': round(scale, 4), 'height_m': row['height_m']})
    return report


def foot_slip(character_id, frame_count, height_m):
    """Worst drift (m) of a planted foot while it is planted (within STANCE_SHARE of the height above its lowest
    point), over the shot's complete plants (the smoke's check). A plant cut by the first or last frame is left
    out: where it started is not in the shot (measured 2026-10-08: whole plants drift 1-3 cm)."""
    rig = bpy.data.objects[f'{character_id}.rig']
    feet = _feet(rig)
    scene = bpy.context.scene
    track = {foot: [] for foot in feet}
    for f in range(1, frame_count + 1):
        scene.frame_set(f)
        for foot in feet:
            track[foot].append(rig.matrix_world @ rig.pose.bones[foot].head)
    worst = 0.0
    for ps in track.values():
        low = min(p.z for p in ps)
        run = []
        for i, p in enumerate(ps + [None]):
            if p is not None and p.z < low + STANCE_SHARE * height_m:
                run.append((i, p))
                continue
            whole = run and run[0][0] > 0 and run[-1][0] < len(ps) - 1
            if len(run) > 2 and whole:
                start = Vector((run[0][1].x, run[0][1].y, 0))
                worst = max(worst, max((Vector((q.x, q.y, 0)) - start).length for _, q in run))
            run = []
    return round(worst, 4)


if __name__ == '__main__':   # registration: blender --background --python characters.py -- job.json out.json
    import sys
    job = json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text())
    Path(sys.argv[sys.argv.index('--') + 2]).write_text(json.dumps(measure(job['fbx'], job['actions'], set(job['locomotion'])), indent=1))
