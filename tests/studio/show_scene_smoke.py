"""Blender: the shot list's show opens the subject for the camera (studio/blender_ops/show_scene.py, show_core.py).

A closed shell (the body) with an engine inside it; frame targets from one side. A section moment cuts the body so the
engine shows from the picture's own view, and shuts again before a later outside moment (the cut switched off, the body
whole again); an explode moment moves the body away; an x-ray moment wraps the body's materials after a clay-style
material swap and keys how see-through it is; the parts are never cut.
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
check('xray_window_for_rays', list(body.get(camera_frame.SEE_WINDOW)) == [1, 60], list(body.get(camera_frame.SEE_WINDOW)))
check('xray_lets_the_engine_be_seen', seen_px(31) >= 30, seen_px(31))
print('SHOW_SCENE_SMOKE', report)
