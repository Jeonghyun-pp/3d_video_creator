"""Storyboard ping-pong end to end (Workbench sheets only, no look render): propose a sheet for a data-built shot, the
user's words as a typed camera edit with a before/after sheet, approval binding the measured frames, and the contract
catching a later change of the camera (STORYBOARD_DRIFT) while the approved version passes.
Run: .venv/bin/python tests/studio/storyboard_smoke.py
"""
from pathlib import Path
import json
import re
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio import storyboard
from studio.blender import revise_shot
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, load_shot, shot_path

layout_src = (ROOT / 'tests/studio/layout_smoke.py').read_text()
ns = {}
exec(layout_src[layout_src.index('Y0, LEVEL_H'):layout_src.index('PROBE = ')], ns)

checks = []
with tempfile.TemporaryDirectory(prefix='storyboard-smoke-') as root:
    p = Path(init_project('storyboard_test', {'request': 'storyboard smoke', 'shots': [{'shot_id': 's', 'frame_count': 60}]}, root)['project_path'])
    project = read_json(p / 'project.json')   # the layout fixture's subject is small on purpose (a mechanics test)
    project['policy'] = {'gates': {'FRAME_SUBJECT_SMALL': 'warn'}}; write_json(p / 'project.json', project)
    shot = read_json(shot_path(p, 's'))
    shot.update({'scene': ns['SCENE'], 'camera': ns['CAMERA'], 'actions': [ns['REVEAL']], 'fill_brief': ns['BRIEF']})
    write_json(shot_path(p, 's'), shot)

    started = time.monotonic()
    first = storyboard.propose(p, 's', [0, 0.5, 1], focus={0.5: ['st.wall'], 1: ['st.wall']}, captions={0: 'street above', 1: 'inside the section'})
    seconds = time.monotonic() - started
    state = read_json(Path(first['sheet']).parent / 'state.json')
    assert Path(first['image']).is_file() and state['engine'] == 'BLENDER_WORKBENCH' and first['frames'] == [0, 30, 59], (first, state['engine'])
    checks.append('sheet_from_workbench_frames')

    with_words = storyboard.revise(p, 's', '카메라를 좀 더 높이서 시작해줘', [{'op': 'camera.height', 'delta_m': 6}])
    assert with_words['rev'] == 'r02' and with_words['changes'] == ['/camera/move/params/above_m: 20 → 26'], with_words
    assert load_shot(p, 's')['camera']['move']['params']['above_m'] == 26
    assert 'before' in Path(with_words['sheet']).read_text() or Path(with_words['image']).is_file()
    try:
        storyboard.revise(p, 's', '옆에서 찍어줘', [{'op': 'camera.angle', 'delta_deg': 90}])   # section_push has no angle knob
        raise AssertionError('unknown knob accepted')
    except StudioError as error:
        assert 'has no angle knob' in error.message, error.message
    checks += ['user_words_as_typed_edit', 'unknown_knob_refused']

    try:
        storyboard.approve(p, 's', '좋아요 이걸로', 'r01')
        raise AssertionError('old sheet approved')
    except StudioError as error:
        assert error.code == 'DECISION_STALE'
    approved = storyboard.approve(p, 's', '좋아요 이걸로', 'r02')
    change, _ = storyboard.apply_ops(load_shot(p, 's'), [{'op': 'camera.closer', 'factor': 0.4}])         # a later change outside the dialogue
    current = load_shot(p, 's')
    write_json(Path(root) / 'c.json', {'base_revision': current['revision'], 'scope': 'scene', 'targets': [], 'change': change, 'preserve': []})
    rebuilt = revise_shot(p, 's', Path(root) / 'c.json')
    (p / 'decisions').mkdir(exist_ok=True); write_json(p / 'decisions/ladder.json', {'schema_version': 1})   # the project is on the ladder
    storyboard.require(p, load_shot(p, 's'), approved['version'])                                         # the approved version passes
    checks.append('approval_binds_the_sheet_seen')
    try:
        storyboard.require(p, load_shot(p, 's'), rebuilt['scene_version'])
        raise AssertionError('drifted camera accepted')
    except StudioError as error:
        assert error.code == 'STORYBOARD_DRIFT', error.code
        drift = error.message
    checks.append('contract_catches_drift')

print('STUDIO_STORYBOARD_SMOKE ' + json.dumps({'ok': True, 'checks': checks, 'sheet_seconds': round(seconds, 1), 'drift': drift[:200]}))
