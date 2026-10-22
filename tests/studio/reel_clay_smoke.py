"""The clay cut end to end (Workbench only, no look render): a data-built shot is built, its layout clip rendered,
captioned with its number and lines, cut into the reel and put on one page with the reel flow.
Run: .venv/bin/python tests/studio/reel_clay_smoke.py
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio import reel, storyboard
from studio.common import read_json, write_json
from studio.project import init_project, load_shot, shot_path
from tests.decisions_fixture import settle   # builds stand on settled decisions (studio/decisions.require)

layout_src = (ROOT / 'tests/studio/layout_smoke.py').read_text()
ns = {}
exec(layout_src[layout_src.index('Y0, LEVEL_H'):layout_src.index('PROBE = ')], ns)

with tempfile.TemporaryDirectory(prefix='reel-clay-smoke-') as root:
    p = Path(init_project('reel_clay', {'request': 'reel clay smoke', 'shots': [{'shot_id': 's', 'frame_count': 60}]}, root)['project_path'])
    settle(p)
    from tests.decisions_fixture import pick_concept   # every storyboard stands on a picked concept (concept.require)
    pick_concept(p, 's')
    project = read_json(p / 'project.json')
    project['policy'] = {'gates': {'FRAME_SUBJECT_SMALL': 'warn'}}; write_json(p / 'project.json', project)
    shot = read_json(shot_path(p, 's'))
    shot.update({'scene': ns['SCENE'], 'camera': ns['CAMERA'], 'actions': [ns['REVEAL']]})   # no fill brief: a render waits for its approval
    shot['narration']['text'] = '길 아래 벽 속을 잘라서 보여 줍니다.'
    write_json(shot_path(p, 's'), shot)
    storyboard.propose(p, 's', [0, 1])            # builds the shot (a Workbench sheet)
    assert load_shot(p, 's').get('scene_version'), 'no version built'
    started = time.monotonic()
    made = reel.clay(p)
    seconds = time.monotonic() - started
    frames = subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-show_entries', 'stream=nb_read_frames', '-of', 'csv=p=0', made['reel']],
                            capture_output=True, text=True).stdout.strip()
    assert made['shots'] == ['s'] and frames == '60', (made, frames)
    page = Path(made['page']).read_text()
    assert '길 아래 벽 속을 잘라서 보여 줍니다.' in page and 'reel_clay.mp4' in page
    assert (p / 'review/clay/clay.json').is_file()

print('STUDIO_REEL_CLAY_SMOKE ' + json.dumps({'ok': True, 'clay_seconds': round(seconds, 1), 'flow': made['warnings'][:3]}, ensure_ascii=False))
