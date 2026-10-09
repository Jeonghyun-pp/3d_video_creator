"""An object the id pass hides (role atmosphere) stays hidden on every frame even when its visibility is keyed - in the
keep masks and in the frame probe (floor_noise, 2026-10-09: keyed wave lines came back white in the people masks).
Runs inside Blender: blender -b --factory-startup --python tests/studio/hidden_keyed_smoke.py"""
import json
from pathlib import Path
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'studio' / 'blender_ops'))

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.resolution_x, scene.render.resolution_y = 64, 64
bpy.ops.mesh.primitive_cube_add(size=1, location=(-1.2, 0, 0))
person = bpy.context.object; person.name = 'person'; person['studio_id'] = 'person'
bpy.ops.mesh.primitive_plane_add(size=1.5, location=(1.2, 0, 0), rotation=(1.5708, 0, 0))
wave = bpy.context.object; wave.name = 'wave'; wave['studio_scene_role'] = 'atmosphere'
wave.hide_render = True; wave.keyframe_insert('hide_render', frame=1)
wave.hide_render = False; wave.keyframe_insert('hide_render', frame=5)
# a hidden helper that still moves (a reveal cutter): its motion must survive, only its visibility keys go
bpy.ops.mesh.primitive_cube_add(size=0.2, location=(0, 0, 3))
mover = bpy.context.object; mover.name = 'mover'; mover['studio_scene_role'] = 'helper'
mover.keyframe_insert('location', frame=1)
mover.location = (0, 0, 5); mover.keyframe_insert('location', frame=6)
mover.hide_render = True; mover.keyframe_insert('hide_render', frame=1)
bpy.ops.object.camera_add(location=(0, -6, 0), rotation=(1.5708, 0, 0))
scene.camera = bpy.context.object

out = Path(tempfile.mkdtemp())
job = out / 'job.json'
job.write_text(json.dumps({'output_dir': str(out), 'frame_count': 6, 'width': 64, 'height': 64, 'parts': ['person'], 'frames': [0, 5]}))
sys.argv = [sys.argv[0], '--', str(job)]   # keep_masks runs its job when imported, like a Blender script
import keep_masks  # noqa: E402,F401
image = bpy.data.images.load(str(out / 'frame_000005.png'))
w, h = image.size
px = list(image.pixels)
white = lambda x0, x1: sum(1 for y in range(h) for x in range(x0, x1) if px[(y * w + x) * 4] > 0.5)  # noqa: E731
left, right = white(0, w // 2), white(w // 2, w)
assert left > 20, f'the kept part is masked white ({left} px)'
assert right == 0, f'the keyed atmosphere plane came back into the mask ({right} px)'
assert wave.hide_render, 'still hidden after frame_set'
scene.frame_set(6)
assert abs(mover.location.z - 5) < 1e-4 and mover.hide_render, ('a hidden helper lost its motion', tuple(mover.location))
print('HIDDEN_KEYED_SMOKE ' + json.dumps({'ok': True, 'person_px': left, 'wave_px': right}))
