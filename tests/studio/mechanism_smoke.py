"""A mechanism built and moved from data alone: `subject planetary` computes an 18/27/72 x3 reducer spec, a layout
instance places it, a `drive` action turns the sun, and the build keys every joint through the couplings. The carrier
must turn at sun x 18/90, the planets must spin backwards on it, and no tooth may pass through another over the motion.
A planet set half a tooth off must be refused with MECHANISM_INTERFERENCE.
Run: .venv/bin/python tests/studio/mechanism_smoke.py
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
from studio.mechanisms import planetary_spec
from studio.project import init_project, shot_path
from studio.subjects import spec_path

SUN_TURNS = 1.0
SCENE = {'world': {'kind': 'blockout', 'color': [0.2, 0.22, 0.26], 'strength': 0.6, 'samples': 16, 'sun': {'energy': 2.0, 'color': [1, 0.95, 0.9]}},
         'instances': [{'id': 'reducer', 'subject': 'reducer', 'at': [0, 0, 0]}]}
CAMERA = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [],
          'move': {'type': 'turntable', 'params': {'target': 'reducer', 'sweep_deg': 40}, 'lens_mm': 50}}   # framed from the reducer's box
DRIVE = {'action_id': 'turn', 'type': 'drive', 'targets': [{'instance_id': 'reducer', 'part_id': 'sun'}], 'start_frame': 0, 'end_frame': 47,
         'easing': 'linear', 'params': {'drives': [{'joint': 'j_sun', 'keys': [{'t': 0, 'value': 0}, {'t': 1, 'value': 360 * SUN_TURNS}]}]}}
PROBE = '''import bpy, json, math
scene = bpy.context.scene
out = {}
for frame in (1, 25, 48):
    scene.frame_set(frame)
    row = {}
    for o in bpy.data.objects:
        if o.get('studio_joint'):
            q = o.rotation_quaternion
            row[o['studio_joint']] = math.degrees(2 * math.atan2(q.z, q.w))
    out[frame] = row
planet = bpy.data.objects.get('reducer/planet_0')
scene.frame_set(1); a = planet.matrix_world.translation.copy()
scene.frame_set(48); b = planet.matrix_world.translation.copy()
out['planet_orbit_deg'] = math.degrees(math.atan2(b.y, b.x) - math.atan2(a.y, a.x))
print('PROBE ' + json.dumps(out))
'''


def wrap(deg):
    return (deg + 180) % 360 - 180


checks = []
with tempfile.TemporaryDirectory(prefix='mechanism-smoke-') as root:
    p = Path(init_project('mechanism_test', {'request': 'mechanism smoke', 'shots': [{'shot_id': 's', 'frame_count': 48}]}, root)['project_path'])
    spec = planetary_spec('reducer', 0.002, 18, 27, 72, 3)
    spec_path(p, 'reducer').parent.mkdir(parents=True, exist_ok=True)
    write_json(spec_path(p, 'reducer'), spec)
    shot = read_json(shot_path(p, 's'))
    shot.update({'scene': SCENE, 'camera': CAMERA, 'actions': [DRIVE]})
    write_json(shot_path(p, 's'), shot)

    built = build_shot(p, 's', None)
    version = p / 'shots/s/versions' / built['scene_version']
    report = read_json(version / 'kinematics_report.json')
    row = report['drives'][0] if isinstance(report, dict) else report[0]
    assert row['interference'] == [] and row['pairs_checked'] == 20, row      # 7 parts, every pair on different bodies (rim + teeth share the root)
    assert abs(row['final_values']['j_carrier'] - 72.0) < 0.01, row['final_values']           # 360 x 18 / (18 + 72)
    assert abs(row['final_values']['j_planet_0'] - (-(360 - 72) * 18 / 27)) < 0.01, row['final_values']
    checks.append('carrier_ratio_and_planet_spin')
    move = read_json(version / 'camera_move_report.json')
    assert move['ok'] and 0.2 < move['notes']['distance_m'] < 1.0, move.get('notes')      # a 16 cm reducer framed from ~half a metre
    checks.append('turntable_fitted_to_the_subject')

    script = version.parent / 'probe.py'; script.write_text(PROBE)
    out = subprocess.run([blender_binary(), '-b', str(version / 'scene.blend'), '--python', str(script)], capture_output=True, text=True).stdout
    state = json.loads(next(line for line in out.splitlines() if line.startswith('PROBE '))[6:])
    mid = state['25']
    assert abs(wrap(mid['j_carrier'] * 5 - mid['j_sun'])) < 0.25, mid      # keys hold the ratio between the ends (angles read mod 360)
    assert abs(wrap(state['planet_orbit_deg']) - 72.0) < 0.5, state['planet_orbit_deg']   # the planet rides the carrier
    checks.append('saved_scene_keys_follow_the_couplings')

    bad = json.loads(json.dumps(spec))
    planet = next(b for b in bad['builders'] if b['part_id'] == 'planet_1')
    planet['transform']['rotation_deg'][2] += 180.0 / 27                    # half a tooth: tooth tip lands on a tooth tip
    write_json(spec_path(p, 'reducer'), bad)
    try:
        build_shot(p, 's', None); raise AssertionError('a mis-phased planet was accepted')
    except StudioError as error:
        assert error.code == 'MECHANISM_INTERFERENCE', (error.code, str(error)[:300])
    checks.append('mis_phased_planet_refused')

    solid = json.loads(json.dumps(spec))
    rim = next(b for b in solid['builders'] if b['part_id'] == 'ring_rim')
    rim['params'].update(closed_profile=False, cap_start=True, cap_end=True)   # the rim capped into a plate the planets sit inside
    write_json(spec_path(p, 'reducer'), solid)
    try:
        build_shot(p, 's', None); raise AssertionError('a part no coupling names was let through the planets')
    except StudioError as error:
        assert error.code == 'MECHANISM_INTERFERENCE' and 'ring_rim' in error.message, (error.code, error.message[:300])
    checks.append('uncoupled_part_in_the_way_refused')

print('STUDIO_MECHANISM_SMOKE ' + json.dumps({'ok': True, 'checks': checks, 'final_values': row['final_values']}))
