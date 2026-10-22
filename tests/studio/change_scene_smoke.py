"""What a line explains happens on screen (direction.change -> blender_ops/change_scene.py -> change_moves.screen_lines):
a tank fills and a plate turns, made by the build and read on the probe's frames at each change's start and end.
- fills: a level 0 -> 0.9 makes '<tank>/fill', water-coloured, growing on screen; the build passes.
- turns: a plate turned 60 deg about z reads; turned 1 deg it is refused (CHANGE_UNSHOWN), with why.
- one motion: a plate turned about its own height while it rises - two changes of one object, keyed together.
- again: a second build of the same shot makes the same change (what an earlier run made is removed first).
No render beyond the probe's id pass, no paid call.
Run: .venv/bin/python tests/studio/change_scene_smoke.py
"""
from pathlib import Path
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, shot_path
from tests.decisions_fixture import settle   # builds stand on settled decisions (studio/decisions.require)

TANK = {'id': 'tank', 'shape': 'box', 'size': [2.0, 2.0, 3.0], 'at': [-1.8, 0.0, 1.5], 'material': 'steel'}
PLATE = {'id': 'plate', 'shape': 'box', 'size': [2.4, 0.2, 2.0], 'at': [1.8, 0.0, 1.0], 'material': 'steel'}
FILL = {'id': 'fill_tank', 'parts': ['tank'], 'kind': 'level', 'from': 0.0, 'to': 0.9, 'at': [0.1, 0.9], 'why': 'the tank fills'}
TURN = {'id': 'turn_plate', 'parts': ['plate'], 'kind': 'turn', 'from': 0, 'to': 60, 'axis': 'z', 'at': [0.1, 0.9], 'why': 'it swings'}


def build(p, changes):
    shot = read_json(shot_path(p, 's'))
    shot['scene'] = {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
                     'materials': {'steel': {'color': [0.55, 0.55, 0.58]}}, 'primitives': [TANK, PLATE]}
    shot['camera'] = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [],
                      'move': {'type': 'slide', 'params': {'target': 'tank', 'span': 0.0, 'fill': 0.3, 'elevation_deg': 10.0,
                                                           'azimuth_deg': 20.0}, 'lens_mm': 35, 'timing': {'profile': 'ease_in_out'}}}
    shot['screen'] = {'subject': ['tank', 'plate'], 'details': []}
    shot['direction'] = {'change': changes}
    write_json(shot_path(p, 's'), shot)
    built = build_shot(p, 's', None)
    return p / 'shots/s/versions' / built['scene_version'], built


def px(version, frame_no, ident):
    probed = {f['frame']: f for f in read_json(version / 'frame_report.json')['frames']}
    return ((probed[frame_no].get('shapes') or {}).get(f'key:{ident}') or {}).get('px') or 0


checks = []
with tempfile.TemporaryDirectory(prefix='change-scene-smoke-') as root:
    p = Path(init_project('change_scene', {'request': 'change scene smoke', 'shots': [{'shot_id': 's', 'frame_count': 61}]}, root)['project_path'])
    settle(p)
    v1, built = build(p, [FILL, TURN])
    made = read_json(v1 / 'change_report.json')['changes']
    assert [r['id'] for r in made] == ['fill_tank', 'turn_plate'], made
    assert made[0]['made'] == ['tank/fill'], made[0]
    start, end = px(v1, 6, 'tank/fill'), px(v1, 54, 'tank/fill')
    assert end > 4 * max(start, 1), ('the water grows on screen', start, end)
    assert not [w for w in built.get('warnings', []) if w.startswith(('CHANGE_UNSHOWN', 'CHANGE_HIDDEN', 'CHANGE_UNMADE'))], built.get('warnings')
    checks.append('a_tank_fills_and_a_plate_turns_on_screen')

    try:
        build(p, [{**TURN, 'to': 1}])
        raise AssertionError('a 1 deg turn nobody can see was built')
    except StudioError as error:
        assert error.code == 'CHANGE_UNSHOWN', (error.code, error.message)
    checks.append('a_change_too_small_to_see_is_refused')

    v3, _ = build(p, [FILL, {**TURN, 'axis': 'height'}, {'id': 'plate_rises', 'parts': ['plate'], 'kind': 'move', 'from': 0, 'to': 1.5,
                                                         'toward': [0, 0, 1], 'at': [0.3, 0.9], 'why': 'it lifts as it swings'}])
    rows = {r['id']: r for r in read_json(v3 / 'change_report.json')['changes']}
    assert {'turn_plate', 'plate_rises'} <= set(rows), rows
    checks.append('two_changes_of_one_object_are_one_motion')

    v2, _ = build(p, [FILL, TURN])
    assert px(v2, 54, 'tank/fill') == px(v1, 54, 'tank/fill'), (px(v2, 54, 'tank/fill'), px(v1, 54, 'tank/fill'))
    checks.append('a_second_build_makes_the_same_change')
print('STUDIO_CHANGE_SCENE_SMOKE', json.dumps({'ok': True, 'checks': checks}))
