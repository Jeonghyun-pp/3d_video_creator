"""Blender: one rule for what a declared id names (scene_index.resolve_ref) and rays that walk to their target.

A cafe, not a rocket (N+1): five diners built as characters `diners.0..4`, three tables as layout copies `tables.0..2`
(parts tagged `tables-<n>/top`), one counter with a till child and a declared anchor.
- a group id (`diners`, `tables`) names the union of its copies: every object, one world box over all of them, the box
  centre as its point; camera_moves.resolve, camera_frame.sample, the rig's look guard all read the group;
- a single object is unchanged: its origin (scene_tools.anchor_for), its mesh tree, the box camera_moves measured;
  a declared anchor and '<id>/center' stay points; a missing id still raises the same message;
- a frame_to_frame move whose target is the diners group frames the group at both moments, nothing given up;
- a part with nothing to sample is diagnosed with every key the camera search reads (hidden_share);
- a marble inside five nested see-through boxes (two faces each: eleven hits on the way) is seen, measured unblocked,
  and nothing is in front of it; with the boxes opaque it is hidden (2026-10-14: rays gave up after four hits).
Run: blender --background --factory-startup --python tests/studio/refs_smoke.py
"""
from pathlib import Path
import json
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
import camera_frame  # noqa: E402
import camera_frame_core as frame_core  # noqa: E402
import camera_moves  # noqa: E402
import camera_rig  # noqa: E402
import show_scene  # noqa: E402
from scene_index import resolve_ref  # noqa: E402
from scene_tools import anchor_for  # noqa: E402

report = {}


def check(name, cond, detail):
    report[name] = detail
    assert cond, f'{name}: {detail}'


def reset():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)


