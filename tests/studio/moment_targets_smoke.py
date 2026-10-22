"""camera.move frame_to_frame whose moments frame different subjects (move.frames[i].target): a pull-back from a small
stone close to the lens to a long hull behind it. Each moment is sized on its own subject; the passage between them is
the scene's directing choice (frames[i].transition), and the engine keeps the floors (the aim never whips round).
- pullback: the first moment frames the stone (it fills its size band), the last the whole hull.
- passages: hold_and_reveal and push between the same two moments give different camera paths.
- whip: the same passage squeezed into a few frames turns the aim faster than the comfort floor: refused, with why.
No render beyond the probe's id pass, no paid call.
Run: .venv/bin/python tests/studio/moment_targets_smoke.py
"""
from pathlib import Path
import json
import math
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, shot_path
from tests.decisions_fixture import settle   # builds stand on settled decisions (studio/decisions.require)

HULL = {'id': 'hull', 'shape': 'box', 'size': [80.0, 9.0, 9.0], 'at': [0.0, 0.0, 0.0], 'material': 'hull'}
STONE = {'id': 'stone', 'shape': 'box', 'size': [0.4, 0.4, 0.4], 'at': [0.0, -14.0, 0.0], 'material': 'stone'}


def frame(at, height, target, elevation=0.0, azimuth=0.0, lens=24, **more):
    return {'at': at, 'height_share': height, 'centre': [0.5, 0.5], 'view': {'elevation_deg': elevation, 'azimuth_deg': azimuth},
            'lens_mm': lens, 'target': target, **more}


def build(p, frames, count=None):
    shot = read_json(shot_path(p, 's'))
    if count:
        shot['duration_frames'] = count
    shot['scene'] = {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
                     'materials': {'hull': {'color': [0.5, 0.5, 0.55]}, 'stone': {'color': [0.6, 0.58, 0.55]}},
                     'primitives': [HULL, STONE]}
    shot['camera'] = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [],
                      'move': {'type': 'frame_to_frame', 'params': {'target': 'hull', 'follow': 0.0}, 'frames': frames,
                               'timing': {'profile': 'ease_in_out'}}}
    shot['screen'] = {'subject': ['hull'], 'details': []}
    write_json(shot_path(p, 's'), shot)
    built = build_shot(p, 's', None)
    return p / 'shots/s/versions' / built['scene_version']


def share(version, frame_no, ident):
    probed = {f['frame']: f for f in read_json(version / 'frame_report.json')['frames']}
    shapes = probed[frame_no].get('shapes') or {}
    box = (shapes.get(f'key:{ident}') or {}).get('bbox')
    return None if not box else box[3] - box[1]


checks = []
with tempfile.TemporaryDirectory(prefix='moment-targets-smoke-') as root:
    p = Path(init_project('moment_targets', {'request': 'moment targets smoke', 'shots': [{'shot_id': 's', 'frame_count': 60}]}, root)['project_path'])
    settle(p)
    hold = [frame(0.0, 0.35, 'stone'), frame(1.0, 0.2, 'hull', transition={'how': 'hold_and_reveal', 'why': 'the hull appears behind the stone'})]
    v1 = build(p, hold)
    report = read_json(v1 / 'camera_move_report.json')
    stone0 = share(v1, 0, 'stone')
    assert stone0 is not None and stone0 > 0.2, ('the first moment is sized on the stone', stone0)
    assert report['passages'][0]['how'] == 'hold_and_reveal' and report['passages'][0]['chosen'], report['passages']
    eye_start, eye_end = report['frames'][0]['position'], report['frames'][1]['position']
    assert math.dist(eye_start, eye_end) > 5.0, ('the camera pulls back between the two subjects', eye_start, eye_end)
    checks.append('each_moment_is_sized_on_its_own_subject')

    push = [frame(0.0, 0.35, 'stone'), frame(1.0, 0.2, 'hull', transition={'how': 'push', 'why': 'leave the stone early'})]
    v2 = build(p, push)
    path1 = read_json(v1 / 'camera_rig_report.json')['samples']
    path2 = read_json(v2 / 'camera_rig_report.json')['samples']
    mid = len(path1) // 2
    pos = lambda s: s.get('camera')   # noqa: E731
    assert pos(path1[mid]) and pos(path2[mid]) and math.dist(pos(path1[mid]), pos(path2[mid])) > 0.5, \
        ('the chosen passage shapes the path', pos(path1[mid]), pos(path2[mid]))
    checks.append('the_passage_chosen_shapes_the_path')

    whip = [frame(0.0, 0.35, 'stone', azimuth=-60.0), frame(1.0, 0.2, 'hull', azimuth=60.0,
                                                                transition={'how': 'swing', 'why': 'a whip on purpose', 'handover': 0.1})]
    try:
        build(p, whip, count=6)
        raise AssertionError('a whip past the comfort floor was built')
    except StudioError as error:
        assert 'AIM_TURN_TOO_FAST' in error.message, error.message
    checks.append('a_whip_past_the_comfort_floor_is_refused_with_why')
print('STUDIO_MOMENT_TARGETS_SMOKE', json.dumps({'ok': True, 'checks': checks}))
