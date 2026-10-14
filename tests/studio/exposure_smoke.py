"""Run inside Blender (headless): the exposure survey calls inside only what no outside side shows.

A closed box shell with a block in it, a fin on it and a coat over its lid: the block is inside; the fin, the shell and the
lid under its coat are not. The same shell open at the bottom: its block shows from below, so nothing has to be opened to
show it. A part wrapped by its own child part is inside: the child is another part.
Version 2 (A3-3, 2026-10-14): every direction's share, the share any side shows (exposable_share), who stops the rays
(enclosed_by: wraps it, in front and behind) and the part's box are kept; the camera's diagnosis judges hidden against the exposable share:
- a bulb 60 % inside its socket, seen from the side: hidden against all of it, shown against what any side shows;
- nine cans in a divided carton that covers 60 % of each: a front can seen from the side shows all a side can show of it; the
  middle can stays hidden - the carton round its foot hides only what no side reaches, the can in front does the rest:
  blocked (another view's job), not an opening's; the bulb's socket alone hides 60 % of all of it: enclosed (own_shell);
- the same middle can with no carton is hidden only by the can in front: blocked, and the can in front is named;
- a pod on a round body at 45 degrees, seen past the body: blocked, not enclosed (it sits in the body's box only).
"""
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
import camera_frame  # noqa: E402
import camera_frame_core as frame_core  # noqa: E402
import exposure_core  # noqa: E402
from exposure_survey import measure  # noqa: E402


def tagged(obj, subject, part, parent):
    obj['studio_id'] = f'{subject}/{part}'
    obj['studio_part_id'] = part
    obj['studio_subject_id'] = subject
    obj.name = f'{subject}/{part}'
    obj.parent = parent
    return obj


def cube(location, scale):
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.active_object
    obj.scale = scale
    return obj


def subject(name, offset, bottom):
    root = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(root)
    root['studio_id'] = root['studio_subject_id'] = name
    x = offset
    walls = [((x + 1, 0, 0), (0.05, 1, 1)), ((x - 1, 0, 0), (0.05, 1, 1)), ((x, 1, 0), (1, 0.05, 1)), ((x, -1, 0), (1, 0.05, 1)),
             ((x, 0, 1), (1, 1, 0.05))] + ([((x, 0, -1), (1, 1, 0.05))] if bottom else [])
    for n, (loc, scale) in enumerate(walls):
        tagged(cube(loc, scale), name, f'wall_{n}', root)
    tagged(cube((x, 0, 0), (0.3, 0.3, 0.3)), name, 'block', root)
    tagged(cube((x + 1.4, 0, 0), (0.3, 0.05, 0.3)), name, 'fin', root)
    tagged(cube((x, 0, 1.052), (1.0, 1.0, 0.0005)), name, 'coat', root)   # soot on the lid, 1.5 mm out: it hides nothing
    return root


bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
closed = subject('closed', 0.0, bottom=True)
opened = subject('opened', 20.0, bottom=False)
parts = ['block', 'fin', 'coat'] + [f'wall_{n}' for n in range(6)]
result = measure('closed', closed, parts, directions=32, samples=120)
threshold = result['threshold']
rows = result['parts']
inside = {p for p, r in rows.items() if exposure_core.inside(r['best_share'], threshold)}
assert inside == {'block'}, rows
assert rows['wall_4']['best_share'] > 0.8, rows['wall_4']   # under the coat
assert rows['block']['hidden_by'] and rows['block']['hidden_by'][0].startswith('wall_'), rows['block']
open_rows = measure('opened', opened, ['block', 'fin'], directions=32, samples=120)['parts']
assert not exposure_core.inside(open_rows['block']['best_share'], threshold), open_rows   # seen from below
assert open_rows['block']['best_dir'][2] < 0, open_rows['block']
nested = bpy.data.objects.new('nested', None)
bpy.context.scene.collection.objects.link(nested)
nested['studio_id'] = nested['studio_subject_id'] = 'nested'
core = tagged(cube((40, 0, 0), (0.3, 0.3, 0.3)), 'nested', 'core', nested)
cover = tagged(cube((40, 0, 0), (0.6, 0.6, 0.6)), 'nested', 'cover', core)   # a child part wrapped round its parent
cover.matrix_parent_inverse = core.matrix_world.inverted()
nested_rows = measure('nested', nested, ['core', 'cover'], directions=32, samples=120)['parts']
assert exposure_core.inside(nested_rows['core']['best_share'], threshold), nested_rows   # its own child hides it
assert nested_rows['core']['hidden_by'] == ['cover'], nested_rows['core']
# a part built as copies (lamp/bolt.0, lamp/bolt.1, one part id): measured as one part over every copy (2026-10-14:
# the survey looked the part up by its exact id and failed on a group the sampler already resolved)
lamp = bpy.data.objects.new('lamp', None)
bpy.context.scene.collection.objects.link(lamp)
lamp['studio_id'] = lamp['studio_subject_id'] = 'lamp'
for n in range(2):
    bolt = tagged(cube((60 + n, 0, 0), (0.1, 0.1, 0.1)), 'lamp', 'bolt', lamp)
    bolt['studio_id'] = bolt.name = f'lamp/bolt.{n}'
