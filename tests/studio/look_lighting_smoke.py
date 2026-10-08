"""Run inside Blender (CPU metering only). Covers preset apply, determinism, idempotency, removal, provenance refusal."""
from pathlib import Path
import json
import shutil
import sys
import tempfile
import bpy
from mathutils import Vector
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'studio/blender_ops'))
from look_lighting import apply_lighting, remove_lighting

LIBRARY = REPO / 'library'
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 1080, 1920, 100
scene.render.filepath = '//smoke_out_'
for i, loc in enumerate([(0, 0, 0.5), (1.4, 0.3, 0.4), (-1.2, 0.5, 0.6)]):
    bpy.ops.mesh.primitive_cube_add(size=0.8 + 0.2 * i, location=loc); bpy.context.object.name = f'cube_{i}'
bpy.ops.mesh.primitive_plane_add(size=8, location=(0, 0, 0)); bpy.context.object.name = 'floor'
cam_data = bpy.data.cameras.new('cam'); cam = bpy.data.objects.new('cam', cam_data); scene.collection.objects.link(cam)
cam.location = (5, -6, 3.5); cam.rotation_euler = (Vector((0, 0, 0.5)) - cam.location).to_track_quat('-Z', 'Y').to_euler()
scene.camera = cam
bpy.ops.object.light_add(type='POINT', location=(2, 2, 3)); user_light = bpy.context.object; user_light.name = 'user_light'

def state():
    return sorted(o.name for o in scene.objects if o.name.startswith('StudioLook_'))
cam_before = (tuple(cam.matrix_world.col[3]), cam.data.lens)
render_before = (scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage,
                 scene.render.filepath, scene.cycles.samples, scene.cycles.device, scene.render.engine)

r1 = apply_lighting(scene, 'studio_product', library_root=LIBRARY)
names1 = state()
assert r1['view_transform'] == 'AgX' and scene.view_settings.view_transform == 'AgX', r1
assert r1['ev_source'] == 'metered' and -4 <= r1['exposure_ev'] <= 6, r1
assert r1['hdri_asset_id'] == 'studio_small_08' and len(r1['hdri_sha256']) == 64, r1
assert names1 and set(r1['lights']) <= set(names1), (r1, names1)
assert all(scene.objects[n].get('studio_look') for n in names1)
assert user_light.hide_render, 'pre-existing light should be hidden from render'
assert (tuple(cam.matrix_world.col[3]), cam.data.lens) == cam_before, 'camera changed'
assert (scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage,
        scene.render.filepath, scene.cycles.samples, scene.cycles.device, scene.render.engine) == render_before, 'render settings not restored'

r2 = apply_lighting(scene, 'studio_product', library_root=LIBRARY)
assert r2 == r1, (r1, r2)
assert state() == names1, (state(), names1)
assert len([w for w in bpy.data.worlds if w.name.startswith('StudioLook_')]) == 1
assert len([l for l in bpy.data.lights if l.name.startswith('StudioLook_')]) == len(r1['lights'])

r3 = apply_lighting(scene, 'studio_product', library_root=LIBRARY, style_light_rig={'exposure_ev': 1.25, 'white_balance_k': 5000})
assert r3['ev_source'] == 'style' and r3['exposure_ev'] == 1.25 and scene.view_settings.exposure == 1.25, r3
assert r3['white_balance_k'] == 5000, r3

remove_lighting(scene)
assert not state(), state()
assert not any(x.name.startswith('StudioLook_') for coll in (bpy.data.objects, bpy.data.lights, bpy.data.worlds, bpy.data.collections, bpy.data.images) for x in coll)
assert scene.world is not None and not scene.world.name.startswith('StudioLook_')
assert not user_light.hide_render, 'pre-existing light not restored'

with tempfile.TemporaryDirectory() as tmp:
    src = LIBRARY / 'assets/studio_small_08'
    dst = Path(tmp) / 'assets/studio_small_08'
    shutil.copytree(src, dst)
    hdr = next(dst.glob('v*/original/*.hdr'))
    data = bytearray(hdr.read_bytes()); data[-1] ^= 0x01; hdr.write_bytes(bytes(data))
    try:
        apply_lighting(scene, 'studio_product', library_root=tmp, style_light_rig={'exposure_ev': 0.0})
        raise AssertionError('tampered HDRI accepted')
    except ValueError as exc:
        assert str(exc).startswith('LOOK_QA_FAILED: hdri provenance'), exc
        tampered = str(exc).split(': ', 1)[1][:60]
    assert not state(), 'refused apply must not touch the scene'


# Per-shot rig (shot.render.lighting, 2026-10-07): the key turned to the camera's left and lowered, the fill one stop
# under, the rim off, a kicker added - each lamp lands where it was declared, relative to the camera.
import math
from look_lighting import merged_rig
remove_lighting(scene)
shot_rig = {'key': {'azimuth_deg': -60, 'elevation_deg': 20}, 'fill': {'irradiance_ratio_of_key': 0.5}, 'rim': None,
            'kicker': {'azimuth_deg': 150, 'elevation_deg': 10, 'irradiance_ratio_of_key': 0.7}}
