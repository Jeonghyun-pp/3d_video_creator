"""Blender: frames check opens what hides a part a moment explains (cheat_table see_enclosure, explode_apart; the
search: frames_check.py; the mechanism: cheat_scene.py) - N+1 scenes, none from the reel the rule came from:
- a crate: a cube inside a closed box, the moment must show the cube - no side shows it; the search makes the box
  see-through (see_enclosure, the cheaper) and the moment solves; the box is not moved.
- a tank capped below, seen from below: its valve sits just over the bottom cap. With the see-through cheats barred (as
  a project may bar them) the search takes the cap apart along the tank's long axis, away from the valve
  (explode_apart), and the moment solves; the cap is back in place afterwards.
- the same cap when the shot's line speaks of it (its state or place): taking it apart is barred (CHEAT_TOUCHES_LOCKED).
- applied for a build at the middle of three moments: the cap is apart at that moment only - keyed back in place at the
  moments beside it - and set aside from the tank's framed box while apart, not from the box the followed point is
  read from (the camera's path does not jump).
- the same tank standing on the ground: the cap taken apart its first way would go into the ground (SUBJECT_SINKS);
  the attempt tries it the other way (a negative value) and is not refused for sinking.
Run: blender --background --factory-startup --python tests/studio/auto_open_smoke.py
"""
from pathlib import Path
import math
import sys
import time

import bmesh
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
import camera_frame  # noqa: E402
import camera_moves  # noqa: E402
import cheat_scene  # noqa: E402
import frames_check  # noqa: E402
from scene_index import apart, resolve_ref, world_box  # noqa: E402
import scene_roles  # noqa: E402

report = {}


def check(name, cond, detail):
    report[name] = detail
    assert cond, f'{name}: {detail}'


def reset():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 48


def tag(obj, ident, parent):
    obj.name = ident
    obj['studio_id'] = ident
    obj['studio_subject_id'], obj['studio_part_id'] = ident.split('/')
    obj.parent = parent
    return obj


def root(name):
    r = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(r)
    r['studio_id'] = r['studio_subject_id'] = name
    return r


def box(ident, centre, size, parent):
    mesh = bpy.data.meshes.new(ident)
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0); bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new(ident, mesh); bpy.context.scene.collection.objects.link(obj)
    obj.location, obj.scale = centre, size
    bpy.context.view_layer.update()
    return tag(obj, ident, parent)


def cylinder(ident, centre, radius, depth, parent, fill='NGON'):
    bpy.ops.mesh.primitive_cylinder_add(vertices=40, radius=radius, depth=depth, location=centre, end_fill_type=fill)
    return tag(bpy.context.active_object, ident, parent)


def moment(at, height, elevation=0.0, azimuth=0.0, lens=35.0, **more):
    return {'at': at, 'height_share': height, 'centre': [0.5, 0.5], 'view': {'elevation_deg': elevation, 'azimuth_deg': azimuth},
            'lens_mm': lens, 'cut_edges': [], **more}


def job(target, frames, locked=None):
    move = {'type': 'frame_to_frame', 'params': {'target': target, 'follow': 0.0}, 'frames': frames}
    return {'shot_id': 's', 'fps': 30, 'output_size': [1080, 1920], 'exposure': {}, 'locked': locked or {},
            'shot': {'shot_id': 's', 'duration_frames': 48, 'actions': [], 'camera': {'move': move},
                     'screen': {'details': [{'id': p, 'min_px': 32} for f in frames for p in f.get('must_see') or []]}}}


def search(j, at=1.0):
    move = j['shot']['camera']['move']
    ctx = camera_moves.frame_context(j, move)
    fr = next(f for f in move['frames'] if f['at'] == at)
    row, failure = frames_check._solve(j, move, fr, ctx)
    if row is not None:
        return {'status': 'as_it_stands', 'row': row}
    return {'as_is': failure, **frames_check.search_intent(j, move, fr, ctx, failure, time.monotonic() + 900)}


# 1. the crate: a cube inside a closed box
reset()
crate = root('crate')
box('crate/box', (0, 0, 1.0), (2.0, 2.0, 2.0), crate)
box('crate/cube', (0, 0, 1.0), (0.7, 0.7, 0.7), crate)
j = job('crate', [moment(0.0, 0.5), moment(1.0, 0.5, must_see=['crate/cube'])])
found = search(j)
check('crate_fails_as_it_stands', (found.get('as_is') or {}).get('code') in ('SHOW_NEEDS_OPENING', 'FRAME_UNSOLVED'), found.get('as_is', {}).get('code'))
kinds = [c['kind'] for c in (found.get('solved_by') or {}).get('cheats') or []]
check('crate_box_made_see_through', found.get('status') == 'cheat' and kinds == ['see_enclosure']
      and found['solved_by']['cheats'][0]['target'] == ['crate/box'], {k: found.get(k) for k in ('status', 'solved_by')} if found.get('status') != 'cheat' else kinds)
check('crate_box_not_moved', tuple(bpy.data.objects['crate/box'].matrix_world.translation) == (0.0, 0.0, 1.0)
      and camera_frame.SEE_WINDOW not in bpy.data.objects['crate/box'], list(bpy.data.objects['crate/box'].matrix_world.translation))


