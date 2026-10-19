"""People do not pass through things (2026-10-19, the bike reel's mechanic went through the frame): a character placed
inside a crate stops the build (CHARACTER_THROUGH_OBJECT); with its hand declared on the crate's handle the body stands
clear of the crate and the hand holds the handle; a handle out of reach says so (CONTACT_UNREACHABLE); a crouch bends
the body with the feet planted. Real Blender builds.
Run: .venv/bin/python tests/studio/character_clearance_smoke.py
"""
from copy import deepcopy
from pathlib import Path
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, shot_path

FRAMES = 24
SCENE = {'world': {'kind': 'blockout', 'color': [0.3, 0.3, 0.35], 'strength': 0.8, 'samples': 16},
         'materials': {'floor': {'color': [0.5, 0.5, 0.5]}, 'wood': {'color': [0.6, 0.4, 0.2]}},
         'primitives': [{'id': 'floor', 'shape': 'box', 'size': [10, 10, 0.2], 'at': [0, 0, -0.1], 'material': 'floor', 'role': 'support'},
                        {'id': 'crate', 'shape': 'box', 'size': [0.6, 0.6, 1.0], 'at': [0, 0, 0.5], 'material': 'wood'},
                        {'id': 'handle', 'shape': 'box', 'size': [0.3, 0.04, 0.04], 'at': [0.1, 0.24, 1.04], 'material': 'wood'}],
         'characters': [{'id': 'worker', 'asset': 'quaternius_human', 'action': 'idle', 'at': [0.0, 0.2, 0], 'facing_deg': -90}]}
CAMERA = {'projection': 'perspective', 'movement': 'authored', 'target_anchor': None,
          'keys': [{'frame': 0, 'location': [3.5, -3.5, 1.6], 'target': [0, 0.3, 0.9]}, {'frame': FRAMES - 1, 'location': [3.5, -3.5, 1.6], 'target': [0, 0.3, 0.9]}]}


def build(p, scene):
    shot = read_json(shot_path(p, 's'))
    shot.update({'scene': scene, 'screen': {'subject': ['crate']}, 'camera': CAMERA})
    write_json(shot_path(p, 's'), shot)
    return build_shot(p, 's', None)


def failed(p, scene):
    try:
        build(p, scene)
    except StudioError as error:
        return error
    raise AssertionError('the build passed')


checks = []
with tempfile.TemporaryDirectory(prefix='clearance-smoke-') as root:
    p = Path(init_project('clear', {'request': 'clearance smoke', 'shots': [{'shot_id': 's', 'frame_count': FRAMES}]}, root)['project_path'])

    error = failed(p, SCENE)
    assert error.code == 'CHARACTER_THROUGH_OBJECT' and 'crate' in error.message, (error.code, error.message)
    checks.append(f'a body inside a crate stops the build ({error.message[:90]})')

    held = deepcopy(SCENE)
    held['characters'][0]['contacts'] = [{'limb': 'right_hand', 'target': 'handle', 'why': 'the worker holds the handle'}]
    # a room round everything (built inward): where the worker stands, not what he passes through (review 2026-10-19)
    held['primitives'].append({'id': 'room', 'shape': 'box', 'size': [6, 6, 3], 'at': [0, 0, 1.5], 'material': 'floor', 'flip_normals': True})
    built = build(p, held)
    version = p / 'shots/s/versions' / built['scene_version']
    pose = read_json(version / 'character_pose.json')['characters'][0]
    clear = read_json(version / 'character_clearance.json')
    assert pose.get('moved') and pose['moved']['distance_m'] > 0, pose
    assert all(e['error_m'] <= 0.01 for e in pose['errors']), pose['errors']
    assert clear['gate_failures'] == [] and not clear['characters']['worker'], clear
    checks.append(f"holding the handle the body stands clear: moved {pose['moved']['distance_m']} m, hand within "
                  f"{max(e['error_m'] for e in pose['errors'])} m")

    crouched = deepcopy(held)   # a handle low on the crate's back: crouched to it, knees clear of the crate
    crouched['characters'][0]['stance'] = 'crouch'
    crouched['primitives'][2].update({'size': [0.3, 0.04, 0.04], 'at': [0.1, 0.32, 0.62]})
    built = build(p, crouched)
    version = p / 'shots/s/versions' / built['scene_version']
    pose = read_json(version / 'character_pose.json')['characters'][0]
    assert pose['stance'] == 'crouch' and all(e['error_m'] <= 0.01 for e in pose['errors']), pose
    assert not read_json(version / 'character_clearance.json')['characters']['worker']
    checks.append('a crouch keeps the hand on the handle and the body clear')

    far = deepcopy(held)
    far['primitives'][2]['at'] = [0.1, -3.0, 1.04]
    error = failed(p, far)
    assert error.code == 'CONTACT_UNREACHABLE', (error.code, error.message)
    checks.append(f'a handle out of reach says so ({error.message[:80]})')

print('STUDIO_CHARACTER_CLEARANCE_SMOKE ' + json.dumps({'ok': True, 'checks': checks}, ensure_ascii=False))
