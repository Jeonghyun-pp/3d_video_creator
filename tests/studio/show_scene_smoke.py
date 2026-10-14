"""Blender: the shot list's show opens the subject for the camera (studio/blender_ops/show_scene.py, show_core.py).

A closed shell (the body) with an engine inside it; frame targets from one side. A section moment cuts the body so the
engine shows from the picture's own view, and shuts again before a later outside moment (the cut switched off, the body
whole again); an explode moment moves the body away; an x-ray moment wraps the body's materials after a clay-style
material swap and keys how see-through it is; the parts are never cut.
Every layer opens (A3, 2026-10-14): a thermos (shell -> glass -> straw) seen through by x-ray shows its straw; a fridge's
bottom drawer cut away from above shows past the shell and the shelves over it; a cut that is not built is found
(SHOW_CUT_MISSES) before any camera is solved, a built one passes; a section held open never cuts a part a later
moment shows.
Run: blender --background --factory-startup --python tests/studio/show_scene_smoke.py
"""
from pathlib import Path
import sys

import bmesh
import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
import camera_frame  # noqa: E402
import camera_frame_core as frame_core  # noqa: E402
import reveal  # noqa: E402
import show_scene  # noqa: E402

report = {}


def check(name, cond, detail):
    report[name] = detail
    assert cond, f'{name}: {detail}'


def reset():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)


def box(ident, centre, size):
    mesh = bpy.data.meshes.new(ident)
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0); bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new(ident, mesh); bpy.context.scene.collection.objects.link(obj)
    obj.location, obj.scale = centre, size
    bpy.context.view_layer.update()
    bpy.ops.object.select_all(action='DESELECT'); obj.select_set(True); bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj['studio_id'] = ident
    return obj


def stage():
    reset()
    body = box('rocket/body', (0, 0, 2), (1.2, 1.2, 4))         # the shell
    box('rocket/engine', (0, 0, 0.8), (0.5, 0.5, 0.6))           # inside it
    material = bpy.data.materials.new('paint'); body.data.materials.append(material)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 60
    return body


def job(rows):
    """Three frame targets seen from -Y (azimuth 0, a little from above): rows are their shows."""
    frames = [{'at': at, 'height_share': 0.6, 'centre': [0.5, 0.5], 'view': {'elevation_deg': 10.0, 'azimuth_deg': 0.0}, 'lens_mm': 35.0,
               **({'show': row} if row else {})} for at, row in zip((0.0, 0.5, 1.0), rows)]
    return {'fps': 30, 'shot': {'shot_id': 's', 'duration_frames': 60, 'actions': [],
                                'camera': {'move': {'type': 'frame_to_frame', 'params': {'target': 'rocket'}, 'frames': frames}}}}


def seen_px(frame):
    """How thick the engine shows from the picture's camera side (frame_core basis), at a 1-based scene frame."""
    forward, _right, _up = frame_core.basis(10.0, 0.0)
    eye = tuple((0.0, 0.0, 0.8)[i] - 9 * c for i, c in enumerate(forward))   # 9 m back from the engine along the view
    return camera_frame.shown_px('rocket/engine', eye, (10.0, 0.0), 35.0, (1080, 1920), frame)


def vertices(obj, frame):
    bpy.context.scene.frame_set(frame)
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh(); n = len(mesh.vertices); ev.to_mesh_clear()
    return n


# 1. section at the middle moment, outside again at the end: opens while the camera moves, shuts before the end
body = stage()
section = {'at': 0.5, 'parts': ['rocket/engine'], 'how': 'section', 'why': 'the engine inside'}
j = job([None, section, None])
rows = show_scene.apply(j)
check('section_found_the_shell', rows and 'rocket/body' in rows[0]['in_front'], rows)
reveal.apply(j['shot'])
check('engine_hidden_before', seen_px(1) == 0.0, seen_px(1))
check('engine_shows_when_open', seen_px(31) >= 30, seen_px(31))
check('engine_never_cut', vertices(bpy.data.objects['rocket/engine'], 31) == 8, vertices(bpy.data.objects['rocket/engine'], 31))
check('body_whole_again_at_the_end', vertices(body, 60) == 8, vertices(body, 60))

