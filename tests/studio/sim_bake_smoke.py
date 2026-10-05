"""Baked simulations: debris falls through a road a reveal is opening, dust drifts, both baked into the .blend.

Checks: simulations bind to camera cues and bake at build; the saved file replays the same frames in any order
(render workers jump around); debris ends below the road (it fell through the cut, onto the concourse); dust is
'atmosphere' and stays out of the control depth range; an unbaked rigid body world refuses the build.
Run: ../.venv/bin/python tests/studio/sim_bake_smoke.py
"""
from pathlib import Path
import json
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.common import StudioError, blender_binary, read_json, write_json
from studio.project import init_project, shot_path
from studio.blender import build_shot
from studio.generative.control import build_control

reveal_src = (ROOT / 'tests/studio/reveal_smoke.py').read_text()
AUTHOR = re.search(r"AUTHOR = '''(.*?)'''", reveal_src, re.S).group(1)
REVEAL = {'action_id': 'open_road', 'type': 'reveal', 'targets': [{'instance_id': 'road', 'part_id': 'road'}],
          'start_frame': 0, 'end_frame': 1, 'easing': 'ease_in_out',
          'time_binding': {'start_cue_id': 'cam-wp0', 'end_cue_id': 'cam-mouth', 'start_offset_frames': 0, 'end_offset_frames': -4},
          'params': {'cutter_object_id': 'road.cutter', 'cap_material_id': 'cap', 'also_cut_overlapping': True,
                     'cutter_keys': [{'t': 0, 'scale': [0.02, 0.02, 1]}, {'t': 1, 'scale': [1, 1, 1]}]}}
DEBRIS = {'action_id': 'debris', 'type': 'simulate', 'targets': [{'instance_id': 'concourse', 'part_id': 'concourse'}],
          'start_frame': 0, 'end_frame': 1, 'easing': 'linear',
          'time_binding': {'start_cue_id': 'cam-mouth', 'end_cue_id': 'cam-inside', 'start_offset_frames': -3, 'end_offset_frames': 10},
          'params': {'kind': 'rigid_debris', 'region': [[3, -2, 0.6], [5, 2, 1.6]], 'count': 24, 'size_range': [0.2, 0.5], 'seed': 3}}
DUST = {'action_id': 'dust', 'type': 'simulate', 'targets': [{'instance_id': 'concourse', 'part_id': 'concourse'}],
        'start_frame': 0, 'end_frame': 1, 'easing': 'linear',
        'time_binding': {'start_cue_id': 'cam-mouth', 'end_cue_id': 'cam-inside', 'start_offset_frames': -3, 'end_offset_frames': 10},
        'params': {'kind': 'dust', 'region': [[2, -3, 0], [6, 3, 1]], 'count': 300, 'seed': 5}}
CAMERA = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [], 'energy': 'high',
          'move': {'type': 'dive_through', 'params': {'opening': 'road.opening', 'below': 'kiosk', 'above_m': 14, 'back_m': 10},
                   'style': 'archcutaway', 'lens_mm': 20, 'guards': {'max_hidden_s': 3.0},
                   'timing': {'profile': 'burst_settle', 'head_frac': 0.45, 'burst_frac': 0.2, 'burst_share': 0.6, 'hold_frac': 0.2}}}
PROBE = '''import bpy, json
scene = bpy.context.scene
pieces = sorted((o for o in bpy.data.objects if o.name.startswith('StudioSim_debris.')), key=lambda o: o.name)
out = {}
for f in (90, 10, 60, 1, 90):
    scene.frame_set(f)
    out.setdefault(str(f), []).append([round(v, 4) for p in pieces[:4] for v in p.matrix_world.translation])
dust = bpy.data.objects['StudioSim_dust']
scene.frame_set(90)
dg = bpy.context.evaluated_depsgraph_get()
dust_top = max((i.matrix_world.translation.z for i in dg.object_instances if i.is_instance and i.parent and i.parent.original == dust), default=None)
print('PROBE ' + json.dumps({'dust_top': dust_top, 'frames': out, 'z_end': [round(p.matrix_world.translation.z, 3) for p in pieces], 'dust_role': dust.get('studio_scene_role'),
                             'baked': scene.rigidbody_world.point_cache.is_baked, 'dust_target': dust.modifiers['dust'].bakes[0].bake_target}))
'''

