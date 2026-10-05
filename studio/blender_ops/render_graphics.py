"""Render a version's explainer graphics into a transparent RGBA frame sequence (not part of the beauty render).

blender -b <version>/scene.blend --python render_graphics.py -- job.json
job: {output_dir, frames, width, height, fps}. EEVEE with a transparent film: graphics objects are unhidden,
every mesh becomes a holdout (it still hides strokes behind it and feeds Line Art, but renders nothing), lights
and world do not matter. Writes frame_000000.png ... and graphics_render.json.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).parent))
from scene_roles import first_blocking_hit  # noqa: E402

job = json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text())
out = Path(job['output_dir'])
out.mkdir(parents=True, exist_ok=True)
scene = bpy.context.scene
render = scene.render
render.engine = 'BLENDER_EEVEE'
scene.eevee.taa_render_samples = 16
render.resolution_x, render.resolution_y, render.resolution_percentage = job['width'], job['height'], 100
render.fps, render.fps_base = job['fps'], 1
render.film_transparent = True
render.use_motion_blur = False
render.use_compositing = False
render.use_sequencer = False
settings = render.image_settings
settings.file_format, settings.color_mode, settings.color_depth = 'PNG', 'RGBA', '8'
scene.view_settings.view_transform, scene.view_settings.look = 'Standard', 'None'
scene.view_settings.exposure = 0.0
# Only what graphics.py built from shot.graphics; an authored mesh tagged role 'graphic' (a 3D title) already
# renders in the beauty pass, so here it is a holdout like any other mesh.
graphics = [o for o in scene.objects if o.get('studio_graphic_layer')]
for obj in scene.objects:
    if obj.get('studio_graphic_layer'):
        obj.hide_render = False
    elif obj.type in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT'}:
        obj.is_holdout = True
    elif obj.type in {'LIGHT', 'VOLUME'}:
        obj.hide_render = True
for camera in (o for o in scene.objects if o.type == 'CAMERA'):
    camera.data.dof.use_dof = False
texts = [(o, json.loads(o['studio_graphic_text'])) for o in graphics if o.get('studio_graphic_text')]
placed = {}


def _place(spec, frame):
    """Screen position of a dimension value (midpoint, offset across the line), or None when hidden."""
    start, end = spec['frames']
    if not start <= frame < end:
        return None
    a, b = Vector(spec['a']), Vector(spec['b'])
    mid = (a + b) / 2
    ua, ub, um = (world_to_camera_view(scene, scene.camera, p) for p in (a, b, mid))
    if um.z <= 0 or not (0 <= um.x <= 1 and 0 <= um.y <= 1):
        return None
    origin = scene.camera.matrix_world.translation
    ray = mid - origin
    if first_blocking_hit(scene, bpy.context.evaluated_depsgraph_get(), origin, ray.normalized(), ray.length - 0.05)[0]:  # the line itself is held out by geometry; so is its value
        return None
    return {'text': spec['text'], 'u': um.x, 'v': 1 - um.y, 'du': ub.x - ua.x, 'dv': -(ub.y - ua.y), 'color_srgb': spec['color_srgb']}


for frame in job['frames']:
    scene.frame_set(frame + 1)
    rows = [row for row in (_place(spec, frame) for _, spec in texts) if row]
    if rows:
        placed[str(frame)] = rows
    render.filepath = str(out / f'frame_{frame:06d}.png')
    bpy.ops.render.render(write_still=True)
(out / 'graphics_render.json').write_text(json.dumps({'graphics': sorted(o.name for o in graphics), 'frames': len(job['frames']),
                                                     'width': job['width'], 'height': job['height'], 'engine': render.engine,
                                                     'texts': placed}))
