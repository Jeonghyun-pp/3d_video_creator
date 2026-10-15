"""camera.move frame_to_frame kept by its pictures' intent (params.solve 'intent', camera_moves._solve_intent): views
placed from the picture's numbers, probed with few rays, the best few refined and judged in full.
A pillar on a quay behind a harbour wall, not the reel the rule came from (N+1):
- walled: the wall stands between the picture's view and the pillar; the build keeps the intent (its size class,
  level, its lens class) from a view the wall leaves free, says which, and the probe's blocked share at the chosen
  view is within 0.05 of the full measure (camera_frame.blocked_share); the lamp on the pillar, a must-see part,
  reads within the probe's slack of what shown_px measures;
- enclosed: an arm inside a housing shown from outside stops the build (SHOW_NEEDS_OPENING), as the exact search does;
- joined: a long quay wall leaves the first picture's view free only well to one side or the other; on its own the
  first moment takes the nearer side, 160 deg from where the second picture looks - the shot is joined across its
  moments (core.joint_rank) and takes the far-side pose that turns little to the second;
- crossed: the line between the moments' best poses runs through a crate standing off both their lines of sight;
  the joined path is checked as the rig checks it (passes_through_geometry): the same poses swing round as an arc,
  else another combination is taken;
- the same build with the default (exact) solve is unchanged by the intent path (no intent fields).
No render beyond the probe's id pass, no paid call.
Run: .venv/bin/python tests/studio/intent_camera_smoke.py
"""
from pathlib import Path
import json
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, shot_path

GROUND = {'id': 'ground', 'shape': 'box', 'size': [200.0, 200.0, 0.2], 'at': [0.0, 0.0, -0.1], 'material': 'ground'}
PILLAR = {'id': 'pillar', 'shape': 'box', 'size': [1.0, 1.0, 10.0], 'at': [0.0, 0.0, 5.0], 'material': 'stone'}
WALL = {'id': 'wall', 'shape': 'box', 'size': [3.0, 0.4, 12.0], 'at': [0.0, -5.0, 6.0], 'material': 'ground'}
LAMP = {'id': 'lamp', 'shape': 'box', 'size': [0.6, 0.6, 0.5], 'at': [0.0, 0.0, 10.25], 'material': 'arm'}   # on the pillar's top


def scene(*extra):
    return {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
            'materials': {'stone': {'color': [0.6, 0.58, 0.55]}, 'ground': {'color': [0.3, 0.32, 0.3]}, 'arm': {'color': [0.8, 0.3, 0.2]}},
            'primitives': [GROUND, PILLAR, *extra]}


def frame(at, height, elevation, azimuth, lens=35, **more):
    return {'at': at, 'height_share': height, 'centre': [0.5, 0.5], 'view': {'elevation_deg': elevation, 'azimuth_deg': azimuth},
            'lens_mm': lens, **more}


def build(p, prims, frames, solve='intent', details=(), parts=None):
    shot = read_json(shot_path(p, 's'))
    shot['scene'] = scene(*prims)
    params = {'target': 'pillar', 'follow': 0.0, **({'parts': parts} if parts else {}), **({'solve': solve} if solve else {})}
    shot['camera'] = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [],
                      'move': {'type': 'frame_to_frame', 'params': params, 'frames': frames, 'timing': {'profile': 'ease_in_out'},
                               'guards': {'clearance_ids': ['ground'], 'min_clearance_m': 0.1}}}
    shot['screen'] = {'subject': ['pillar'], 'details': [dict(d) for d in details]}
    write_json(shot_path(p, 's'), shot)
    built = build_shot(p, 's', None)
    return p / 'shots/s/versions' / built['scene_version']


