"""A declared exaggeration in a real build: a scene row with display_scale and no label is refused under the default
explain policy (DISPLAY_SCALE_UNDISCLOSED) when the shot's line speaks of it (its size is then a claim); with no line
about it the build says it and goes on (the user, 2026-10-14: a size-exaggeration label "상관없을듯" unless the narration
claims the size); a label anchored to it passes; a project policy may soften the refusal to a warning.
Run: .venv/bin/python tests/studio/display_scale_build_smoke.py"""
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, shot_path
from tests.decisions_fixture import settle   # builds stand on settled decisions (studio/decisions.require)

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
    settle(p)
    said = build(p, [])   # no line speaks of the buffer: said, not refused
    assert any(w.startswith('DISPLAY_SCALE_UNDISCLOSED') and 'no line of the shot speaks of it' in w for w in said['warnings']), said['warnings']
    # the shot's line speaks of the buffer (cheat_core.locked reads it from the facts; tests/test_cheats.py covers that)
    from unittest import mock
    import studio.cheat_core as cheat_core
    spoken = mock.patch.object(cheat_core, 'locked', lambda path, shot: {'ids': ['buffer'], 'structure': [], 'pose': [], 'state': []})
    spoken.start()
    try:
        build(p, [])
        raise AssertionError('an undisclosed exaggeration of what the line speaks of built')
    except StudioError as error:
        assert error.code == 'DISPLAY_SCALE_UNDISCLOSED' and 'buffer x3' in error.message, (error.code, error.message[:300])
    built = build(p, [LABEL])   # labelled: passes, spoken of or not
    assert not any(w.startswith('DISPLAY_SCALE') for w in built['warnings']), built['warnings']
    declared = read_json(p / 'shots/s/versions' / built['scene_version'] / 'display_scale.json')
    assert declared == [{'id': 'buffer', 'factor': 3.0, 'reason': '30 mm buffer reads at phone size'}], declared
    project = read_json(p / 'project.json')
    project.setdefault('policy', {})['gates'] = {'DISPLAY_SCALE_UNDISCLOSED': 'warn'}
    write_json(p / 'project.json', project)
    soft = build(p, [])
    assert any(w.startswith('DISPLAY_SCALE_UNDISCLOSED') for w in soft['warnings']), soft['warnings']
print('display_scale build smoke OK')
