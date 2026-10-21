"""frames candidates end to end (studio/frames_check.candidates, blender_ops/frame_candidates.py): a pillar on a lawn,
two pictured moments, each solved by its picture's intent keeping two poses apart round it, both rendered (Workbench)
and laid out on a sheet; then a pick recorded in the shot's frames is marked on the next sheet (which still shows every
candidate) and is the pose the build takes - the intent solve tries a pick first (camera_moves._solve_intent seeds).
Run: .venv/bin/python tests/studio/frame_candidates_smoke.py"""
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio import frames_check  # noqa: E402
from studio.common import read_json, write_json  # noqa: E402
from studio.project import init_project, shot_path  # noqa: E402
from tests.decisions_fixture import settle   # builds stand on settled decisions (studio/decisions.require)

GROUND = {'id': 'lawn', 'shape': 'box', 'size': [60.0, 60.0, 0.2], 'at': [0.0, 0.0, -0.1], 'material': 'm'}
PILLAR = {'id': 'pillar', 'shape': 'box', 'size': [0.8, 0.8, 4.0], 'at': [0.0, 0.0, 2.0], 'material': 'm'}


def frame(at, azimuth):
    return {'at': at, 'height_share': 0.5, 'centre': [0.5, 0.5], 'view': {'elevation_deg': 5.0, 'azimuth_deg': azimuth}, 'lens_mm': 35.0,
            'cut_edges': []}


with tempfile.TemporaryDirectory(prefix='frame-candidates-smoke-') as root:
    p = Path(init_project('frame_candidates', {'request': 'candidates smoke', 'shots': [{'shot_id': 's', 'frame_count': 48}]}, root)['project_path'])
    settle(p)
    row = read_json(shot_path(p, 's'))
    row['scene'] = {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
                    'materials': {'m': {'color': [0.6, 0.6, 0.6]}}, 'primitives': [GROUND, PILLAR]}
    row['camera'] = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [],
                     'move': {'type': 'frame_to_frame', 'params': {'target': 'pillar', 'follow': 0.0},
                              'frames': [frame(0.0, 0.0), frame(1.0, 20.0)]}}
    row['screen'] = {'subject': ['pillar']}
    write_json(shot_path(p, 's'), row)
    started = time.monotonic()
    out = frames_check.candidates(p, 's', keep=2)
    seconds = round(time.monotonic() - started, 1)
    folder = p / 'concepts/s/candidates'
    found = read_json(folder / 'candidates.json')
    assert (folder / 'sheet.png').is_file() and (folder / 'sheet.html').is_file(), list(folder.iterdir())
    for m in found['moments']:
        assert len(m['candidates']) == 2, m
        assert all((folder / c['image']).is_file() for c in m['candidates']), m
        a, b = (c['view'][1] for c in m['candidates'])
        assert abs((b - a + 180) % 360 - 180) >= 20.0, (a, b)   # a choice, not a copy
    # a pick in the shot's frames (frames pick, then frames apply carry it): the next sheet's first is the pick
    picked = found['moments'][1]['candidates'][1]
    row = read_json(shot_path(p, 's'))
    row['camera']['move']['frames'][1]['chosen'] = {'view': picked['view'], 'lens_mm': picked['lens_mm'], 'placement': picked['placement'],
                                                   'by': 'user', 'user_words': 'the other side'}
    write_json(shot_path(p, 's'), row)
    again = read_json(Path(frames_check.candidates(p, 's', keep=2)['artifacts'][0]))
    shown = again['moments'][1]['candidates']
    assert len(shown) > 1, shown   # every candidate again, not only the pick
    assert any(c.get('picked') and c['view'] == picked['view'] for c in shown), shown
    # and the build tries the pick first (camera_moves._solve_intent seeds): its moment's pose is the picked one
    from studio.blender import build_shot
    version = p / 'shots/s/versions' / build_shot(p, 's', None)['scene_version']
    built = read_json(version / 'camera_move_report.json')['frames'][1]
    assert built['view'] == picked['view'] and built['lens_mm'] == picked['lens_mm'], (built['view'], picked['view'])
    print('frame_candidates smoke OK', seconds, 's', [[c['view'] for c in m['candidates']] for m in found['moments']])