checks = []
with tempfile.TemporaryDirectory(prefix='intent-camera-smoke-') as root:
    p = Path(init_project('intent_camera', {'request': 'intent camera smoke', 'shots': [{'shot_id': 's', 'frame_count': 48}]}, root)['project_path'])

    walled = [frame(0.0, 0.5, 0.0, 0.0, must_see=['lamp']), frame(1.0, 0.6, 3.0, 0.0, must_see=['lamp'])]
    started = time.monotonic()
    version = build(p, [WALL, LAMP], walled, details=[{'id': 'lamp', 'min_px': 20, 'why': 'what the shot explains'}],
                    parts=['pillar', 'lamp'])
    seconds = round(time.monotonic() - started, 1)
    rows = read_json(version / 'camera_move_report.json')['frames']
    for row in rows:
        assert row['mode'] == 'intent' and row['engaged'][0] == 'intent', row
        assert row['intent']['size']['class'] in ('꽉 참', '전체') and row['intent']['elevation']['class'] == 'level', row['intent']
        assert row['offset_from_picture']['azimuth_deg'] != 0.0, row['offset_from_picture']   # the wall is round
        assert row['blocked_share'] is not None and row['blocked_share'] <= 0.3, row['blocked_share']
        kept = next(c for c in row['candidates'] if c['kept'] and c['view'] == row['view'] and c['lens_mm'] == row['lens_mm'])
        assert abs(row['probe_at_pose']['blocked_share'] - row['blocked_share']) <= 0.05, (row['probe_at_pose'], row['blocked_share'])
        probe_px, full_px = row['probe_at_pose']['must_see_px']['lamp'], row['must_see_px']['lamp']
        assert full_px >= 20 and 0.8 * full_px <= probe_px <= full_px / 0.8, (probe_px, full_px)   # PROBE_PX_SLACK
        assert row['intent']['elevation']['band'][0] <= row['view'][0] <= row['intent']['elevation']['band'][1], row['view']
        assert any(r['code'] == 'FOCUS_BLOCKED' and 'wall' in r['why'] for r in row['rejected']), row['rejected'][:3]
    checks.append(f"walled: both moments turned {[r['offset_from_picture'] for r in rows]} at {[r['lens_mm'] for r in rows]} mm, "
                  f"probed {[r['probe'] for r in rows]}; build {seconds} s")

    # an arm inside a housing, shown from outside: the shot list's opening, as the exact search says
    arm = {'id': 'arm', 'shape': 'box', 'size': [0.4, 0.3, 0.6], 'at': [0.7, 0.0, 8.0], 'material': 'arm'}
    housing = {'id': 'housing', 'shape': 'box', 'size': [0.6, 0.5, 0.8], 'at': [0.7, 0.0, 8.0], 'material': 'stone'}
    try:
        build(p, [arm, housing], [frame(0.0, 0.5, 0.0, -30.0), frame(1.0, 0.6, 0.0, -30.0, must_see=['arm'])],
              details=[{'id': 'arm', 'min_px': 20, 'why': 'what the shot explains'}], parts=['pillar', 'arm'])
        raise AssertionError('a part inside a housing, shown from outside, must stop the build')
    except StudioError as error:
        assert 'SHOW_NEEDS_OPENING' in str(error) and 'housing' in str(error), str(error)[:400]
    checks.append('enclosed: the build stops and says to open the housing')

    # a quay wall from az -50 to +70 round the pillar (seen from its foot): the first picture's own view is behind it;
    # -60 is its nearest free side, +90 the next; the second picture looks from 100
    quay = {'id': 'quay', 'shape': 'box', 'size': [19.7, 0.4, 14.0], 'at': [3.9, -5.0, 7.0], 'material': 'ground'}
    joined = [frame(0.0, 0.5, 0.0, 0.0), frame(1.0, 0.5, 0.0, 100.0)]
    version = build(p, [quay], joined)
    rows = read_json(version / 'camera_move_report.json')['frames']
    first, last = rows
    turned = abs((last['view'][1] - first['view'][1] + 180) % 360 - 180)
    assert first['joined']['pose'] != 0, first['joined']          # not the first moment's own winner
    assert turned <= 90.0, (first['view'], last['view'])
    checks.append(f"joined: views {[r['view'] for r in rows]}, turned {turned:.0f} deg between the moments "
                  f"(the first moment's own winner would have turned more), cost {first['joined']['cost']}")

    # a block on the line between the first moment's far pose (30 deg left) and the second's near one (30 right), off
    # both their lines of sight: the joining refuses the path through it and takes another combination
    crossed = [frame(0.0, 0.3, 0.0, -30.0), frame(1.0, 0.9, 0.0, 30.0)]
    eyes = [r['position'] for r in read_json(build(p, [], crossed) / 'camera_move_report.json')['frames']]
    middle = [(a + b) / 2 for a, b in zip(*eyes)]   # where the line between the two poses runs; off both sight lines
    block = {'id': 'crate', 'shape': 'box', 'size': [0.8, 0.8, 2 * middle[2] + 2.0], 'at': [middle[0], middle[1], middle[2] + 1.0],
             'material': 'ground'}
    version = build(p, [block], crossed)
    report = read_json(version / 'camera_move_report.json')
    rows = report['frames']
    joined = rows[0]['joined']
    assert any('crate' in t for t in joined.get('tried', [])) and (joined['rank'] > 0 or report['path'] == 'arc'), (joined, report['path'])
    checks.append(f"crossed: the first joining went through the crate; then rank {joined['rank']} on a {report['path']} path, "
                  f"views {[r['view'] for r in rows]}")

    # the default solve is the exact search, with no intent fields
    version = build(p, [], walled, solve=None)
    rows = read_json(version / 'camera_move_report.json')['frames']
    assert all('intent' not in r and r['mode'] != 'intent' for r in rows), rows[0].get('mode')
    checks.append(f"exact by default: modes {[r['mode'] for r in rows]}")

print(json.dumps({'ok': True, 'checks': checks}, ensure_ascii=False, indent=1))
