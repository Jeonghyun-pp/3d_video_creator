"""Patterns in a real build: a shot whose role is `explain` and whose subject is tiny gets SCREEN_PATTERN_OFF (advice);
declaring the departure silences it; the build result and the storyboard sheet carry the comparison.
Run: .venv/bin/python tests/studio/pattern_build_smoke.py"""
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio import patterns
from studio.blender import build_shot
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, shot_path
from tests.decisions_fixture import settle   # builds stand on settled decisions (studio/decisions.require)
from studio.storyboard import propose

SCENE = {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
         'materials': {'m': {'color': [0.7, 0.4, 0.2]}},
         'primitives': [{'id': 'part', 'shape': 'box', 'size': [0.3, 0.3, 0.3], 'at': [0, 0, 0.15], 'material': 'm'},
                        {'id': 'floor', 'shape': 'box', 'size': [20, 20, 0.2], 'at': [0, 0, -0.1], 'material': 'm'}]}
CAMERA = {'projection': 'perspective', 'movement': 'authored', 'target_anchor': None,
          'keys': [{'frame': 0, 'location': [0, -12, 3], 'target': [0, 0, 0.15]}, {'frame': 29, 'location': [0, -12, 3], 'target': [0, 0, 0.15]}]}

assert patterns.library_name(), 'a pattern library must exist (library/patterns)'


def build(p, **screen):
    shot = read_json(shot_path(p, 's'))
    shot.update({'scene': SCENE, 'camera': CAMERA, 'screen': {'subject': ['part'], 'role': 'explain', **screen}})
    write_json(shot_path(p, 's'), shot)
    return build_shot(p, 's', None)


with tempfile.TemporaryDirectory(prefix='pattern-smoke-') as root:
    p = Path(init_project('pattern_test', {'request': 'pattern smoke', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, root)['project_path'])
    settle(p)
    from tests.decisions_fixture import pick_concept   # every storyboard stands on a picked concept (concept.require)
    pick_concept(p, 's')
    small = build(p)
    off = [w for w in small['warnings'] if w.startswith('SCREEN_PATTERN_OFF')]
    assert off and 'height_share' in off[0], small['warnings']
    assert small['screen']['pattern']['measured_on']['source'] == 'screen.subject', small['screen']['pattern']['measured_on']
    assert any(w.startswith('PATTERN_UNREAD') for w in small['warnings']), small['warnings']
    rows = {r['item']: r for r in small['screen']['pattern']['rows']}
    assert rows['height_share']['position'] == 'outside' and rows['height_share']['off'], rows['height_share']
    departed = build(p, pattern_departs=[{'item': 'height_share', 'why': 'the part shown small inside its room on purpose'},
                                         {'item': 'area_share', 'why': 'the part shown small inside its room on purpose'},
                                         {'item': 'width_share', 'why': 'the part shown small inside its room on purpose'}])
    assert not any(w.startswith('PATTERN_UNREAD') for w in departed['warnings']), departed['warnings']   # a record now
    still_off = [r for r in departed['screen']['pattern']['rows'] if r['off']]
    assert not any(r['item'] in ('height_share', 'area_share', 'width_share') for r in still_off), still_off
    # a gate made an error refuses the build before its version is committed (2026-10-12 audit): shot.json keeps the
    # version it had, the attempt is filed as failed_*, and no new vNNNN appears
    project = read_json(p / 'project.json')
    project.setdefault('policy', {})['gates'] = {'SCREEN_PATTERN_OFF': 'error'}
    write_json(p / 'project.json', project)
    before = sorted(d.name for d in (p / 'shots/s/versions').iterdir())
    try:
        build(p)
        raise AssertionError('a refused build was committed')
    except StudioError as error:
        assert error.code == 'SCREEN_PATTERN_OFF', error.code
    after = sorted(d.name for d in (p / 'shots/s/versions').iterdir())
    assert [d for d in after if d not in before and d.startswith('failed_')] and not [d for d in after if d not in before and d.startswith('v')], (before, after)
    assert read_json(shot_path(p, 's'))['scene_version'] == departed['scene_version']
    project['policy'].pop('gates')
    write_json(p / 'project.json', project)
    sheet = propose(p, 's', [0.0, 0.5, 1.0])
    text = Path(sheet['sheet']).read_text()
    assert 'Against explain shots' in text and '| height_share |' in text and 'declared departure' in text, text[-800:]
    assert sheet['pattern']['section'] == 'explain'
print('pattern build smoke OK')
