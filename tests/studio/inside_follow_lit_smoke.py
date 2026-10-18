"""Blender: three rules of a pose and a picture's facts, on a buoy moored in a harbour, not the reel the rules came from (N+1).
- the camera inside the subject (camera_moves._inside_subject, _refusal): a camera up inside the buoy's skirt (a hollow
  open at the bottom, its faces single-sided and pointing inward) is refused CAMERA_IN_SUBJECT whichever way the faces
  point; one close outside a face is not; the moment's show opening the subject (interior) never asks; softened by
  the project's policy it is allowed;
- a pose carried with its subject (camera_moves.subject_pose, core.follow_subject): the buoy leaned 20 deg and moved
  since the pose was recorded - the camera stands where it stood relative to the buoy, and still looks at the lamp;
- what lights a part (picture_passes._glows, _lighters): a flame made of a volume's blackbody glow lights, a lamp object
  lights, a scattering fog box does not.
Run: blender --background --factory-startup --python tests/studio/inside_follow_lit_smoke.py
"""
from pathlib import Path
import json
import math
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'studio/blender_ops'))
import camera_moves  # noqa: E402
import picture_passes  # noqa: E402
import scene_roles  # noqa: E402

report = {}


def check(name, cond, detail):
    report[name] = detail
    assert cond, f'{name}: {detail}'


for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj)
scene = bpy.context.scene
scene['studio_gate_severity'] = '{}'
scene.frame_start, scene.frame_end = 1, 24


