"""Blender: what a subject stands on (scene_roles 'support') - the camera stays above it, the subject never sinks into it.

A robot arm at the edge of a raised work platform (N+1). The platform is tagged support; the arm's
gripper reaches down past the edge toward a conveyor, so it shows from under the platform too.
- a picture from under the platform top: the solve reaches a pose there (the gripper shows past the edge) and it is
  refused, FRAME_BELOW_SUPPORT - never lifted onto the platform (2026-10-14: a 24 mm step went under the support);
- a picture from above: the camera stands at least the support margin over the platform top; the margin follows the
  framed subject's size (a 0.2 m product on a bench from a low 30 degree picture is accepted);
- the floor search: under / on / beside the platform, and a support above the subject is its ceiling (a street over a
  station hall), not its floor; a camera inside the platform slab is kept out with no guards declared;
- the arm swung 0.1 m into the platform during the shot fails SUBJECT_SINKS with the depth and frame; 0.01 m passes; a
  crate shown as a collection instance lowered into it fails too; a building placed once with its basement below a
  support ground is not judged (a subject the shot moves must not move into its support);
- an earth shell (environment_shell) around an underground room, a street slab (support) over it: a camera inside the
  room is not refused;
- engine-made ground carries the role: the hall kit's floor, the backdrop relation's support.
Run: blender --background --factory-startup --python tests/studio/support_smoke.py
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
import generate  # noqa: E402
import scene_roles  # noqa: E402

report = {}
TOP = 0.75   # the platform top


def check(name, cond, detail):
    report[name] = detail
    assert cond, f'{name}: {detail}'


def reset():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    bpy.context.scene['studio_gate_severity'] = '{}'


def box(name, lo, hi, ident=None, role=None, parent=None):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation((Vector(lo) + Vector(hi)) / 2)
                          @ Matrix.Diagonal((*(h - l for l, h in zip(lo, hi)), 1.0)))
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    if parent is not None:
        obj.parent = parent
    obj['studio_id'] = ident or name
    if role:
        obj[scene_roles.TAG] = role
    bpy.context.view_layer.update()
    return obj


def arm_on_platform():
    """The platform (support) spans x -4..0.5, y -4..4; the arm stands near its +x edge, origin at its base's bottom."""
    reset()
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 24
    platform = box('platform', (-4.0, -4.0, TOP - 0.04), (0.5, 4.0, TOP), ident='platform', role='support')
    root = bpy.data.objects.new('arm', None)
    scene.collection.objects.link(root)
    root['studio_id'] = 'arm'
    root.location = (0.25, 0.0, TOP)
    bpy.context.view_layer.update()
    # parts in the root's frame (z 0 = the platform top): base, column, forearm out past the edge, gripper hanging down
    for name, lo, hi in (('base', (-0.15, -0.15, 0.0), (0.15, 0.15, 0.1)), ('column', (-0.05, -0.05, 0.1), (0.05, 0.05, 0.5)),
                         ('forearm', (-0.05, -0.05, 0.45), (0.85, 0.05, 0.53)), ('gripper', (0.78, -0.04, -0.3), (0.85, 0.04, 0.45))):
        part = box(f'arm_{name}', lo, hi, ident=f'arm/{name}', parent=root)
    bpy.context.view_layer.update()
    return platform, root


def frames_job(elevation, azimuth=-90.0, height_share=0.35, frames=24):
    pictures = [{'at': at, 'height_share': height_share, 'centre': [0.5, 0.5], 'lens_mm': 35.0,
                 'view': {'elevation_deg': elevation, 'azimuth_deg': azimuth}} for at in (0.0, 1.0)]
    return {'fps': 24, 'output_size': [1080, 1920], 'probe': {'subjects': ['arm']},
            'shot': {'shot_id': 'cell', 'duration_frames': frames, 'actions': [],
                     'camera': {'move': {'type': 'frame_to_frame', 'params': {'target': 'arm', 'follow': 0.0, 'pace': 'timing'},
                                         'frames': pictures, 'timing': {'profile': 'ease_in_out'}}}}}


