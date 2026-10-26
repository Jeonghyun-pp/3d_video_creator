"""Takes of several shots at once give what takes made one by one give (Workbench only, clay renders): the same projects,
one made shot by shot and one by `storyboard takes --shots` with three at once, end with the same versions, takes, sheet
pictures and clips; the shots' Blender processes did run at the same time; and a build whose shot changed while Blender
ran (the project lock is not held then) is refused rather than written over the change.
Run: .venv/bin/python tests/studio/many_shots_smoke.py
"""
from pathlib import Path
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
from datetime import datetime
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from PIL import Image  # noqa: E402
from studio import __main__ as cli, storyboard, timings  # noqa: E402
from studio.blender import build_shot  # noqa: E402
from studio.common import StudioError, read_json, write_json  # noqa: E402
from studio.project import init_project, load_shot, shot_path  # noqa: E402
from tests.decisions_fixture import moved, pick_concept, settle  # noqa: E402

SCENE = {'world': {'kind': 'blockout', 'color': [0.2, 0.24, 0.4], 'strength': 0.5, 'samples': 16},
         'materials': {'body': {'color': [0.62, 0.62, 0.64]}, 'ground': {'color': [0.3, 0.3, 0.3]}, 'part': {'color': [0.8, 0.5, 0.3]}},
         'primitives': [{'id': 'ground', 'shape': 'box', 'size': [60, 60, 1], 'at': [0, 0, -0.5], 'material': 'ground'},
                        {'id': 'machine', 'shape': 'box', 'size': [4, 3, 2.5], 'at': [0, 0, 1.25], 'material': 'body'},
                        {'id': 'machine.arm', 'shape': 'box', 'size': [0.4, 2.5, 0.4], 'at': [1.2, 0, 2.8], 'material': 'part'}]}
SHOTS = ('s1', 's2', 's3')
RECIPES = {'s1': ('pass_by', 30), 's2': ('watch', 70), 's3': ('pass_by', 110)}


def project(root, name):
    p = Path(init_project(name, {'request': 'many shots smoke', 'shots': [{'shot_id': s, 'frame_count': 48} for s in SHOTS]}, root)['project_path'])
    settle(p)
    for sid in SHOTS:
        pick_concept(p, sid)
        shot = read_json(shot_path(p, sid))
        shot.update(moved({**shot, 'frame': {'focus': ['machine'], 'size': 'medium'},
                           'show': [{'at': 'start', 'parts': ['machine'], 'how': 'exterior', 'why': 'the machine'}]}))
        recipe, azimuth = RECIPES[sid]
        shot['direction']['move'] = {**shot['direction']['move'], 'recipe': recipe, 'settings': {'height_share': 0.55, 'azimuth_deg': azimuth}}
        shot['scene'] = SCENE
        write_json(shot_path(p, sid), shot)
    return p


def pixels(path):
    with Image.open(path) as im:
        return hashlib.md5(im.convert('RGBA').tobytes()).hexdigest()


def made(p):
    """What takes left of each shot, without times and the project's own path."""
    out = {}
    for sid in SHOTS:
        env = storyboard.envelope(p, sid)
        versions = sorted(d.name for d in (shot_path(p, sid).parent / 'versions').glob('v[0-9]*'))
        takes = [{k: t.get(k) for k in ('id', 'label', 'recipe', 'version', 'change', 'eyes', 'comfort', 'dynamics')} for t in env['variants']['takes']]
        sheet = p / Path(env['variants']['path']).parent
        out[sid] = {'versions': versions, 'takes': takes, 'failed': [f['id'] for f in env['variants']['failed']],
                    'snapshots': {v: {k: x for k, x in read_json(shot_path(p, sid).parent / 'versions' / v / 'shot.snapshot.json').items()
                                      if k not in ('updated_at',)} for v in versions},
                    'sheet': {str(f.relative_to(sheet)): pixels(f) for f in sorted(sheet.rglob('*.png'))},
                    'clips': {t['version']: hashlib.md5((p / t['animatic']).read_bytes()).hexdigest() for t in env['variants']['takes']}}
    return out


checks = []
with tempfile.TemporaryDirectory(prefix='many-shots-smoke-') as root:
    one = project(root, 'one_by_one')
    for sid in SHOTS:
        storyboard.takes(one, sid)
    many = project(root, 'at_once')
    out = io.StringIO()
    with patch.dict(os.environ, {'STUDIO_WORKERS': '3'}), contextlib.redirect_stdout(out):
        code = cli.main(['storyboard', 'takes', '--project', str(many), '--shots', *SHOTS])
    body = json.loads(out.getvalue())
    assert code == 0 and body['workers'] == 3 and [r['shot_id'] for r in body['results']] == list(SHOTS), body.get('failed') or body
    a, b = made(one), made(many)
    for sid in SHOTS:
        for key in a[sid]:
            assert a[sid][key] == b[sid][key], (sid, key, json.dumps(a[sid][key])[:300], json.dumps(b[sid][key])[:300])
    checks += ['same_versions_takes_sheets_and_clips']

    spans = {}
    for r in timings.rows(many):
        if r['kind'] == 'blender' and r.get('shots'):
            end = datetime.fromisoformat(r['at']).timestamp()
            spans.setdefault(r['shots'][0], []).append((end - r['seconds'], end))
    overlapped = any(s1 < e2 and s2 < e1 for x in spans for y in spans if x < y for s1, e1 in spans[x] for s2, e2 in spans[y])
    assert set(spans) == set(SHOTS) and overlapped, spans
    checks.append('shots_blender_ran_at_the_same_time')

    from studio import blender as blender_module
    real = blender_module.run_command

    def edited_meanwhile(command, *args, **kwargs):   # someone changes the shot while Blender runs
        if 'build_scene.py' in ' '.join(map(str, command)):
            shot = read_json(shot_path(one, 's1')); shot['revision'] += 1; write_json(shot_path(one, 's1'), shot)
        return real(command, *args, **kwargs)
    before = load_shot(one, 's1')
    with patch('studio.blender.run_command', side_effect=edited_meanwhile):
        try:
            build_shot(one, 's1', None)
            raise AssertionError('a build wrote over a shot changed while it ran')
        except StudioError as error:
            assert error.code == 'REVISION_CONFLICT', error.code
    after = load_shot(one, 's1')
    assert after['revision'] == before['revision'] + 1 and after.get('scene_version') == before.get('scene_version'), (before['revision'], after['revision'])
    checks.append('build_refuses_a_shot_changed_while_it_ran')

print('STUDIO_MANY_SHOTS_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
