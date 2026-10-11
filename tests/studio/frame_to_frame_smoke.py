"""camera.move frame_to_frame (camera_moves._compile_frames): a camera pose for each frame target, chosen near its
picture, joined and hung from an anchor that shares part of the subject's travel.
- steep: a pillar held up off the ground runs off the top of the first frame and stands large, seen from below, in
  the last; the steep last view would put the camera under the ground at the asked lens, so it goes wider and closer
  instead of flatter. The probe measures both moments against the targets (frame_targets.compare).
- blocked: a slab stands between the picture's view and the pillar: another view near the picture is chosen, and
  the report says which and why.
- must_see: an arm on the pillar's side is hidden from the picture's view: the view turns until it shows.
- unseeable: an arm on the back cannot show from any view near the picture: the build stops and lists each view's reason.
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
from studio.common import StudioError, read_json, write_json
from studio.frame_targets import compare
from studio.project import init_project, shot_path

GROUND = {'id': 'ground', 'shape': 'box', 'size': [200.0, 200.0, 0.2], 'at': [0.0, 0.0, -0.1], 'material': 'ground'}
PILLAR = {'id': 'pillar', 'shape': 'box', 'size': [1.0, 1.0, 10.0], 'at': [0.0, 0.0, 9.0], 'material': 'stone'}


def scene(*extra):
    return {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
            'materials': {'stone': {'color': [0.6, 0.58, 0.55]}, 'ground': {'color': [0.3, 0.32, 0.3]}, 'arm': {'color': [0.8, 0.3, 0.2]}},
            'primitives': [GROUND, PILLAR, *extra]}


def frame(at, height, centre, elevation, azimuth, lens=35, **more):
    return {'at': at, 'height_share': height, 'centre': centre, 'view': {'elevation_deg': elevation, 'azimuth_deg': azimuth}, 'lens_mm': lens, **more}


def target(row):
    """The check a picture reading would give for these frames (visible box: the part inside the frame)."""
    x0, x1 = row['centre'][0] - 0.05, row['centre'][0] + 0.05
    y0, y1 = row['centre'][1] - row['height_share'] / 2, row['centre'][1] + row['height_share'] / 2
    return {'at': row['at'], 'view': row['view'], 'check': {'box': [x0, max(0.0, y0), x1, min(1.0, y1)], 'cut_edges': ['top'] if y0 < 0 else []}}


def build(p, prims, frames, details=(), parts=None):
    shot = read_json(shot_path(p, 's'))
    shot['scene'] = scene(*prims)
    shot['camera'] = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [],
                      'move': {'type': 'frame_to_frame', 'params': {'target': 'pillar', 'follow': 0.0, **({'parts': parts} if parts else {})},
                               'frames': frames, 'timing': {'profile': 'ease_in_out'},
                               'guards': {'clearance_ids': ['ground'], 'min_clearance_m': 0.1}}}
    shot['screen'] = {'subject': ['pillar'], 'details': [dict(d) for d in details]}
    write_json(shot_path(p, 's'), shot)
    built = build_shot(p, 's', None)
    return p / 'shots/s/versions' / built['scene_version']


checks = []
with tempfile.TemporaryDirectory(prefix='frame-to-frame-smoke-') as root:
    p = Path(init_project('frame_to_frame', {'request': 'frame to frame smoke', 'shots': [{'shot_id': 's', 'frame_count': 48}]}, root)['project_path'])

    steep = [frame(0.0, 0.5, [0.5, 0.1], 0.0, 0.0), frame(1.0, 0.6, [0.5, 0.5], -35.0, 20.0)]
    version = build(p, [], steep)
    rows = read_json(version / 'camera_move_report.json')['frames']
    assert [r['frame'] for r in rows] == [0, 47], rows
    assert rows[0]['lens_mm'] == 35 and rows[0]['offset_from_picture'] == {'azimuth_deg': 0.0, 'elevation_deg': 0.0}, rows[0]
    assert rows[1]['lens_mm'] < 35 and rows[1]['view'] == [-35.0, 20.0], rows[1]          # wider, not flatter
    assert rows[1]['position'][2] > 0.1, rows[1]['position']
    pitch = {s['frame']: s['pitch_deg'] for s in read_json(version / 'camera_rig_report.json')['samples']}
    probed = {f['frame']: f for f in read_json(version / 'frame_report.json')['frames']}
    for want, row in zip(steep, rows):
        box = (probed[row['frame']]['shapes'].get('subject') or {}).get('bbox')
        problems = [q for q in compare(target(want), box, -pitch[row['frame']]) if 'centre' in q or 'tall' in q or 'past' in q or 'looks' in q]
        assert not problems, (want['at'], box, problems)
    checks.append(f"steep: end at {rows[1]['lens_mm']} mm, camera z {rows[1]['position'][2]:.2f} m, both moments match")

    slab = {'id': 'slab', 'shape': 'box', 'size': [2.0, 0.3, 14.0], 'at': [0.0, -4.0, 8.0], 'material': 'ground'}
    version = build(p, [slab], [frame(0.0, 0.5, [0.5, 0.5], 0.0, 0.0), frame(1.0, 0.6, [0.5, 0.5], 5.0, 0.0)])
    move = read_json(version / 'camera_move_report.json')
    first = move['frames'][0]
    assert first['offset_from_picture']['azimuth_deg'] != 0.0 and first['rejected'], first
    assert any('behind something else' in r['why'] for r in first['rejected']), first['rejected']
    assert first['blocked_share'] is not None and first['blocked_share'] <= 0.3, first['blocked_share']
    checks.append(f"blocked: turned {first['offset_from_picture']} from the picture after {len(first['rejected'])} views, "
                  f"{first['blocked_share']} hidden; path {move['path']}")

    arm = {'id': 'arm', 'shape': 'box', 'size': [0.4, 0.3, 0.6], 'at': [0.7, 0.0, 12.0], 'material': 'arm'}
    version = build(p, [arm], [frame(0.0, 0.5, [0.5, 0.5], 0.0, -30.0), frame(1.0, 0.6, [0.5, 0.5], 0.0, -30.0, must_see=['arm'])],
                    details=[{'id': 'arm', 'min_px': 20, 'why': 'what the shot explains'}], parts=['pillar', 'arm'])
    last = read_json(version / 'camera_move_report.json')['frames'][1]
    assert last['offset_from_picture']['azimuth_deg'] > 0 and last['must_see_px']['arm'] >= 20, last
    checks.append(f"must_see: turned {last['offset_from_picture']} until the arm shows {last['must_see_px']['arm']} px")

    back = {'id': 'arm', 'shape': 'box', 'size': [0.3, 0.3, 0.6], 'at': [0.0, 0.65, 12.0], 'material': 'arm'}
    try:
        build(p, [back], [frame(0.0, 0.5, [0.5, 0.5], 0.0, 0.0), frame(1.0, 0.6, [0.5, 0.5], 0.0, 0.0, must_see=['arm'])],
              details=[{'id': 'arm', 'min_px': 20, 'why': 'what the shot explains'}], parts=['pillar', 'arm'])
        raise AssertionError('an arm no view near the picture shows must stop the build')
    except StudioError as error:
        assert 'FRAME_UNSOLVED' in str(error) and 'must-see' in str(error), str(error)[:400]
    checks.append('unseeable: stopped with each view\'s reason')

print('STUDIO_FRAME_TO_FRAME_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