# --- the camera under the platform is refused, never lifted ----------------------------------------------------------
platform, arm = arm_on_platform()
margin = camera_moves._support_margin(camera_moves.resolve({'target': 'arm'})['boxes']['arm'])
# exact, asked for by name: the search reaches the pose under the platform and it is refused, never lifted
exact = frames_job(-25.0)
exact['shot']['camera']['move']['params']['solve'] = 'exact'
try:
    _rig, moved = camera_moves.compile_move(exact)
    raise AssertionError('a pose under the platform must be refused: ' + json.dumps([r['position'] for r in moved['frames']]))
except ValueError as error:
    check('under_the_platform_is_refused', str(error).startswith('FRAME_BELOW_SUPPORT'), str(error)[:400])
# the default (intent) also looks round the platform: no pose is ever under it, nor within the support margin of its
# top over its footprint - refused FRAME_BELOW_SUPPORT, or solved from a pose clear of it
try:
    _rig, moved = camera_moves.compile_move(frames_job(-25.0))
    under = [r['position'] for r in moved['frames'] if -4.0 <= r['position'][0] <= 0.5 and -4.0 <= r['position'][1] <= 4.0
             and r['position'][2] < TOP + margin - 1e-6]
    check('intent_never_under_the_platform', not under, {'under': under, 'poses': [r['position'] for r in moved['frames']]})
except ValueError as error:
    check('intent_never_under_the_platform', str(error).startswith('FRAME_BELOW_SUPPORT'), str(error)[:400])

# --- a picture from above: the camera stays SUPPORT_MARGIN_M over the platform top -----------------------------------
_rig, moved = camera_moves.compile_move(frames_job(25.0))
for row in moved['frames']:
    x, y, z = row['position']
    over = -4.0 <= x <= 0.5 and -4.0 <= y <= 4.0
    margin = camera_moves._support_margin(camera_moves.resolve({'target': 'arm'})['boxes']['arm'])
    check(f"above_view_{row['at']}_stays_over_the_top", not over or z >= TOP + margin - 1e-6,
          {'position': row['position'], 'lens_rejected': row['lens_rejected']})

# --- the floor under a position ---------------------------------------------------------------------------------------
depsgraph = bpy.context.evaluated_depsgraph_get()
mine = {o.name for o in camera_frame.named_objects('arm')}
floor = camera_frame.support_floor(depsgraph, bpy.context.scene, frozenset(mine))
check('floor_under_the_platform', abs(floor((-1.0, 0.0, 0.3), 1.0) - TOP) < 1e-4, floor((-1.0, 0.0, 0.3), 1.0))
check('floor_over_the_platform', abs(floor((-1.0, 0.0, 3.0)) - TOP) < 1e-4, floor((-1.0, 0.0, 3.0)))
check('nothing_beside_it', floor((2.0, 0.0, 0.3)) is None, floor((2.0, 0.0, 0.3)))
check('a_support_over_the_search_is_a_ceiling', floor((-1.0, 0.0, 0.3), 0.5) is None, floor((-1.0, 0.0, 0.3), 0.5))
below = camera_moves._below_support(floor, (-1.0, 0.0, 0.3), 0.9, 0.5)
check('below_says_so', below and below.startswith('FRAME_BELOW_SUPPORT'), below)
check('just_over_the_margin_is_fine', camera_moves._below_support(floor, (-1.0, 0.0, TOP + 0.51), 0.9, 0.5) is None, 'ok')
check('no_support_no_rule', camera_frame.support_floor(depsgraph, bpy.context.scene, frozenset(mine | {'platform'})) is None, 'None')
inside = camera_moves._keep_out([], 1, (-1.0, 0.0, TOP - 0.02), 0.0)
check('inside_the_slab_is_kept_out_without_guards', inside and 'platform' in inside, inside)

