"""Storyboard frames for the user (studio/storyboard.py): a few Workbench frames of the built shot, one top view, and
the measured state the user's approval will be bound to - camera pose and lens per frame, where the focus objects sit
in the frame. Workbench only (~0.02 s/frame): cheap enough for every round of the conversation, never a look render.

Usage: blender -b <scene.blend> --python storyboard_render.py -- <job.json>
job: {frames: [0-based], focus: {frame: [ids]}, out_dir, height, path_every, render (false = measure only)}
"""
import json
import math
from pathlib import Path
import sys

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).parent))
from scene_tools import object_by_id  # noqa: E402

job = json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text())
scene = bpy.context.scene
out = Path(job['out_dir']); out.mkdir(parents=True, exist_ok=True)
camera = scene.camera
aspect = scene.render.resolution_x / scene.render.resolution_y
scene.render.engine = 'BLENDER_WORKBENCH'
scene.display.shading.light = 'STUDIO'
scene.display.shading.color_type = 'MATERIAL'
scene.render.resolution_y = job.get('height', 480)
scene.render.resolution_x = max(2, round(scene.render.resolution_y * aspect / 2) * 2)
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.render.use_compositing = False   # exposure keys and lens effects belong to the look, not to blocking


def meshes(obj):
    return [o for o in [obj, *obj.children_recursive] if o.type == 'MESH']


def projected(objs):
    xs, ys, front = [], [], False
    for o in objs:
        for corner in o.bound_box:
            p = world_to_camera_view(scene, camera, o.matrix_world @ Vector(corner))
            if p.z > 0:
                front = True
                xs.append(min(max(p.x, 0), 1)); ys.append(min(max(1 - p.y, 0), 1))
    if not front or not xs:
        return None
    return [round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4)]


state = {'frames': [], 'path': []}
for frame in job['frames']:
    scene.frame_set(frame + 1)
    forward = camera.matrix_world.to_quaternion() @ Vector((0, 0, -1))
    row = {'frame': frame, 'eye': [round(v, 4) for v in camera.matrix_world.translation], 'forward': [round(v, 5) for v in forward],
           'lens_mm': round(camera.data.lens, 3), 'focus': {}}
    distances = []
    for ident in job.get('focus', {}).get(str(frame), []):
        obj = object_by_id(ident)
        row['focus'][ident] = projected(meshes(obj)) if obj else 'missing'
        parts = meshes(obj) if obj else []
        if parts:   # how far the camera is from what it shows: the scale of "the same place" for the contract
            points = [o.matrix_world @ Vector(c) for o in parts for c in o.bound_box]
            distances.append((sum(points, Vector()) / len(points) - camera.matrix_world.translation).length)
    row['focus_distance_m'] = round(min(distances), 3) if distances else None
    if job.get('render', True):
        scene.render.filepath = str(out / f'frame_{frame:06d}.png')
        bpy.ops.render.render(write_still=True)
    state['frames'].append(row)
for f in range(1, scene.frame_end + 1, max(1, job.get('path_every', 3))):
    scene.frame_set(f)
    state['path'].append([round(v, 3) for v in camera.matrix_world.translation])
if not job.get('render', True):   # measurement only (the render gate compares it with the approved storyboard)
    (out / 'state.json').write_text(json.dumps(state, indent=1))
    raise SystemExit(0)
# top view: an orthographic camera over everything that renders, looking down
corners = [o.matrix_world @ Vector(c) for o in scene.objects if o.type == 'MESH' and not o.hide_render for c in o.bound_box]
corners += [Vector(p) for p in state['path']]
lo = Vector([min(c[i] for c in corners) for i in range(3)]); hi = Vector([max(c[i] for c in corners) for i in range(3)])
center = (lo + hi) / 2
top = bpy.data.objects.new('storyboard_top', bpy.data.cameras.new('storyboard_top'))
scene.collection.objects.link(top)
top.data.type = 'ORTHO'
top.data.sensor_fit = 'HORIZONTAL'   # ortho_scale is the visible width (AUTO would apply it to a portrait frame's height)
scale = max(hi.x - lo.x, (hi.y - lo.y) * aspect) * 1.25   # a margin so path dots at the edge stay whole
top.data.ortho_scale = scale
top.location = (center.x, center.y, hi.z + 50)
top.rotation_euler = (0, 0, 0)
top.data.clip_end = (hi.z - lo.z) + 200
scene.camera = top
scene.frame_set(1)
scene.render.filepath = str(out / 'top.png')
bpy.ops.render.render(write_still=True)
state['top'] = {'center': [round(center.x, 3), round(center.y, 3)], 'ortho_scale': round(scale, 3),
                'resolution': [scene.render.resolution_x, scene.render.resolution_y]}
state['engine'] = scene.render.engine
(out / 'state.json').write_text(json.dumps(state, indent=1))
print('STORYBOARD ' + json.dumps({'frames': len(state['frames'])}))
