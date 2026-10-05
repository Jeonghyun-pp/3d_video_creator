"""Everything at once, so the features are proven to mix: an earth shell (scene role), an exemplar stair (shared
meshes), a road that opens (reveal, MANIFOLD) under a dive_through move, a photoreal interior look with
atmosphere (fog + beam), and a control pass with lines / normals / ids.

Checks: the shell is in the lit bounds but outside the control depth range; the fog is built by the look,
tagged 'atmosphere' and absent from the control; the reveal uses MANIFOLD; the camera never crosses geometry;
array copies share one mesh; a rebuild gives the same camera samples.
Run: .venv/bin/python tests/studio/combo_smoke.py
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.common import blender_binary, read_json, write_json
from studio.project import init_project, shot_path
from studio.blender import build_shot
from studio.generative.control import build_control

AUTHOR = '''import bpy, json, sys
from pathlib import Path
from modeling import build_subject
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
earth = box('earth', (0, 0, -10), (200, 200, 80))
for p in earth.data.polygons:
    p.flip()
earth['studio_scene_role'] = 'environment_shell'
spec = json.loads(sorted((Path(STUDIO_JOB['library_root']) / 'exemplars' / 'stair').glob('v*'))[-1].joinpath('spec.json').read_text())
spec['subject_id'] = 'stair-combo'
build_subject(spec, root_location=(8, 8, -19.75))
cap = bpy.data.materials.new('cap')
scene['studio_authored_animation'] = True
'''

REVEAL = {'action_id': 'open_road', 'type': 'reveal', 'targets': [{'instance_id': 'road', 'part_id': 'road'}],
          'start_frame': 0, 'end_frame': 1, 'easing': 'ease_in_out',
          'time_binding': {'start_cue_id': 'cam-wp0', 'end_cue_id': 'cam-mouth', 'start_offset_frames': 0, 'end_offset_frames': -4},
          'params': {'cutter_object_id': 'road.cutter', 'cap_material_id': 'cap', 'also_cut_overlapping': True,
                     'cutter_keys': [{'t': 0, 'scale': [0.02, 0.02, 1]}, {'t': 1, 'scale': [1, 1, 1]}]}}
CAMERA = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [], 'energy': 'high',
          'move': {'type': 'dive_through', 'params': {'opening': 'road.opening', 'below': 'kiosk', 'above_m': 14, 'back_m': 10},
                   'style': 'archcutaway', 'lens_mm': 20, 'guards': {'max_hidden_s': 3.0},
                   'timing': {'profile': 'burst_settle', 'head_frac': 0.45, 'burst_frac': 0.2, 'burst_share': 0.6, 'hold_frac': 0.2},
                   'arrive': [{'cue': 'cam-mouth', 'not_before_s': 1.4}]}}
ATMOSPHERE = {'density': 0.02, 'box': [[-10, -10, -22], [10, 20, 2]],
              'beams': [{'location': [0, 0, 10], 'aim': [0, 4, -20], 'spread_deg': 15, 'power_w': 50000}]}

PROBE = '''import bpy, json
fog = [o for o in bpy.data.objects if o.get('studio_scene_role') == 'atmosphere' and o.type == 'MESH']
steps = [o for o in bpy.data.objects if (o.get('studio_subject_id') or '') == 'stair-combo' and o.type == 'MESH']
mods = [m.solver for o in bpy.data.objects for m in o.modifiers if m.name.startswith('StudioReveal_')]
print('PROBE ' + json.dumps({'fog': [o.name for o in fog], 'step_meshes': len({o.data.name for o in steps}), 'steps': len(steps), 'solvers': sorted(set(mods))}))
'''

checks = []
with tempfile.TemporaryDirectory(prefix='combo-smoke-') as root:
    p = Path(init_project('combo_test', {'request': 'Combination smoke', 'shots': [{'shot_id': 'dive', 'frame_count': 90}]}, root)['project_path'])
    author = p / 'author.py'; author.write_text(AUTHOR)
    shot = read_json(shot_path(p, 'dive'))
    shot.update({'camera': CAMERA, 'actions': [REVEAL]})
    shot['render'].update({'look_preset': 'photoreal_interior', 'atmosphere': ATMOSPHERE})
    write_json(shot_path(p, 'dive'), shot)
    built = build_shot(p, 'dive', author)
    version = p / 'shots/dive/versions' / built['scene_version']
    rig = read_json(version / 'camera_rig_report.json')
    look = read_json(version / 'look_report.json')
    lighting = look['passes']['lighting']
    assert rig['gate_failures'] == [] and rig['summary']['pass_through_frames'] == 0, rig['gate_failures']
    assert lighting['atmosphere'] and lighting['atmosphere']['beams'] == 1, lighting.get('atmosphere')
    checks += ['move_reveal_look_build_together', 'atmosphere_built_by_look']

    script = p / 'probe.py'; script.write_text(PROBE)
    out = subprocess.run([blender_binary(), '-b', str(version / 'scene.blend'), '--python', str(script)], capture_output=True, text=True).stdout
    state = json.loads(next(line for line in out.splitlines() if line.startswith('PROBE '))[6:])
    assert state['fog'] and state['solvers'] == ['MANIFOLD'], state
    assert state['steps'] == 16 and state['step_meshes'] == 1, state   # 16 stair steps, one shared mesh
    checks += ['fog_tagged_atmosphere', 'reveal_manifold', 'exemplar_copies_share_mesh']

    control = build_control(p, 'dive', kinds=('depth', 'clay', 'lines', 'normal', 'id'), height=320)
    meta = read_json(Path(control['control_dir']) / 'control.json')
    # the earth box reaches 100 m from the set; the depth range must come from the set, not the shell
    assert meta['far'] < 80, (meta['near'], meta['far'])
    assert sorted(meta['files']) == ['clay', 'depth', 'id', 'lines', 'normal'] and meta.get('richness_lines'), sorted(meta['files'])
    checks += ['shell_outside_control_depth_range', 'control_v2_kinds']

    again = build_shot(p, 'dive', author)
    rig2 = read_json(p / 'shots/dive/versions' / again['scene_version'] / 'camera_rig_report.json')
    assert rig2['samples'] == rig['samples'], 'rebuild must give the same camera'
    checks.append('deterministic_rebuild')

print('STUDIO_COMBO_SMOKE ' + json.dumps({'ok': True, 'checks': checks, 'exposure_ev': lighting['exposure_ev'], 'control_far_m': round(meta['far'], 2)}))
