"""Blender: declared cheats (studio/blender_ops/cheat_scene.py) and the search for one (frames_check.py) - N+1 scenes,
none from the reel the layer came from:
- a cafe booth: the diner the picture frames sits inside four tall screens closed at the corners; no view within the
  picture's height band sees in. The search (search_intent, on the probe) moves a screen aside (context_move) and the
  moment solves. When the shot's line speaks of the screens (locked), no allowed cheat solves it and only the barred
  move would: the moment needs redrawing (FRAME_NEEDS_REDRAW).
- a pallet of 3 x 3 cans under a lid: the middle can, seen from the side, hides behind its neighbours from every view;
  the search makes the neighbours in front see-through (see_neighbours) - the cans stay where they are. A can inside
  its own sleeve is not offered it: what wraps round a part is the shot list's opening.
- applying cheats for a build: a move keyed at one moment holds over the shot; a scale keyed at two moments runs smooth
  between them about the thing's base; a volume shrinks toward its source; a lying subject turns about its own long
  axis through its middle; a see-through cheat gives rays a window and joins the look's skins; a lens and a view cheat
  change the frame target and say what it was; every changed object is tagged.
- keys: the author's own delta keys are never overwritten (CHEAT_CONFLICT); a trial on a thing a recorded cheat keyed
  is solved with the trial's value; a group that carries what a line speaks of is not moved (CHEAT_TOUCHES_LOCKED).
Run: blender --background --factory-startup --python tests/studio/cheat_scene_smoke.py
"""
from pathlib import Path
import math
import sys
import time

import bmesh
import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
import camera_frame  # noqa: E402
import camera_moves  # noqa: E402
import cheat_scene  # noqa: E402
import frames_check  # noqa: E402
from scene_index import resolve_ref  # noqa: E402

report = {}


def check(name, cond, detail):
    report[name] = detail
    assert cond, f'{name}: {detail}'


def reset():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 48


def box(ident, centre, size, role=None, part=None):
    mesh = bpy.data.meshes.new(ident)
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0); bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new(ident, mesh); bpy.context.scene.collection.objects.link(obj)
    obj.location, obj.scale = centre, size
    bpy.context.view_layer.update()
    bpy.ops.object.select_all(action='DESELECT'); obj.select_set(True); bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj['studio_id'] = ident
    if part:
        obj['studio_subject_id'], obj['studio_part_id'] = part
    if role:
        obj['studio_scene_role'] = role
    return obj


def job(target, frames, details=(), locked=None):
    move = {'type': 'frame_to_frame', 'params': {'target': target, 'follow': 0.0}, 'frames': frames}
    return {'shot_id': 's', 'fps': 30, 'output_size': [1080, 1920], 'exposure': {}, 'locked': locked or {},
            'shot': {'shot_id': 's', 'duration_frames': 48, 'actions': [], 'screen': {'details': list(details)},
                     'camera': {'move': move}}}


def moment(at, height, elevation=0.0, azimuth=0.0, lens=35.0, **more):
    return {'at': at, 'height_share': height, 'centre': [0.5, 0.5], 'view': {'elevation_deg': elevation, 'azimuth_deg': azimuth},
            'lens_mm': lens, 'cut_edges': [], **more}


def search(j, at=1.0):
    move = j['shot']['camera']['move']
    ctx = camera_moves.frame_context(j, move)
    fr = next(f for f in move['frames'] if f['at'] == at)
    row, failure = frames_check._solve(j, move, fr, ctx)
    if row is not None:
        return {'status': 'moved' if frames_check.core.kept_intent(row) else 'as_intended', 'row': row}
    return {'as_is': failure, **frames_check.search_intent(j, move, fr, ctx, failure, time.monotonic() + 600)}


# 1. the cafe: a diner inside a booth of four tall screens, closed at the corners
SCREENS = ('screen_s', 'screen_n', 'screen_e', 'screen_w')


