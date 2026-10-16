"""Blender: the two measures a 3D-first picture rests on (studio/picture3d.py), in scenes that are not the reel they
came from (N+1):
- registration (blender_ops/register_core.register): a line drawing of a house moved 5 % and scaled 1.04 is laid back
  within a step; moved 20 % it is found past REGISTER_LIMIT (refused, not mis-fitted at the search's edge);
- part states (blender_ops/picture_passes._states): a drawbridge's leaf lying on its deck reads retracted, swung up
  reads extended; a kettle's foot 1 cm over its hob touches it, a lifted one is clear; a whole crate on the hob touches
  it; out of frame on/off is unknown, hidden from rendering it is off;
- a painting answered at 2:3 of a 9:16 clay is cut to the clay's aspect before it is laid back (picture_register).
Run: blender --background --factory-startup --python tests/studio/picture3d_core_smoke.py
"""
from pathlib import Path
import json
import math
import sys

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
import register_core as rc  # noqa: E402
import picture_passes  # noqa: E402

report = {}


def check(name, cond, detail):
    report[name] = detail
    assert cond, f'{name}: {detail}'


# registration: a house outline (walls, roof, door, window) on 720 x 1280
H, W = 1280, 720
lines = np.zeros((H, W), bool)
lines[600:602, 160:560] = lines[1000:1002, 160:560] = True                  # floor and eaves
lines[600:1000, 160:162] = lines[600:1000, 558:560] = True                  # walls
for x in range(160, 360):                                                   # roof
    y = int(600 - (x - 160) * 0.9)
    lines[y:y + 2, x] = lines[y:y + 2, 720 - x] = True
lines[800:1000, 330:332] = lines[800:1000, 390:392] = lines[800:802, 330:392] = True   # door
lines[680:740, 220:222] = lines[680:740, 280:282] = lines[680:682, 220:282] = lines[740:742, 220:282] = True   # window
region = rc.dilate(lines, 40)
moved = rc.warp(lines, 1.04, round(0.05 * W), round(0.03 * H))
fit = rc.register(moved, lines, region)
check('a_moved_painting_lays_back', abs(fit['scale'] - 1 / 1.04) < 0.03 and abs(fit['dx_px'] + 0.05 * W / 1.04) <= 12
      and fit['subject_f1'] > 0.8 and fit['unpainted_share'] < 0.10 and not fit['at_search_edge'], fit)
far = rc.warp(lines, 1.0, round(0.20 * W), 0)
fit_far = rc.register(far, lines, rc.dilate(lines, 40))
check('a_painting_moved_too_far_is_past_the_limit', fit_far['unpainted_share'] > 0.10, fit_far)
grown = rc.register(rc.warp(lines, 1 / 1.2, 0, 0), lines, rc.dilate(lines, 40))   # drawn smaller (zoomed out): laid back, it only crops
check('a_painting_drawn_smaller_lays_back_by_cropping', abs(grown['scale'] - 1.2) < 0.03 and grown['unpainted_share'] == 0.0, grown)

# a painting answered in another aspect (2:3 of a 9:16 clay): cut to the clay's aspect first, then laid back unmoved
import tempfile  # noqa: E402
import OpenImageIO as oiio  # noqa: E402
import picture_register  # noqa: E402


def write_gray(path, img):
    buf = oiio.ImageBuf(oiio.ImageSpec(img.shape[1], img.shape[0], 3, oiio.UINT8))
    buf.set_pixels(oiio.ROI(0, img.shape[1], 0, img.shape[0], 0, 1, 0, 3), np.repeat(img[:, :, None], 3, axis=2).astype(np.float32))
    buf.write(str(path))


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    write_gray(tmp / 'lines.png', np.where(lines, 0.0, 1.0))
    write_gray(tmp / 'region.png', np.where(rc.dilate(lines, 40), 1.0, 0.0))
    wide = np.ones((1536, 1024))                          # the clay's drawing at 1.2x (864 x 1536), 80 px painted on each side
    ys, xs = np.nonzero(rc.dilate(lines, 1))
    keep = (ys * 1.2 < 1536) & (xs * 1.2 < 864)
    wide[(ys[keep] * 1.2).astype(int), (xs[keep] * 1.2).astype(int) + 80] = 0.0
    wide = np.where(rc.dilate(wide < 0.5, 1), 0.0, 1.0)
    write_gray(tmp / 'paint_1.png', wide)
    row = picture_register.one({'folder': str(tmp), 'painted': str(tmp / 'paint_1.png'), 'out': str(tmp / 'registered_1.png')},
                               [W, H], 0.10)
    check('a_painting_of_another_aspect_is_cut_to_the_clay_first',
          row['crop'] == [80, 0, 864, 1536] and row['painted_size'] == [1024, 1536] and abs(row['scale'] - 1.0) < 0.03
          and abs(row['dx_px']) <= 8 and abs(row['dy_px']) <= 8 and not row['over_limit'], row)

