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

print('STUDIO_LOOK_LIGHTING_SMOKE ' + json.dumps({'ok': True, 'exposure_ev': r1['exposure_ev'], 'white_balance_k': r1['white_balance_k'],
      'look': r1['look'], 'lights': r1['lights'], 'hdri': r1['hdri_asset_id'], 'tampered_refused': tampered,
      'checks': ['agx', 'ev_range', 'render_settings_restored', 'camera_untouched', 'reapply_identical', 'style_ev_wb', 'remove_clean', 'tampered_hdri_refused']}))