def cafe():
    reset()
    box('diner', (0, 0, 0.85), (0.5, 0.5, 1.7))
    box('screen_s', (0, -1.2, 1.5), (2.9, 0.4, 3.0))
    box('screen_n', (0, 1.2, 1.5), (2.9, 0.4, 3.0))
    box('screen_e', (1.2, 0, 1.5), (0.4, 2.9, 3.0))
    box('screen_w', (-1.2, 0, 1.5), (0.4, 2.9, 3.0))
    return job('diner', [moment(0.0, 0.5), moment(1.0, 0.5)])


found = search(cafe())
check('cafe_fails_as_it_stands', found.get('as_is') and found['as_is']['code'] in ('FRAME_UNSOLVED', 'FOCUS_BLOCKED'), found.get('as_is', {}).get('code'))
moved_screen = (found.get('solved_by') or {}).get('cheats') or [{}]
check('cafe_screen_moved', found['status'] == 'cheat' and [c['kind'] for c in found['solved_by']['cheats']] == ['context_move']
      and moved_screen[0].get('target') in SCREENS, found.get('solved_by', {}).get('cheats'))
check('cafe_scene_put_back', all(bpy.data.objects[n].matrix_world.translation.z == 1.5 for n in SCREENS)
      and abs(bpy.data.objects['screen_s'].matrix_world.translation.y + 1.2) < 1e-6, list(bpy.data.objects['screen_s'].matrix_world.translation))
spoken = search(cafe() | {'locked': {'ids': list(SCREENS), 'structure': [], 'pose': list(SCREENS)}})
# screens a line speaks of are never moved; made see-through (see_context, 2026-10-20) they stay where the line says -
# no fact changes, as see_neighbours on a subject's own parts
check('cafe_spoken_screens_faded_not_moved', spoken['status'] == 'cheat'
      and [c['kind'] for c in spoken['solved_by']['cheats']] == ['see_context'], {k: spoken.get(k) for k in ('status', 'solved_by')})