# 2. explode: the shell moves away toward the camera's side
body = stage()
j = job([None, {**section, 'how': 'explode'}, None])
show_scene.apply(j)
bpy.context.scene.frame_set(1); before = body.matrix_world.translation.copy()
bpy.context.scene.frame_set(31); after = body.matrix_world.translation.copy()
check('explode_moves_the_shell', (after - before).length > 0.5, [list(before), list(after)])

# 3. x-ray: after a clay-style swap of every material, the body's final materials are wrapped and keyed
body = stage()
j = job([None, {**section, 'how': 'xray'}, None])
show_scene.apply(j)
clay = bpy.data.materials.new('clay'); body.data.materials.clear(); body.data.materials.append(clay)   # what look._clay does
rows = show_scene.see_through(j)
wrapped = body.material_slots[0].material
bpy.context.scene.frame_set(1); start = body.get(show_scene.SEE_PROP)
bpy.context.scene.frame_set(31); opened = body.get(show_scene.SEE_PROP)
check('xray_wraps_the_final_material', wrapped.get('studio_see_through_of') == 'clay', wrapped.name)
check('xray_keyed_open', (start, opened) == (0.0, 1.0), (start, opened))
check('xray_window_for_rays', list(body.get(camera_frame.SEE_WINDOW)) == [16, 48], list(body.get(camera_frame.SEE_WINDOW)))   # mid-fade to mid-shut
check('xray_lets_the_engine_be_seen', seen_px(31) >= 30, seen_px(31))
# 4. two openings in one shot: the second finds the shell, not the first's cutter (2026-10-13 re-audit)
body = stage()
j = job([None, section, {**section, 'at': 1.0, 'how': 'xray'}])
rows = show_scene.apply(j)
check('second_opening_sees_the_shell', len(rows) == 2 and list(rows[1]['in_front']) == ['rocket/body'], [r['in_front'] for r in rows])
check('first_stays_open_into_the_second', rows[0]['close'] is None, rows[0]['close'])


def seen_from(ref, centre, elevation, frame, distance=9.0):
    """How thick a part shows from a camera `distance` m back from `centre` along the picture's view (azimuth 0)."""
    forward, _right, _up = frame_core.basis(elevation, 0.0)
    eye = tuple(centre[i] - distance * c for i, c in enumerate(forward))
    return camera_frame.shown_px(ref, eye, (elevation, 0.0), 35.0, (1080, 1920), frame)


def picture_job(rows, elevation):
    j = job(rows)
    for f in j['shot']['camera']['move']['frames']:
        f['view']['elevation_deg'] = elevation
    j['shot']['camera']['move']['params']['target'] = rows[1]['parts'][0].split('/')[0]
    return j


# 5. x-ray through two layers: the shell and the glass inside it both fade; the straw shows (2026-10-14: only the
#    first layer faded, the glass still hid the straw)
reset()
box('thermos/shell', (0, 0, 1), (0.8, 0.8, 2.0))
box('thermos/glass', (0, 0, 1), (0.6, 0.6, 1.8))
box('thermos/straw', (0, 0, 1), (0.12, 0.12, 1.4))
bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 60
straw = {'at': 0.5, 'parts': ['thermos/straw'], 'how': 'xray', 'why': 'the straw inside'}
j = picture_job([None, straw, None], 10.0)
rows = show_scene.apply(j)
check('xray_finds_both_layers', set(rows[0]['in_front']) == {'thermos/shell', 'thermos/glass'}, rows[0]['in_front'])
show_scene.see_through(j)
check('straw_hidden_before', seen_from('thermos/straw', (0, 0, 1), 10.0, 1, 5.0) == 0.0, seen_from('thermos/straw', (0, 0, 1), 10.0, 1, 5.0))
check('straw_shows_through_both', seen_from('thermos/straw', (0, 0, 1), 10.0, 31, 5.0) >= 20, seen_from('thermos/straw', (0, 0, 1), 10.0, 31, 5.0))