def skirt(name, flip):
    """A 2 m box with no bottom face: a hollow open below. Faces single-sided, pointing in when `flip`."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bottom = [f for f in bm.faces if f.normal.z < -0.9]
    bmesh.ops.delete(bm, geom=bottom, context='FACES_ONLY')
    if flip:
        bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    obj['studio_subject_id'] = 'buoy'
    return obj


root = bpy.data.objects.new('buoy_root', None)
scene.collection.objects.link(root)
root['studio_subject_id'] = 'buoy'
body = skirt('buoy_body', flip=True)
body.parent = root
body.location = (0.0, 0.0, 2.0)
lamp = bpy.data.objects.new('buoy_lamp', bpy.data.meshes.new('lamp_mesh'))
scene.collection.objects.link(lamp)
lamp['studio_subject_id'] = 'buoy'
lamp.parent = root
lamp.location = (0.0, 0.0, 3.4)
bpy.context.view_layer.update()

# --- the camera inside the subject
mine = {'buoy_body'}
inside = camera_moves._inside_subject(1, (0.0, 0.0, 2.5), mine, {})
check('inside_the_skirt_refused', inside is not None and inside.startswith('CAMERA_IN_SUBJECT'), inside)
outside = camera_moves._inside_subject(1, (1.3, 0.0, 2.0), mine, {})   # 0.3 m off a face whose normal points away
check('close_outside_a_face_allowed', outside is None, outside)
box = ((-1.0, -1.0, 1.0), (1.0, 1.0, 3.0))
exterior = {'show': {'how': 'exterior', 'parts': []}}
interior = {'show': {'how': 'interior', 'parts': []}}
refused = camera_moves._refusal(0, (0.0, 0.0, 2.5), box, 2.0, None, [], 0.0, mine, {}, exterior)
check('exterior_moment_refuses', refused is not None and refused[0] == 'CAMERA_IN_SUBJECT', refused)
check('interior_moment_never_asks', camera_moves._refusal(0, (0.0, 0.0, 2.5), box, 2.0, None, [], 0.0, mine, {}, interior) is None, 'interior')
scene['studio_gate_severity'] = json.dumps({'CAMERA_IN_SUBJECT': 'warn'})
check('softened_allowed', camera_moves._refusal(0, (0.0, 0.0, 2.5), box, 2.0, None, [], 0.0, mine, {}, exterior) is None, 'warn')
scene['studio_gate_severity'] = '{}'
body2 = skirt('buoy_body_out', flip=False)   # the same hollow with its faces pointing out
body2.location = (10.0, 0.0, 2.0)
bpy.context.view_layer.update()
inside2 = camera_moves._inside_subject(1, (10.0, 0.0, 2.5), {'buoy_body_out'}, {})
check('inside_whichever_way_faces_point', inside2 is not None, inside2)
bpy.data.objects.remove(body2)

# --- a pose carried with its subject
then = camera_moves.subject_pose('buoy', 1)
eye, aim = (6.0, -4.0, 4.0), tuple(lamp.matrix_world.translation)
root.rotation_euler = (math.radians(20.0), 0.0, math.radians(35.0))
root.location = (3.0, 2.0, -0.5)
bpy.context.view_layer.update()
now = camera_moves.subject_pose('buoy', 1)
eye2, aim2 = camera_moves.core.follow_subject(eye, aim, [0, 0, 2], [0, 0, 2], then, now)
inv_then = Matrix([then[i * 4:i * 4 + 4] for i in range(4)]).inverted()
inv_now = root.matrix_world.inverted()
rel_then, rel_now = inv_then @ Vector(eye), inv_now @ Vector(eye2)
check('eye_kept_relative_to_the_leaned_buoy', (rel_then - rel_now).length < 1e-4, [list(rel_then), list(rel_now)])
check('still_looks_at_the_lamp', (Vector(aim2) - lamp.matrix_world.translation).length < 1e-4, list(aim2))
# its roll rides too (core.follow_roll): the camera's up, as the built camera turns it, stays where it was on the buoy
import camera_rig_core  # noqa: E402
def camera_up(e, a, roll_deg):
    return Vector(camera_rig_core.q_rotate(camera_rig_core.look_rotation(e, a, math.radians(roll_deg)), (0.0, 1.0, 0.0)))
roll2 = camera_moves.core.follow_roll(eye, aim, 8.0, then, now)
up_then, up_now = inv_then.to_3x3() @ camera_up(eye, aim, 8.0), inv_now.to_3x3() @ camera_up(eye2, aim2, roll2)
check('camera_up_kept_relative_to_the_leaned_buoy', (up_then.normalized() - up_now.normalized()).length < 1e-4,
      [list(up_then), list(up_now), roll2])
eye3, _aim3 = camera_moves.core.follow_subject(eye, aim, [0, 0, 2], [3.0, 2.0, 1.5])   # an older record: the centre only
check('older_record_slides_with_its_centre', (Vector(eye3) - Vector((9.0, -2.0, 3.5))).length < 1e-6, list(eye3))

# --- what lights a part
def volume_box(name, centre, size, glow):
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Diagonal((*size, 1.0)))
    bm.to_mesh(obj.data); bm.free()
    obj.location = centre
    scene.collection.objects.link(obj)
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.remove(nodes['Principled BSDF'])
    vol = nodes.new('ShaderNodeVolumePrincipled')
    vol.inputs['Density'].default_value = 0.5
    vol.inputs['Blackbody Intensity'].default_value = 1.0 if glow else 0.0
    vol.inputs['Emission Strength'].default_value = 0.0
    mat.node_tree.links.new(vol.outputs[0], nodes['Material Output'].inputs['Volume'])
    obj.data.materials.append(mat)
    return obj


flame = volume_box('flare', (0.0, 0.0, 4.2), (0.3, 0.3, 1.2), glow=True)
fog = volume_box('harbour_fog', (0.0, 0.0, 5.0), (200.0, 200.0, 20.0), glow=False)
fog[scene_roles.TAG] = 'atmosphere'   # a fog by role: it scatters, it does not light
check('blackbody_flame_glows', picture_passes._glows(flame), 'flare')
check('scattering_fog_does_not', not picture_passes._glows(fog), 'fog')
light = bpy.data.objects.new('mast_light', bpy.data.lights.new('mast_light', 'POINT'))
light.data.energy = 50.0
light.location = (0.0, 0.0, 3.6)
scene.collection.objects.link(light)
dark = bpy.data.objects.new('spare_light', bpy.data.lights.new('spare_light', 'POINT'))
dark.data.energy = 0.0
scene.collection.objects.link(dark)
bpy.context.view_layer.update()
lighters = picture_passes._lighters(scene, scene_roles)
check('flame_and_lamp_light_fog_and_dark_lamp_do_not', 'flare' in lighters and 'mast_light' in lighters
      and 'harbour_fog' not in lighters and 'spare_light' not in lighters, sorted(lighters))

print('INSIDE_FOLLOW_LIT_SMOKE ' + json.dumps(report, default=str))