# --- a 0.2 m product on a bench from a low 30 degree picture: the margin follows the subject's size ----------------------
reset()
bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 24
box('bench', (-1.5, -1.0, 0.86), (1.5, 1.0, 0.9), role='support')
box('product', (-0.06, -0.06, 0.9), (0.06, 0.06, 1.1), ident='product')
job = frames_job(30.0, azimuth=0.0, height_share=0.5)
job['shot']['camera']['move']['params']['target'] = 'product'
_rig, moved = camera_moves.compile_move(job)
for row in moved['frames']:
    over = row['position'][2] - 0.9
    check(f"small_product_low_view_{row['at']}", 0.05 - 1e-6 <= over < camera_moves.SUPPORT_MARGIN_M and not row['lens_rejected'],
          {'over_bench_m': round(over, 3), 'lens_rejected': row['lens_rejected']})

# --- SUBJECT_SINKS: a subject the shot moves never moves into its support ----------------------------------------------
def tilted(dip, severity='{}'):
    """The arm keyed upright at frame 1 and swung about its base centre by `dip` m at the base corner on frame 12."""
    platform, arm = arm_on_platform()
    bpy.context.scene['studio_gate_severity'] = severity
    arm.rotation_euler = (0.0, 0.0, 0.0)
    arm.keyframe_insert('rotation_euler', frame=1)
    arm.rotation_euler = (0.0, math.asin(dip / 0.15), 0.0)   # +y rotation lowers the +x corner of the 0.30 m base
    arm.keyframe_insert('rotation_euler', frame=12)
    bpy.context.scene.frame_set(1)
    return generate._sinks(frames_job(25.0))


sunk = tilted(0.10)
deepest = sunk['deepest']
check('sinks_fails', sunk['gate_failures'] and sunk['gate_failures'][0]['gate'] == 'SUBJECT_SINKS'
      and abs(deepest['depth_m'] - 0.10) < 0.005 and deepest['frame'] == 11 and deepest['support'] == 'platform', sunk)
check('sinks_says_depth_and_frame', '0.100 m into platform at shot frame 11' in sunk['gate_failures'][0]['why'], sunk['gate_failures'][0]['why'])
touch = tilted(0.01)
check('a_touch_passes', not touch['gate_failures'] and abs(touch['deepest']['depth_m'] - 0.01) < 0.002, touch)
soft = tilted(0.10, json.dumps({'SUBJECT_SINKS': 'warn'}))
check('policy_softens_it', not soft['gate_failures'] and soft['warnings'] and 'SUBJECT_SINKS' in soft['warnings'][0], soft)

# a cutaway building whose basement goes below the ground (a support), placed once: built that way, not sunk
reset()
bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 24
box('ground', (-50.0, -50.0, -0.5), (50.0, 50.0, 0.0), role='support')
box('tower', (-8.0, -8.0, -10.0), (8.0, 8.0, 20.0), ident='tower')
basement = generate._sinks({**frames_job(25.0), 'probe': {'subjects': ['tower']}})
check('static_basement_is_not_sunk', basement['gate_failures'] == [] and basement['moved'] == [] and basement['deepest'] is None, basement)

# a crate shown as a collection instance, lowered 0.1 m into the platform during the shot
reset()
bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 24
box('platform', (-4.0, -4.0, TOP - 0.04), (0.5, 4.0, TOP), role='support')
crates = bpy.data.collections.new('crates')
bpy.context.scene.collection.children.link(crates)
source = box('crate_src', (-0.2, -0.2, 0.0), (0.2, 0.2, 0.4))
bpy.context.scene.collection.objects.unlink(source); crates.objects.link(source)
bpy.context.view_layer.layer_collection.children['crates'].exclude = True
crate = bpy.data.objects.new('crate', None)
crate.instance_type, crate.instance_collection = 'COLLECTION', crates
bpy.context.scene.collection.objects.link(crate)
crate['studio_id'] = 'crate'
crate.location = (-1.0, 0.0, TOP); crate.keyframe_insert('location', frame=1)
crate.location = (-1.0, 0.0, TOP - 0.1); crate.keyframe_insert('location', frame=20)
bpy.context.scene.frame_set(1)
dropped = generate._sinks({**frames_job(25.0), 'probe': {'subjects': ['crate']}})
check('instanced_subject_sinks', dropped['gate_failures'] and dropped['deepest']['object'] == 'crate'
      and abs(dropped['deepest']['depth_m'] - 0.1) < 0.005, dropped)

