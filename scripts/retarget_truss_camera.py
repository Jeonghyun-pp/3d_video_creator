"""Save a camera-swing study from the loaded DDP truss .blend.

blender -b truss_flythrough.blend --python scripts/retarget_truss_camera.py -- output.blend
"""

import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector


output = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
scene = bpy.context.scene
camera = bpy.data.objects["Camera 35.25-42.25"]
for frame in range(1, 211):
    scene.frame_set(frame)
    t = (frame - 1) / 209
    position = camera.location.copy()
    swing = -2.5 * math.exp(-((t - 0.50) / 0.24) ** 2)
    target = Vector((0.85 + 2.2 * t + 0.35 * math.sin(t * 4.2 + 0.6) + swing,
                     position.y + 7.6,
                     1.85 + 0.38 * math.sin(t * 4.4)))
    camera.rotation_euler = (target - position).to_track_quat("-Z", "Y").to_euler()
    camera.keyframe_insert(data_path="rotation_euler", frame=frame)
output.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(output))
