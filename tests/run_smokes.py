"""Run every smoke in tests/studio and report pass/fail as JSON (one command instead of hand-run tallies).

How a smoke runs is read from the file, not from a list: a module-level `import bpy` (or bmesh/mathutils; ast, so scripts inside strings do not count)
means it runs inside Blender (`blender -b --factory-startup --python`), anything else runs on the host
venv (those drive Blender themselves through the studio CLI). A new smoke is picked up automatically.
KNOWN_FAILING lists smokes failing for a recorded reason; it may only shrink, and a known failure that
starts passing is reported so it can be removed.

Run: .venv/bin/python tests/run_smokes.py [name ...] [--json out.json]
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SMOKES = ROOT / 'tests' / 'studio'
sys.path.insert(0, str(ROOT))
KNOWN_FAILING = {}   # name -> reason (BUILD_REPORT entry); keep empty unless a failure is recorded there
BLENDER_MODULES = {'bpy', 'bmesh', 'mathutils', 'bpy_extras'}
TIMEOUT_S = 1800


def mode(path):
    """Module-level imports only (ast): a Blender script embedded in a string does not count."""
    tree = ast.parse(path.read_text())
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names |= {a.name.split('.')[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split('.')[0])
    return 'blender' if names & BLENDER_MODULES else 'host'


def run(path):
    from studio.common import blender_binary
    command = ([blender_binary(), '-b', '--factory-startup', '--python-exit-code', '1', '--python', str(path)]
               if mode(path) == 'blender' else [str(Path(sys.executable)), str(path)])
    started = time.monotonic()
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=TIMEOUT_S)
        code, out = result.returncode, (result.stdout + result.stderr)
    except subprocess.TimeoutExpired:
        code, out = -1, f'timeout after {TIMEOUT_S}s'
    tail = [line for line in out.splitlines() if line.strip()][-3:]
    return {'smoke': path.stem, 'mode': mode(path), 'passed': code == 0, 'exit': code,
            'seconds': round(time.monotonic() - started, 1), 'tail': tail}


def main(argv):
    out = None
    if '--json' in argv:
        out = Path(argv[argv.index('--json') + 1]); argv = argv[:argv.index('--json')] + argv[argv.index('--json') + 2:]
    paths = sorted(SMOKES.glob('*.py'))
    if argv:
        paths = [p for p in paths if p.stem in argv]
    rows = []
    for path in paths:
        row = run(path)
        row['known_failing'] = KNOWN_FAILING.get(path.stem)
        rows.append(row)
        print(f"{'PASS' if row['passed'] else ('KNOWN' if row['known_failing'] else 'FAIL')} {row['smoke']} ({row['mode']}, {row['seconds']}s)", flush=True)
    unexpected = [r['smoke'] for r in rows if not r['passed'] and not r['known_failing']]
    fixed = [r['smoke'] for r in rows if r['passed'] and r['known_failing']]
    report = {'total': len(rows), 'passed': sum(r['passed'] for r in rows), 'unexpected_failures': unexpected,
              'known_failing_now_passing': fixed, 'rows': rows}
    if out:
        out.write_text(json.dumps(report, indent=1))
    print(json.dumps({k: report[k] for k in ('total', 'passed', 'unexpected_failures', 'known_failing_now_passing')}))
    return 1 if unexpected else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
