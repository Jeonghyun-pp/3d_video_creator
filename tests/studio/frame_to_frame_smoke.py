"""camera.move frame_to_frame (camera_moves._compile_frames): a camera pose solved for each frame target and joined -
a tall pillar, held up off the ground, that runs off the top of the first frame and stands large, seen from below, in the last; the steep
last view would put the camera under the ground at the asked lens, so the solve goes wider and closer instead of
giving up the angle. The frame probe measures both moments against the targets (frame_targets.compare).
No render beyond the probe's id pass, no paid call.
Run: .venv/bin/python tests/studio/frame_to_frame_smoke.py
"""
from pathlib import Path
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import read_json, write_json
from studio.frame_targets import compare
from studio.project import init_project, shot_path

SCENE = {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
         'materials': {'stone': {'color': [0.6, 0.58, 0.55]}, 'ground': {'color': [0.3, 0.32, 0.3]}},
         'primitives': [{'id': 'ground', 'shape': 'box', 'size': [200.0, 200.0, 0.2], 'at': [0.0, 0.0, -0.1], 'material': 'ground'},
                        {'id': 'pillar', 'shape': 'box', 'size': [1.0, 1.0, 10.0], 'at': [0.0, 0.0, 9.0], 'material': 'stone'}]}
FRAMES = [{'at': 0.0, 'height_share': 0.5, 'centre': [0.5, 0.1], 'view': {'elevation_deg': 0.0, 'azimuth_deg': 0.0}, 'lens_mm': 35},
          {'at': 1.0, 'height_share': 0.6, 'centre': [0.5, 0.5], 'view': {'elevation_deg': -35.0, 'azimuth_deg': 20.0}, 'lens_mm': 35}]


def target(frame):
    """The check a picture reading would give for these frames (visible box: the part inside the frame)."""
    x0, x1 = frame['centre'][0] - 0.05, frame['centre'][0] + 0.05
    y0, y1 = frame['centre'][1] - frame['height_share'] / 2, frame['centre'][1] + frame['height_share'] / 2
    box = [x0, max(0.0, y0), x1, min(1.0, y1)]
    return {'at': frame['at'], 'view': frame['view'], 'check': {'box': box, 'cut_edges': ['top'] if y0 < 0 else []}}


checks = []
with tempfile.TemporaryDirectory(prefix='frame-to-frame-smoke-') as root:
    p = Path(init_project('frame_to_frame', {'request': 'frame to frame smoke', 'shots': [{'shot_id': 's', 'frame_count': 48}]}, root)['project_path'])
    shot = read_json(shot_path(p, 's'))
    shot['scene'] = SCENE
    shot['camera'] = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [],
                      'move': {'type': 'frame_to_frame', 'params': {'target': 'pillar'}, 'frames': FRAMES,
                               'timing': {'profile': 'ease_in_out'}, 'guards': {'clearance_ids': ['ground'], 'min_clearance_m': 0.1}}}
    shot['screen'] = {'subject': ['pillar']}
    write_json(shot_path(p, 's'), shot)
    built = build_shot(p, 's', None)
    version = p / 'shots/s/versions' / built['scene_version']
    move = read_json(version / 'camera_move_report.json')
    rows = move['frames']
    assert [r['frame'] for r in rows] == [0, 47], rows
    assert rows[0]['lens_mm'] == 35 and len(rows[0]['tried']) == 1, rows[0]['tried']
    assert rows[1]['lens_mm'] < 35 and 'clash' in rows[1]['tried'][0], rows[1]['tried']     # wider, not flatter
    assert rows[1]['position'][2] > 0.1, rows[1]['position']
    checks.append(f"steep end view: tried {[t['lens_mm'] for t in rows[1]['tried']]} mm, camera at z {rows[1]['position'][2]:.2f} m")
    rig = read_json(version / 'camera_rig_report.json')
    pitch = {s['frame']: s['pitch_deg'] for s in rig['samples']}
    report = read_json(version / 'frame_report.json')
    by_frame = {f['frame']: f for f in report['frames']}
    for frame, row in zip(FRAMES, rows):
        assert row['frame'] in by_frame, (row['frame'], sorted(by_frame))
        shape = by_frame[row['frame']]['shapes'].get('subject') or {}
        problems = compare(target(frame), shape.get('bbox'), -pitch[row['frame']])
        problems = [q for q in problems if 'centre' in q or 'tall' in q or 'past' in q or 'looks' in q]
        assert not problems, (frame['at'], shape.get('bbox'), problems)
        checks.append(f"t={frame['at']}: probe box {shape.get('bbox')}, pitch {pitch[row['frame']]:.1f} deg")

print('STUDIO_FRAME_TO_FRAME_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
