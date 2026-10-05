"""Section staging (studio/blender_ops/section.py) in Blender: soil around the structure, poché on the cut faces only,
lights kept by the look, LED rows; the front cutter ends short of the face (never coplanar with it).
Run inside Blender (run_smokes detects it)."""
from pathlib import Path
import json
import sys

import bmesh
import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'studio' / 'blender_ops'))
import section  # noqa: E402

checks = []


def check(name, condition, detail=''):
    assert condition, f'{name}: {detail}'
    checks.append(name)


bpy.ops.wm.read_factory_settings(use_empty=True)
for name, loc, size in (('slab1', (0, 80, -7.4), (34, 40, 0.8)), ('wall_l', (-17, 80, -10), (1.2, 40, 20)), ('col', (5, 70, -3), (1, 1, 6))):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc); o = bpy.context.object; o.name = name; o.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
box = ((-17.6, 60.0, -21.5), (17.6, 100.0, 0.0))
report = section.stage('sec', box, ceilings=[-1.2, -8.2], lit_depth_m=30)
slab = bpy.data.objects['slab1']
on_face = [p for p in slab.data.polygons if abs((slab.matrix_world @ p.center).y - 60) < 0.02]
check('poche_on_cut_faces', report['poche_faces'] == 2 and slab.data.materials[on_face[0].material_index].name == 'sec.poche', report)
col = bpy.data.objects['col']
check('uncut_parts_untouched', all(slab.data.materials[0] != m for m in col.data.materials) or len(col.data.materials) <= 1)
soil = [bpy.data.objects[n] for n in report['soil']]
check('soil_around_not_poche', len(soil) == 3 and all(o.get('studio_scene_role') == 'environment_shell' and len(o.data.materials) == 1 for o in soil))
lights = [o for o in bpy.data.objects if o.type == 'LIGHT']
check('lights_kept_by_the_look', lights and all(o.get('studio_keep_light') for o in lights) and len(lights) == report['lights'], len(lights))
check('led_rows_are_fixtures', report['strips'] == 8 and all(bpy.data.objects[f'sec.led{l}.{k}'].get('studio_scene_role') == 'light_fixture'
                                                               for l in range(2) for k in range(4)))
cutter = section.front_cutter('cut', 60.0, reach_m=100, half_w_m=50, z_lo=-30, z_hi=0.5)
ys = [(cutter.matrix_world @ v.co).y for v in cutter.data.vertices]
bm = bmesh.new(); bm.from_mesh(cutter.data); volume = bm.calc_volume(signed=True); bm.free()
check('front_cutter_short_of_face', max(ys) < 60 and min(ys) < -39 and volume > 0, (max(ys), volume))
print('STUDIO_SECTION_SMOKE ' + json.dumps({'ok': True, 'checks': checks, 'report': report}))
