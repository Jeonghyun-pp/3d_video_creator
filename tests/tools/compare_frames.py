"""Compare a shot's camera frame rows against a recorded baseline (host, stdlib only).

A frame row is one entry of versions/<v>/camera_move_report.json 'frames'. Per row (matched by order and 'frame'):
  lens_mm equal; offset_from_picture equal;
  |position - base.position| <= POSITION_SHARE * |base.position - base.aim|   (scale-free: 1% of the camera distance);
  achieved.height_share relative diff <= HEIGHT_REL; |achieved.centre - base.centre| <= CENTRE_TOL;
  a row that carries 'engaged' (the mechanisms that fired) must name the same ones as its baseline row: it records how
  each pose was reached (intent, placed, refined ...).
Baselines recorded before 2026-10-17 are of the exact search, removed then: a current build does not match them (another
solve), so record a new baseline from a build the user approved (solver_regress/baseline_intent.json, 2026-10-16).

Run: .venv/bin/python tests/tools/compare_frames.py --baseline B.json --project P --shots s01 s02 [--version v0003]
     .venv/bin/python tests/tools/compare_frames.py --record B.json --project P --shots s01=v0002 s05
Prints a JSON result; exits 1 when any row fails (or a shot/report is missing).
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys

POSITION_SHARE = 0.01
HEIGHT_REL = 0.01
CENTRE_TOL = 0.01
FIELDS = ('frame', 'at', 'position', 'aim', 'lens_mm', 'offset_from_picture', 'achieved', 'steps', 'limited_by', 'view', 'mode', 'engaged')
VERSION = re.compile(r'^v\d{4}$')


def latest_version(project, shot):
    names = sorted(p.name for p in (Path(project) / 'shots' / shot / 'versions').iterdir() if VERSION.match(p.name))
    if not names:
        raise FileNotFoundError(f'{shot}: no built version')
    return names[-1]


def report_path(project, shot, version=None):
    return Path(project) / 'shots' / shot / 'versions' / (version or latest_version(project, shot)) / 'camera_move_report.json'


def extract(report):
    """The compared fields of every frame row ('mode'/'engaged' only when the row has them)."""
    return [{k: row[k] for k in FIELDS if k in row} for row in report.get('frames') or []]


def record(project, specs):
    """specs: ['s01', 's23=v0003', ...] -> baseline dict."""
    shots = {}
    for spec in specs:
        shot, _, version = spec.partition('=')
        path = report_path(project, shot, version or None)
        shots[shot] = {'version': path.parent.name, 'source': str(path), 'frames': extract(json.loads(path.read_text()))}
    return {'schema_version': 1, 'project': str(project), 'shots': shots}


def compare_rows(base, row):
    """Failure strings for one frame row against its baseline row (empty list = same)."""
    bad = []
    if row.get('frame') != base.get('frame'):
        bad.append(f"frame {row.get('frame')} != {base.get('frame')}")
    if row.get('lens_mm') != base.get('lens_mm'):
        bad.append(f"lens_mm {row.get('lens_mm')} != {base.get('lens_mm')}")
    if row.get('offset_from_picture') != base.get('offset_from_picture'):
        bad.append(f"offset_from_picture {row.get('offset_from_picture')} != {base.get('offset_from_picture')}")
    try:
        moved, reach = math.dist(row['position'], base['position']), math.dist(base['position'], base['aim'])
        if moved > POSITION_SHARE * reach:
            bad.append(f'position moved {moved:.4f} > {POSITION_SHARE} x distance {reach:.4f}')
    except (KeyError, TypeError) as error:
        bad.append(f'position/aim missing: {error}')
    a, b = row.get('achieved'), base.get('achieved')
    if not a or not b:
        if bool(a) != bool(b):
            bad.append(f'achieved {a} vs baseline {b}')
    else:
        h, hb = a.get('height_share'), b.get('height_share')
        if h is None or hb is None or abs(h - hb) > HEIGHT_REL * max(abs(hb), 1e-9):
            bad.append(f'height_share {h} vs {hb} (> {HEIGHT_REL:.0%} relative)')
        c, cb = a.get('centre'), b.get('centre')
        if c is None or cb is None or math.dist(c, cb) > CENTRE_TOL:
            bad.append(f'centre {c} vs {cb} (> {CENTRE_TOL})')
    if sorted(row.get('engaged') or []) != sorted(base.get('engaged') or []):
        bad.append(f"engaged {row.get('engaged') or []} != baseline {base.get('engaged') or []}")
    return bad


def compare(baseline, project, shots=None, version=None):
    """baseline: dict or path. Returns {'ok', 'shots': {sid: {'version', 'ok', 'failures': [...]}}}."""
    if not isinstance(baseline, dict):
        baseline = json.loads(Path(baseline).read_text())
    out = {}
    for shot in shots or sorted(baseline['shots']):
        failures, used = [], None
        base = (baseline['shots'].get(shot) or {}).get('frames')
        try:
            path = report_path(project, shot, version)
            used = path.parent.name
            rows = extract(json.loads(path.read_text()))
        except (OSError, ValueError) as error:
            rows, failures = None, [f'report: {error}']
        if base is None:
            failures.append('no baseline rows')
        elif rows is not None:
            if len(rows) != len(base):
                failures.append(f'{len(rows)} rows vs baseline {len(base)}')
            for i, (b, r) in enumerate(zip(base, rows)):
                failures += [f'row {i}: {m}' for m in compare_rows(b, r)]
        out[shot] = {'version': used, 'ok': not failures, 'failures': failures}
    return {'ok': all(v['ok'] for v in out.values()), 'shots': out}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--baseline')
    ap.add_argument('--record', help='write a baseline from the project instead of comparing')
    ap.add_argument('--project', required=True)
    ap.add_argument('--shots', nargs='+')
    ap.add_argument('--version')
    args = ap.parse_args(argv)
    if args.record:
        data = record(args.project, args.shots or [])
        Path(args.record).write_text(json.dumps(data, indent=1) + '\n')
        print(json.dumps({'ok': True, 'recorded': {k: v['version'] for k, v in data['shots'].items()}}))
        return 0
    if not args.baseline:
        ap.error('--baseline or --record is required')
    result = compare(args.baseline, args.project, args.shots, args.version)
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
