"""camera.move.focus (camera_frame.py): the build solves where the camera stands so the focus shows at the asked size
and place - a deep layer section seen from above, a column partly in front - and the frame probe measures the result.
No render beyond the probe's id pass, no paid call.
Run: .venv/bin/python tests/studio/frame_solve_smoke.py
"""
from pathlib import Path
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import read_json, write_json
from studio.patterns import framing
from studio.project import init_project, shot_path
from tests.decisions_fixture import settle   # builds stand on settled decisions (studio/decisions.require)

LAYERS = [('top', 0.03, [0.55, 0.35, 0.2]), ('screed', 0.08, [0.6, 0.58, 0.55]), ('foam', 0.05, [0.05, 0.05, 0.06]),
          ('slab', 0.2, [0.45, 0.45, 0.47])]


def scene(with_column):
    prims, z = [], 0.36
    for name, thick, colour in LAYERS:   # a 4 m wide, 3 m deep floor build-up, top at z = 0.36
        z -= thick
        prims.append({'id': name, 'shape': 'box', 'size': [4.0, 3.0, thick], 'at': [0.0, 0.0, z + thick / 2], 'material': name})
    if with_column:   # in front of the section's left half, nearer the camera
        prims.append({'id': 'column', 'shape': 'box', 'size': [0.3, 0.3, 2.0], 'at': [-0.9, -2.2, 1.0], 'material': 'slab'})
    return {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
            'materials': {name: {'color': colour} for name, _, colour in LAYERS}, 'primitives': prims}


def move(height, centre=None):
    focus = {'height_share': height, **({'centre': centre} if centre else {})}
    return {'type': 'macro_push', 'params': {'target': [n for n, _, _ in LAYERS], 'detail': ['top', 'screed', 'foam'],
                                             'elevation_deg': 20.0, 'azimuth_deg': -10.0}, 'lens_mm': 50,
            'timing': {'profile': 'burst_settle', 'burst_frac': 0.3, 'burst_share': 0.62, 'hold_frac': 0.4, 'drift': 0.006},
            'focus': focus}


checks = []
with tempfile.TemporaryDirectory(prefix='frame-solve-smoke-') as root:
    p = Path(init_project('frame_solve', {'request': 'frame solve smoke', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, root)['project_path'])
    settle(p)
    focus_ids = [n for n, _, _ in LAYERS]
    for name, with_column, height, centre in (('section', False, 0.6, None), ('occluded', True, 0.5, [0.5, 0.45])):
        shot = read_json(shot_path(p, 's'))
        shot['scene'] = scene(with_column)
        shot['camera'] = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [], 'move': move(height, centre)}
        shot['screen'] = {'subject': focus_ids, 'details': [{'id': 'foam', 'min_px': 20, 'why': 'the resilient layer the shot explains'}]}
        write_json(shot_path(p, 's'), shot)
        built = build_shot(p, 's', None)
        version = p / 'shots/s/versions' / built['scene_version']
        solved = read_json(version / 'camera_move_report.json')['focus']
        report = read_json(version / 'frame_report.json')
        judged = framing(report, focus_ids, 30)
        assert not judged['found'], (name, judged)                       # not cut, not filling, nothing off screen
        settled = [f for f in report['frames'] if f['frame'] >= 18]
        boxes = [f['shapes'][f'key:{i}']['bbox'] for f in settled for i in focus_ids if f['shapes'].get(f'key:{i}')]
        last = [f for f in settled if f['frame'] == max(g['frame'] for g in settled)][0]
        union = [min(f['shapes'][f'key:{i}']['bbox'][0] for i in focus_ids if f['shapes'].get(f'key:{i}')) for f in [last]]
        top = min(last['shapes'][f'key:{i}']['bbox'][1] for i in focus_ids if last['shapes'].get(f'key:{i}'))
        bottom = max(last['shapes'][f'key:{i}']['bbox'][3] for i in focus_ids if last['shapes'].get(f'key:{i}'))
        measured = bottom - top
        assert abs(measured - height) <= 0.05, (name, 'probe height', measured, 'solved', solved['achieved'])
        if centre:
            assert abs((top + bottom) / 2 - centre[1]) <= 0.05, (name, top, bottom)
        assert solved['detail_px']['foam'] >= 20, solved['detail_px']
        checks.append(f"{name}: asked {height}, probe {round(measured, 3)}, solved in {solved['steps']} steps, foam {solved['detail_px']['foam']} px")

print('STUDIO_FRAME_SOLVE_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