# 2. the tank capped below, seen from below
def tank():
    reset()
    t = root('tank')
    cylinder('tank/tube', (0, 0, 2.0), 0.8, 4.0, t, fill='NOTHING')       # open at both ends
    cylinder('tank/cap', (0, 0, 0.0), 0.82, 0.08, t)                      # the bottom cap
    cylinder('tank/top', (0, 0, 4.0), 0.82, 0.08, t)                      # the top cap
    box('tank/valve', (0, 0, 0.45), (0.5, 0.5, 0.5), t)                   # just over the bottom cap
    box('tank/rail', (0, 0, 3.0), (0.1, 0.1, 1.0), t)                     # high inside: hides nothing below
    return job('tank', [moment(0.0, 0.5, elevation=-10.0), moment(0.5, 0.45, elevation=-35.0, must_see=['tank/valve']),
                        moment(1.0, 0.5, elevation=-10.0)])


j = tank()
frames_check.ordered = lambda: [k for k in frames_check.CHEATS if frames_check.CHEATS[k]['applies'] != 'see_through']  # barred
found = search(j, at=0.5)
kinds = [c['kind'] for c in (found.get('solved_by') or {}).get('cheats') or []]
check('tank_cap_taken_apart', found.get('status') == 'cheat' and kinds == ['explode_apart']
      and found['solved_by']['cheats'][0]['target'] == ['tank/cap'], {'status': found.get('status'), 'probes': [(p['cheats'], p['ok'], p['code']) for p in found.get('probes') or []],
       'attempts': [(a['cheats'], a['code'], (a.get('message') or '')[:300]) for a in found.get('attempts') or []]}
      if found.get('status') != 'cheat' else found['solved_by']['cheats'])
check('tank_cap_back_in_place', tuple(round(v, 6) for v in bpy.data.objects['tank/cap'].matrix_world.translation) == (0.0, 0.0, 0.0)
      and not apart(bpy.data.objects['tank/cap'], 25), list(bpy.data.objects['tank/cap'].matrix_world.translation))

# 3. the same cap, which the shot's line speaks of: never taken apart
j = tank()
j['locked'] = {'ids': ['tank/cap'], 'state': ['tank/cap'], 'structure': ['tank'], 'pose': []}
move = j['shot']['camera']['move']
ctx = camera_moves.frame_context(j, move)
fr = move['frames'][1]
row, failure = frames_check._solve(j, move, fr, ctx)
allowed, barred = frames_check._split(j, move, fr, ctx, failure, None)
check('claimed_cap_barred', not any(c['kind'] == 'explode_apart' and 'tank/cap' in c['target'] for c in allowed)
      and any(c['kind'] == 'explode_apart' and 'state or place' in c.get('why_locked', '') for c in barred)
      and any(c['kind'] == 'explode_apart' and c['target'] == ['tank/tube'] for c in allowed),   # the tube is not the cap
      [(c['kind'], c['target'], c.get('why_locked')) for c in barred][:4])
try:
    cheat_scene.trial(j, fr, {'kind': 'explode_apart', 'target': ['tank/cap'], 'value': 0.6})
    held = None
except ValueError as error:
    held = str(error)
check('claimed_cap_trial_refused', held and held.startswith('CHEAT_TOUCHES_LOCKED'), held)
frames_check.ordered = __import__('cheat_table').ordered   # the table's own order again

# 4. applied for a build at the middle moment: apart there, back beside it, out of the framed box while apart
j = tank()
j['shot']['camera']['move']['frames'][1]['cheats'] = [{'kind': 'explode_apart', 'target': ['tank/cap'], 'value': 0.6}]
done = cheat_scene.apply(j)
cap = bpy.data.objects['tank/cap']
scene = bpy.context.scene
at = {}
for frame in (1, 25, 48):
    scene.frame_set(frame)
    at[frame] = round(cap.matrix_world.translation.z, 3)
check('cap_apart_at_its_moment_only', at[1] == 0.0 and at[48] == 0.0 and at[25] < -0.5, at)
check('cap_apart_window', apart(cap, 25) and not apart(cap, 1) and not apart(cap, 48), list(cap.get('studio_apart_window')))
scene.frame_set(25)
lo = resolve_ref('tank')['box'][0][2]
check('cap_out_of_the_framed_box_while_apart', lo > -0.1, lo)
whole = world_box(resolve_ref('tank')['objects'], resolve_ref('tank')['roots'], set_aside=False)[0][2]
check('cap_kept_in_the_followed_box', whole < -0.5, whole)
scene.frame_set(1)
check('cap_in_the_framed_box_otherwise', resolve_ref('tank')['box'][0][2] < -0.03, resolve_ref('tank')['box'][0][2])

# 5. the tank on the ground: the cap's first way goes into it, so it is tried the other way
j = tank()
ground = box('ground/slab', (0, 0, -2.04), (20.0, 20.0, 4.0), root('ground'))   # top at -0.04: the cap stands on it
ground[scene_roles.TAG] = 'support'
move = j['shot']['camera']['move']
ctx = camera_moves.frame_context(j, move)
got = frames_check._attempt(j, move, move['frames'][1], ctx, [{'kind': 'explode_apart', 'target': ['tank/cap'], 'value': 0.6}])
check('sunk_cap_tried_the_other_way', got['cheats'][0]['value'] == -0.6 and got['code'] != 'SUBJECT_SINKS',
      {k: got[k] for k in ('cheats', 'code', 'message')})
check('sunk_cap_back_in_place', tuple(round(v, 6) for v in bpy.data.objects['tank/cap'].matrix_world.translation) == (0.0, 0.0, 0.0),
      list(bpy.data.objects['tank/cap'].matrix_world.translation))
print('AUTO_OPEN_SMOKE', report)
