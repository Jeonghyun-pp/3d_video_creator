"""Hash what must not change by accident, and diff it against a recorded baseline.

Watched groups (each change has a known cost, see docs/BUILD_REPORT.md):
  render_fingerprint  render_frames / scene_tools / render_profile / jobs: every render cache is invalidated
  look_inputs         look modules + look_data/*.json: photoreal revisions re-apply every look pass
  control             control_pass.py (+ scene_tools): every control pass is regenerated
  contracts           every projects/**/shot.json and style.json (other sessions' projects included)
  jet_rig_samples     jet_canyon_rig chase: per-frame camera samples of the current version

Run: .venv/bin/python tests/freeze_check.py record [baseline.json]   (before a phase)
     .venv/bin/python tests/freeze_check.py check  [baseline.json] [--allow group ...]
check exits 1 when a group changed that is not allowed; the report lists the files.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / 'studio' / 'blender_ops'
DEFAULT = ROOT / '.studio' / 'freeze_baseline.json'


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _look_files():
    sys.path.insert(0, str(OPS))
    from code_closure import module_closure   # the same file set the look hashes into its inputs
    files = [OPS / m for m in module_closure('look.py')] + sorted((OPS / 'look_data').glob('*.json'))
    return sorted({f for f in files if f.is_file()})


def groups():
    jet = ROOT / 'tests/fixtures/jet_canyon_rig/shots/chase'
    jet_samples = {}
    if (jet / 'shot.json').is_file():
        version = json.loads((jet / 'shot.json').read_text()).get('scene_version')
        report = jet / 'versions' / str(version) / 'camera_rig_report.json'
        if report.is_file():
            jet_samples = {f'{version}/samples': hashlib.sha256(json.dumps(json.loads(report.read_text())['samples'], sort_keys=True).encode()).hexdigest()}
    return {
        'render_fingerprint': {p.name: _sha(p) for p in (OPS / 'render_frames.py', OPS / 'scene_tools.py', OPS / 'render_profile.py', ROOT / 'studio/render_worker.py')},
        'look_inputs': {str(p.relative_to(ROOT)): _sha(p) for p in _look_files()},
        'control': {p.name: _sha(p) for p in (OPS / 'control_pass.py', OPS / 'scene_tools.py', OPS / 'scene_roles.py', OPS / 'scene_geometry.py')},
        'contracts': {str(p.relative_to(ROOT)): _sha(p) for p in sorted((ROOT / 'projects').glob('**/shots/*/shot.json')) + sorted((ROOT / 'projects').glob('**/style.json'))},
        'jet_rig_samples': jet_samples,
    }


def diff(old, new):
    out = {}
    for group in sorted(set(old) | set(new)):
        a, b = old.get(group, {}), new.get(group, {})
        changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        if changed:
            out[group] = changed
    return out


def main(argv):
    command = argv[0] if argv else 'check'
    rest = argv[1:]
    allow = set(rest[rest.index('--allow') + 1:]) if '--allow' in rest else set()
    rest = rest[:rest.index('--allow')] if '--allow' in rest else rest
    path = Path(rest[0]) if rest else DEFAULT
    if command == 'record':
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(groups(), indent=1, sort_keys=True))
        print(json.dumps({'recorded': str(path)}))
        return 0
    if not path.is_file():   # fresh clone: nothing recorded yet
        print(json.dumps({'changed': {}, 'baseline': None, 'hint': f'record one first: tests/freeze_check.py record ({path})'}))
        return 0
    changed = diff(json.loads(path.read_text()), groups())
    # contracts: only files that existed in the baseline count as changes (new projects may appear)
    baseline = json.loads(path.read_text())
    if 'contracts' in changed:
        changed['contracts'] = [k for k in changed['contracts'] if k in baseline.get('contracts', {})]
        if not changed['contracts']:
            del changed['contracts']
    blocked = {g: files for g, files in changed.items() if g not in allow}
    print(json.dumps({'changed': changed, 'allowed': sorted(allow), 'blocked': blocked}, indent=1))
    return 1 if blocked else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
