"""Render one frame of a built version as a look reference still (generate still).

blender -b <version>/scene.blend --python render_still.py -- job.json
job: {frame (0-based), output, height, samples, device}. The scene renders as built (its look preset, lights,
compositor); only resolution, samples and device change. Never part of a shot render (jobs.py owns those).
"""
import json
import sys
from pathlib import Path

import bpy

job = json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text())
scene = bpy.context.scene
render = scene.render
scale = job['height'] / render.resolution_y
render.resolution_x, render.resolution_y = max(2, round(render.resolution_x * scale / 2) * 2), job['height'] // 2 * 2
render.resolution_percentage = 100
render.image_settings.file_format, render.image_settings.color_mode = 'PNG', 'RGB'
if render.engine == 'CYCLES':
    scene.cycles.samples = job['samples']
    if job['device'] == 'GPU':
        prefs = bpy.context.preferences.addons['cycles'].preferences
        for kind in ('METAL', 'OPTIX', 'CUDA', 'HIP', 'ONEAPI'):
            try:
                prefs.compute_device_type = kind
                break
            except TypeError:
                continue
        prefs.get_devices()
        for device in prefs.devices:
            device.use = True
        scene.cycles.device = 'GPU'
scene.frame_set(job['frame'] + 1)
render.filepath = job['output']
bpy.ops.render.render(write_still=True)
