"""Environment kits in Blender: a street from the verified city-kit exemplars, with no project code.

Checks: the dense night street builds every layer (lamps, trees, buildings, roofs, signs, traffic) from instances;
lights and signs are tagged light_fixture and lane paint is realized clutter (a reveal cuts it); no vehicle ever
enters the opening; the same seed rebuilds identical placement and another seed changes it; N+1 - a curved two-lane
suburban daytime street builds with the same functions and no window emits by day; the window_grid shader lights
about lit_ratio of the windows (rendered and counted).
Run inside Blender (run_smokes detects it).
"""
from pathlib import Path
import json
import math
import sys

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'studio' / 'blender_ops'))
import env_kits  # noqa: E402
import env_fill_core as core  # noqa: E402
from env_materials import window_grid  # noqa: E402

LIB = str(ROOT / 'library')
checks = []


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def check(name, condition, detail=''):
    assert condition, f'{name}: {detail}'
    checks.append(name)


def street(seed, **kw):
    reset()
    return env_kits.street('st', [(0, 0, 0), (0, 400, 0)], library_root=LIB, road_w_m=24, lanes_per_direction=3, night=True,
                           avoid=[(180, 205)], frames=(1, 90), seed=seed, intersections=[{'s': 100.0}], **kw)


report = street(5)
counts = report['counts']
check('all_layers_built', all(counts[k] > 0 for k in ('lamps', 'trees', 'buildings', 'roofs', 'signs', 'vehicles', 'dashes')), counts)
meshes = [o for o in bpy.data.objects if o.type != 'LIGHT']   # a light cannot be instanced: the lamp spots are real lights
check('instances_not_objects', len(meshes) < 0.25 * sum(counts[k] for k in ('lamps', 'trees', 'roofs', 'signs', 'dashes', 'people')), len(meshes))


def instances(prefix, part=None):
    """World positions of the instanced parts of a host (one part per copy when `part` names it)."""
    dg = bpy.context.evaluated_depsgraph_get()
    return [tuple(i.matrix_world.translation) for i in dg.object_instances if i.is_instance and i.parent and i.parent.original.name.startswith(prefix)
            and (part is None or i.object.original.name.rsplit('/', 1)[-1].split('.')[0] == part)]


s_of = lambda p: p[1]   # the street runs along +y from 0
lamps, trees, people = instances('scatter.st.lamps', 'pole'), instances('scatter.st.trees', 'trunk'), instances('scatter.st.people', 'torso')
check('nothing_stands_in_the_opening', not [p for p in lamps + trees + people if 180 <= s_of(p) <= 205], 'lamp/tree/person in avoid')
dash_points = [p for o in bpy.data.objects if o.name.startswith('scatter.st.dash') for p in json.loads(o['studio_scatter']).get('sample', [])]
check('real_lane_geometry', abs(report['geometry']['lane_w_m'] - 3.4) < 1e-6 and report['geometry']['shoulder_w_m'] > 0, report['geometry'])
placed = report['placements']
check('intersection_built', counts['intersections'] == 1 and placed['signal_poles'] == 4 and placed['ped_signals'] == 8
      and counts['crosswalk_stripes'] > 40, (counts, placed))
crossing = [p for p in lamps + trees if abs(s_of(p) - 100) < 6.6]
check('no_lamp_or_tree_in_the_crossing', not crossing, crossing[:3])
poles = [(p[0], p[1]) for p in lamps + trees]
near = [p for p in people if any(math.dist(p[:2], q) < 0.6 for q in poles)]
check('people_keep_out_of_poles', people and not near, (len(people), near[:3]))
paint = bpy.data.objects.get('st.paint')
check('markings_are_cut_like_the_road', paint and paint.get('studio_scene_role') == 'clutter')
spots = [o for o in bpy.data.objects if o.type == 'LIGHT' and o.name.startswith('st.spots')]
check('lamp_spots_kept_by_the_look', spots and len(spots) == placed['lamps'] and all(o.get('studio_keep_light') for o in spots), len(spots))
import bmesh
volumes = []
for o in bpy.data.objects:
    if o.type == 'MESH' and (o.name.startswith('st.road') or o.name.startswith('st.walk') or o.name.startswith('st.paint')):
        bm = bmesh.new(); bm.from_mesh(o.data); volumes.append(bm.calc_volume(signed=True)); bm.free()
