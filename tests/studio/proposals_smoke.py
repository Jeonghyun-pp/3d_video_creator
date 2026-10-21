"""A blocked moment's choices, end to end (blender frames_check.proposals, camera_moves.wide_search, studio/frames_check.
proposals): a crate hung high with a plate under its floor, drawn from a little above - no view within the picture's
intent shows the plate. frames check says redraw and carries
- A: poses from below (outside the intent, proposed - the moment stays redraw), none standing inside the crate
  (CAMERA_IN_SUBJECT refuses the close-ups aimed at the plate from above, through the crate), each solved again as a
  picture redrawn from there would be;
- B: the plate dropped, the moment solves as drawn.
frames proposals then renders the page (picture | intended clay with the plate outlined | A clays | B clay) and puts the
A poses on the candidates sheet. Nothing painted, no paid call.
Run: .venv/bin/python tests/studio/proposals_smoke.py"""
from pathlib import Path
import json
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio import frames_check  # noqa: E402
from studio.common import read_json, write_json  # noqa: E402
from studio.project import init_project, shot_path  # noqa: E402
from tests.decisions_fixture import settle   # builds stand on settled decisions (studio/decisions.require)

GROUND = {'id': 'yard', 'shape': 'box', 'size': [120.0, 120.0, 0.2], 'at': [0.0, 0.0, -0.1], 'material': 'm'}
CRATE = {'id': 'crate', 'shape': 'box', 'size': [4.0, 4.0, 4.0], 'at': [0.0, 0.0, 10.0], 'material': 'm'}
PLATE = {'id': 'plate', 'shape': 'box', 'size': [1.6, 1.6, 0.06], 'at': [0.0, 0.0, 7.97], 'material': 'r'}


def frame(at, plate=True):
    return {'at': at, 'height_share': 0.5, 'centre': [0.5, 0.5], 'view': {'elevation_deg': 10.0, 'azimuth_deg': 0.0}, 'lens_mm': 35.0,
            'cut_edges': [], **({'must_see': ['plate'], 'show': {'at': 'end', 'parts': ['plate'], 'how': 'exterior',
                                                                   'why': 'the plate under the floor'}} if plate else {})}


checks = []
with tempfile.TemporaryDirectory(prefix='proposals-smoke-') as root:
    p = Path(init_project('proposals', {'request': 'proposals smoke', 'shots': [{'shot_id': 's', 'frame_count': 48}]}, root)['project_path'])
    settle(p)
    row = read_json(shot_path(p, 's'))
    row['scene'] = {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
                    'materials': {'m': {'color': [0.6, 0.6, 0.6]}, 'r': {'color': [0.8, 0.3, 0.2]}}, 'primitives': [GROUND, CRATE, PLATE]}
    row['camera'] = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [],
                     'move': {'type': 'frame_to_frame', 'params': {'target': 'crate', 'parts': ['crate', 'plate'], 'follow': 0.0},
                              'frames': [frame(0.0, plate=False), frame(1.0)], 'guards': {'clearance_ids': ['yard'], 'min_clearance_m': 0.1}}}
    row['screen'] = {'subject': ['crate'], 'details': [{'id': 'plate', 'min_px': 20, 'why': 'what the shot explains'}]}
    write_json(shot_path(p, 's'), row)
    started = time.monotonic()
    found = frames_check.check(p, 's', moment_s=900, shot_s=1800)
    seconds = round(time.monotonic() - started, 1)
    first, full = read_json(p / 'concepts/s/frames_check.json')['moments']
    assert first['status'] == 'as_intended' and not first.get('proposals'), first['status']   # a moment that solves: no search
    assert full['status'] == 'redraw', (full['status'], full.get('as_is'))
    a, b = full['proposals']['a'], full['proposals']['b']
    search = full['proposals']['a_search']
    assert a, search
    for pose in a:
        assert pose['view'][0] < -5.0, pose['view']                       # from below: outside the picture's band
        assert pose['resolve']['status'] in ('as_intended', 'moved'), pose['resolve']
        assert set(pose['placement']) == {'aim', 'closer'} and pose['subject_at'], pose
    assert search['refused'].get('CAMERA_IN_SUBJECT', 0) > 0, search['refused']
    assert [o['part'] for o in b] == ['plate'] and b[0]['status'] == 'as_intended', b
    assert any(line.startswith('A1:') for line in found['moments'][1]['proposals']), found['moments'][1]
    checks.append(f"check: redraw with {len(a)} A pose(s) {[x['view'] for x in a]} ({search['probed']} probed, refused {search['refused']}, "
                  f"{search['seconds']} s) and B {b[0]['status']}; frames check {seconds} s")

    made = frames_check.proposals(p, 's')
    record = read_json(p / 'concepts/s/proposals/proposals.json')
    moment = record['moments'][0]
    assert moment['intended']['image'].endswith('outlined.png'), moment['intended']
    assert all((p / x['image']).is_file() for x in moment['a']) and (p / moment['b'][0]['image']).is_file(), moment
    page = (p / 'review/proposals.html').read_text(encoding='utf-8')
    assert '샷마다 A/B를 말씀해 주세요' in page and 'A1:' in page, page[:300]
    checks.append(f"proposals: page {made['artifacts'][1]}, {len(moment['a'])} A + {len(moment['b'])} B tiles, "
                  f"{made['candidates_added']} A on the candidates sheet")

print(json.dumps({'ok': True, 'checks': checks}, ensure_ascii=False, indent=1))
