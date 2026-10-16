"""Blender: fog, plume and beam volumes (scene_roles 'atmosphere') block no ray, and the camera never stands in one (A5).

A hydraulic press on a factory floor, a column of steam rising between it and the camera, the whole hall hazy (a fog
box round everything), not the reel the check came from (N+1):
- a close picture of the press puts the camera inside the steam: that pose is refused, CAMERA_IN_ATMOSPHERE - the
  camera goes round the column (said turned) or, when every pose meets the steam, the frame fails with that code;
- the press in a clear well walled by steam far past every pose: the frame fails CAMERA_IN_ATMOSPHERE, never
  FRAME_UNSOLVED;
- softened by the project's policy it builds, and the report says so;
- the hall's haze encloses the press too: it is the air the press stands in, and a camera in it is not refused;
- a far picture stands outside the steam: it solves, the press is still judged visible through it (volumes block no
  ray: blocked_share 0), and the finished camera measures how much the steam covers (camera_frame.atmosphere_cover):
  the frame (only nearer than the press: a backdrop card behind it is not over the frame) and the focus behind it;
- a dust cloud (thousands of instanced grains) is the region it fills: round the press it is the press's air and a camera
  in it solves; a cloud standing apart from the press refuses a pose inside its region;
- an open card (one quad) is a surface, not a room: a camera 40 m behind it is not "inside"; a backdrop's reflection
  dome hidden from camera rays is no volume at all;
- a diver turned head-on to the camera: the lean the final camera sees means nothing (sight_deg under 45), side-on it
  matches the picture; a rolled pose baked into the rig reads the same lean as the move's row (camera_moves.measure_built).
Run: blender --background --factory-startup --python tests/studio/atmosphere_smoke.py
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
import camera_frame  # noqa: E402
import camera_moves  # noqa: E402
import camera_rig  # noqa: E402
import scene_roles  # noqa: E402
import simulate  # noqa: E402

report = {}


def check(name, cond, detail):
    report[name] = detail
    assert cond, f'{name}: {detail}'


def reset():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    bpy.context.scene['studio_gate_severity'] = '{}'
    bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 24


def box(name, lo, hi, role=None, rotation=None):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Diagonal((*(h - l for l, h in zip(lo, hi)), 1.0)))
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = (Vector(lo) + Vector(hi)) / 2
    if rotation:
        obj.rotation_euler = rotation
    obj['studio_id'] = name
    if role:
        obj[scene_roles.TAG] = role
    bpy.context.view_layer.update()
    return obj


def factory():
    reset()
    box('floor', (-30, -30, -0.2), (30, 30, 0.0), role='support')
    box('press', (-0.8, -0.6, 0.0), (0.8, 0.6, 3.0))
    box('steam', (-3.0, -7.0, 0.0), (3.0, -2.0, 6.0), role='atmosphere')   # a column between the press and the camera
    box('haze', (-30.0, -30.0, -1.0), (30.0, 30.0, 12.0), role='atmosphere')   # the hall's air, round everything


def job(height_share, elevation=10.0, target='press'):
    pictures = [{'at': at, 'height_share': height_share, 'centre': [0.5, 0.5], 'lens_mm': 35.0,
                 'view': {'elevation_deg': elevation, 'azimuth_deg': 0.0}} for at in (0.0, 1.0)]
    return {'fps': 24, 'output_size': [1080, 1920], 'probe': {'subjects': [target]},
            'shot': {'shot_id': 'press', 'duration_frames': 24, 'actions': [],
                     'camera': {'move': {'type': 'frame_to_frame', 'params': {'target': target, 'follow': 0.0, 'pace': 'timing'},
                                         'frames': pictures, 'timing': {'profile': 'ease_in_out'}}}}}


def look_from(position, aim, roll=0.0):
    """The scene camera at position looking at aim (no rig: what measure_built reads is the scene camera)."""
    cam = bpy.data.objects.get('cam') or bpy.data.objects.new('cam', bpy.data.cameras.new('cam'))
    if cam.name not in bpy.context.scene.collection.objects:
        bpy.context.scene.collection.objects.link(cam)
    cam.location = position
    forward = (Vector(aim) - Vector(position)).normalized()
    cam.rotation_euler = forward.to_track_quat('-Z', 'Y').to_euler()
    cam.rotation_euler.rotate_axis('Z', math.radians(roll))
    cam.data.lens = 35.0
    bpy.context.scene.camera = cam
    bpy.context.scene.render.resolution_x, bpy.context.scene.render.resolution_y = 1080, 1920
    bpy.context.view_layer.update()
    return cam


# --- a close picture: no pose stands in the fog -----------------------------------------------------------------------
factory()
# the camera goes round the column: it never stands in it (nor within ATMOSPHERE_NEAR_M) - refused
# CAMERA_IN_ATMOSPHERE, or solved from outside and said turned
try:
    _rig, moved = camera_moves.compile_move(job(0.9))
    near = camera_moves.ATMOSPHERE_NEAR_M
    inside = [r['position'] for r in moved['frames'] if -3.0 - near <= r['position'][0] <= 3.0 + near
              and -7.0 - near <= r['position'][1] <= -2.0 + near and r['position'][2] <= 6.0 + near]
    check('intent_never_in_the_fog', not inside and all(r.get('departed') for r in moved['frames']),
          {'inside': inside, 'departed': [r.get('departed') for r in moved['frames']]})
except ValueError as error:
    check('intent_never_in_the_fog', str(error).startswith('CAMERA_IN_ATMOSPHERE'), str(error)[:400])

# --- every pose meets the steam: the frame fails with that code ------------------------------------------------------
# the press in a narrow clear well walled by steam on all four sides, far past any pose of a close picture: no view in
# the band, no lens, stands outside it - the steam does not hold the press, so it is not the press's own air
reset()
box('floor', (-60, -60, -0.2), (60, 60, 0.0), role='support')
box('press', (-0.8, -0.6, 0.0), (0.8, 0.6, 3.0))
for name, lo, hi in (('steam_w', (-50.0, -50.0, 0.0), (-1.2, 50.0, 50.0)), ('steam_e', (1.2, -50.0, 0.0), (50.0, 50.0, 50.0)),
                     ('steam_s', (-1.2, -50.0, 0.0), (1.2, -1.0, 50.0)), ('steam_n', (-1.2, 1.0, 0.0), (1.2, 50.0, 50.0))):
    box(name, lo, hi, role='atmosphere')
try:
    _rig, moved = camera_moves.compile_move(job(0.9))
    check('all_in_the_steam_fails_with_that_code', False, [r['position'] for r in moved['frames']])
except ValueError as error:
    check('all_in_the_steam_fails_with_that_code', str(error).startswith('CAMERA_IN_ATMOSPHERE'), str(error)[:400])
factory()

# --- softened by the project: it builds and says so ------------------------------------------------------------------
bpy.context.scene['studio_gate_severity'] = json.dumps({'CAMERA_IN_ATMOSPHERE': 'warn'})
_rig, soft = camera_moves.compile_move(job(0.9))
check('softened_builds_and_warns', any(w.startswith('CAMERA_IN_ATMOSPHERE (warning by policy)') for w in soft['warnings'])
      and all(r.get('in_atmosphere') for r in soft['frames']), soft['warnings'][:3])
bpy.context.scene['studio_gate_severity'] = '{}'

# --- the near-surface rule, the hall's air, and outside the steam nothing is said ------------------------------------
press = (0.0, 0.0, 1.5)
near = camera_moves._in_atmosphere(1, (0.0, -7.6, 1.0), press)
check('within_a_metre_of_the_steam_is_in_it', near and 'keeps' in near and 'steam' in near, near)
inside = camera_moves._in_atmosphere(1, (0.0, -4.0, 1.0), press)
check('inside_the_steam_is_in_it', 'inside' in (inside or '') and 'steam' in inside, inside)
check('in_the_halls_air_is_fine', camera_moves._in_atmosphere(1, (0.0, -9.5, 1.0), press) is None, 'None')

# --- a far picture solves outside the fog; the fog is measured, not a blocker ----------------------------------------
_rig, far = camera_moves.compile_move(job(0.15, elevation=5.0))
for row in far['frames']:
    check(f"far_{row['at']}_outside_the_steam", camera_moves._in_atmosphere(1, row['position'], press) is None and row['blocked_share'] == 0.0,
          {'position': row['position'], 'blocked_share': row['blocked_share']})
look_from(far['frames'][0]['position'], far['frames'][0]['aim'])
camera_moves.measure_built(job(0.15, elevation=5.0), far)
cover = far['frames'][0]['built']['atmosphere']
check('steam_covers_part_of_the_frame', 0.05 < cover['frame_share'] < 0.9, cover)
check('the_press_shows_through_steam', cover['focus_share'] > 0.9, cover)
check('upright_press_side_on', far['frames'][0]['built']['sight_deg'] > 80 and abs(far['frames'][0]['built']['tilt_deg']) < 1,
      far['frames'][0]['built'])
card = box('backdrop_card', (-40.0, 25.0, -5.0), (40.0, 25.1, 40.0), role='atmosphere')   # behind the set
behind = camera_frame.atmosphere_cover(['press'], 1)
check('a_card_behind_the_press_is_not_over_the_frame', behind['frame_share'] == cover['frame_share'], {'with_card': behind, 'without': cover})
for name in ('steam', 'backdrop_card'):
    bpy.data.objects.remove(bpy.data.objects[name])
check('only_the_halls_air_left_no_cover', camera_frame.atmosphere_cover(['press'], 1) is None, 'None')

# --- dust: a cloud of grains is the region it fills -------------------------------------------------------------------
def dusty(region):
    reset()
    box('floor', (-30, -30, -0.2), (30, 30, 0.0), role='support')
    box('press', (-0.8, -0.6, 0.0), (0.8, 0.6, 3.0))
    simulate.dust({'action_id': 'grit', 'start_frame': 1, 'params': {'region': region, 'count': 2000, 'seed': 1}}, 24)


dusty([[-10, -10, 0], [10, 10, 3.5]])                               # round the press: its air
_rig, aired = camera_moves.compile_move(job(0.9))
check('a_camera_in_the_dust_round_the_press_solves', all(not r['lens_rejected'] for r in aired['frames']),
      [r['position'] for r in aired['frames']])
dusty([[-4, -9, 0], [4, -2, 4]])                                    # a cloud between the press and the camera
inside = camera_moves._in_atmosphere(1, (0.0, -5.0, 1.5), press)
check('inside_a_cloud_apart_from_the_press_is_refused', inside and 'inside' in inside, inside)
check('clear_of_the_cloud_is_fine', camera_moves._in_atmosphere(1, (0.0, -12.0, 1.5), press) is None, 'None')

# --- an open card is not a room; a dome hidden from the camera is no volume -----------------------------------------------
factory()
for name in ('steam', 'haze'):
    bpy.data.objects.remove(bpy.data.objects[name])
mesh = bpy.data.meshes.new('card')
mesh.from_pydata([(-10, -60, -2), (10, -60, -2), (10, -60, 18), (-10, -60, 18)], [], [(0, 3, 2, 1)])   # its face looks to -y
card = bpy.data.objects.new('card', mesh)
bpy.context.scene.collection.objects.link(card)
card[scene_roles.TAG] = 'atmosphere'
bpy.context.view_layer.update()
check('behind_an_open_card_is_not_inside', camera_moves._in_atmosphere(1, (0.0, -20.0, 1.5), press) is None,
      camera_moves._in_atmosphere(1, (0.0, -20.0, 1.5), press))
dome = box('dome', (-3.0, -3.0, 0.0), (3.0, -1.0, 3.0), role='atmosphere')
dome.visible_camera = False
check('a_dome_hidden_from_the_camera_is_no_volume', not camera_frame.is_atmosphere(dome)
      and camera_moves._in_atmosphere(1, (0.0, -2.0, 1.5), press) is None, 'None')

# --- a diver: the lean the final camera sees, end-on and side-on --------------------------------------------------------
reset()
diver = box('diver', (-0.2, -0.15, -0.9), (0.2, 0.15, 0.9))          # 1.8 m tall, long along her own z
diver.location = (0.0, 0.0, 5.0)
dive = job(0.4, elevation=0.0, target='diver')
look_from((0.0, -12.0, 5.0), (0.0, 0.0, 5.0))
built = camera_moves.measure_built(dive, {'frames': [{'at': 0.0, 'frame': 0}]})['frames'][0]['built']
check('side_on_diver', built['sight_deg'] > 85 and abs(built['tilt_deg']) < 0.5, built)
diver.rotation_euler = (math.radians(80), 0.0, 0.0)                    # tucked over: her head turned toward the camera
bpy.context.view_layer.update()
built = camera_moves.measure_built(dive, {'frames': [{'at': 0.0, 'frame': 0}]})['frames'][0]['built']
check('end_on_diver', built['sight_deg'] < 45, built)
diver.rotation_euler = (0.0, math.radians(-20), 0.0)                   # leaning in the picture plane
bpy.context.view_layer.update()
built = camera_moves.measure_built(dive, {'frames': [{'at': 0.0, 'frame': 0}]})['frames'][0]['built']
check('leaning_diver_reads_her_lean', abs(abs(built['tilt_deg']) - 20) < 0.5 and built['sight_deg'] > 85, built)


# --- a rolled pose baked into the rig reads the lean the move's row says --------------------------------------------------
reset()
box('diver', (-0.2, -0.15, 4.1), (0.2, 0.15, 5.9))
rolled = job(0.4, elevation=5.0, target='diver')
for fr in rolled['shot']['camera']['move']['frames']:
    fr['tilt_deg'] = 15.0                                              # the picture leans her 15 deg: the camera rolls
rig, moved = camera_moves.compile_move(rolled)
rolled['shot']['camera']['rig'] = rig
camera_rig.bake_camera_rig(rolled)
camera_moves.measure_built(rolled, moved)
for row in moved['frames']:
    check(f"baked_roll_{row['at']}_reads_the_rows_lean", abs(row['built']['tilt_deg'] - row['tilt_deg']) < 0.5 and abs(row['roll_deg']) > 5,
          {'built': row['built'].get('tilt_deg'), 'row': row['tilt_deg'], 'roll': row['roll_deg']})

print(json.dumps({'ok': True, 'checks': report}, default=str))