bolt_rows = measure('lamp', lamp, ['bolt'], directions=32, samples=120)['parts']
assert bolt_rows['bolt']['best_share'] > 0.8, bolt_rows   # two loose bolts in the open: each shows
assert len(rows['block']['per_direction']) == 32 and rows['block']['exposable_share'] <= 0.05, rows['block']
# each wall is its own part: none wraps the block by itself (in on one wall, out through another)
assert max(rows['block']['enclosed_by'].values(), default=0.0) < 0.5, rows['block']['enclosed_by']
assert rows['block']['box'] == [[-0.3, -0.3, -0.3], [0.3, 0.3, 0.3]], rows['block']['box']
assert nested_rows['core']['enclosed_by']['cover'] > 0.5, nested_rows['core']   # wrapped all round: MUST_SEE_NESTED reads this

assert rows['block']['reach_m'] >= (0.6 ** 2 + 0.6 ** 2) ** 0.5 - 1e-3, rows['block']['reach_m']   # never thinner than it can show
# a strut slanted 45 degrees: as thin as it is on its own axes, not as its world box (MUST_SEE_UNREACHABLE reads reach_m)
strut = bpy.data.objects.new('strut', None)
bpy.context.scene.collection.objects.link(strut)
strut['studio_id'] = strut['studio_subject_id'] = 'strut'
bar = tagged(cube((140, 0, 0), (0.05, 0.05, 1.0)), 'strut', 'bar', strut)
bar.rotation_euler = (0.785398, 0.0, 0.0)
bar_row = measure('strut', strut, ['bar'], directions=32, samples=120)['parts']['bar']
assert bar_row['reach_m'] < 0.15 and exposure_core.reach_m(bar_row['box']) > 0.5, bar_row


def root_of(name):
    root = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(root)
    root['studio_id'] = root['studio_subject_id'] = name
    return root


def cylinder(location, radius, depth):
    bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=radius, depth=depth, location=location)
    return bpy.context.active_object


def side_view(ref, centre, elevation=-3.0, distance=1.0, exposable=None):
    """diagnose_part from a camera `distance` m out on -y, a little below level (azimuth 0)."""
    toward = exposure_core.toward_camera(elevation, 0.0)
    eye = tuple(c + t * distance for c, t in zip(centre, toward))
    return camera_frame.diagnose_part(ref, [ref.split('/')[0]], eye, frame_core.view_of(eye, centre), 35.0, (1080, 1920), 1,
                                      exposable=exposable)


# a bulb 60 % inside its socket: the side view shows all of it a side can show
stand = root_of('lampstand')
tagged(cube((100, 0, -0.025), (0.35, 0.35, 0.225)), 'lampstand', 'socket', stand)
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.3, location=(100, 0, 0.14), segments=24, ring_count=12)
tagged(bpy.context.active_object, 'lampstand', 'bulb', stand)
bulb_rows = measure('lampstand', stand, ['bulb', 'socket'], directions=64, samples=200)['parts']
bulb = bulb_rows['bulb']
assert 0.3 < bulb['exposable_share'] < 0.55, bulb['exposable_share']
before = side_view('lampstand/bulb', (100, 0, 0.14), elevation=0.0, distance=3.0)
after = side_view('lampstand/bulb', (100, 0, 0.14), elevation=0.0, distance=3.0, exposable=bulb['exposable_share'])
assert before['hidden_share'] >= camera_frame.HIDDEN_SHARE and before['own_shell'], before            # the old reading: hidden
assert after['hidden_share'] < camera_frame.HIDDEN_SHARE and after['hidden_raw'] == before['hidden_share'], after

