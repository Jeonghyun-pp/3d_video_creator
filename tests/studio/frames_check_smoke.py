"""frames check end to end on a project the build never ran (studio/frames_check.py): with no authored.blend the author
step alone makes one (no version, shot.json untouched), then each moment is solved on it: a garden statue seen as drawn,
and the same statue behind a hedge from every view near the picture - the hedge is moved aside (context_move), checked
again with every moment, and recorded only under --apply.
The statue in a ring of four hedges, taller than it, closed at the corners - no side within
the pictures' height band shows it. The cheats are scored on the probe (each one tried by probing again, no full solve)
and only the best are solved in full: one hedge moved aside; the verdict is the whole search's (complete), and a run
whose time is spent says 'stopped', never 'undecided'.
Run: .venv/bin/python tests/studio/frames_check_smoke.py"""
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio import frames_check  # noqa: E402
from studio.common import read_json, write_json  # noqa: E402
from studio.project import init_project, shot_path  # noqa: E402

GROUND = {'id': 'lawn', 'shape': 'box', 'size': [60.0, 60.0, 0.2], 'at': [0.0, 0.0, -0.1], 'material': 'm'}
STATUE = {'id': 'statue', 'shape': 'box', 'size': [0.6, 0.6, 2.0], 'at': [0.0, 0.0, 1.0], 'material': 'm'}
HEDGE = {'id': 'hedge', 'shape': 'box', 'size': [3.0, 0.5, 3.0], 'at': [0.0, -1.2, 1.5], 'material': 'm'}
RING = [{'id': 'hedge_s', 'shape': 'box', 'size': [3.0, 0.5, 3.0], 'at': [0.0, -1.2, 1.5], 'material': 'm'},
        {'id': 'hedge_n', 'shape': 'box', 'size': [3.0, 0.5, 3.0], 'at': [0.0, 1.2, 1.5], 'material': 'm'},
        {'id': 'hedge_e', 'shape': 'box', 'size': [0.5, 3.0, 3.0], 'at': [1.2, 0.0, 1.5], 'material': 'm'},
        {'id': 'hedge_w', 'shape': 'box', 'size': [0.5, 3.0, 3.0], 'at': [-1.2, 0.0, 1.5], 'material': 'm'}]


def frame(at):
    return {'at': at, 'height_share': 0.5, 'centre': [0.5, 0.5], 'view': {'elevation_deg': 0.0, 'azimuth_deg': 0.0}, 'lens_mm': 35.0, 'cut_edges': []}


def shot(p, prims):
    row = read_json(shot_path(p, 's'))
    row['scene'] = {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
                    'materials': {'m': {'color': [0.6, 0.6, 0.6]}}, 'primitives': prims}
    row['camera'] = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [],
                     'move': {'type': 'frame_to_frame', 'params': {'target': 'statue', 'follow': 0.0}, 'frames': [frame(0.0), frame(1.0)]}}
    row['screen'] = {'subject': ['statue']}
    write_json(shot_path(p, 's'), row)


with tempfile.TemporaryDirectory(prefix='frames-check-smoke-') as root:
    p = Path(init_project('frames_check', {'request': 'frames check smoke', 'shots': [{'shot_id': 's', 'frame_count': 48}]}, root)['project_path'])
    shot(p, [GROUND, STATUE])
    before = read_json(shot_path(p, 's'))
    found = frames_check.check(p, 's', moment_s=600, shot_s=1200)
    versions = sorted(x.name for x in (p / 'shots/s/versions').iterdir())
    assert len(versions) == 1 and versions[0].startswith('authored_'), versions          # the checkpoint only, no version
    assert read_json(shot_path(p, 's'))['scene_version'] == before['scene_version'], 'shot.json changed'
    # a picture kept is as_intended
    assert [m['status'] for m in found['moments']] == ['as_intended', 'as_intended'], found['moments']
    shot(p, [GROUND, STATUE, HEDGE])   # a new author state: the newest checkpoint is the old one, made again by a build
    for old in (p / 'shots/s/versions').iterdir():
        for f in old.iterdir():
            f.unlink()
        old.rmdir()
    found = frames_check.check(p, 's', moment_s=900, shot_s=2400)
    statuses = [m['status'] for m in found['moments']]
    assert statuses == ['cheat', 'cheat'] and all(m['verified'] for m in found['moments']), found['moments']
    assert all(c['kind'] == 'context_move' and c['target'] == 'hedge' for m in found['moments'] for c in m['cheats']), found['moments']
    assert 'cheats' not in str(read_json(p / 'concepts/s/frames.json') if (p / 'concepts/s/frames.json').is_file() else {}), 'recorded without --apply'
    # a ring of hedges; probed cheats, the best solved in full, a complete verdict
    shot(p, [GROUND, STATUE, *RING])
    found = frames_check.check(p, 's', moment_s=900, shot_s=2400)
    moments = found['moments']
    assert found['complete'], found
    assert [m['status'] for m in moments] == ['cheat', 'cheat'], [(m['status'], m.get('as_is')) for m in moments]
    assert all(c['kind'] == 'context_move' and c['target'].startswith('hedge') for m in moments for c in m['cheats']), moments
    for m in moments:
        assert m['probed'] > m['solved_in_full'] and m['solved_in_full'] <= 2, (m['probed'], m['solved_in_full'])
    intent_statuses = [m['status'] for m in moments]
    # a run whose time is spent stops: nothing is decided
    found = frames_check.check(p, 's', moment_s=0.5, shot_s=2400)
    assert all(m['status'] == 'stopped' for m in found['moments']), [m['status'] for m in found['moments']]
    print('frames_check smoke OK', statuses, 'intent', intent_statuses, [(m['probed'], m['solved_in_full']) for m in moments])
