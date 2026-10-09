"""A declared exaggeration in a real build: a scene row with display_scale and no label is refused under the default
explain policy (DISPLAY_SCALE_UNDISCLOSED); a label anchored to it passes; a project policy may soften it to a warning.
Run: .venv/bin/python tests/studio/display_scale_build_smoke.py"""
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, shot_path

SCENE = {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
         'materials': {'m': {'color': [0.7, 0.4, 0.2]}},
         'primitives': [{'id': 'slab', 'shape': 'box', 'size': [2, 2, 0.2], 'at': [0, 0, 0.1], 'material': 'm'},
                        {'id': 'buffer', 'shape': 'box', 'size': [2, 2, 0.09], 'at': [0, 0, 0.245], 'material': 'm',
                         'display_scale': {'factor': 3, 'reason': '30 mm buffer reads at phone size'}}]}
CAMERA = {'projection': 'perspective', 'movement': 'authored', 'target_anchor': None,
          'keys': [{'frame': 0, 'location': [0, -4, 1], 'target': [0, 0, 0.2]}, {'frame': 29, 'location': [0, -4, 1], 'target': [0, 0, 0.2]}]}
LABEL = {'label_id': 'buffer', 'text': '완충재 30 mm (확대 표현 ×3)', 'anchor': 'buffer', 'start_frame': 0, 'end_frame': 30, 'occlusion_policy': 'show'}


def build(p, labels):
    shot = read_json(shot_path(p, 's'))
    shot.update({'scene': SCENE, 'camera': CAMERA, 'labels': labels})
    write_json(shot_path(p, 's'), shot)
    return build_shot(p, 's', None)


with tempfile.TemporaryDirectory(prefix='display-scale-smoke-') as root:
    p = Path(init_project('display_scale_test', {'request': 'display scale smoke', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, root)['project_path'])
    try:
        build(p, [])
        raise AssertionError('an undisclosed exaggeration built')
    except StudioError as error:
        assert error.code == 'DISPLAY_SCALE_UNDISCLOSED' and 'buffer x3' in error.message, (error.code, error.message[:300])
    built = build(p, [LABEL])
    assert not any(w.startswith('DISPLAY_SCALE') for w in built['warnings']), built['warnings']
    declared = read_json(p / 'shots/s/versions' / built['scene_version'] / 'display_scale.json')
    assert declared == [{'id': 'buffer', 'factor': 3.0, 'reason': '30 mm buffer reads at phone size'}], declared
    project = read_json(p / 'project.json')
    project.setdefault('policy', {})['gates'] = {'DISPLAY_SCALE_UNDISCLOSED': 'warn'}
    write_json(p / 'project.json', project)
    soft = build(p, [])
    assert any(w.startswith('DISPLAY_SCALE_UNDISCLOSED') for w in soft['warnings']), soft['warnings']
print('display_scale build smoke OK')
