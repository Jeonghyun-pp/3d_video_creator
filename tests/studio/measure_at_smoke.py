"""Run inside Blender (headless): the camera reads a part's thickness as the build's frame probe does, and a coat is the
part it lies on.

A rig: a big body (a cylinder), a thin rod beside it, a see-through skin over the rod open at the frame (keyed visible),
a helper the id pass leaves out, and a thin band round the body's middle - its own part, as a soot shell is.
- frame_probe.measure_at from a pose gives the same px as the build's own id pass from that pose (_classes, _setup, the
  scene camera there, frame_probe_core.thickness_px), the big body and the thin rod alike - where the rasterised points
  (camera_frame.shown_px) read them apart; and it leaves the scene as it found it (colours, render visibility and its
  keyed curves, resolution, camera, frame, no camera left behind);
- the band marked a coat of the body (coats.mark): the body reads as thick as with the band hidden, the band is not in
  front of the body (diagnose_part occluder_parts, probe part_blockers); unmarked it is - the case this guards.
- the exposure survey finds the unmarked band lying on the body (coated_by) and not once it is marked.
Timings printed.
"""
from pathlib import Path
import math
import sys
import time

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
import camera_frame  # noqa: E402
import camera_frame_core as frame_core  # noqa: E402
import coats  # noqa: E402
import frame_probe  # noqa: E402
import frame_probe_core as probe_core  # noqa: E402
import id_view  # noqa: E402
from frame_candidates import _pose  # noqa: E402
import scene_roles  # noqa: E402
import cheat_scene  # noqa: E402
import frames_check  # noqa: E402
import show_scene  # noqa: E402
from mathutils import Vector  # noqa: E402

SIZE, FRAME, LENS = (1080, 1920), 10, 35.0
EYE, AIM = (0.0, -9.0, 1.5), (0.6, 0.0, 1.5)


def tagged(obj, part, parent):
    obj['studio_id'] = f'rig/{part}'
    obj['studio_part_id'] = part
    obj['studio_subject_id'] = 'rig'
    obj.name = f'rig/{part}'
    obj.parent = parent
    return obj


def cylinder(location, radius, depth):
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=radius, depth=depth, location=location)
    return bpy.context.active_object


bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.frame_start, scene.frame_end = 1, 48
scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 540, 960, 50
root = bpy.data.objects.new('rig', None)
scene.collection.objects.link(root)
root['studio_id'] = root['studio_subject_id'] = 'rig'
body = tagged(cylinder((0.0, 0.0, 1.5), 0.6, 3.0), 'body', root)
rod = tagged(cylinder((1.4, 0.0, 1.5), 0.03, 2.4), 'rod', root)
band = tagged(cylinder((0.0, 0.0, 1.5), 0.603, 0.8), 'band', root)        # 3 mm out: a soot shell on the body
back = tagged(cylinder((0.0, 1.2, 1.5), 0.2, 0.3), 'back', root)        # behind the body, under the band's height
skin = tagged(cylinder((1.4, 0.0, 1.5), 0.25, 2.6), 'skin', root)        # round the rod, see-through at the frame
camera_frame.add_see_window(skin, (1, 48))
skin.hide_render = False
skin.keyframe_insert('hide_render', frame=1)
helper = cylinder((0.0, -4.0, 1.5), 0.4, 3.0)                            # a guide before the body, keyed visible at the
helper.name, helper[scene_roles.TAG] = 'guide', 'helper'                 # judged frame, hidden now: never drawn
helper.hide_render = True
helper.keyframe_insert('hide_render', frame=1)
helper.hide_render = False
helper.keyframe_insert('hide_render', frame=FRAME)
cam_data = bpy.data.cameras.new('shot_cam')
cam_data.sensor_width = 36.0
cam_data.shift_x = 0.08                                                  # the measure takes the scene camera's shift too
scene.camera = bpy.data.objects.new('shot_cam', cam_data)
scene.collection.objects.link(scene.camera)
scene.camera.location = (5.0, 5.0, 5.0)
for n, o in enumerate(bpy.data.objects):
    if o.type == 'MESH':
        o.color = (0.1 * n, 0.2, 0.3, 1.0)
