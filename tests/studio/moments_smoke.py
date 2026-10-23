"""direction.move.moments (recipe_moves.moments_move -> camera.move frame_to_frame): a motion written as the framings the
camera passes through, built without a picture. Four moments on a pillar: the whole of it, arriving close at its
front, the same framing again (the camera rests while the shot's change would happen), then round to its side.
- rest: between the two equal moments the camera barely moves.
- round: from the front to 70 deg round, the camera's path bends round the pillar (not a straight chord).
- reach: each moment's framing is reached (camera_move_report frames at their at).
Why (2026-10-23): the user - "카메라 움직임이 직선적이기만 한것" - one recipe made one straight move per shot.
No render beyond the probe's id pass, no paid call.
Run: .venv/bin/python tests/studio/moments_smoke.py
"""
from pathlib import Path
import json
import math
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import read_json, write_json
from studio.project import init_project, shot_path
from studio.recipe_moves import camera_with, moments_move
from tests.decisions_fixture import settle   # builds stand on settled decisions (studio/decisions.require)

COUNT = 90
MOMENTS = [{'at': 0.0, 'size': 0.45, 'view': {'elevation_deg': 5, 'azimuth_deg': 0}, 'why': 'the whole pillar'},
           {'at': 0.35, 'size': 0.8, 'view': {'elevation_deg': 5, 'azimuth_deg': 0}, 'why': 'arrive at its front'},
           {'at': 0.65, 'size': 0.8, 'view': {'elevation_deg': 5, 'azimuth_deg': 0}, 'why': 'rest while it changes'},
           {'at': 1.0, 'size': 0.8, 'view': {'elevation_deg': 5, 'azimuth_deg': 70}, 'why': 'go round to its side'}]

checks = []
with tempfile.TemporaryDirectory(prefix='moments-smoke-') as root:
    p = Path(init_project('moments', {'request': 'moments smoke', 'shots': [{'shot_id': 's', 'frame_count': COUNT}]}, root)['project_path'])
    settle(p)
    shot = read_json(shot_path(p, 's'))
    shot['scene'] = {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
                     'materials': {'stone': {'color': [0.6, 0.58, 0.55]}, 'ground': {'color': [0.3, 0.32, 0.3]}},
                     'primitives': [{'id': 'ground', 'shape': 'box', 'size': [200.0, 200.0, 0.2], 'at': [0.0, 0.0, -0.1], 'material': 'ground'},
                                    {'id': 'pillar', 'shape': 'box', 'size': [2.0, 1.0, 6.0], 'at': [0.0, 0.0, 3.0], 'material': 'stone'}]}
    shot['frame'] = {'focus': ['pillar'], 'size': 'medium'}
    intent = {'recipe': 'dig_in', 'reveals': 'the pillar', 'key_at': 0.5, 'why': 'arrive, rest, go round', 'moments': MOMENTS, 'follow': 0.0}
    move = moments_move(shot, intent, centre=[0.5, 0.5])['move']
    move['guards'] = {'clearance_ids': ['ground'], 'min_clearance_m': 0.1}
    shot['camera'] = camera_with({'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': []}, move)
    shot['screen'] = {'subject': ['pillar'], 'details': []}
    write_json(shot_path(p, 's'), shot)
    version = p / 'shots/s/versions' / build_shot(p, 's', None)['scene_version']

    rows = read_json(version / 'camera_move_report.json')['frames']
    want = [round(m['at'] * (COUNT - 1)) for m in MOMENTS]
    assert [r['frame'] for r in rows] == want, ([r['frame'] for r in rows], want)
    checks.append(f"reach: {len(rows)} moments framed at frames {want}")

    cam = {s['frame']: s['camera'] for s in read_json(version / 'camera_rig_report.json')['samples']}
    step = lambda a, b: math.dist(cam[a], cam[b])   # noqa: E731
    travel = sum(step(f, f + 1) for f in range(want[0], want[1]))
    rest = sum(step(f, f + 1) for f in range(want[1] + 3, want[2] - 3))   # inside the rest, its eases aside
    assert rest < 0.05 * travel, (rest, travel)
    checks.append(f"rest: the camera moves {rest:.3f} m inside the rest against {travel:.2f} m arriving")

    a, b = cam[want[2]], cam[want[3]]
    mid = cam[(want[2] + want[3]) // 2]
    chord_mid = [(x + y) / 2 for x, y in zip(a, b)]
    centre = (0.0, 0.0)
    bulge = math.hypot(mid[0] - centre[0], mid[1] - centre[1]) - math.hypot(chord_mid[0] - centre[0], chord_mid[1] - centre[1])
    assert bulge > 0.05 * math.dist(a, b), (bulge, math.dist(a, b))
    checks.append(f"round: the path bends {bulge:.2f} m out from the straight chord round the pillar")

    # hold (2026-10-23, r3 s07): a ball falls 4 m; the camera framed on it at the start keeps its pose, the ball crosses
    # the frame - two equal framings would each have framed the ball where it then was, and the camera would fall with it
    shot = read_json(shot_path(p, 's'))
    shot['scene']['primitives'].append({'id': 'ball', 'shape': 'box', 'size': [0.6, 0.6, 0.6], 'at': [0.0, -6.0, 5.0], 'material': 'stone'})
    shot['frame'] = {'focus': ['ball'], 'size': 'medium'}
    shot['direction'] = {'move': {'recipe': 'watch', 'reveals': 'the ball', 'key_at': 0.5, 'why': 'the ball falls through a still frame'},
                         'change': [{'id': 'drop', 'parts': ['ball'], 'kind': 'move', 'from': 0, 'to': 4, 'at': [0.0, 1.0],
                                     'toward': [0, 0, -1], 'why': 'it falls'}]}
    held = [{'at': 0.0, 'size': 0.25, 'view': {'elevation_deg': 0, 'azimuth_deg': 0}, 'centre': [0.5, 0.25], 'why': 'the ball high in the frame'},
            {'at': 1.0, 'hold': True, 'why': 'the camera stays; the ball has fallen through'}]
    move = moments_move(shot, {**shot['direction']['move'], 'moments': held, 'follow': 0.0}, centre=[0.5, 0.5])['move']
    move['guards'] = {'clearance_ids': ['ground'], 'min_clearance_m': 0.1}
    shot['camera'] = camera_with({'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': []}, move)
    shot['screen'] = {'subject': ['ball'], 'details': []}
    write_json(shot_path(p, 's'), shot)
    version = p / 'shots/s/versions' / build_shot(p, 's', None)['scene_version']
    samples = read_json(version / 'camera_rig_report.json')['samples']
    drift = max(math.dist(samples[0]['camera'], s['camera']) for s in samples)
    assert drift < 0.02, drift
    probed = [r['shapes']['subject'] for r in read_json(version / 'frame_report.json')['frames'] if (r['shapes'].get('subject') or {}).get('px')]
    assert probed, read_json(version / 'frame_report.json')['frames'][0]
    fall = probed[-1]['centroid'][1] - probed[0]['centroid'][1]
    assert fall > 0.2, (probed[0], probed[-1])   # down the frame (and out of it, when it falls far enough)
    checks.append(f"hold: the camera stays within {drift:.3f} m while the ball falls {fall:.2f} of the frame down it")

print('STUDIO_MOMENTS_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
