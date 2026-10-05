"""The second combination: everything added for reference-grade explainer shots, in one scene, so they are
proven to mix: a scattered crowd (Geometry Nodes instances), a road that opens (reveal) under a dive_through
move, debris that falls through the opening (baked rigid bodies, cue-bound), arrows / a dimension / an outline
(Grease Pencil, own layer, cue-bound), the explainer_finish compositor, and a control pass with lines / normals / ids.

Checks: the camera never crosses geometry, instances included; the crowd is instanced (one host) and its
placement and the debris fall repeat exactly on a rebuild; simulations are baked inside the .blend; graphics are
hidden from the beauty scene (no render-visible Grease Pencil; control clay identity is in graphics_smoke) and appear only in their own layer, bound to the camera cue; the finish compositor is applied.
Run: .venv/bin/python tests/studio/combo2_smoke.py
"""
from pathlib import Path
import json
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from PIL import Image
from studio.common import blender_binary, read_json, write_json
from studio.project import init_project, project_dir, shot_path
from studio.blender import build_shot
from studio.generative.control import build_control
from studio.graphics import render_graphics

combo_src = (ROOT / 'tests/studio/combo_smoke.py').read_text()
sim_src = (ROOT / 'tests/studio/sim_bake_smoke.py').read_text()
REVEAL = eval(re.search(r"^REVEAL = (\{.*?\})\n(?=[A-Z])", combo_src, re.S | re.M).group(1))
CAMERA = eval(re.search(r"^CAMERA = (\{.*?\})\n(?=[A-Z])", combo_src, re.S | re.M).group(1))
DEBRIS = eval(re.search(r"^DEBRIS = (\{.*?\})\n(?=[A-Z])", sim_src, re.S | re.M).group(1))

AUTHOR = '''import bpy
from scatter import scatter
scene = bpy.context.scene
def box(name, loc, size):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc); o = bpy.context.object; o.scale = size; o.name = name; o['studio_id'] = name
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return o
road = box('road', (0, 0, -.5), (60, 60, 1)); road['studio_dim_role'] = 'none'
box('road.cutter', (0, 0, -.5), (12, 8, 3))
marker = bpy.data.objects.new('road.opening', None); marker.empty_display_type = 'CUBE'; marker.empty_display_size = 1
marker.scale = (6, 4, .5); marker.location = (0, 0, -.5); marker['studio_id'] = 'road.opening'; scene.collection.objects.link(marker)
box('concourse', (0, 6, -20), (30, 30, .5)); box('kiosk', (0, 12, -18.5), (2, 2, 3))
# a two-part worker (body + head) as one scatter source, instanced along both sides of the concourse
root = bpy.data.objects.new('worker.template', None); scene.collection.objects.link(root)
for part in (box('worker.body', (0, 0, .55), (.45, .3, 1.1)), box('worker.head', (0, 0, 1.3), (.25, .25, .25))):
    part.parent = root
points = [(x, y, -19.75) for x in (-11, -9, -7, 7, 9, 11) for y in (-6, -2, 2, 6, 10, 14, 18)]
scatter('crowd', [root], points=points, seed=4, scale=(0.9, 1.1))
cap = bpy.data.materials.new('cap')
bpy.ops.object.light_add(type='SUN', location=(0, 0, 30))
scene['studio_authored_animation'] = True
'''
BOUND = {'start_cue_id': 'cam-mouth', 'end_cue_id': 'cam-inside', 'start_offset_frames': 0, 'end_offset_frames': 10}
GRAPHICS = [
    {'graphic_id': 'kiosk_arrow', 'kind': 'arrow', 'anchors': [[0, 12, -12], 'kiosk'], 'start_frame': 0, 'end_frame': 1,
     'time_binding': BOUND, 'draw_on_s': 0.4},
    {'graphic_id': 'depth', 'kind': 'dimension', 'anchors': [[8, -4, -19.75], [8, -4, -0.5]], 'start_frame': 40},
    {'graphic_id': 'kiosk_outline', 'kind': 'outline', 'target': 'kiosk', 'start_frame': 40, 'color_srgb': [1.0, 0.85, 0.1]},
]
PROBE = '''import bpy, json, sys
sys.path.insert(0, %r)
from scatter import instance_count, digest
scene = bpy.context.scene
host = bpy.data.objects['scatter.crowd']
pieces = sorted((o for o in bpy.data.objects if o.name.startswith('StudioSim_debris.')), key=lambda o: o.name)
scene.frame_set(90)
gp = [o for o in bpy.data.objects if o.type == 'GREASEPENCIL']
print('PROBE ' + json.dumps({'instances': instance_count(host), 'digest': digest(host), 'host_role': host.get('studio_scene_role'),
    'debris_end': [round(v, 4) for p in pieces for v in p.matrix_world.translation],
    'baked': scene.rigidbody_world.point_cache.is_baked,
    'gp_roles': sorted({o.get('studio_scene_role') for o in gp}), 'gp_render_visible': [o.name for o in gp if not o.hide_render],
    'compositor': scene.compositing_node_group is not None}))
''' % str(ROOT / 'studio/blender_ops')