scene.frame_set(1)
PARTS = ['rig/body', 'rig/rod']


def state():
    curves = [c.mute for o in bpy.data.objects if o.animation_data and o.animation_data.action
              for c in id_view._curves(o.animation_data.action)[0]]
    return {'colors': {o.name: tuple(round(v, 6) for v in o.color) for o in bpy.data.objects},
            'hide': {o.name: o.hide_render for o in bpy.data.objects}, 'mute': curves, 'camera': scene.camera.name,
            'res': (scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage),
            'frame': scene.frame_current, 'objects': len(bpy.data.objects), 'cameras': len(bpy.data.cameras),
            'engine': scene.render.engine, 'path': scene.render.filepath}


def measure():
    return frame_probe.measure_at(PARTS, EYE, AIM, LENS, 0.0, FRAME, SIZE, subjects=['rig'])


def blamed(ref):
    view = frame_core.view_of(EYE, AIM)
    told = camera_frame.diagnose_part(ref, ['rig'], EYE, view, LENS, SIZE, FRAME)['occluder_parts']
    prepared = camera_frame.probe_set(['rig'], ['rig'], [ref], FRAME)
    probed = camera_frame.probe(['rig'], ['rig'], [ref], EYE, view, LENS, SIZE, FRAME, prepared=prepared)['part_blockers'][ref]
    scene.frame_set(1)
    return told, probed


# --- the coat: marked, it is the body; unmarked, it is a layer in front of the body (the guarded case) ----------------
told, probed = blamed('rig/body')
assert 'rig/band' in told or 'rig/band' in probed, (told, probed)       # unmarked: blamed
told, probed = blamed('rig/back')
assert 'rig/band' in told, (told, probed)                               # unmarked: named beside the body
coats.mark(band, 'rig/body')
told, probed = blamed('rig/body')
assert 'rig/band' not in told and 'rig/band' not in probed, (told, probed)
told, probed = blamed('rig/back')           # behind the body: the band on it is named as the body, not beside it
assert 'rig/body' in told and 'rig/band' not in told and 'rig/band' not in probed, (told, probed)

# --- measure_at: the scene left as found, the build's numbers ---------------------------------------------------------
before = state()
started = time.monotonic()
got = measure()
seconds = time.monotonic() - started
after = state()
assert after == before, {k: (before[k], after[k]) for k in before if before[k] != after[k]}
started = time.monotonic()
for _ in range(3):
    measure()
repeat = (time.monotonic() - started) / 3
band.hide_render = True
bare = measure()
band.hide_render = False
assert abs(got['rig/body'] - bare['rig/body']) <= 0.5, (got, bare)   # the coat drawn as the body: no thinner for it
del band[coats.COAT]
unmarked = measure()
coats.mark(band, 'rig/body')
assert unmarked['rig/body'] <= got['rig/body'], (unmarked, got)
assert got['rig/rod'] > 0, got                                         # through its see-through skin
rasterised = {p: camera_frame.shown_px(p, EYE, frame_core.view_of(EYE, AIM), LENS, SIZE, FRAME) for p in PARTS}
scene.frame_set(1)

# --- a coat over the whole near face of its part: the probe and the opening read the part as shown; a cheat on the part
# takes its coat along; a coat the shot keys itself keeps its own class -----------------------------------------------
band.scale.z = 3.02 / 0.8
bpy.context.view_layer.update()
VIEW = frame_core.view_of(EYE, AIM)


def probe_px(ref):
    prepared = camera_frame.probe_set(['rig'], ['rig'], [ref], FRAME)
    out = camera_frame.probe(['rig'], ['rig'], [ref], EYE, VIEW, LENS, SIZE, FRAME, prepared=prepared)['must_see_px'][ref]
    scene.frame_set(1)
    return out


