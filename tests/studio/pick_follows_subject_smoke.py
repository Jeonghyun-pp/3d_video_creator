"""A user's pick is the pose the build shoots, on a scene authored again since it was picked (camera_moves._solve_intent's
seeds, camera_moves_core.follow_subject). A pillar on a quay, not the reel the rule came from (N+1):
- moved: the pick was recorded where the pillar stood 150 m lower and 30 m aside (its subject_at says where): the build carries the pose
  with the pillar - the same view and lens, the eye where the free solve put it, 'seeded';
- unanchored: the same pose recorded without subject_at looks at the air where the pillar stood: it is placed again from
  its view and lens round the pillar as it stands now - the picked view and lens kept, and the row says why;
- every candidate and pick row carries placement {aim, closer} and subject_at (frame_candidates, frames pick).
No render beyond the probe's id pass, no paid call.
Run: .venv/bin/python tests/studio/pick_follows_subject_smoke.py
"""
from pathlib import Path
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import read_json, write_json
from studio.project import init_project, shot_path

GROUND = {'id': 'ground', 'shape': 'box', 'size': [200.0, 200.0, 0.2], 'at': [0.0, 0.0, -0.1], 'material': 'ground'}
PILLAR = {'id': 'pillar', 'shape': 'box', 'size': [1.0, 1.0, 10.0], 'at': [0.0, 0.0, 5.0], 'material': 'stone'}
LAMP = {'id': 'lamp', 'shape': 'box', 'size': [0.6, 0.6, 0.5], 'at': [0.0, 0.0, 10.25], 'material': 'arm'}
SHIFT = [30.0, 0.0, 150.0]   # where the pillar stood when the pick was made, from where it stands now


def frame(at, height, elevation, azimuth, lens=35, **more):
    return {'at': at, 'height_share': height, 'centre': [0.5, 0.5], 'view': {'elevation_deg': elevation, 'azimuth_deg': azimuth},
            'lens_mm': lens, 'must_see': ['lamp'], **more}


def build(p, frames):
    shot = read_json(shot_path(p, 's'))
    shot['scene'] = {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
                     'materials': {'stone': {'color': [0.6, 0.58, 0.55]}, 'ground': {'color': [0.3, 0.32, 0.3]},
                                   'arm': {'color': [0.8, 0.3, 0.2]}},
                     'primitives': [GROUND, PILLAR, LAMP]}
    shot['camera'] = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [],
                      'move': {'type': 'frame_to_frame', 'params': {'target': 'pillar', 'follow': 0.0, 'parts': ['pillar', 'lamp']},
                               'frames': frames, 'timing': {'profile': 'ease_in_out'},
                               'guards': {'clearance_ids': ['ground'], 'min_clearance_m': 0.1}}}
    shot['screen'] = {'subject': ['pillar'], 'details': [{'id': 'lamp', 'min_px': 20, 'why': 'what the shot explains'}]}
    write_json(shot_path(p, 's'), shot)
    built = build_shot(p, 's', None)
    return read_json(p / 'shots/s/versions' / built['scene_version'] / 'camera_move_report.json')['frames']


def near(a, b, tol=0.05):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


checks = []
with tempfile.TemporaryDirectory(prefix='pick-follows-smoke-') as root:
    p = Path(init_project('pick_follows', {'request': 'pick follows subject smoke', 'shots': [{'shot_id': 's', 'frame_count': 48}]},
                          root)['project_path'])
    plain = [frame(0.0, 0.5, 10.0, 0.0), frame(1.0, 0.5, 10.0, 40.0)]
    free = build(p, plain)
    assert all(r.get('subject_at') and len(r['subject_at']) == 3 for r in free), [r.get('subject_at') for r in free]
    assert all(r.get('placement', {}).get('aim') for r in free), [r.get('placement') for r in free]
    # the end moment picked at a pose of its own - 20 deg round from what the engine chose, wider - recorded on a scene
    # where the pillar stood SHIFT away
    end = free[1]
    pick_view = [end['view'][0], end['view'][1] + 20.0]
    probe = build(p, [plain[0], dict(plain[1], view={'elevation_deg': pick_view[0], 'azimuth_deg': pick_view[1]}, lens_mm=24.0)])[1]
    assert probe['view'] == pick_view and probe['lens_mm'] == 24.0, (probe['view'], probe['lens_mm'])
    shifted = lambda v: [round(a + s, 5) for a, s in zip(v, SHIFT)]   # noqa: E731
    pick = {'view': probe['view'], 'lens_mm': probe['lens_mm'], 'eye': shifted(probe['position']), 'aim': shifted(probe['aim']),
            'roll_deg': probe.get('roll_deg') or 0.0, 'placement': probe['placement'], 'subject_at': shifted(probe['subject_at']),
            'candidate': 0, 'by': 'user', 'user_words': 'the wider one from the side', 'at': 'now'}

    rows = build(p, [plain[0], dict(plain[1], chosen=pick)])
    row = rows[1]
    assert near(row['view'], pick['view'], 0.01) and row['lens_mm'] == pick['lens_mm'], (row['view'], row['lens_mm'], pick['view'])
    assert near(row['position'], probe['position']), (row['position'], probe['position'])
    assert 'seeded' in row['engaged'], row['engaged']
    assert not any('pick' in d for d in row.get('departed') or []), row.get('departed')
    checks.append(f"moved: the pick recorded {SHIFT} away held at {row['view']} {row['lens_mm']} mm, eye {row['position']}")

    legacy = {k: v for k, v in pick.items() if k != 'subject_at'}
    rows = build(p, [plain[0], dict(plain[1], chosen=legacy)])
    row = rows[1]
    assert near(row['view'], pick['view'], 0.01) and row['lens_mm'] == pick['lens_mm'], (row['view'], row['lens_mm'], pick['view'])
    said = ' '.join(row.get('departed') or [])
    assert 're-placed from its view' in said and 'in world coordinates' in said, said
    assert 'the engine chose this one' not in said, said
    checks.append(f"unanchored: placed again from its view, kept {row['view']} {row['lens_mm']} mm, said: {said[:140]}")

print(json.dumps({'ok': True, 'checks': checks}, ensure_ascii=False, indent=1))
