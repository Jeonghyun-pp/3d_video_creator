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

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT = ROOT / '.studio' / 'freeze_baseline.json'


from studio.freeze import diff, groups as _groups   # one definition: the build-time check uses the same groups


def groups():
    return {g: files for g, files in _groups().items() if g != 'guard'}


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
