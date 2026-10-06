"""A strain wave (harmonic) gear built and driven from data: `harmonic_spec` (100-tooth flexspline, 102-tooth circular
spline, module 0.5 mm), a drive turning the wave generator once. The flexspline must turn -7.2 deg (two teeth the other
way), its ellipse must follow the wave generator (shape keys keyed per frame), and no two parts may pass through each other
over the motion - the flexspline's evaluated, flexed shape is what is checked. A circular spline half a tooth off and a
flexspline pushed further than its wave generator must both be refused (MECHANISM_INTERFERENCE).
Run: .venv/bin/python tests/studio/harmonic_smoke.py
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import StudioError, blender_binary, read_json, write_json
from studio.mechanisms import harmonic_spec
from studio.project import init_project, shot_path
from studio.subjects import spec_path

SCENE = {'world': {'kind': 'blockout', 'color': [0.2, 0.22, 0.26], 'strength': 0.6, 'samples': 16, 'sun': {'energy': 2.0, 'color': [1, 0.95, 0.9]}},
         'instances': [{'id': 'hd', 'subject': 'hd', 'at': [0, 0, 0]}]}
CAMERA = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [],
          'move': {'type': 'turntable', 'params': {'target': 'hd', 'sweep_deg': 30}, 'lens_mm': 50}}
DRIVE = {'action_id': 'turn', 'type': 'drive', 'targets': [{'instance_id': 'hd', 'part_id': 'wave_generator'}], 'start_frame': 0, 'end_frame': 47,
         'easing': 'linear', 'params': {'drives': [{'joint': 'j_wave', 'keys': [{'t': 0, 'value': 0}, {'t': 1, 'value': 360}]}]}}
PROBE = '''import bpy, json, math
scene = bpy.context.scene
flex = bpy.data.objects['hd/flexspline']
out = {}
for frame in (1, 13, 25):
    scene.frame_set(frame)
    deps = bpy.context.evaluated_depsgraph_get()
    mesh = flex.evaluated_get(deps).to_mesh()
    far = max(mesh.vertices, key=lambda v: v.co.x * v.co.x + v.co.y * v.co.y)
    world = flex.matrix_world @ far.co
    wave = bpy.data.objects['hd/j_wave.pivot'].rotation_quaternion
    out[frame] = {'bulge_deg': math.degrees(math.atan2(world.y, world.x)), 'wave_deg': math.degrees(2 * math.atan2(wave.z, wave.w))}
    flex.evaluated_get(deps).to_mesh_clear()
print('PROBE ' + json.dumps(out))
'''


def axis_gap(a, b):   # an ellipse's major axis is a line: compare mod 180
    return abs((a - b + 90) % 180 - 90)


checks = []
with tempfile.TemporaryDirectory(prefix='harmonic-smoke-') as root:
    p = Path(init_project('harmonic_test', {'request': 'harmonic smoke', 'shots': [{'shot_id': 's', 'frame_count': 48}]}, root)['project_path'])
    spec = harmonic_spec('hd', 0.0005, 100)
    spec_path(p, 'hd').parent.mkdir(parents=True, exist_ok=True); write_json(spec_path(p, 'hd'), spec)
    shot = read_json(shot_path(p, 's')); shot.update({'scene': SCENE, 'camera': CAMERA, 'actions': [DRIVE]}); write_json(shot_path(p, 's'), shot)
    built = build_shot(p, 's', None)
    version = p / 'shots/s/versions' / built['scene_version']
    row = read_json(version / 'kinematics_report.json')['drives'][0]
    assert row['interference'] == [] and row['pairs_checked'] == 3, row
    assert abs(row['final_values']['j_flex'] - (-7.2)) < 1e-3, row['final_values']
    checks.append('flexspline_lags_two_teeth_per_turn_without_clash')

    script = version.parent / 'probe.py'; script.write_text(PROBE)
    out = subprocess.run([blender_binary(), '-b', str(version / 'scene.blend'), '--python', str(script)], capture_output=True, text=True).stdout
    state = json.loads(next(line for line in out.splitlines() if line.startswith('PROBE '))[6:])
    for frame, s in state.items():
        assert axis_gap(s['bulge_deg'], s['wave_deg']) < 4.0, (frame, s)   # the flexspline bulges where the wave generator points
    checks.append('ellipse_follows_the_wave_generator')

    for name, mutate in (('circular spline half a tooth off', lambda sp: sp['builders'][0]['params'].__setitem__('phase_deg', 0.0)),
                         ('flexspline pushed past its wave generator', lambda sp: sp['couplings'][0].__setitem__('deflection_m', 0.0008))):
        bad = json.loads(json.dumps(spec)); mutate(bad); write_json(spec_path(p, 'hd'), bad)
        try:
            build_shot(p, 's', None); raise AssertionError(f'{name} was accepted')
        except StudioError as error:
            assert error.code == 'MECHANISM_INTERFERENCE', (name, error.code, error.message[:300])
    checks.append('wrong_phase_and_wrong_deflection_refused')

print('STUDIO_HARMONIC_SMOKE ' + json.dumps({'ok': True, 'checks': checks, 'final_values': row['final_values'], 'probe': state}))
