"""Workbench shot edits (set_shot_value): any value of the shot changed in a session, which then shows what a build of
that shot makes; commit builds the same thing and must end where the session ended.

On the driven reducer (data-built, turntable + drive): a camera move param and an action value changed by path, a variant
kept and restored, commit -> shot.json holds the values and the build's camera after its generators equals the session's;
a scene edit commits as a fresh build; a wrong post-generator expectation fails; refusals: a value nothing reads, hand
camera keys after a shot edit.
Run: .venv/bin/python tests/studio/workbench_shot_smoke.py
"""
from pathlib import Path
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio import workbench
from studio.blender import build_shot
from studio.common import StudioError, read_json, write_json
from studio.mechanisms import planetary_spec
from studio.project import init_project, load_shot, shot_path
from studio.subjects import spec_path

source = (ROOT / 'tests/studio/mechanism_smoke.py').read_text()
ns = {}
exec(source[source.index('SUN_TURNS'):source.index('PROBE = ')], ns)


def refused(call, code, words=''):
    try:
        call(); raise AssertionError(f'expected {code}')
    except StudioError as error:
        assert error.code == code and words in error.message, (error.code, error.message[:300])


checks = []
with tempfile.TemporaryDirectory(prefix='workbench-shot-') as root:
    p = Path(init_project('wb_shot', {'request': 'workbench shot edits', 'shots': [{'shot_id': 's', 'frame_count': 48}]}, root)['project_path'])
    spec_path(p, 'reducer').parent.mkdir(parents=True, exist_ok=True)
    write_json(spec_path(p, 'reducer'), planetary_spec('reducer', 0.002, 18, 27, 72, 3))
    shot = read_json(shot_path(p, 's')); shot.update({'scene': ns['SCENE'], 'camera': ns['CAMERA'], 'actions': [ns['DRIVE']]})
    write_json(shot_path(p, 's'), shot)
    build_shot(p, 's', None)

    session = workbench.start(p, 's')['session_id']
    try:
        edit = workbench.call(p, session, 'set_shot_value', {'ops': [{'op': 'set', 'path': '/camera/move/params/elevation_deg', 'value': 40}]})['result']
        assert edit['generated'] and edit['changes'] == ['/camera/move/params/elevation_deg: 25.0 → 40'], edit
        workbench.call(p, session, 'set_shot_value', {'ops': [{'op': 'set', 'path': '/actions/0/params/drives/0/keys/1/value', 'factor': 2}]})
        preview = workbench.call(p, session, 'preview', {'views': ['shot'], 'passes': ['shaded'], 'size': 160, 'frames': [1, 40]})['result']
        assert preview['images'], preview
        checks.append('camera_and_action_values_by_path_previewed')

        workbench.call(p, session, 'variant_save', {'name': 'high', 'note': 'elevation 40'})
        workbench.call(p, session, 'set_shot_value', {'ops': [{'op': 'set', 'path': '/camera/move/params/elevation_deg', 'value': 10}]})
        workbench.call(p, session, 'variant_restore', {'name': 'high'})
        assert workbench.call(p, session, 'current_shot')['result']['shot']['camera']['move']['params']['elevation_deg'] == 40
        checks.append('variant_restore_returns_the_shot_too')

        refused(lambda: workbench.call(p, session, 'set_shot_value', {'ops': [{'op': 'set', 'path': '/camera/move/params/elevaton_deg', 'value': 1}]}),
                'INPUT_INVALID', "nothing reads 'elevaton_deg'")
        refused(lambda: workbench.call(p, session, 'set_camera_keys', {'keys': [{'frame': 1, 'location': [0, -1, 0.3], 'rotation_deg': [80, 0, 0]}]}),
                'INPUT_INVALID', 'set_shot_value')
        checks.append('unread_value_and_hand_keys_refused')

        committed = workbench.commit(p, session, diagnosis='camera higher, motor twice as far', chosen_variant='high', why='shows the planets riding the carrier')
        assert committed['shot_edited'] and not committed['built_fresh'], committed
        final = load_shot(p, 's')
        assert final['camera']['move']['params']['elevation_deg'] == 40 and final['actions'][0]['params']['drives'][0]['keys'][1]['value'] == 720, final['actions']
        version = p / 'shots/s/versions' / committed['scene_version']
        report = read_json(version / 'replay_report.json')
        assert report['ok'] and report['stage'] == 'after generators', report['issues'][:3]
        checks.append('commit_builds_what_the_session_showed')
    finally:
        workbench.stop(p, session)

    session = workbench.start(p, 's')['session_id']
    try:
        workbench.call(p, session, 'set_shot_value', {'ops': [{'op': 'set', 'path': '/scene/instances/0/at', 'value': [0, 0, 0.05]}]})
        fresh = workbench.commit(p, session, diagnosis='reducer raised 5 cm')
        assert fresh['built_fresh'] and load_shot(p, 's')['scene']['instances'][0]['at'] == [0, 0, 0.05], fresh
        checks.append('scene_edit_commits_as_a_fresh_build')
    finally:
        workbench.stop(p, session)

    wrong = {'after': {'subjects': {}, 'objects': {}, 'camera': {'0': [0.0] * 17}}}
    refused(lambda: build_shot(p, 's', None, expect=wrong), 'WORKBENCH_REPLAY_MISMATCH')
    checks.append('post_generator_mismatch_fails')

print('STUDIO_WORKBENCH_SHOT_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
