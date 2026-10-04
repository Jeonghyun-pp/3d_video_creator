"""Run inside Blender, no renderer/GPU required. Covers look_scale audit + explicit rescale."""
from pathlib import Path
import sys
import json
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
from look_scale import audit_scale, rescale_scene

bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
X = 10.0  # every part modelled 10x too large

def cyl(name, verts, radius, depth, loc):
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=radius, depth=depth, location=loc)
    obj = bpy.context.object; obj.name = name
    return obj

# M20 hex head: s = 30 mm across flats (= r*sqrt(3) for a 6-gon), k = 12.5 mm
bolt = cyl('part_a', 6, X * 0.030 / 3 ** 0.5, X * 0.0125, (0, 0, 0)); bolt['studio_dim_role'] = 'bolt_hex_head'
stud = cyl('part_b', 32, X * 0.010, X * 0.10, (1, 0, 0)); stud['studio_role'] = 'threaded_rod'   # role regex path
washer = cyl('washer_M20', 32, X * 0.037 / 2, X * 0.003, (2, 0, 0))                              # name fallback path
bpy.ops.mesh.primitive_cube_add(size=1, location=(3, 0, 0)); bpy.context.object.name = 'mystery_blob'
bpy.ops.mesh.primitive_plane_add(size=20); ground = bpy.context.object; ground.name = 'ground'; ground['studio_dim_role'] = 'none'

light_data = bpy.data.lights.new('key', 'POINT'); light_data.energy = 1000.0
light = bpy.data.objects.new('key', light_data); light.location = (2, -2, 3); scene.collection.objects.link(light)
cam_data = bpy.data.cameras.new('cam'); cam_data.dof.use_dof = True; cam_data.dof.focus_distance = 5.0
cam_data.clip_start = 0.1; cam_data.clip_end = 100.0
cam = bpy.data.objects.new('cam', cam_data); cam.location = (0, -5, 0); cam.rotation_euler = (1.5708, 0, 0)
scene.collection.objects.link(cam); scene.camera = cam
bpy.context.view_layer.update()

before = audit_scale(scene)
f = before['suggested_uniform_factor']
assert f is not None and abs(f - 1 / X) <= 0.3 / X, before
assert before['flag_ratio'] == 1.0, before['flag_ratio']
assert before['unclassified'] == ['mystery_blob'] and before['exempt'] == ['ground'], before
assert {r['classified_by'] for r in before['checks']} == {'dim_role', 'role', 'name'}, before['checks']
assert before == audit_scale(scene), 'audit must be deterministic'
assert scene.objects['part_a'].dimensions.z > 0.1, 'audit must not modify the scene'

# refusal: keyed camera (object, then data) -> nothing changes
refusals = []
for owner, path in ((cam, 'location'), (cam_data, 'lens')):
    owner.keyframe_insert(path, frame=1)
    try:
        rescale_scene(scene, f)
    except ValueError as error:
        assert str(error).startswith('LOOK_SCALE: '), error
        refusals.append(owner.name)
    owner.animation_data_clear()
assert refusals == ['cam', 'cam'], refusals
assert light_data.energy == 1000.0 and abs(cam_data.dof.focus_distance - 5.0) < 1e-6
try:
    rescale_scene(scene, 0); raise AssertionError('factor 0 accepted')
except ValueError:
    pass

log = rescale_scene(scene, f)
bpy.context.view_layer.update()
after = audit_scale(scene)
g = after['suggested_uniform_factor']
assert 0.8 <= g <= 1.25, after
assert after['flag_ratio'] == 0.0, after['flag_ratio']
assert abs(light_data.energy - 1000.0 * f * f) < 1e-6 * 1000, light_data.energy
assert abs(cam_data.dof.focus_distance - 5.0 * f) < 1e-6 and abs(cam_data.clip_start - 0.1 * f) < 1e-9
assert abs(cam_data.clip_end - 100.0 * f) < 1e-6 and abs(cam.location.y + 5.0 * f) < 1e-6
assert abs(before['framing']['field_width_at_focus_m'] * f - after['framing']['field_width_at_focus_m']) < 1e-3
print('STUDIO_LOOK_SCALE_SMOKE ' + json.dumps({
    'ok': True, 'factor_before': f, 'flag_ratio_before': before['flag_ratio'], 'factor_after': g,
    'flag_ratio_after': after['flag_ratio'], 'energy_after': round(light_data.energy, 4),
    'energy_expected': round(1000.0 * f * f, 4), 'focus_after': round(cam_data.dof.focus_distance, 5),
    'refusals': refusals, 'unreviewed': log['unreviewed'],
    'checks': ['audit_factor', 'classification_paths', 'unclassified_reported', 'exempt', 'deterministic',
               'audit_read_only', 'refuse_keyed_camera', 'refuse_keyed_camera_data', 'refuse_bad_factor',
               're_audit_in_band', 'energy_f2', 'camera_lengths']}))
