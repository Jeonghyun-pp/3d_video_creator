"""Host smoke (needs Blender): workbench session on a temp copy of the winch project.

Proves: ID preview colours map 1:1 to parts, spec edits rebuild only the affected part, checkpoint/restore
is exact, spec-owned data and exec are refused, a commit replays into a new immutable version with
identical measurements, and a tampered expectation fails with WORKBENCH_REPLAY_MISMATCH.
Run: .venv/bin/python tests/studio/workbench_smoke.py
"""
from pathlib import Path
import json
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.common import StudioError, read_json  # noqa: E402
from studio import workbench  # noqa: E402
from studio.blender import build_shot  # noqa: E402

SOURCE = ROOT / 'tests/fixtures/winch_spec'
report = {}


def check(name, cond, detail):
    report[name] = detail
    assert cond, f'{name}: {detail}'


def refused(fn, code):
    try:
        fn()
    except StudioError as exc:
        return exc.code == code or code in exc.message
    return False


(ROOT / 'projects/harness_validation').mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT / 'projects/harness_validation') as tmp:
    project = Path(tmp) / 'winch_spec'
    shutil.copytree(SOURCE, project, ignore=shutil.ignore_patterns('renders', 'workbench', 'failed_*', 'runs'))
    started = workbench.start(project, 'winch')
    sid = started['session_id']
    try:
        call = lambda tool, args=None: workbench.call(project, sid, tool, args or {})  # noqa: E731
        call('build_subject', {'subject_id': 'winch'})
        import socket   # malformed requests are answered and the resident session keeps serving
        for raw in (b'\xff\n', b'[]\n', b'"x"\n'):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(workbench._session(project, sid)['socket']); client.sendall(raw)
                assert b'"ok": false' in client.recv(1 << 16), raw
        times =[call('measure', {'target': 'winch/drum'})['round_trip_ms'] for _ in range(20)]
        t0 = time.perf_counter()
        prev = call('preview', {'subject_id': 'winch', 'views': ['front', 'side', 'top', 'shot']})['result']
        preview_s = time.perf_counter() - t0
        parts = {'winch/' + p for p in ('axle', 'drum', 'flange_a', 'flange_b', 'handle', 'spokes', 'wheel_rim')}
        seen = set().union(*[set(v['parts']) for v in prev['pixels'].values()])
        check('preview_id_exact', parts <= seen and all(v['unmatched_px'] == 0 for v in prev['pixels'].values())
              and len(set(prev['palette'])) == len(prev['palette']) and 'shot' in prev['images'],
              {'views': sorted(prev['images']), 'parts_seen': len(seen), 'seconds': round(preview_s, 2)})
        check('latency', sorted(times)[len(times) // 2] < 50 and preview_s < 3,
              {'measure_median_ms': sorted(times)[len(times) // 2], 'preview_4_views_s': round(preview_s, 2)})
        base = call('measure', {'target': 'winch/drum'})['result']['dimensions']
        call('checkpoint', {'name': 'a'})
        edit = call('set_spec_param', {'subject_id': 'winch', 'pointer': '/builders/0/params/profile/1/0', 'value': 0.18})['result']
        grown = call('measure', {'target': 'winch/drum'})['result']['dimensions']
        call('restore', {'name': 'a'})
        back = call('measure', {'target': 'winch/drum'})['result']['dimensions']
        check('spec_edit_and_restore', edit['rebuilt'] == ['drum'] and abs(grown[1] - 0.36) < 1e-6 and back == base,
              {'base': base, 'grown': grown, 'restored': back})
        check('ownership_refusals', refused(lambda: call('set_transform', {'id': 'winch/drum', 'location': [0, 0, 1]}), 'belongs to subject')
              and refused(lambda: call('exec', {'code': 'result = 1'}), 'exec is disabled')
              and refused(lambda: call('no_such_tool'), 'unknown tool'), 'set_transform on a part, exec, unknown tool')
        call('set_spec_param', {'subject_id': 'winch', 'pointer': '/builders/6/params/path/2/0', 'value': 0.25})
        call('set_transform', {'id': 'Area', 'location': [1.6, -2, 3]})
        committed = workbench.commit(project, sid, diagnosis='handle reads short')
        version = committed['scene_version']
        vdir = project / 'shots/winch/versions' / version
        replay = read_json(vdir / 'replay_report.json')
        spec = read_json(project / 'subjects/winch/spec.json')
        check('commit_replays', replay['ok'] and committed['replayed_ops'] == 3 and spec['builders'][6]['params']['path'][2][0] == 0.25
              and read_json(vdir / 'changes.json')['diagnosis'] == 'handle reads short' and committed['fidelity']['passed'],
              {'version': version, 'ops': committed['replayed_ops']})
        expect = read_json(project / 'workbench' / sid / 'commit_expect.json')
        expect['subjects']['winch']['parts']['handle']['x'] += 0.01
        before = read_json(project / 'shots/winch/shot.json')['scene_version']
        mismatch = refused(lambda: build_shot(project, 'winch', project / 'workbench' / sid / 'patch.py', base=version, expect=expect),
                           'WORKBENCH_REPLAY_MISMATCH')
        check('tampered_expect_refused', mismatch and read_json(project / 'shots/winch/shot.json')['scene_version'] == before,
              'shot.json unchanged after mismatch')
    finally:
        workbench.stop(project, sid)

    # Exploration: three camera alternatives, compared at the same frames, one chosen with a reason.
    from PIL import Image
    from studio import repair
    sid = workbench.start(project, 'winch')['session_id']
    try:
        call = lambda tool, args=None: workbench.call(project, sid, tool, args or {})  # noqa: E731
        stale_before = repair.load_ledger(project, 'winch')['stale_attempts']
        rig = read_json(project / 'shots/winch/shot.json')['camera']['rig']
        options = {'wide': {'radius_m': 3.6}, 'close': {'radius_m': 2.4, 'height_m': 0.3}, 'fast': {'deg_per_s': 60}}
        rig_reports = {}
        for name, change in options.items():
            variant = json.loads(json.dumps(rig)); variant['orbit'].update(change)
            rig_reports[name] = call('set_camera_rig', {'rig': variant})['result']
            saved = call('variant_save', {'name': name, 'note': f'orbit {change}'})['result']
        before = call('measure', {'target': '@camera'})['result']['location']
        compared = workbench.compare(project, sid, ['wide', 'close', 'fast'], frames=[1, 30, 60], views=['shot'], size=200)
        sheet = Image.open(compared['sheet'])
        after = call('measure', {'target': '@camera'})['result']['location']
        call('variant_restore', {'name': 'wide'})
        chosen = workbench.commit(project, sid, diagnosis='orbit crops the hand wheel', chosen_variant='wide', why='wide orbit keeps wheel and drum in frame at every sampled moment')
        committed_rig = read_json(project / 'shots/winch/shot.json')['camera']['rig']
        changes = read_json(project / 'shots/winch/versions' / chosen['scene_version'] / 'changes.json')
        ledger = repair.load_ledger(project, 'winch')
        check('explore_compare_choose', compared['rows'] == ['wide', 'close', 'fast'] and len(compared['columns']) == 3
              and sheet.size[1] > 3 * 150 and before == after  # compare returns to the state it started from
              and changes['variants_considered'] == ['close', 'fast', 'wide'] and changes['chosen_variant'] == 'wide'
              and committed_rig['orbit']['radius_m'] == 3.6 and compared['metrics']['fast']['camera_rig']['summary']
              and ledger['attempts'][-1]['chosen_variant'] == 'wide' and ledger['stale_attempts'] == stale_before,
              {'sheet': compared['sheet'], 'size': sheet.size, 'version': chosen['scene_version'], 'warnings': chosen['warnings'],
               'rig_guards': {k: len(v['gate_failures']) for k, v in rig_reports.items()}})
        refused_choice = refused(lambda: workbench.commit(project, sid, chosen_variant='nope', why='because it is nicer'), 'not a saved variant')
        check('choice_must_exist', refused_choice, 'unknown chosen variant refused')
    finally:
        workbench.stop(project, sid)

print('STUDIO_WORKBENCH_SMOKE ' + json.dumps({'ok': True, 'checks': report}, sort_keys=True))