lit = apply_lighting(scene, 'studio_product', library_root=LIBRARY, shot_lighting={'rig': shot_rig})
rows = {r['name']: r for r in lit['rig']}
assert set(rows) == {'key', 'fill', 'kicker'} and 'StudioLook_rim' not in state(), (rows, state())
assert rows['fill']['stops_vs_key'] == -1.0 and abs(rows['kicker']['stops_vs_key'] - math.log2(0.7)) < 0.01, rows
center = Vector(lit['rig_target'])
for name, want in (('key', (-60, 20)), ('kicker', (150, 10))):
    lamp = scene.objects['StudioLook_' + name]
    d = (lamp.location - center).normalized()
    to_cam = cam.location - center
    az = math.degrees(math.atan2(d.y, d.x) - math.atan2(to_cam.y, to_cam.x))
    az = (az + 180) % 360 - 180
    el = math.degrees(math.asin(d.z))
    assert abs(az - want[0]) < 0.5 and abs(el - want[1]) < 0.5, (name, az, el, want)
for bad in ({'rim2': None}, {'key': {'power': 3}}, {'new': {'azimuth_deg': 0}}):
    try:
        merged_rig(json.loads((REPO / 'studio/blender_ops/look_data/lighting_presets.json').read_text())['presets']['studio_product']['rig'], bad)
        raise AssertionError(f'accepted {bad}')
    except ValueError as error:
        assert 'LOOK_QA_FAILED' in str(error), error
remove_lighting(scene)
print('LOOK_LIGHTING_SHOT_RIG_OK')

# The preset's other light, per shot (2026-10-08, archcut3): the sun is a rig row (so screen.light can measure a sun
# preset), and moves camera-relative; practicals and the world take factors; a physical sky replaces the camera sky.
day = apply_lighting(scene, 'exterior_day', library_root=LIBRARY)
sun0 = next(r for r in day['rig'] if r['name'] == 'sun')
moved = apply_lighting(scene, 'exterior_day', library_root=LIBRARY, shot_lighting={'sun': {'azimuth_deg': -45, 'elevation_deg': 12, 'irradiance_factor': 0.5}})
sun1 = next(r for r in moved['rig'] if r['name'] == 'sun')
assert abs(sun1['azimuth_deg'] + 45) < 0.5 and abs(sun1['elevation_deg'] - 12) < 0.5 and abs(sun1['irradiance'] - 0.5 * sun0['irradiance']) < 1e-3, (sun0, sun1)
inside = apply_lighting(scene, 'interior_industrial', library_root=LIBRARY)
dim = apply_lighting(scene, 'interior_industrial', library_root=LIBRARY, shot_lighting={'practicals': {'irradiance_factor': 0.25}, 'world': {'strength_factor': 2.0}})
energy = lambda: sum(o.data.energy for o in scene.objects if o.name.startswith('StudioLook_practical_'))  # noqa: E731
world = next(n for n in scene.world.node_tree.nodes if n.name == 'StudioLook_HDRI').inputs['Strength'].default_value
apply_lighting(scene, 'interior_industrial', library_root=LIBRARY)
assert abs(next(n for n in scene.world.node_tree.nodes if n.name == 'StudioLook_HDRI').inputs['Strength'].default_value * 2 - world) < 1e-6
full = energy()
apply_lighting(scene, 'interior_industrial', library_root=LIBRARY, shot_lighting={'practicals': {'irradiance_factor': 0.25}})
assert abs(energy() - 0.25 * full) < 1e-3 * full, (energy(), full)
dusk = apply_lighting(scene, 'night_city', library_root=LIBRARY, shot_lighting={'sky': {'sun_elevation_deg': 2, 'sun_azimuth_deg': 0}})
sky = next((n for n in scene.world.node_tree.nodes if n.type == 'TEX_SKY'), None)
assert sky is not None and sky.sky_type == 'MULTIPLE_SCATTERING' and dusk['camera_sky'], dusk['camera_sky']
remove_lighting(scene)
print('LOOK_LIGHTING_SUN_PRACTICALS_SKY_OK')
print('STUDIO_LOOK_LIGHTING_SMOKE ' + json.dumps({'ok': True, 'exposure_ev': r1['exposure_ev'], 'white_balance_k': r1['white_balance_k'],
      'look': r1['look'], 'lights': r1['lights'], 'hdri': r1['hdri_asset_id'], 'tampered_refused': tampered,
      'checks': ['sun_row_and_shot_sun', 'practicals_world_factors', 'physical_sky', 'agx', 'ev_range', 'render_settings_restored', 'camera_untouched', 'reapply_identical', 'style_ev_wb', 'remove_clean', 'tampered_hdri_refused']}))