# --- an underground room inside an earth shell, a street slab over it ----------------------------------------------------
reset()
scene = bpy.context.scene
scene.frame_start, scene.frame_end = 1, 24
lo, hi = (-12.0, -30.0, -20.0), (12.0, 30.0, -12.0)   # the room
for name, a, b in (('earth_l', (-40, -30, -60), (lo[0], 30, -0.4)), ('earth_r', (hi[0], -30, -60), (40, 30, -0.4)),
                   ('earth_b', (lo[0], -30, -60), (hi[0], 30, lo[2] - 0.4)), ('earth_t', (lo[0], -30, hi[2]), (hi[0], 30, -0.4)),
                   ('earth_back', (lo[0], hi[1], lo[2]), (hi[0], 40, hi[2]))):
    box(name, a, b, role='environment_shell')
box('room_floor', (lo[0], lo[1], lo[2] - 0.4), (hi[0], hi[1], lo[2]), role='support')
box('street', (-40.0, -30.0, -0.4), (40.0, 30.0, 0.0), role='support')
box('train', (-1.5, -10.0, lo[2]), (1.5, 10.0, lo[2] + 3.8), ident='train')
job = frames_job(10.0, azimuth=30.0, height_share=0.3)
job['shot']['camera']['move']['params']['target'] = 'train'
job['probe'] = {'subjects': ['train']}
_rig, moved = camera_moves.compile_move(job)
for row in moved['frames']:
    check(f"inside_the_shell_{row['at']}", lo[2] + 0.5 <= row['position'][2] < hi[2] and not any(
        r['code'] == 'FRAME_BELOW_SUPPORT' for r in row['lens_rejected']), {'position': row['position'], 'rejected': row['lens_rejected']})
check('train_stands_on_its_floor', generate._sinks(job)['gate_failures'] == [], generate._sinks(job))
middle = (lo[2] + lo[2] + 3.8) / 2
train = {o.name for o in camera_frame.named_objects('train')}
check('keep_out_street_is_a_ceiling', camera_moves._keep_out([], 1, (0.0, 0.0, -0.2), 0.0, middle, train) is None,
      camera_moves._keep_out([], 1, (0.0, 0.0, -0.2), 0.0, middle, train))
check('keep_out_street_without_a_subject', 'street' in (camera_moves._keep_out([], 1, (0.0, 0.0, -0.2), 0.0) or ''), 'street')

# --- engine-made ground carries the role --------------------------------------------------------------------------------
reset()
import env_kits_space  # noqa: E402
env_kits_space.hall('hall', [[0, 0, 0], [16, 16, 6]], library_root=str(ROOT / 'library'), light_rows=False)
check('hall_floor_is_a_support', scene_roles.role(bpy.data.objects['hall.floor']) == 'support', scene_roles.role(bpy.data.objects['hall.floor']))
check('hall_ceiling_is_not', scene_roles.role(bpy.data.objects['hall.ceiling']) == 'object', 'object')
import layout  # noqa: E402
reset()
box('gear', (-0.1, -0.1, 0.0), (0.1, 0.1, 0.2))['studio_subject_id'] = 'gear'
made = layout._support({'kind': 'bench'}, {}, {}, False, str(ROOT / 'library'))
check('backdrop_support_is_a_support', scene_roles.role(made) == 'support', scene_roles.role(made))
print('SUPPORT_SMOKE ' + json.dumps(report, default=str)[:4000])
