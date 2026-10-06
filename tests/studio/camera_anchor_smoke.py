"""camera.target_anchor on a keyed camera, built from data: keys without a target aim at the anchor, and a camera that
loses the anchor fails the build (CAMERA_ANCHOR_OUT_OF_VIEW). An anchor on a rig or move shot is refused (nothing reads it).
Run: .venv/bin/python tests/studio/camera_anchor_smoke.py
"""
from pathlib import Path
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, shot_path, validate_shot

SCENE = {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
         'materials': {'m': {'color': [0.7, 0.4, 0.2]}},
         'primitives': [{'id': 'pump', 'shape': 'box', 'size': [1, 1, 1], 'at': [3, 0, 0.5], 'material': 'm'},
                        {'id': 'floor', 'shape': 'box', 'size': [20, 20, 0.2], 'at': [0, 0, -0.1], 'material': 'm'}]}
KEYS = [{'frame': 0, 'location': [-6, -8, 3]}, {'frame': 29, 'location': [8, -8, 3]}]

checks = []
with tempfile.TemporaryDirectory(prefix='anchor-smoke-') as root:
    p = Path(init_project('anchor_test', {'request': 'anchor smoke', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, root)['project_path'])
    shot = read_json(shot_path(p, 's'))
    shot.update({'scene': SCENE, 'camera': {'projection': 'perspective', 'movement': 'authored', 'target_anchor': 'pump/center', 'keys': KEYS}})
    write_json(shot_path(p, 's'), shot)
    built = build_shot(p, 's', None)
    checks.append('keys_without_target_aim_at_the_anchor')

    away = json.loads(json.dumps(shot)); away['camera']['keys'] = [dict(k, target=[k['location'][0], 10, 3]) for k in KEYS]  # looks past the pump
    write_json(shot_path(p, 's'), away)
    try:
        build_shot(p, 's', None); raise AssertionError('a camera that never sees its anchor was built')
    except StudioError as error:
        assert error.code == 'CAMERA_ANCHOR_OUT_OF_VIEW', (error.code, error.message[:300])
    checks.append('lost_anchor_fails_the_build')

    no_anchor = json.loads(json.dumps(shot)); no_anchor['camera']['target_anchor'] = None
    try:
        validate_shot(no_anchor); raise AssertionError('a key without target or anchor was accepted')
    except StudioError as error:
        assert 'needs camera.target_anchor' in error.message
    on_move = json.loads(json.dumps(shot))
    on_move['camera'] = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': 'pump/center', 'keys': [],
                         'move': {'type': 'turntable', 'params': {'target': 'pump'}}}
    try:
        validate_shot(on_move); raise AssertionError('an anchor on a move was accepted')
    except StudioError as error:
        assert 'target_anchor' in error.message
    checks.append('anchor_refused_where_nothing_reads_it')

print('STUDIO_CAMERA_ANCHOR_SMOKE ' + json.dumps({'ok': True, 'checks': checks, 'version': built['scene_version']}))
