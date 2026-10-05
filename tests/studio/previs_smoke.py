"""Run inside Blender: hybrid previs conventions in the clay pass.

blender -b --factory-startup --python tests/studio/previs_smoke.py
A spec-built P-51D gets orientation tints (red faces point along the spec length axis, blue the other
way) and a placeholder part keyed black only inside its frame window; checked on Cycles pixels.
"""
from pathlib import Path
import json
import sys
import tempfile

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'studio/blender_ops'))
from modeling import build_subject  # noqa: E402
from look import _clay  # noqa: E402

bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete()
spec_path = ROOT / 'tests/fixtures/p51_autofit/subjects/p51d/spec.json'
spec = json.loads(spec_path.read_text())
build_subject(spec, root_location=(0, 0, 0))
root = next(o for o in bpy.data.objects if o.get('studio_id') == 'p51d')
root.rotation_euler = (0, 0, 1.2)  # heading must follow the subject, not the world
bpy.context.view_layer.update()
scene = bpy.context.scene
scene.frame_start, scene.frame_end = 1, 30
job = {'shot': {'route': {'generative': {'previs': {'orientation_colors': True, 'placeholders': [
    {'start_frame': 10, 'end_frame': 20, 'part_ids': ['p51d/canopy'], 'description': 'a pilot in a leather helmet'}]}}}},
       'subject_spec_paths': {'p51d': str(spec_path)}}
report = _clay(scene, job)
assert report['oriented_subjects'] == ['p51d'] and report['placeholders'] == ['p51d/canopy'], report

sun = bpy.data.objects.new('sun', bpy.data.lights.new('sun', 'SUN')); scene.collection.objects.link(sun); sun.data.energy = 4
sun.rotation_euler = (0.3, 0.2, 0)
scene.world = scene.world or bpy.data.worlds.new('w')
cam = bpy.data.objects.new('cam', bpy.data.cameras.new('cam')); scene.collection.objects.link(cam); scene.camera = cam
r = scene.render; r.engine = 'CYCLES'; scene.cycles.samples = 4; scene.cycles.use_denoising = False
r.resolution_x = r.resolution_y = 160; r.film_transparent = True
scene.view_settings.view_transform = 'Standard'
forward = (root.matrix_world.to_3x3() @ Vector((0, 1, 0))).normalized()


def shoot(direction, frame, name):
    scene.frame_set(frame)
    cam.location = forward * 18 * direction + Vector((0, 0, 1.0))
    cam.rotation_euler = (-forward * direction).to_track_quat('-Z', 'Y').to_euler()
    path = Path(tempfile.gettempdir()) / f'previs_{name}.png'
    r.filepath = str(path); bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(str(path)); px = np.empty(len(img.pixels), dtype=np.float32); img.pixels.foreach_get(px); bpy.data.images.remove(img)
    px = px.reshape(-1, 4); px = px[px[:, 3] > 0.9]
    red = np.mean((px[:, 0] > 0.12) & (px[:, 0] > 2 * px[:, 2]) & (px[:, 0] > 2 * px[:, 1]))  # hue dominance; shading varies
    blue = np.mean((px[:, 2] > 0.12) & (px[:, 2] > 2 * px[:, 0]))
    black = np.mean(px[:, :3].max(axis=1) < 0.03)
    return {'red': round(float(red), 3), 'blue': round(float(blue), 3), 'black': round(float(black), 4)}


front = shoot(1, 1, 'front')
back = shoot(-1, 1, 'back')
canopy_off = shoot(1, 5, 'canopy_off')  # front view, outside the placeholder window
canopy_on = shoot(1, 15, 'canopy_on')   # inside it
result = {'front': front, 'back': back, 'canopy_off': canopy_off, 'canopy_on': canopy_on}
assert front['red'] > 0.1 and front['red'] > 10 * front['blue'], result
assert back['blue'] > 0.01 and back['blue'] > 10 * back['red'], result  # tapered tails show few rear-facing faces
assert canopy_on['black'] > 0 and canopy_off['black'] == 0, result
print('STUDIO_PREVIS_SMOKE ' + json.dumps({'ok': True, **result}))