checks = []
with tempfile.TemporaryDirectory(prefix='sim-smoke-') as root:
    p = Path(init_project('sim_test', {'request': 'Simulation smoke', 'shots': [{'shot_id': 'dive', 'frame_count': 90}]}, root)['project_path'])
    author = p / 'author.py'; author.write_text(AUTHOR)
    shot = read_json(shot_path(p, 'dive')); shot.update({'camera': CAMERA, 'actions': [REVEAL, DEBRIS, DUST]}); write_json(shot_path(p, 'dive'), shot)
    built = build_shot(p, 'dive', author)
    version = p / 'shots/dive/versions' / built['scene_version']
    move = read_json(version / 'camera_move_report.json')
    sims = {row['action_id']: row for row in move['reveal']['simulations']}
    assert sims['debris']['baked'] and sims['debris']['pieces'] == 24 and sims['dust']['baked'] and sims['dust']['bake_target'] == 'PACKED', sims
    rig = read_json(version / 'camera_rig_report.json')
    assert rig['gate_failures'] == [], rig['gate_failures']
    checks += ['simulations_bound_and_baked_at_build', 'camera_guards_pass_with_debris']

    script = p / 'probe.py'; script.write_text(PROBE)
    out = subprocess.run([blender_binary(), '-b', str(version / 'scene.blend'), '--python', str(script)], capture_output=True, text=True).stdout
    state = json.loads(next(line for line in out.splitlines() if line.startswith('PROBE '))[6:])
    assert state['frames']['90'][0] == state['frames']['90'][1], 'frame 90 must replay identically after jumping around'
    assert state['baked'] and state['dust_target'] == 'PACKED' and state['dust_role'] == 'atmosphere', state
    assert all(-19.8 < z < -1.0 for z in state['z_end']), state['z_end']   # through the opening (road top 0), above the concourse (-19.75)
    assert state['dust_top'] is not None and state['dust_top'] <= 1.0 + 0.04, state['dust_top']   # region top 1 m + grain: none escapes
    checks += ['saved_file_replays_any_order', 'debris_fell_through_opening', 'dust_is_atmosphere', 'dust_stays_below_its_ceiling']

    control = build_control(p, 'dive', kinds=('depth', 'clay'), height=200)
    meta = read_json(Path(control['control_dir']) / 'control.json')
    checks.append('control_builds_with_simulations')

# a collider a reveal is cutting is refused with a clear reason
with tempfile.TemporaryDirectory(prefix='sim-smoke-') as root:
    p = Path(init_project('sim_test3', {'request': 'Cut collider', 'shots': [{'shot_id': 'dive', 'frame_count': 90}]}, root)['project_path'])
    author = p / 'author.py'; author.write_text(AUTHOR)
    cut = json.loads(json.dumps(DEBRIS)); cut['targets'] = [{'instance_id': 'road', 'part_id': 'road'}]
    shot = read_json(shot_path(p, 'dive')); shot.update({'camera': CAMERA, 'actions': [REVEAL, cut]}); write_json(shot_path(p, 'dive'), shot)
    try:
        build_shot(p, 'dive', author)
        raise AssertionError('collider cut by a reveal accepted')
    except StudioError as error:
        assert 'cut by a reveal' in str(error), str(error)[-300:]
        checks.append('collider_cut_by_reveal_refused')

# an unbaked rigid body world is refused
UNBAKED = AUTHOR.replace("scene['studio_authored_animation'] = True", """with bpy.context.temp_override(scene=scene):
    bpy.ops.rigidbody.world_add()
rock = box('rock', (0, 0, 5), (1, 1, 1))
bpy.context.view_layer.objects.active = rock
with bpy.context.temp_override(active_object=rock, object=rock):
    bpy.ops.rigidbody.object_add()
cam = bpy.data.objects.new('cam', bpy.data.cameras.new('cam')); scene.collection.objects.link(cam); scene.camera = cam
scene['studio_authored_animation'] = True""")
with tempfile.TemporaryDirectory(prefix='sim-smoke-') as root:
    p = Path(init_project('sim_test2', {'request': 'Unbaked', 'shots': [{'shot_id': 'still', 'frame_count': 10}]}, root)['project_path'])
    author = p / 'author.py'; author.write_text(UNBAKED)
    try:
        build_shot(p, 'still', author)
        raise AssertionError('unbaked simulation accepted')
    except StudioError as error:
        assert 'SIMULATION_NOT_BAKED' in str(error), str(error)[-300:]
        checks.append('unbaked_rigid_body_refused')

print('STUDIO_SIM_BAKE_SMOKE ' + json.dumps({'ok': True, 'checks': checks, 'z_end_sample': state['z_end'][:6], 'control_far_m': round(meta['far'], 2)}))