# part states
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
scene = bpy.context.scene


def box(name, size, at, rot=(0, 0, 0), subject=None, part=None):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=at, rotation=rot)
    o = bpy.context.object
    o.name = name
    o.scale = size
    if subject:
        o['studio_subject_id'] = subject
    if part:
        o['studio_part_id'] = part
    return o


box('bridge/deck', (10.0, 2.0, 0.4), (0, 0, 0), subject='bridge', part='deck')
leaf = box('bridge/leaf', (4.0, 1.8, 0.2), (2.0, 0.0, 0.35), subject='bridge', part='leaf')
hob = box('hob', (2.0, 2.0, 0.1), (20, 0, 0.95))
hob['studio_scene_role'] = 'support'
box('kettle/body', (0.6, 0.6, 0.8), (20, 0, 1.42), subject='kettle', part='body')
foot = box('kettle/foot', (0.3, 0.3, 0.02), (20, 0, 1.02), subject='kettle', part='foot')
bpy.context.view_layer.update()
cam = picture_passes._camera(scene)
from frame_candidates import _pose  # noqa: E402
_pose(cam, (5, -30, 8), (5, 0, 0), 35.0, 0.0)
scene.camera = cam
from scene_index import AnchorIndex  # noqa: E402
things = {'bridge/feat.leaf': {'kind': 'feature', 'ids': ['bridge/leaf'], 'parts': ['bridge/leaf'], 'owner': 'bridge'},
          'kettle/feat.foot': {'kind': 'feature', 'ids': ['kettle/foot'], 'parts': ['kettle/foot'], 'owner': 'kettle'}}
lying = picture_passes._states(scene, cam, things, AnchorIndex(), bpy.context.evaluated_depsgraph_get())
check('a_leaf_on_its_deck_is_retracted', lying['bridge/feat.leaf']['extension'] == 'retracted', lying['bridge/feat.leaf'])
check('a_foot_on_its_hob_touches', lying['kettle/feat.foot']['contact'] == 'contact', lying['kettle/feat.foot'])
leaf.rotation_euler = (0, -math.radians(70), 0)    # swung up about its hinge side
leaf.location = (0.6, 0.0, 1.9)
foot.location.z = 1.5
bpy.context.view_layer.update()
up = picture_passes._states(scene, cam, things, AnchorIndex(), bpy.context.evaluated_depsgraph_get())
check('a_leaf_swung_up_is_extended', up['bridge/feat.leaf']['extension'] == 'extended', up['bridge/feat.leaf'])
check('a_lifted_foot_is_clear', up['kettle/feat.foot']['contact'] == 'clear', up['kettle/feat.foot'])
# a thing of any kind stands on its support or not (a whole subject, not only a part with an owner); on/off is said only
# when it can be: in frame on, hidden from rendering off, out of frame unknown
crate = box('crate', (0.5, 0.5, 0.5), (20.7, 0.7, 1.25), subject='crate', part='body')
lamp = box('lamp', (0.2, 0.2, 0.2), (200, 0, 0))
dark = box('dark', (0.2, 0.2, 0.2), (200, 5, 0))
dark.hide_render = True
bpy.context.view_layer.update()
idx = AnchorIndex()
seen = picture_passes._states(scene, cam, {'crate': {'kind': 'subject', 'ids': ['crate']}, 'lamp': {'kind': 'element', 'ids': ['lamp']},
                                          'dark': {'kind': 'element', 'ids': ['dark']}}, idx, bpy.context.evaluated_depsgraph_get())
check('a_whole_subject_on_its_support_touches', seen['crate']['contact'] == 'contact', seen['crate'])
check('a_thing_out_of_frame_is_on_or_off_unknown', seen['lamp']['render'] == 'unknown' and not seen['lamp']['in_frame'], seen['lamp'])
check('a_thing_hidden_from_rendering_is_off', seen['dark']['render'] == 'off', seen['dark'])
print('PICTURE3D_CORE_SMOKE ' + json.dumps(report, default=str)[:2500])
