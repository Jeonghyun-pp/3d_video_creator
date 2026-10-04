"""Run inside Blender, no render/GPU: look_camera realism keeps 3D label anchors where they were."""
from pathlib import Path
import json
import math
import sys
import bpy
from mathutils import Vector
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
from scene_tools import anchors_for_frame
import look_camera

bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.render.fps = 30; scene.frame_start, scene.frame_end = 1, 30
scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 1080, 1920, 100
W, H = 1080, 1920

bpy.ops.mesh.primitive_cube_add(size=0.4, location=(0, 0, 0.2)); gear = bpy.context.object
gear.name = 'gear'; gear['studio_id'] = 'test/gear'
bpy.ops.mesh.primitive_cube_add(size=1, location=(1.5, 3, 0.5)); bpy.context.object.name = 'backdrop'

# Rig-like camera: per-frame quaternion keys (what camera_rig bakes), 30-frame dolly in with a slight pitch.
data = bpy.data.cameras.new('cam'); cam = bpy.data.objects.new('cam', data); scene.collection.objects.link(cam)
scene.camera = cam; cam.rotation_mode = 'QUATERNION'; data.lens = 50
for f in range(1, 31):
    cam.location = Vector((0.6 * (f - 1) / 29, -4.0 + 1.5 * (f - 1) / 29, 1.2))
    cam.rotation_quaternion = (Vector((0, 0, 0.2)) - cam.location).to_track_quat('-Z', 'Y')
    cam.keyframe_insert('location', frame=f); cam.keyframe_insert('rotation_quaternion', frame=f)

LABELS = [{'label_id': 'gear_label', 'anchor': 'test/gear', 'start_frame': 0, 'end_frame': 30}]
INV = look_camera.PRESETS['invariants']


def anchor_px():
    out = {}
    for f in range(1, 31):
        scene.frame_set(f)
        row = anchors_for_frame(LABELS, f - 1)[0]
        out[f] = (row['u'] * W, row['v'] * H)
    return out


def matrices():
    out = []
    for f in range(1, 31):
        scene.frame_set(f)
        out.append(tuple(tuple(r) for r in cam.matrix_world))
    return out


def drift(a, b, frames):
    return [math.hypot(a[f][0] - b[f][0], a[f][1] - b[f][1]) for f in frames]


def studio_look_items():
    mods = [m.name for fc in look_camera._channelbag(cam).fcurves for m in fc.modifiers if m.name.startswith('StudioLook_')]
    curves = [fc.data_path for fc in look_camera._channelbag(cam).fcurves if fc.data_path.startswith('delta_')]
    groups = [g.name for g in bpy.data.node_groups if g.name.startswith('StudioLook_')]
    props = [k for k in scene.keys() if k.startswith('StudioLook_')]
    return mods + curves + groups + props


base_px, base_m = anchor_px(), matrices()
checks = []

# 1) shake none + labels: anchors must not move (DOF / motion blur only), compositor drops lens.
rep = look_camera.apply_camera_realism(scene, {'shot_type': 'product', 'focus_anchor': 'test/gear', 'shake': 'none',
                                               'motion_blur': True, 'seed': 7}, labels=LABELS)
assert rep['anchored_labels'] and not rep['gate_failures'], rep
assert rep['dof']['use_dof'] and cam.data.dof.use_dof and rep['dof']['subject_worst_coc_px'] <= INV['subject_max_coc_px'], rep['dof']
assert rep['shutter'] > 0 and rep['motion_blur']['anchor_blur_px'] <= INV['anchor_max_blur_px'] + 1e-6, rep['motion_blur']
sampled = [1, 8, 15, 22, 30]
still = max(drift(anchor_px(), base_px, sampled))
assert still <= 0.5, still
comp = look_camera.compositor_setup(scene, 'subtle_nograin')
assert 'lens' in comp['dropped_for_anchored_labels'], comp
assert not any(n.bl_idname == 'CompositorNodeLensdist' for n in scene.compositing_node_group.nodes)
unlabelled = look_camera.compositor_setup(scene, 'subtle_nograin', anchored_labels=False)
assert any(n.bl_idname == 'CompositorNodeLensdist' for n in scene.compositing_node_group.nodes), unlabelled
try:
    look_camera.compositor_setup(scene, 'subtle'); raise AssertionError('grain preset accepted')