def box(name, centre, size, ident=None, parent=None, shell=0.0):
    """A box mesh (a closed shell `shell` m thick when given: outer and inner faces), sized in its data."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    for s in ([1.0] + ([1.0 - 2 * shell / max(size)] if shell else [])):
        bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Diagonal((*(v * s for v in size), 1.0)))
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = centre
    if parent is not None:
        obj.parent = parent
        obj.matrix_parent_inverse = parent.matrix_world.inverted()
    if ident:
        obj['studio_id'] = ident
    bpy.context.view_layer.update()
    return obj


def world_box(objs):
    corners = [o.matrix_world @ Vector(c) for o in objs if o.type == 'MESH' for c in o.bound_box]
    return (tuple(min(c[i] for c in corners) for i in range(3)), tuple(max(c[i] for c in corners) for i in range(3)))


def close(a, b, tol=1e-5):
    return all(abs(x - y) <= tol for x, y in zip([v for p in a for v in p], [v for p in b for v in p]))


# --- the cafe ---------------------------------------------------------------------------------------------------
reset()
scene = bpy.context.scene
scene.frame_start, scene.frame_end = 1, 48
box('floor', (0, 0, -0.05), (30, 30, 0.1), ident='floor')
diners = []
for n in range(5):
    body = box(f'diner{n}', (n * 1.2 - 2.4, 1.0, 0.6), (0.4, 0.3, 1.2), ident=f'diners.{n}')
    box(f'diner{n}_head', (n * 1.2 - 2.4, 1.0, 1.35), (0.25, 0.25, 0.25), ident=f'diners.{n}/head', parent=body)
    diners.append(body)
tables = []
for n in range(3):
    top = box(f'table{n}', (n * 2.0 - 2.0, 0.0, 0.75), (0.8, 0.8, 0.05), ident=f'tables-{n}/top')
    top['studio_layout_id'] = f'tables.{n}'
    tables.append(top)
counter = box('counter', (0.0, 4.0, 0.5), (3.0, 0.6, 1.0), ident='counter')
till = box('till', (1.0, 4.0, 1.1), (0.3, 0.3, 0.2), ident='counter/till', parent=counter)
counter.location.x += 0.3   # the origin is no longer the box centre: a single object's point stays its origin
counter['studio_anchors'] = json.dumps({'till_slot': [1.0, 0.0, 0.7], 'tip-tray': [-1.0, 0.0, 0.5]})
bpy.context.view_layer.update()

group = resolve_ref('diners')
tree = [o for d in diners for o in (d, *d.children_recursive)]
check('group_names_every_copy', sorted(o.name for o in group['roots']) == sorted(d.name for d in diners)
      and set(group['objects']) == set(tree), sorted(o.name for o in group['objects']))
check('group_box_is_the_union', close(group['box'], world_box(tree)), group['box'])
check('group_point_is_the_box_centre', close([group['point']], [[(a + b) / 2 for a, b in zip(*group['box'])]]), list(group['point']))
check('layout_copies_union', close(resolve_ref('tables')['box'], world_box(tables)), resolve_ref('tables')['box'])

single = resolve_ref('counter')
check('single_object_point_is_its_origin', close([single['point']], [anchor_for('counter')[1]]), list(single['point']))
check('single_object_tree', single['roots'] == [counter] and single['objects'] == [counter, till], [o.name for o in single['objects']])
check('single_object_box_as_before', close(single['box'], world_box([counter, till])), single['box'])
for anchor in ('till_slot', 'counter/center'):
    r = resolve_ref(anchor)
    check(f'anchor_{anchor}_is_a_point', close([r['point']], [anchor_for(anchor)[1]]) and r['box'] is None and r['objects'] == [],
          list(r['point']))
spelled = resolve_ref('tip_tray')   # the layout spelling of a declared anchor: still a point, never its owner's origin
check('anchor_spelling_stays_a_point', spelled['objects'] == [] and close([spelled['point']], [anchor_for('tip-tray')[1]]), list(spelled['point']))
check('anchor_has_no_axis', camera_moves._axis('till_slot', 'z') is None and camera_moves._axis('counter', 'z') is not None, 'axis')
lamp = box('lamp0', (5.0, 0.0, 2.0), (0.2, 0.2, 0.2), ident='lamps.0')   # a member under a non-member under a member
arm = box('lamp_arm', (5.0, 0.0, 2.5), (0.05, 0.05, 0.5), ident='bracket', parent=lamp)
inner = box('lamp1', (5.0, 0.0, 3.0), (0.2, 0.2, 0.2), ident='lamps.1', parent=arm)
nest = resolve_ref('lamps')
check('nested_member_is_not_a_root', nest['roots'] == [lamp] and nest['objects'] == [lamp, arm, inner], [o.name for o in nest['objects']])
for o in (inner, arm, lamp):
    bpy.data.objects.remove(o)
check('missing_is_none', resolve_ref('waiters') is None, None)
try:
    camera_moves.resolve({'target': 'waiters'})
    raise AssertionError('a missing id must raise')
except ValueError as error:
    check('missing_raises_as_before', str(error) == 'CAMERA_MOVE: reference not found in scene: waiters', str(error))

geo = camera_moves.resolve({'target': 'diners', 'detail': 'counter'})
check('move_reads_the_group', close(geo['boxes']['diners'], group['box']) and close([geo['points']['counter']], [single['point']]), geo['boxes'])
points = camera_frame.sample(['diners'], bpy.context.evaluated_depsgraph_get(), count=400)
xs = sorted({round(p[0] / 1.2 + 2) for p in points})
check('sample_spreads_over_the_group', xs == [0, 1, 2, 3, 4], xs)
guarded = camera_rig._guard_meshes({'guard_target': 'diners'}, None)
check('rig_guard_watches_the_group', set(guarded) == set(tree), len(guarded))

empty = bpy.data.objects.new('ghost', None); scene.collection.objects.link(empty); empty['studio_id'] = 'ghost'
diag = camera_frame.diagnose_part('ghost', ['diners'], (0, -10, 1), (5.0, 0.0), 35.0, (1080, 1920), 1)
check('empty_part_diagnosis_has_every_key', {'hidden_share', 'screen_centre', 'own_shell', 'on_screen'} <= set(diag), diag)
bpy.data.objects.remove(empty)

frames = [{'at': at, 'height_share': h, 'centre': [0.5, 0.5], 'view': {'elevation_deg': 12.0, 'azimuth_deg': az}, 'lens_mm': 35.0}
          for at, h, az in ((0.0, 0.10, 0.0), (1.0, 0.14, 15.0))]   # the row fits the portrait frame's width
job = {'fps': 24, 'output_size': [1080, 1920], 'shot': {'shot_id': 'cafe', 'duration_frames': 48, 'actions': [],
       'camera': {'move': {'type': 'frame_to_frame', 'params': {'target': 'diners', 'follow': 0.0}, 'frames': frames,
                           'timing': {'profile': 'ease_in_out'}}}}}
# a group target is framed whole: no smaller than the build check allows (SIZE_SMALLEST_FLOOR of the drawn height),
# nothing given up of the picture's intent
from_targets = camera_moves.core.SIZE_SMALLEST_FLOOR   # = frame_targets.HEIGHT_RATIO[0] (a host module)
_rig, moved = camera_moves.compile_move(job)
for row, want in zip(moved['frames'], frames):
    achieved = (row.get('seen') or row['achieved'])['height_share']
    check(f"group_target_intent_at_{row['at']}", achieved >= want['height_share'] * from_targets and not row.get('departed'),
          {'achieved': achieved, 'want': want['height_share'], 'departed': row.get('departed'), 'mode': row.get('mode')})

# --- the nested marble ---------------------------------------------------------------------------------------------
reset()
marble = box('marble', (0, 0, 0), (0.2, 0.2, 0.2), ident='marble')
boxes = [box(f'case{n}', (0, 0, 0), (0.6 + 0.4 * n,) * 3, ident=f'case{n}', shell=0.03) for n in range(5)]
depsgraph = bpy.context.evaluated_depsgraph_get()
points = camera_frame.sample(['marble'], depsgraph, count=200)
eye = (0.0, -6.0, 0.4)
check('opaque_cases_hide_it', not camera_frame.visible(points, depsgraph)(eye), 'visible')
for b in boxes:
    b[camera_frame.SEE_WINDOW] = [0, 10 ** 6]
seen = camera_frame.visible(points, depsgraph)(eye)
check('see_through_cases_show_it', len(seen) > len(points) * 0.3, f'{len(seen)}/{len(points)}')
blocked = camera_frame.blocked_share(['marble'], ['marble'], eye, (0, 0, 0), 35.0, 1080 / 1920, 1)
check('nothing_blocks_it', blocked == 0.0, blocked)
diag = camera_frame.diagnose_part('marble', ['marble'], eye, frame_core.view_of(eye, (0, 0, 0)), 35.0, (1080, 1920), 1)
check('diagnosis_sees_it', diag['hidden_share'] == 0.0, diag)
check('nothing_in_front', show_scene.in_front(['marble'], (0, -1, 0), 1, 6.0) == {}, 'in front')
print('REFS_SMOKE ' + json.dumps(report, default=str)[:3000])
