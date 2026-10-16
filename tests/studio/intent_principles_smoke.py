"""Blender: an intent camera keeps its picture's layout (camera_moves_core.picture_terms, framed_pose's width fit).
A barge by a buoy and a gripper on an arm, not the reel the rules came from (N+1):
- barge: drawn whole, side-on, its width 0.8 of the frame, the buoy left of it: placed by its height alone it ran off
  both sides (a stripe); now the whole hull is in the frame, no edge cut, the buoy still on its left, nothing said;
- walled barge: tall quay walls along both sides leave only its ends free: whatever is chosen, seeing it down its
  length is said (FRAME_INTENT_DEPARTED), never silent;
- gripper: a close-up whose numbers put the gripper above the frame: the camera aims toward it and keeps the arm it
  belongs to in the frame (the owner rule), or says how little of it shows.
Run: blender --background --factory-startup --python tests/studio/intent_principles_smoke.py
"""
from pathlib import Path
import json
import sys

import bmesh
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
import camera_frame_core as frame_core  # noqa: E402
import camera_moves  # noqa: E402

report = {}


def check(name, cond, detail):
    report[name] = detail
    assert cond, f'{name}: {detail}'


def reset():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)


def box(ident, centre, size, role=None):
    mesh = bpy.data.meshes.new(ident)
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0); bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new(ident, mesh); bpy.context.scene.collection.objects.link(obj)
    obj.location, obj.scale = centre, size
    bpy.context.view_layer.update()
    bpy.ops.object.select_all(action='DESELECT'); obj.select_set(True); bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj['studio_id'] = ident
    if role:
        obj['studio_scene_role'] = role
    return obj


def job(target, frames, layout, details=()):
    move = {'type': 'frame_to_frame', 'params': {'target': target, 'follow': 0.0, 'solve': 'intent'}, 'frames': frames}
    return {'shot_id': 's', 'fps': 30, 'output_size': [1080, 1920], 'exposure': {}, 'picture_layout': layout,
            'shot': {'shot_id': 's', 'duration_frames': 48, 'actions': [], 'screen': {'details': list(details)},
                     'camera': {'move': move}}}


def moment(at, concept, height, elevation=0.0, azimuth=0.0, lens=24.0, **more):
    return {'at': at, 'height_share': height, 'centre': [0.5, 0.5], 'view': {'elevation_deg': elevation, 'azimuth_deg': azimuth},
            'lens_mm': lens, 'cut_edges': [], 'picture': {'concept_id': concept, 'sha256': 'x'}, **more}


def solve(j, at):
    move = j['shot']['camera']['move']
    ctx = camera_moves.frame_context(j, move)
    fr = next(f for f in move['frames'] if f['at'] == at)
    _key, row, warnings = camera_moves.solve_frame(j, move, fr, ctx)
    return row, warnings


ASPECT = 1080 / 1920
BARGE_LAYOUT = {'subject': {'box': [0.1, 0.45, 0.9, 0.57], 'cut_edges': []},
                'context': [{'name': 'buoy', 'ids': ['buoy'], 'box': [0.02, 0.5, 0.07, 0.56]}], 'unnamed': []}


def barge_scene(walled=False):
    reset()
    box('water', (0, 0, -0.5), (400, 400, 1.0), role='support')
    box('barge', (0, 0, 1.0), (40.0, 4.0, 2.0))
    box('buoy', (-23.0, 0.0, 1.0), (1.5, 1.5, 2.0))
    if walled:   # quay walls along both long sides: only the ends are free
        box('quay_n', (0, 9.0, 15.0), (120.0, 1.0, 30.0))
        box('quay_s', (0, -9.0, 15.0), (120.0, 1.0, 30.0))


barge_scene()
frames = [moment(0.0, 'c1', 0.12, 8.0, width_share=0.8), moment(1.0, 'c2', 0.12, 8.0, width_share=0.8)]
row, warnings = solve(job('barge', frames, {'c1': BARGE_LAYOUT, 'c2': BARGE_LAYOUT}), 0.0)
seen = row['probe_at_pose']
check('barge_whole_in_frame', seen['cut_sides'] == [] and seen['seen_box'][2] - seen['seen_box'][0] <= 0.95,
      {'seen': seen, 'view': row['view'], 'lens': row['lens_mm']})
eye = row['position']
buoy = frame_core.screen_box(((-23.75, -0.75, 0.0), (-22.25, 0.75, 2.0)), eye, row['view'], row['lens_mm'], ASPECT, row.get('roll_deg', 0.0))
check('buoy_left_of_the_barge', buoy is not None and 0.0 <= (buoy[0] + buoy[2]) / 2 < (seen['seen_box'][0] + seen['seen_box'][2]) / 2,
      {'buoy': buoy, 'barge': seen['seen_box']})
check('nothing_said', not row.get('departed') and row['picture_terms'].get('cut_edge', 0) == 0 and not row['picture_terms'].get('layout_flip'),
      {'departed': row.get('departed'), 'terms': row['picture_terms']})

barge_scene(walled=True)
row, warnings = solve(job('barge', frames, {'c1': BARGE_LAYOUT, 'c2': BARGE_LAYOUT}), 0.0)
said = ' '.join(row.get('departed') or [])
check('end_on_is_said', 'its end' in said or 'turned' in said, {'departed': row.get('departed'), 'view': row['view'], 'terms': row['picture_terms']})

# a gripper on a tall arm: the picture's numbers (a close-up centred low) put the gripper above the frame
reset()
box('floor', (0, 0, -0.05), (40, 40, 0.1), role='support')
box('arm', (0, 0, 1.6), (0.3, 0.3, 3.2))
box('gripper', (0, 0, 3.35), (0.5, 0.5, 0.3))
arm_layout = {'subject': {'box': [0.35, 0.05, 0.65, 1.0], 'cut_edges': ['bottom']}, 'context': [], 'unnamed': []}
gripper = [moment(0.0, 'g1', 1.6, 5.0, 0.0, 35.0, centre=[0.5, 0.75], cut_edges=['bottom'], must_see=['gripper']),
           moment(1.0, 'g2', 1.6, 5.0, 0.0, 35.0, centre=[0.5, 0.75], cut_edges=['bottom'], must_see=['gripper'])]
row, warnings = solve(job('arm', gripper, {'g1': arm_layout, 'g2': arm_layout}, details=[{'id': 'gripper', 'min_px': 32, 'why': 'x'}]), 0.0)
short = row['picture_terms'].get('owner_short', 0)
said = ' '.join(row.get('departed') or [])
check('the_arm_stays_with_its_gripper', short == 0 or 'the subject shows' in said,
      {'placement': row.get('placement'), 'terms': row['picture_terms'], 'departed': row.get('departed'), 'seen': row['probe_at_pose']})
check('gripper_shown', row['must_see_px']['gripper'] >= 32, row['must_see_px'])
print('INTENT_PRINCIPLES_SMOKE ' + json.dumps(report, default=str)[:3000])
