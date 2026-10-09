"""The propagation shader: a glowing front leaves the origin at start_frame and is speed_mps x time away later, on any
surface, from a frame driver (no keys, no Python) - measured on rendered frames (EEVEE, top view of a plate).
Runs inside Blender: blender -b --factory-startup --python tests/studio/propagation_smoke.py"""
import json
import math
from pathlib import Path
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'studio' / 'blender_ops'))
import env_materials  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = 'BLENDER_EEVEE'
scene.render.resolution_x = scene.render.resolution_y = 128
scene.render.fps = 24   # the material is made at another rate; the driver must follow the scene's rate when it renders
scene.world = bpy.data.worlds.new('dark'); scene.world.color = (0, 0, 0)
bpy.ops.mesh.primitive_plane_add(size=10)
plate = bpy.context.object
plate.data.materials.append(env_materials.make('propagation', 'wave', {'origin': [0, 0, 0], 'speed_mps': 2.0, 'start_frame': 0,
                                                                      'band_m': 0.25, 'base_srgb': [0, 0, 0], 'trail': 0.0}))
bpy.ops.object.camera_add(location=(0, 0, 10))
camera = bpy.context.object; camera.data.type = 'ORTHO'; camera.data.ortho_scale = 10; scene.camera = camera
scene.render.fps = 30
out = Path(tempfile.mkdtemp())
radii = {}
for frame in (15, 45):   # shot frames 15 and 45 at 30 fps: 0.5 s and 1.5 s, the front 1 m and 3 m out
    scene.frame_set(frame + 1)
    scene.render.filepath = str(out / f'f{frame}.png')
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(scene.render.filepath)
    w, h = image.size
    px = list(image.pixels)
    best = max(range(w // 2, w), key=lambda x: px[((h // 2) * w + x) * 4 + 1])   # brightest pixel along the +x half-row
    radii[frame] = (best + 0.5 - w / 2) / w * 10
for frame, expected in ((15, 1.0), (45, 3.0)):
    assert abs(radii[frame] - expected) <= 0.1 * expected + 0.1, (frame, radii[frame], expected)
print('PROPAGATION_SMOKE ' + json.dumps({'ok': True, 'front_m': {str(k): round(v, 3) for k, v in radii.items()}}))