# 6. a fridge's bottom drawer cut away from above: the cut clears the top, both shelves and the drawer front's cover
reset()
box('fridge/body', (0, 0, 1), (0.8, 0.8, 2.0))                    # a solid stand-in for the cabinet
for n, z in enumerate((1.4, 0.9)):
    box(f'fridge/shelf_{n}', (0, 0, z), (0.7, 0.7, 0.04))
box('fridge/drawer', (0, 0, 0.3), (0.6, 0.6, 0.3))
bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 60
drawer = {'at': 0.5, 'parts': ['fridge/drawer'], 'how': 'cutaway', 'why': 'what the bottom drawer keeps'}
j = picture_job([None, drawer, None], 70.0)
rows = show_scene.apply(j)
check('cutaway_finds_every_layer', {'fridge/body', 'fridge/shelf_0', 'fridge/shelf_1'} <= set(rows[0]['in_front']), rows[0]['in_front'])
missed = show_scene.check_cuts(j, rows)                             # the boolean is not built yet: the drawer stays hidden
check('unbuilt_cut_misses', missed['gate_failures'] and missed['gate_failures'][0]['gate'] == 'SHOW_CUT_MISSES', missed)
reveal.apply(j['shot'])
built = show_scene.check_cuts(j, rows)
check('built_cut_uncovers_the_drawer', not built['gate_failures'] and built['checked'][0]['hidden_share'] <= 0.5, built)
check('drawer_shows_from_above', seen_from('fridge/drawer', (0, 0, 0.3), 70.0, 31, 5.0) >= 20, seen_from('fridge/drawer', (0, 0, 0.3), 70.0, 31, 5.0))

# 7. two openings: a section held open from the middle (closes never) and a cutaway at the end on a pipe that stands
#    in front of the pump the section opens. What any moment shows is never cut by any opening (review 2026-10-14: the
#    held section cut the pipe the end moment shows away to nothing)
reset()
box('boiler/casing', (0, 0, 2), (1.2, 1.2, 4))
box('boiler/pump', (0, 0.2, 0.8), (0.3, 0.3, 0.6))
box('boiler/pipe', (0, -0.3, 0.8), (0.15, 0.15, 0.6))                 # in front of the pump (camera on -y), inside the casing
bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 60
pump = {'at': 0.5, 'parts': ['boiler/pump'], 'how': 'section', 'why': 'the pump'}
pipe = {'at': 1.0, 'parts': ['boiler/pipe'], 'how': 'cutaway', 'why': 'the pipe'}
j = job([None, pump, pipe])
j['shot']['camera']['move']['params']['target'] = 'boiler'
rows = show_scene.apply(j)
check('held_opening_keeps_the_later_part', 'boiler/pipe' not in rows[0]['in_front'] and rows[0]['close'] is None, rows[0]['in_front'])
targeted = {(t['instance_id'], t['part_id']) for a in j['shot']['actions'] for t in a['targets']}
check('no_opening_targets_a_shown_part', not targeted & {('boiler', 'pipe'), ('boiler', 'pump')}, sorted(targeted))
reveal.apply(j['shot'])
check('pipe_whole_at_the_end', vertices(bpy.data.objects['boiler/pipe'], 60) == 8 and vertices(bpy.data.objects['boiler/pipe'], 31) == 8,
      (vertices(bpy.data.objects['boiler/pipe'], 31), vertices(bpy.data.objects['boiler/pipe'], 60)))
cut = show_scene.check_cuts(j, rows)
check('both_cuts_checked', sorted(c['part'] for c in cut['checked']) == ['boiler/pipe', 'boiler/pump'], cut)
print('SHOW_SCENE_SMOKE', report)
