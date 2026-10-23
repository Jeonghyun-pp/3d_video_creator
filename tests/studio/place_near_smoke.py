"""A subject placed by where it shows (scene.instances near -> blender_ops/place_near.py): a stone asked to show at
(0.3, 0.3) of the frame, a quarter of its height tall, half way through a shot whose camera looks at a 6 m pillar; the
build moves it there once the camera is baked and the frame shows it so - the pillar behind it still in view.
- placed: shown within PLACE_TOL of the place and SIZE_TOL of the size (place_report.json).
Why (2026-10-23, submarine r3): a stone near the lens with the boat behind could not be put anywhere by world numbers.
No render beyond the probe's id pass, no paid call.
Run: .venv/bin/python tests/studio/place_near_smoke.py
"""
from pathlib import Path
import json
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import read_json, write_json
from studio.project import init_project, shot_path
from tests.decisions_fixture import settle   # builds stand on settled decisions (studio/decisions.require)

STONE = ROOT / 'projects/harness_validation/e2e/submarine_r3/subjects/stone/spec.json'
EYE, AIM = [0.0, -24.0, 3.0], [0.0, 0.0, 3.0]


def build(p, near):
    shot = read_json(shot_path(p, 's'))
    shot['scene'] = {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
                     'materials': {'stone': {'color': [0.6, 0.58, 0.55]}},
                     'primitives': [{'id': 'pillar', 'shape': 'box', 'size': [2.0, 2.0, 6.0], 'at': [0.0, 0.0, 3.0], 'material': 'stone'}],
                     'instances': [{'id': 'stone', 'subject': 'stone', 'at': [0.0, 0.0, 0.0], 'near': near}]}
    shot['camera'] = {'projection': 'perspective', 'movement': 'authored', 'target_anchor': None,
                      'keys': [{'frame': 0, 'location': EYE, 'target': AIM}, {'frame': 47, 'location': EYE, 'target': AIM}]}
    shot['screen'] = {'subject': ['pillar'], 'details': []}
    write_json(shot_path(p, 's'), shot)
    return build_shot(p, 's', None)


checks = []
with tempfile.TemporaryDirectory(prefix='place-near-smoke-') as root:
    p = Path(init_project('place_near', {'request': 'place near smoke', 'shots': [{'shot_id': 's', 'frame_count': 48}]}, root)['project_path'])
    settle(p)
    (p / 'subjects/stone').mkdir(parents=True)
    shutil.copy(STONE, p / 'subjects/stone/spec.json')
    built = build(p, {'at': 0.5, 'screen': [0.3, 0.3], 'size': 0.25})
    report = read_json(p / 'shots/s/versions' / built['scene_version'] / 'place_report.json')
    row = report['placed'][0]
    assert not report['gate_failures'], report
    assert abs(row['shown']['screen'][0] - 0.3) <= 0.05 and abs(row['shown']['screen'][1] - 0.3) <= 0.05, row
    assert abs(row['shown']['size'] - 0.25) <= 0.25 * 0.25 and 0 < row['depth_m'] < 24, row   # in front of the pillar
    checks.append(f"placed: shows at {row['shown']['screen']}, {row['shown']['size']} tall, {row['depth_m']} m from the lens")
    probe = read_json(p / 'shots/s/versions' / built['scene_version'] / 'frame_report.json')
    assert probe['frames'][0]['subject_share'] > 0.02, probe['frames'][0]   # the pillar is still there behind it
    checks.append('the pillar stays in view behind it')

print('STUDIO_PLACE_NEAR_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