# 2. the pallet of cans under a lid, on a floor
def pallet():
    reset()
    box('floor', (0, 0, -0.05), (20, 20, 0.1), role='support')
    for n in range(9):
        x, y = (n % 3 - 1) * 0.62, (n // 3 - 1) * 0.62
        box(f'cans/c{n}', (x, y, 1.0), (0.6, 0.6, 2.0), part=('cans', f'c{n}'))
    box('lid', (0, 0, 2.03), (2.0, 2.0, 0.06))
    return job('cans', [moment(0.0, 0.6), moment(1.0, 0.6, must_see=['cans/c4'])], details=[{'id': 'cans/c4', 'min_px': 20}])


found = search(pallet())
check('cans_fail_as_they_stand', found.get('as_is') and found['as_is']['code'] == 'FRAME_UNSOLVED', found.get('as_is', {}).get('code'))
kinds = [c['kind'] for c in (found.get('solved_by') or {}).get('cheats') or []]
check('cans_neighbours_see_through', found['status'] == 'cheat' and 'see_neighbours' in kinds
      and 'cans/c1' in next(c['target'] for c in found['solved_by']['cheats'] if c['kind'] == 'see_neighbours'), found.get('solved_by'))
check('cans_stay_put', all(abs(bpy.data.objects[f'cans/c{n}'].matrix_world.translation.z - 1.0) < 1e-6 for n in range(9))
      and camera_frame.SEE_WINDOW not in bpy.data.objects['cans/c1'], 'cans unmoved, windows put back')
check('cans_middle_shows', found['solved_by']['row']['must_see_px']['cans/c4'] >= 20, found['solved_by']['row']['must_see_px'])

# 2b. a can inside its own sleeve: the sleeve wraps round it - an opening's job (the shot list), not a see-through cheat
reset()
box('floor', (0, 0, -0.05), (20, 20, 0.1), role='support')
box('tin/sleeve', (0, 0, 1.0), (1.0, 1.0, 2.0), part=('tin', 'sleeve'))
box('tin/core', (0, 0, 1.0), (0.5, 0.5, 1.6), part=('tin', 'core'))
j = job('tin', [moment(0.0, 0.6), moment(1.0, 0.6, must_see=['tin/core'])], details=[{'id': 'tin/core', 'min_px': 20}])
move = j['shot']['camera']['move']
ctx = camera_moves.frame_context(j, move)
offered = frames_check.candidates(j, move, move['frames'][1], ctx, None)
check('enclosure_not_see_through', not [c for c in offered if c['kind'] == 'see_neighbours'], [(c['kind'], c['target']) for c in offered])

# 3. cheats applied for a build, keyed per moment
reset()
box('ground', (0, 0, -0.05), (30, 30, 0.1), role='support')
hull = box('hull', (0, 0, 0.5), (4.0, 1.0, 1.0))                 # a lying subject, long along x
crate = box('crate', (3, 2, 0.5), (1.0, 1.0, 1.0))
sign = box('sign', (-3, 2, 1.0), (1.0, 0.2, 2.0))
smoke = box('smoke', (0, 0, 3.0), (1.0, 1.0, 2.0), role='atmosphere')  # a volume above the subject
skin = box('skin', (0, -1.0, 0.5), (1.0, 0.2, 1.0))
skin.data.materials.append(bpy.data.materials.new('paint'))
cheats0 = [{'kind': 'context_scale', 'target': 'sign', 'value': 0.5}, {'kind': 'lens', 'target': None, 'value': 24.0},
           {'kind': 'view', 'target': None, 'value': {'azimuth_deg': 12.0, 'elevation_deg': -10.0}}]
cheats1 = [{'kind': 'context_move', 'target': 'crate', 'value': {'move_m': [0.0, 1.0, 0.0], 'turn_deg': 90.0, 'size_m': 1.0}},
           {'kind': 'context_scale', 'target': 'sign', 'value': 1.0}, {'kind': 'atmosphere_scale', 'target': 'smoke', 'value': 0.5},
           {'kind': 'subject_turn', 'target': 'hull', 'value': 45.0}, {'kind': 'see_neighbours', 'target': ['skin'], 'value': 0.55}]
j = job('hull', [moment(0.0, 0.4, 10.0, cheats=cheats0), moment(1.0, 0.4, cheats=cheats1)])
applied = cheat_scene.apply(j)
scene = bpy.context.scene


def at(frame, ident):
    scene.frame_set(frame)
    return resolve_ref(ident)['box']


crate_box = at(1, 'crate')
check('move_holds_over_the_shot', abs((crate_box[0][1] + crate_box[1][1]) / 2 - 3.0) < 1e-4, crate_box)
check('move_turns_about_its_upright', abs(bpy.data.objects['crate'].matrix_world.to_euler().z - math.radians(90)) < 1e-4,
      list(bpy.data.objects['crate'].matrix_world.to_euler()))
s1, s48, s24 = at(1, 'sign'), at(48, 'sign'), at(24, 'sign')
check('scale_keyed_at_both_moments', abs((s1[1][2] - s1[0][2]) - 1.0) < 1e-4 and abs((s48[1][2] - s48[0][2]) - 2.0) < 1e-4, (s1, s48))
check('scale_smooth_between', 1.0 < s24[1][2] - s24[0][2] < 2.0, s24)
check('scale_stands_on_its_base', all(abs(b[0][2]) < 1e-4 for b in (s1, s24, s48)), (s1[0][2], s24[0][2], s48[0][2]))
smoke_box = at(48, 'smoke')
check('volume_shrinks_toward_its_source', abs(smoke_box[0][2] - 2.0) < 1e-4 and abs(smoke_box[1][2] - 3.0) < 1e-4, smoke_box)
hull_box = at(48, 'hull')
check('subject_turns_about_its_long_axis', abs(hull_box[1][0] - 2.0) < 1e-4 and abs((hull_box[0][2] + hull_box[1][2]) / 2 - 0.5) < 1e-4
      and hull_box[1][1] - hull_box[0][1] > 1.3, hull_box)
check('see_through_window', list(skin.get(camera_frame.SEE_WINDOW))[1] == 10 ** 6 and j['shot']['_see_through'][0]['objects'] == ['skin'],
      (list(skin.get(camera_frame.SEE_WINDOW)), j['shot'].get('_see_through')))
import show_scene  # noqa: E402
show_scene.see_through(j)
check('see_through_joins_the_skins', skin.material_slots[0].material.get('studio_see_through_of') == 'paint', skin.material_slots[0].material.name)
first = j['shot']['camera']['move']['frames'][0]
check('camera_cheats_retarget', first['lens_mm'] == 24.0 and first['view'] == {'elevation_deg': 0.0, 'azimuth_deg': 12.0}
      and first['_cheated'] == {'lens_from': 35.0, 'view_from': {'elevation_deg': 10.0, 'azimuth_deg': 0.0}}, first)
check('changed_things_tagged', {o.name: o.get(cheat_scene.TAG) for o in scene.objects if o.get(cheat_scene.TAG)} ==
      {'crate': 'context_move', 'sign': 'context_scale', 'smoke': 'atmosphere_scale', 'hull': 'subject_turn', 'skin': 'see_neighbours'},
      {o.name: o.get(cheat_scene.TAG) for o in scene.objects})
check('report_rows', len(applied['cheats']) == len(cheats0) + len(cheats1), applied['cheats'])
try:
    cheat_scene.apply(job('hull', [moment(0.0, 0.4), moment(1.0, 0.4, cheats=[{'kind': 'context_move', 'target': 'ground',
                                                                                'value': {'move_m': [40.0, 0, 0], 'size_m': 30.0}}])]))
    raise AssertionError('a move beyond the size limit applied')
except ValueError as error:
    check('limits_held_in_the_scene', 'CHEAT_OUT_OF_RANGE' in str(error), str(error))

# 4. keys: an author's own keys on a delta channel are not overwritten (CHEAT_CONFLICT); a trial on a thing a recorded
#    cheat keyed is solved with the trial's value, not the keys'; a thing that carries what a line speaks of is not moved
reset()
author = box('cart', (0, 0, 0.5), (1.0, 1.0, 1.0))
author.delta_location = (0.0, 0.0, 0.0); author.keyframe_insert('delta_location', frame=1)
author.delta_location = (2.0, 0.0, 0.0); author.keyframe_insert('delta_location', frame=48)
try:
    cheat_scene.apply(job('cart', [moment(0.0, 0.4), moment(1.0, 0.4, cheats=[{'kind': 'context_scale', 'target': 'cart', 'value': 0.5}])]))
    raise AssertionError("a cheat keyed over the author's own delta keys")
except ValueError as error:
    check('author_keys_conflict', 'CHEAT_CONFLICT' in str(error), str(error))
reset()
crate = box('crate', (3, 0, 0.5), (1.0, 1.0, 1.0))
j = job('crate', [moment(0.0, 0.4), moment(1.0, 0.4, cheats=[{'kind': 'context_move', 'target': 'crate',
                                                              'value': {'move_m': [0.0, 1.0, 0.0], 'turn_deg': 0.0, 'size_m': 1.0}}])])
cheat_scene.apply(j)
fr = j['shot']['camera']['move']['frames'][1]
undo = cheat_scene.trial(j, fr, {'kind': 'context_move', 'target': 'crate', 'value': {'move_m': [1.0, 0.0, 0.0], 'turn_deg': 0.0, 'size_m': 1.0}})
scene.frame_set(48)
tried = resolve_ref('crate')['box']
check('trial_over_recorded_keys', abs((tried[0][0] + tried[1][0]) / 2 - 4.0) < 1e-4 and abs((tried[0][1] + tried[1][1]) / 2 - 1.0) < 1e-4, tried)
undo(); scene.frame_set(48)
back = resolve_ref('crate')['box']
check('trial_undone_keys_back', abs((back[0][0] + back[1][0]) / 2 - 3.0) < 1e-4 and abs((back[0][1] + back[1][1]) / 2 - 1.0) < 1e-4, back)
reset()
shelf = box('shelf', (0, 2, 1.0), (2.0, 0.5, 2.0))
jar = box('jar', (0, 2, 2.2), (0.3, 0.3, 0.4))
jar['studio_id'] = 'jar_9'                      # a line speaks of it; the shelf it stands on is not spoken of
jar.parent = shelf; jar.matrix_parent_inverse = shelf.matrix_world.inverted()
j = job('shelf', [moment(0.0, 0.4), moment(1.0, 0.4, cheats=[{'kind': 'context_move', 'target': 'shelf',
                                                               'value': {'move_m': [1.0, 0.0, 0.0], 'turn_deg': 0.0, 'size_m': 2.0}}])],
        locked={'ids': ['jar_9'], 'structure': [], 'pose': []})
try:
    cheat_scene.apply(j)
    raise AssertionError('moved a thing carrying what a line speaks of')
except ValueError as error:
    check('carried_lock_held', 'CHEAT_TOUCHES_LOCKED' in str(error) and 'jar_9' in str(error), str(error))

# 5. a cheat held still never sinks a thing into its support: a hull lying on a deck turned 45 deg about its long axis
#    (subject_turn, keyed at one moment and held) reaches into the deck - refused in a build and in the search; a post
#    standing 0.1 m into the deck by design, moved aside, keeps its depth and passes
import generate  # noqa: E402


def dock(*cheats):
    reset()
    box('deck', (0, 0, -0.25), (30, 12, 0.5), role='support')   # its top at z 0
    box('hull', (0, 0, 0.5), (4.0, 1.0, 1.0))                  # lying on it, long along x
    box('post', (6, 4, 1.4), (1.0, 1.0, 3.0))                  # its foot 0.1 m into the deck
    j = job('hull', [moment(0.0, 0.4), moment(1.0, 0.4, cheats=list(cheats))])
    j['probe'] = {'subjects': ['hull']}
    return j


turn = {'kind': 'subject_turn', 'target': 'hull', 'value': 45.0}
j = dock(turn)
allowance = generate.sink_allowance(j)
cheat_scene.apply(j)
sunk = generate._sinks(j, allowance)
check('turned_hull_sinks', sunk['gate_failures'] and sunk['deepest']['object'] == 'hull'
      and abs(sunk['deepest']['depth_m'] - (math.sqrt(0.5) - 0.5)) < 0.01 and 'SUBJECT_SINKS' == sunk['gate_failures'][0]['gate'], sunk)
j = dock({'kind': 'context_move', 'target': 'post', 'value': {'move_m': [0.0, 0.5, 0.0], 'turn_deg': 0.0, 'size_m': 3.0}})
allowance = generate.sink_allowance(j)
cheat_scene.apply(j)
kept = generate._sinks(j, allowance)
check('a_post_moved_at_its_own_depth_passes', not kept['gate_failures'] and abs(kept['deepest']['depth_m'] - 0.1) < 0.01
      and abs(kept['deepest']['allowance_m'] - 0.1) < 0.01, kept)
j = dock()
move = j['shot']['camera']['move']
tried = frames_check._attempt(j, move, move['frames'][1], camera_moves.frame_context(j, move), [turn])
check('the_search_refuses_a_sinking_turn', not tried['ok'] and tried['code'] == 'SUBJECT_SINKS', tried)
check('the_trial_is_undone', not bpy.data.objects['hull'].get(cheat_scene.TAG)
      and abs(bpy.data.objects['hull'].matrix_world.to_euler().x) < 1e-6, dict(bpy.data.objects['hull'].items()))
# a hull the shot itself lowers 0.3 m into the deck stays refused when a turn is added on top: the cheat is no allowance
j = dock(turn)
hull = bpy.data.objects['hull']
hull.keyframe_insert('location', frame=1)
hull.location.z = 0.2
hull.keyframe_insert('location', frame=j['shot']['duration_frames'])
allowance = generate.sink_allowance(j)
cheat_scene.apply(j)
driven = generate._sinks(j, allowance)
check('a_driven_sink_is_not_excused_by_a_cheat', allowance.get('hull') == 0.0 and driven['gate_failures']
      and driven['deepest']['object'] == 'hull' and not driven['deepest'].get('allowance_m'), {'allowance': allowance, 'sinks': driven})

# 6. a thing that hangs from another is cheated in its parent's space: steam on its turned, scaled stack shrinks toward
#    its source, a crate on a turned wall bracket moves the world way the cheat says
reset()
box('ground', (0, 0, -0.05), (30, 30, 0.1), role='support')
box('hull', (0, 0, 0.5), (4.0, 1.0, 1.0))
stack = bpy.data.objects.new('stack', None); bpy.context.scene.collection.objects.link(stack)
stack.location, stack.rotation_euler, stack.scale = (4.0, 0.0, 0.0), (0.0, 0.0, math.pi / 2), (2.0, 2.0, 2.0)
steam = box('steam', (0, 0, 0), (1.0, 1.0, 2.0), role='atmosphere')
steam.parent = stack; steam.location = (0.0, 0.0, 3.0)                   # in the world: x 3..5, y -1..1, z 4..8
wall = bpy.data.objects.new('wall', None); bpy.context.scene.collection.objects.link(wall)
wall.location, wall.rotation_euler = (0.0, 5.0, 0.0), (0.0, 0.0, math.pi / 2)
crate = box('crate', (0, 0, 0), (1.0, 1.0, 1.0))
crate.parent = wall; crate.location = (1.0, 0.0, 0.5)                    # in the world: centred at (0, 6, 0.5)
bpy.context.view_layer.update()
j = job('hull', [moment(0.0, 0.4), moment(1.0, 0.4, cheats=[
    {'kind': 'atmosphere_scale', 'target': 'steam', 'value': 0.5},
    {'kind': 'context_move', 'target': 'crate', 'value': {'move_m': [0.0, 1.0, 0.0], 'turn_deg': 0.0, 'size_m': 1.0}}])])
cheat_scene.apply(j)
steam_box, crate_box = at(48, 'steam'), at(48, 'crate')
check('parented_steam_shrinks_to_its_source', all(abs(a - b) < 1e-4 for a, b in zip([*steam_box[0], *steam_box[1]], [3, -0.5, 4, 4, 0.5, 6])),
      steam_box)
check('parented_crate_moves_in_the_world', all(abs((crate_box[0][i] + crate_box[1][i]) / 2 - c) < 1e-4 for i, c in enumerate((0.0, 7.0, 0.5))),
      crate_box)
check('parents_stay_put', tuple(stack.matrix_world.translation) == (4.0, 0.0, 0.0) and tuple(wall.matrix_world.translation) == (0.0, 5.0, 0.0),
      (list(stack.matrix_world.translation), list(wall.matrix_world.translation)))

# 7. see-through windows: an opening early in the shot and a cheat late in it are two windows, solid between; the
#    skins fade by when they open, whichever row came first (a cheat's row is added before the openings')
reset()
w = box('window', (0, 0, 0.5), (1.0, 1.0, 1.0))
camera_frame.add_see_window(w, [10, 20]); camera_frame.add_see_window(w, [30, 40])
check('two_windows_apart', [camera_frame.passes_through(w, f) for f in (15, 25, 35)] == [True, False, True], camera_frame.see_windows(w))
camera_frame.add_see_window(w, [18, 31])
check('overlapping_windows_join', camera_frame.see_windows(w) == [(10, 40)], camera_frame.see_windows(w))
w.data.materials.append(bpy.data.materials.new('glass'))
late = {'action_id': 'cheat_1_see_neighbours', 'how': 'see_neighbours', 'objects': ['window'], 'open': [29, 34], 'close': [39, 44],
        'see_through': {'alpha': 0.5, 'rim': False}}
early = {'action_id': 'show_0', 'how': 'transparency', 'objects': ['window'], 'open': [9, 19], 'close': None,
         'see_through': {'alpha': 0.5, 'rim': False}}
show_scene.see_through({'shot': {'_see_through': [late, early]}})


def faded(frame):
    bpy.context.scene.frame_set(frame)
    return w[show_scene.SEE_PROP]


check('an_opening_listed_after_a_cheat_still_opens', [faded(f) for f in (5, 25, 42, 48)] == [0.0, 1.0, 1.0, 1.0],
      [faded(f) for f in (5, 25, 42, 48)])
print('cheat_scene smoke OK', sorted(report))