# nine cans packed in a carton 60 % of their height (walls, floor and dividers: one part of nine objects)
R, H, STEP = 0.033, 0.12, 0.068     # packed: 2 mm between cans


def cans(name, x0, carton):
    root = root_of(name)
    if carton:
        inner, wall, tall = STEP + R + 0.002, 0.002, 0.6 * H / 2
        for n, (loc, half) in enumerate(((( x0, -inner - wall, tall), (inner + 2 * wall, wall, tall)), ((x0, inner + wall, tall), (inner + 2 * wall, wall, tall)),
                                         ((x0 - inner - wall, 0, tall), (wall, inner + 2 * wall, tall)), ((x0 + inner + wall, 0, tall), (wall, inner + 2 * wall, tall)),
                                         ((x0, 0, -wall), (inner + 2 * wall, inner + 2 * wall, wall)),
                                         # the dividers between the cells, as a can carton has them
                                         ((x0, -STEP / 2, tall), (inner, 0.0005, tall)), ((x0, STEP / 2, tall), (inner, 0.0005, tall)),
                                         ((x0 - STEP / 2, 0, tall), (0.0005, inner, tall)), ((x0 + STEP / 2, 0, tall), (0.0005, inner, tall)))):
            panel = tagged(cube(loc, half), name, 'carton', root)
            panel['studio_id'] = panel.name = f'{name}/carton.{n}'
    for j in range(3):
        for i in range(3):
            tagged(cylinder((x0 + (i - 1) * STEP, (j - 1) * STEP, H / 2), R, H), name, f'can_{3 * j + i}', root)
    return root


crate = cans('crate', 80.0, carton=True)
crate_rows = measure('crate', crate, ['carton'] + [f'can_{n}' for n in range(9)], directions=64, samples=200)['parts']
front, middle = crate_rows['can_1'], crate_rows['can_4']
outer_raw = side_view('crate/can_1', (80.0, -STEP, H / 2))
outer = side_view('crate/can_1', (80.0, -STEP, H / 2), exposable=front['exposable_share'])
assert outer_raw['hidden_share'] >= camera_frame.HIDDEN_SHARE, outer_raw                              # the carton wall in front
assert outer['hidden_share'] < camera_frame.HIDDEN_SHARE, (outer, front['exposable_share'])            # all a side shows of it
inner = side_view('crate/can_4', (80.0, 0.0, H / 2), exposable=middle['exposable_share'])
# hidden: the can in front covers what the carton leaves; the carton alone hides only what no side reaches - the can in
# front is a sibling, so another view may show it (blocked, not an opening's job)
assert inner['hidden_share'] >= camera_frame.HIDDEN_SHARE and not inner['own_shell'] and inner['shell_share'] > 0.5, (inner, middle['exposable_share'])
assert any(o.startswith('crate/carton') for o in inner['occluders']), inner['occluders']
tray = cans('tray', 120.0, carton=False)
open_middle = side_view('tray/can_4', (120.0, 0.0, H / 2))
assert open_middle['hidden_share'] >= camera_frame.HIDDEN_SHARE and not open_middle['own_shell'], open_middle   # blocked, not enclosed
assert next(iter(open_middle['occluder_parts'])) == 'tray/can_1', open_middle['occluder_parts']   # named as the part in front
# a pod on the outside of a round body at 45 degrees, seen from behind the body: it sits inside the body's world box but
# not inside the body - blocked (another view shows it), never an opening's job (review 2026-10-14)
hull = root_of('hull')
bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=1.0, depth=4.0, location=(160, 0, 2))
tagged(bpy.context.active_object, 'hull', 'body', hull)
tagged(cube((160.78, 0.78, 2.0), (0.06, 0.06, 0.15)), 'hull', 'pod', hull)
behind = side_view('hull/pod', (160.78, 0.78, 2.0), elevation=0.0, distance=12.0)   # from -y: the body is in front
assert behind['hidden_share'] >= camera_frame.HIDDEN_SHARE and not behind['own_shell'], behind
assert next(iter(behind['occluder_parts'])) == 'hull/body', behind['occluder_parts']
print('exposure smoke ok', {p: r['best_share'] for p, r in rows.items()}, open_rows['block'],
      {'bulb': [bulb['exposable_share'], before['hidden_share'], after['hidden_share']],
       'front_can': [front['exposable_share'], outer_raw['hidden_share'], outer['hidden_share']],
       'middle_can': [middle['exposable_share'], inner['hidden_share'], inner['shell_share']], 'tray_middle': open_middle['hidden_share']})