def probe(p, version):
    script = p / 'probe.py'; script.write_text(PROBE)
    out = subprocess.run([blender_binary(), '-b', str(version / 'scene.blend'), '--python', str(script)], capture_output=True, text=True).stdout
    return json.loads(next(line for line in out.splitlines() if line.startswith('PROBE '))[6:])


def alpha(path):
    return sum(1 for a in Image.open(path).getchannel('A').getdata() if a > 128)


checks = []
with tempfile.TemporaryDirectory(prefix='combo2-smoke-') as root:
    p = Path(init_project('combo2_test', {'request': 'Combination smoke 2', 'shots': [{'shot_id': 'dive', 'frame_count': 90}]}, root)['project_path'])
    style = read_json(p / 'style.json'); style.setdefault('look', {})['compositor'] = 'explainer_finish'; write_json(p / 'style.json', style)
    author = p / 'author.py'; author.write_text(AUTHOR)
    shot = read_json(shot_path(p, 'dive'))
    shot.update({'camera': CAMERA, 'actions': [REVEAL, DEBRIS], 'graphics': GRAPHICS})
    shot['render'].update({'look_preset': 'photoreal_interior'})
    write_json(shot_path(p, 'dive'), shot)
    built = build_shot(p, 'dive', author)
    version = p / 'shots/dive/versions' / built['scene_version']
    rig = read_json(version / 'camera_rig_report.json')
    move = read_json(version / 'camera_move_report.json')
    look = read_json(version / 'look_report.json')
    assert rig['gate_failures'] == [] and rig['summary']['pass_through_frames'] == 0, rig['gate_failures']
    sims = {r['action_id']: r for r in move['reveal']['simulations']}
    assert sims['debris']['baked'], sims
    assert look['passes']['compositor'].get('preset') == 'explainer_finish', look['passes']['compositor']
    checks += ['move_reveal_scatter_sim_graphics_build_together', 'finish_compositor_applied']

    graphics = {g['graphic_id']: g for g in read_json(version / 'graphics_report.json')['graphics']}
    cues = move['camera_cues']
    assert graphics['kiosk_arrow']['frames'] == [cues['cam-mouth'], cues['cam-inside'] + 10], (graphics['kiosk_arrow'], cues)
    checks.append('graphic_bound_to_camera_cue')

    state = probe(p, version)
    # 42 placements x 3 instanced objects (template empty, body, head)
    assert state['instances'] == 42 * 3 and state['host_role'] == 'scatter' and state['baked'], state
    assert state['gp_roles'] == ['graphic'] and state['gp_render_visible'] == [] and state['compositor'], state
    checks += ['crowd_instanced_one_host', 'simulation_baked_in_blend', 'graphics_hidden_in_beauty']

    control = build_control(p, 'dive', kinds=('depth', 'clay', 'lines', 'normal', 'id'), height=320)
    meta = read_json(Path(control['control_dir']) / 'control.json')
    assert sorted(meta['files']) == ['clay', 'depth', 'id', 'lines', 'normal'], sorted(meta['files'])
    checks.append('control_v2_kinds')  # graphics never reach it: graphics_smoke proves the clay is byte-identical without them

    layer = render_graphics(p, 'dive', height=320)
    frames = Path(layer['frames_dir'])
    first = cues['cam-mouth']
    early, late = alpha(frames / 'frame_000010.png'), alpha(frames / f'frame_{first + 15:06d}.png')
    assert early == 0 and late > 0, (early, late)
    checks.append('graphics_only_in_their_layer')

    again = build_shot(p, 'dive', author)
    state2 = probe(p, p / 'shots/dive/versions' / again['scene_version'])
    rig2 = read_json(p / 'shots/dive/versions' / again['scene_version'] / 'camera_rig_report.json')
    assert state2['digest'] == state['digest'] and state2['debris_end'] == state['debris_end'] and rig2['samples'] == rig['samples']
    checks.append('deterministic_rebuild')

print('STUDIO_COMBO2_SMOKE ' + json.dumps({'ok': True, 'checks': checks, 'instances': state['instances'], 'cues': {k: cues[k] for k in ('cam-mouth', 'cam-inside')},
                                         'alpha_px': {'f10': early, f'f{first + 15}': late}, 'control_far_m': round(meta['far'], 2)}))