check('surfaces_wind_outward', volumes and min(volumes) > 0, min(volumes) if volumes else None)
roles = {}
for obj in bpy.context.scene.objects:
    roles.setdefault(obj.get('studio_scene_role'), set()).add(obj.name.split('.')[1] if '.' in obj.name else obj.name)
dash_hosts = [o for o in bpy.data.objects if o.name.startswith('scatter.st.dash')]
check('lane_paint_realized_clutter', dash_hosts and all(o.get('studio_scene_role') == 'clutter' for o in dash_hosts))
fixtures = [o for o in bpy.data.objects if 'light_fixture' in (o.get('studio_scene_role'), o.get('studio_source_role'))]
check('lights_are_fixtures', {'head', 'headlights', 'taillights', 'panel'} <= {o.name.rsplit('/', 1)[-1].split('.')[0] for o in fixtures},
      sorted({o.name for o in fixtures})[:6])
cars = [o for o in bpy.data.objects if o.name.startswith('traffic.st.traffic.')]
inside = []
for car in cars:
    for frame in (1, 45, 90):
        bpy.context.scene.frame_set(frame)
        y = car.matrix_world.translation.y
        if 180 - 2 < y < 205 + 2:
            inside.append((car.name, frame, round(y, 1)))
check('no_vehicle_in_opening', not inside, inside[:3])
digest = report['digest']
check('same_seed_same_street', street(5)['digest'] == digest)
check('other_seed_other_street', street(6)['digest'] != digest)

# N+1: curved two-lane suburban street by day, same functions, no code change
reset()
bend = [(0, 0, 0), (0, 80, 0)] + [(60 - 60 * math.cos(a), 80 + 60 * math.sin(a), 0) for a in [i * math.pi / 12 for i in range(1, 7)]]
day = env_kits.street('sub', bend, library_root=LIB, road_w_m=8, lanes_per_direction=1, sidewalk_w_m=3, night=False, density='suburban',
                      frames=(1, 60), seed=2)
check('curved_suburban_street_builds', day['counts']['buildings'] > 0 and day['counts']['vehicles'] >= 0, day['counts'])
emitting = []
for mat in bpy.data.materials:
    if mat.get('studio_shader') == 'window_grid':
        strength = [n for n in mat.node_tree.nodes if n.type == 'MATH' and n.operation == 'MULTIPLY' and n.inputs[1].default_value > 0.5
                    and any(l.to_node.type == 'BSDF_PRINCIPLED' and l.to_socket.name == 'Emission Strength' for l in n.outputs[0].links)]
        emitting += [mat.name for n in strength]
check('no_window_emits_by_day', not emitting, emitting[:3])

# window_grid lights about lit_ratio of the windows (front elevation, counted at window centres)
reset()
scene = bpy.context.scene
scene.render.engine = 'CYCLES'; scene.cycles.samples = 2; scene.cycles.use_denoising = False
scene.render.resolution_x, scene.render.resolution_y = 150, 300
scene.view_settings.view_transform = 'Standard'
bpy.ops.mesh.primitive_cube_add(size=1, location=(15, 0, 30)); facade = bpy.context.object; facade.scale = (30, 20, 60)
cam = bpy.data.objects.new('cam', bpy.data.cameras.new('cam')); scene.collection.objects.link(cam); scene.camera = cam
cam.data.type = 'ORTHO'; cam.data.ortho_scale = 60; cam.location = (15, -50, 30); cam.rotation_euler = (math.pi / 2, 0, 0)
out = Path(bpy.app.tempdir) / 'wg.png'
measured = {}
for ratio in (0.3, 0.7):
    facade.data.materials.clear(); facade.data.materials.append(window_grid(f'wg{ratio}', lit_ratio=ratio, seed=3))
    scene.render.filepath = str(out); bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(out)); px = list(image.pixels); w, h = image.size
    lit = total = 0
    for i in range(10):
        for j in range(16):
            u, v = int((i * 3 + 1.5) / 30 * w), int((j * 3.6 + 3.6 * 0.575) / 60 * h)
            k = (v * w + u) * 4
            total += 1; lit += (0.2126 * px[k] + 0.7152 * px[k + 1] + 0.0722 * px[k + 2]) > 0.2
    measured[ratio] = round(lit / total, 3)
check('window_grid_lit_ratio', all(abs(measured[r] - r) <= 0.08 for r in measured), measured)

print('STUDIO_ENV_KITS_SMOKE ' + json.dumps({'ok': True, 'checks': checks, 'counts': counts, 'suburban': day['counts'], 'lit_ratio': measured}))
