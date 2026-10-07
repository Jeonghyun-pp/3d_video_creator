"""Run inside Blender (headless): workbench previews under a photoreal look's view settings (exposure, AgX, white
balance, a tone curve, a compositor). The id pass matches its palette exactly and counts the same parts as on a neutral
scene; every setting comes back; the lit pass keeps the look, so it matches a real render with the same settings."""
from pathlib import Path
import json
import sys
import tempfile

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
from modeling import build_subject  # noqa: E402
import workbench_tools  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
report = {}


def check(name, cond, detail):
    report[name] = detail
    assert cond, f'{name}: {detail}'


build_subject({'subject_id': 'p', 'builders': [
    {'part_id': 'block', 'builder': 'box', 'params': {'size': [0.4, 0.3, 0.2]}},
    {'part_id': 'boss', 'builder': 'revolve', 'params': {'profile': [[0, 0], [0.06, 0], [0.06, 0.1], [0, 0.1]]},
     'transform': {'location': [0.1, 0, 0.1]}}]})
material = bpy.data.materials.new('grey'); material.diffuse_color = (0.6, 0.6, 0.6, 1)
for o in scene.objects:
    if o.type == 'MESH':
        o.data.materials.append(material)
world = bpy.data.worlds.new('w'); world.use_nodes = True; scene.world = world
world.node_tree.nodes['Background'].inputs['Strength'].default_value = 0.8
tmp = tempfile.mkdtemp()
state = {'session_dir': tmp}
camera = {'target': [0, 0, 0.1], 'distance_m': 1.6, 'azimuth_deg': -60, 'elevation_deg': 25, 'lens_mm': 50, 'width': 320, 'height': 240, 'name': 'v'}


def preview(passes):
    return workbench_tools._preview_frame(state, [camera], passes, 320, 1, 'p', {'samples': 8, 'light': 'scene'})


neutral = preview(['id'])['pixels']['v']
look = {'view_settings.view_transform': 'AgX', 'view_settings.look': 'AgX - Base Contrast', 'view_settings.exposure': 2.85,
        'view_settings.gamma': 1.1, 'view_settings.use_white_balance': True, 'view_settings.use_curve_mapping': True,
        'render.use_compositing': True, 'render.dither_intensity': 1.0, 'render.film_transparent': False}
for path, value in look.items():
    owner, name = scene, path
    for part in path.split('.')[:-1]:
        owner = getattr(owner, part)
    setattr(owner, path.split('.')[-1], value)
scene.view_settings.white_balance_temperature = 4500
scene.use_nodes = True   # a compositor tree is present (Render Layers -> Composite)


def current():
    out = {}
    for path in look:
        owner = scene
        for part in path.split('.')[:-1]:
            owner = getattr(owner, part)
        out[path] = getattr(owner, path.split('.')[-1])
    return out


before = current()
looked = preview(['id', 'shaded', 'lit'])
check('id_exact_under_look', looked['pixels']['v']['unmatched_px'] == 0, looked['pixels']['v'])
check('id_same_parts_as_neutral', looked['pixels']['v']['parts'] == neutral['parts'], [looked['pixels']['v']['parts'], neutral['parts']])
after = current()
check('settings_restored', after == before, {k: (before[k], after[k]) for k in before if before[k] != after[k]})


def mean(path):
    img = bpy.data.images.load(path)
    try:
        buf = np.empty(len(img.pixels), dtype=np.float32); img.pixels.foreach_get(buf)
    finally:
        bpy.data.images.remove(img)
    return float(buf.reshape(-1, 4)[:, :3].mean())


# the real render path: the same scene settings, Cycles, rendered directly through the preview's own camera
scene.render.engine = 'CYCLES'; scene.cycles.samples = 8
view_cam, _ = workbench_tools._orbit_camera(scene, camera)
scene.camera = view_cam
scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 320, 240, 100
scene.render.image_settings.file_format, scene.render.image_settings.color_mode = 'PNG', 'RGB'
scene.render.filepath = str(Path(tmp) / 'real.png')
bpy.ops.render.render(write_still=True)
lit, real = mean(looked['images']['v']['lit']), mean(str(Path(tmp) / 'real.png'))
check('lit_matches_real_render', abs(lit - real) < 0.03, {'lit': round(lit, 4), 'real': round(real, 4)})
print(json.dumps(report, indent=1, default=str))
print('PREVIEW VIEW SMOKE OK')