except ValueError:
    pass
checks += ['no_shake_anchor_drift_le_0.5px', 'labels_drop_lens_distortion', 'grain_rejected', 'dof_coc', 'anchor_blur']

# 2) handheld_light + labels: anchor jitter within the RMS invariant; deterministic per seed.
look_camera.revert_camera_realism(scene)
realism = {'shot_type': 'product', 'focus_anchor': 'test/gear', 'shake': 'handheld_light', 'motion_blur': True, 'seed': 7}
rep2 = look_camera.apply_camera_realism(scene, realism, labels=LABELS)
assert rep2['shake']['shake'] and not rep2['gate_failures'], rep2
d = drift(anchor_px(), base_px, range(1, 31))
rms = math.sqrt(sum(x * x for x in d) / len(d))
assert 0 < rms <= INV['shake_max_rms_px'] * 1.01, rms
assert rep2['motion_blur']['anchor_blur_px'] <= INV['anchor_max_blur_px'] + 1e-6, rep2['motion_blur']
m1 = matrices()
look_camera.revert_camera_realism(scene)
rep3 = look_camera.apply_camera_realism(scene, realism, labels=LABELS)
assert matrices() == m1 and rep3 == rep2
rep4 = look_camera.apply_camera_realism(scene, {**realism, 'seed': 8}, labels=LABELS)
assert matrices() != m1
checks += ['shake_jitter_within_invariant', 'same_seed_identical_matrices', 'other_seed_differs']

# 3) rig already shook the camera -> no second shake.
scene['studio_camera_rig_shake'] = True
rep5 = look_camera.apply_camera_realism(scene, realism, labels=LABELS)
assert not rep5['shake']['shake'] and matrices() == base_m, rep5['shake']
del scene['studio_camera_rig_shake']
checks.append('rig_shake_skips')

# 4) two-point: verticals vertical, optical axis re-projects.
rep6 = look_camera.apply_camera_realism(scene, {'shot_type': 'interior', 'shake': 'none', 'motion_blur': False}, labels=LABELS)
assert rep6['two_point']['two_point'] and not rep6['gate_failures'], rep6
for f in (1, 15, 30):
    scene.frame_set(f)
    from bpy_extras.object_utils import world_to_camera_view as w2c
    a, b = w2c(scene, cam, Vector((1, 2, 0))), w2c(scene, cam, Vector((1, 2, 2)))
    assert abs((b.x - a.x) * W) < 1e-3, (f, (b.x - a.x) * W)
checks.append('two_point_verticals')

# 5) revert removes every StudioLook_ item and restores the camera exactly.
look_camera.compositor_setup(scene, 'subtle_nograin')
look_camera.revert_camera_realism(scene)
assert not studio_look_items(), studio_look_items()
assert matrices() == base_m and not cam.data.dof.use_dof and cam.data.shift_y == 0
assert not any(fc.data_path in ('shift_y', 'dof.focus_distance') for fc in look_camera._channelbag(cam.data).fcurves) if look_camera._channelbag(cam.data) else True
checks.append('revert_clean')

print('STUDIO_LOOK_CAMERA_SMOKE ' + json.dumps({'ok': True, 'checks': checks, 'no_shake_max_drift_px': round(still, 4),
                                                'shake_rms_px': round(rms, 3), 'shake_report': rep2['shake'], 'dof': rep2['dof'],
                                                'shutter': rep2['shutter'], 'two_point': rep6['two_point']}, sort_keys=True))