coated_px = probe_px('rig/body')
band.hide_render = True
bare_px = probe_px('rig/body')
band.hide_render = False
assert bare_px > 0 and coated_px >= 0.85 * bare_px, (coated_px, bare_px)   # under its own coat the body shows
toward = tuple((Vector(EYE) - Vector(AIM)).normalized())
hidden = show_scene.hidden_share('rig/body', ['rig/body'], toward, FRAME, 20.0)
assert hidden < 0.05, hidden                                                  # its own coat never hides it
scene.frame_set(1)
band.scale.z = 1.0
bpy.context.view_layer.update()
assert band in cheat_scene._roots('rig/body'), cheat_scene._roots('rig/body')            # moved with the body
assert band in cheat_scene._objects('rig/body') and band in frames_check._owner_objects('rig/body')
classes, _keys, _c = frame_probe._classes({'subjects': ['rig'], 'key_parts': [{'id': 'rig/body'}, {'id': 'rig/band'}]})
assert classes['rig/band'] == 'key:rig/band', classes['rig/band']              # declared itself: not drawn as the body
classes, _keys, _c = frame_probe._classes({'subjects': ['rig'], 'key_parts': [{'id': 'rig/body'}]})
assert classes['rig/band'] == 'key:rig/body', classes['rig/band']

# the build's own pass (last: it changes the scene for good, as the build's copy does)
scene.render.resolution_x, scene.render.resolution_y = SIZE
classes, keys, _concealed = frame_probe._classes({'subjects': ['rig'], 'key_parts': [{'id': p} for p in PARTS]})
colour = frame_probe._setup(scene, classes, keys)
frame_probe._mute_visibility(skin)
skin.hide_render = True                                                # the build's through-render leaves the open skin out
scene.frame_set(FRAME)
_pose(scene.camera, EYE, AIM, LENS, 0.0)
labels, names = frame_probe._decode(frame_probe._render(scene, Path(bpy.app.tempdir) / 'build_pass.png'), colour)
shapes = frame_probe._shapes(labels, names, [0.0, 0.0, 1.0, 1.0])
build = {p: round(probe_core.thickness_px(shapes.get(f'key:{p}'), SIZE), 1) for p in PARTS}
assert got == build, (got, build)
assert build['rig/body'] > 3 * build['rig/rod'], build
print('MEASURE_AT_SMOKE', {'measure_at': got, 'build': build, 'band_hidden': bare, 'band_unmarked': unmarked,
                           'rasterised': rasterised, 'probe_coated': coated_px, 'probe_bare': bare_px, 'first_s': round(seconds, 3), 'repeat_s': round(repeat, 3)})

# --- the exposure survey: an unmarked layer lying on a part is found, a marked one is not ---------------------------
from exposure_survey import measure as survey  # noqa: E402
del band[coats.COAT]
rows = survey('rig', root, ['body', 'band', 'rod'], directions=16, samples=60)['parts']
assert rows['body'].get('coated_by', {}).get('band', 0) > 0, rows['body']
coats.mark(band, 'rig/body')
over = tagged(cylinder((0.0, 0.0, 1.5), 0.606, 0.4), 'band_over', root)   # a second shell over the first
rows = survey('rig', root, ['band'], directions=16, samples=60)['parts']
assert rows['band'].get('coated_by', {}).get('band_over', 0) > 0, rows['band']   # unmarked: it lies on the band
coats.mark(over, 'rig/body')                                                     # marked on the body, as the band is
rows = survey('rig', root, ['body', 'band', 'rod'], directions=16, samples=60)['parts']
assert not rows['body'].get('coated_by', {}).get('band'), rows['body']
assert not rows['band'].get('coated_by', {}).get('band_over'), rows['band']   # coats of one part do not flag each other
print('MEASURE_AT_SMOKE ok')
