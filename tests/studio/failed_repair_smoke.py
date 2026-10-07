"""Host smoke (needs Blender): a build that fails its frame probe can be opened in the workbench, fixed with a shot edit
and committed as a new version (2026-10-07: failed versions had no shot snapshot, so set_shot_value could not run).

The winch's drum is declared a key part with the orbit pulled out to 12 m: the build fails KEY_PART_*. The failed
version keeps its input snapshots; a session on it reports base_failed; set /camera/rig/orbit/radius_m back to 3.2 m;
commit -> a passing vNNNN whose shot has the new radius.
Run: .venv/bin/python tests/studio/failed_repair_smoke.py
"""
from pathlib import Path
import json
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio import workbench  # noqa: E402
from studio.blender import build_shot  # noqa: E402
from studio.common import StudioError, read_json, write_json  # noqa: E402

report = {}


def check(name, cond, detail):
    report[name] = detail
    assert cond, f'{name}: {detail}'


(ROOT / 'projects/harness_validation').mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT / 'projects/harness_validation') as tmp:
    project = Path(tmp) / 'winch_spec'
    shutil.copytree(ROOT / 'tests/fixtures/winch_spec', project, ignore=shutil.ignore_patterns('renders', 'workbench', 'failed_*', 'runs', 'versions'))
    shot = read_json(project / 'shots/winch/shot.json')
    shot['scene_version'] = None
    shot['key_parts'] = [{'id': 'winch/drum'}]
    shot['camera']['rig']['orbit']['radius_m'] = 12.0
    write_json(project / 'shots/winch/shot.json', shot)
    meta = read_json(project / 'project.json')   # the size gate is taste (a warning by default): made to block, so the build fails
    meta['policy'] = {**(meta.get('policy') or {}), 'strictness': 'all-strict'}; write_json(project / 'project.json', meta)
    try:
        build_shot(project, 'winch', ROOT / 'tests/fixtures/winch_spec_inputs/author_winch.py')
        code = None
    except StudioError as exc:
        code = exc.code
    check('build_fails_its_probe', code is not None and code.startswith('KEY_PART'), code)
    failed = sorted((project / 'shots/winch/versions').glob('failed_*'))
    check('failed_keeps_inputs', failed and all((failed[0] / n).is_file() for n in ('shot.snapshot.json', 'style.snapshot.json', 'author_job.json')),
          [p.name for p in failed[0].iterdir()] if failed else None)
    session = workbench.start(project, 'winch', failed[0].name)
    sid = session['session_id']
    try:
        check('session_knows_it_fixes_a_failure', session['base_failed'] is True, session['base_failed'])
        edit = workbench.call(project, sid, 'set_shot_value', {'ops': [{'op': 'set', 'path': '/camera/rig/orbit/radius_m', 'value': 3.2}]})
        check('edit_applies', any('radius_m' in c for c in edit['result']['changes']), edit['result']['changes'])
        committed = workbench.commit(project, sid, diagnosis='key part too small: orbit radius 12 m -> 3.2 m')
    finally:
        workbench.stop(project, sid)
    version = committed['scene_version']
    saved = read_json(project / 'shots/winch/versions' / version / 'shot.snapshot.json')
    check('commit_is_a_new_passing_version', version.startswith('v') and saved['camera']['rig']['orbit']['radius_m'] == 3.2, version)

print('FAILED REPAIR SMOKE OK', json.dumps(report, default=str))
