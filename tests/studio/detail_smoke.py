"""Host smoke (needs Blender): the fidelity detail check (SKILL #8) on a real build of the winch with three added parts
in front of the camera - a plain box guard (a placeholder: fails), the same box declared `plain` (passes, reason kept)
and a cast housing (`casting`: passes). The winch's own parts are reported with their measured px per face direction.
Run: .venv/bin/python tests/studio/detail_smoke.py
"""
from pathlib import Path
import json
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot  # noqa: E402
from studio.common import read_json, write_json  # noqa: E402

SOURCE = ROOT / 'tests/fixtures/winch_spec'
report = {}


def check(name, cond, detail):
    report[name] = detail
    assert cond, f'{name}: {detail}'


GUARD = {'builder': 'box', 'params': {'size': [0.5, 0.04, 0.5]}, 'transform': {'location': [0.0, -0.45, 0.0]}}
HOUSING = {'builder': 'casting', 'transform': {'location': [0.0, 0.45, 0.0]}, 'params': {
    'voxel_m': 0.006, 'fillet_m': 0.012, 'round_m': 0.006,
    'members': [{'builder': 'box', 'params': {'size': [0.4, 0.1, 0.3]}},
                {'builder': 'revolve', 'params': {'profile': [[0, 0], [0.07, 0], [0.07, 0.12], [0, 0.12]], 'segments': 48, 'axis': 'y'},
                 'transform': {'location': [0, -0.02, 0]}},
                {'builder': 'box', 'params': {'size': [0.02, 0.14, 0.28]}, 'transform': {'location': [0.12, 0, 0]}},
                {'builder': 'box', 'params': {'size': [0.02, 0.14, 0.28]}, 'transform': {'location': [-0.12, 0, 0]}}],
    'cuts': [{'builder': 'revolve', 'params': {'profile': [[0, -0.2], [0.03, -0.2], [0.03, 0.3], [0, 0.3]], 'segments': 48, 'axis': 'y'}}]}}

(ROOT / 'projects/harness_validation').mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT / 'projects/harness_validation') as tmp:
    project = Path(tmp) / 'winch_spec'
    shutil.copytree(SOURCE, project, ignore=shutil.ignore_patterns('renders', 'workbench', 'failed_*', 'runs', 'versions'))
    shot = read_json(project / 'shots/winch/shot.json'); shot['scene_version'] = None; write_json(project / 'shots/winch/shot.json', shot)
    spec_file = project / 'subjects/winch/spec.json'
    spec = read_json(spec_file)
    spec['builders'] += [{'part_id': 'guard', **GUARD}, {'part_id': 'guard_plain', **GUARD, 'transform': {'location': [0.0, -0.6, 0.0]},
                                                         'plain': 'a flat sheet-metal guard: plain on both faces'},
                         {'part_id': 'housing', **HOUSING}]
    write_json(spec_file, spec)
    built = build_shot(project, 'winch', ROOT / 'tests/fixtures/winch_spec_inputs/author_winch.py')
    fidelity = read_json(project / 'shots/winch/versions' / built['scene_version'] / 'fidelity_report.json')
    rows = {c['id']: c for s in fidelity['subjects'] for c in s['checks'] if c['kind'] == 'detail'}
    geometry = read_json(project / 'shots/winch/versions' / built['scene_version'] / 'fidelity_geometry.json')
    measured = {p: r['px_per_face'] for s in geometry['subjects'] for p, r in (s.get('detail') or {}).items()}
    report['px_per_face'] = measured
    check('placeholder_fails', rows.get('guard', {}).get('passed') is False and 'SKILL #8' in rows['guard'].get('note', ''), rows.get('guard'))
    check('plain_passes_with_reason', rows.get('guard_plain', {}).get('passed') is True and 'sheet-metal' in rows['guard_plain']['note'],
          rows.get('guard_plain'))
    check('casting_passes', 'housing' in measured and measured['housing'] <= 40 and 'housing' not in rows, measured.get('housing'))
    check('honest_winch_parts_pass', not [p for p in ('drum', 'axle', 'flange_a', 'flange_b', 'spokes', 'wheel_rim', 'handle') if p in rows], sorted(rows))
    check('report_blocks', fidelity['passed'] is False, [f for s in fidelity['subjects'] for f in s['failures']][:3])

print('DETAIL SMOKE OK', json.dumps(report))
