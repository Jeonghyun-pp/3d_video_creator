#!/usr/bin/env python3
"""Where a production run stands, as one JSON line: per shot the passing and failed versions, counted renders, the
agent's decision notes, and (with --log-dir) whether the launcher run has ended and how.

Directories are walked with os.scandir - no shell globs, so an empty folder is a 0, never an error (2026-10-07: a
monitor built from zsh globs missed a failed build). Watching a run is calling this in a loop and printing changes.

Usage:
  scripts/run_status.py PROJECT [--log-dir DIR]
  scripts/run_status.py PROJECT --log-dir DIR --watch [--every 30]    # one line per change, exits when the run ends
"""
import argparse
import json
import os
import time
from pathlib import Path


def _dirs(path):
    try:
        return sorted(e.name for e in os.scandir(path) if e.is_dir())
    except FileNotFoundError:
        return []


def status(project, log_dir=None):
    project = Path(project)
    shots = {}
    for shot in _dirs(project / 'shots'):
        versions = _dirs(project / 'shots' / shot / 'versions')
        shots[shot] = {'passed': [v for v in versions if v[:1] == 'v' and v[1:].isdigit()],
                       'failed': [v for v in versions if v.startswith('failed_')],
                       'renders': len(_dirs(project / 'shots' / shot / 'renders'))}
    log = project / 'decisions' / 'agent_log.jsonl'
    notes = sum(1 for line in log.read_text().splitlines() if line.strip()) if log.is_file() else 0
    out = {'project': str(project), 'exists': project.is_dir(), 'shots': shots, 'decision_notes': notes,
           'delegated': bool(project.is_dir() and (project / 'project.json').is_file()
                             and json.loads((project / 'project.json').read_text()).get('delegation'))}
    if log_dir:
        run = Path(log_dir) / 'run.json'
        record = json.loads(run.read_text()) if run.is_file() else {}
        out['run'] = {'started_at': record.get('started_at'), 'ended': 'exit_code' in record, 'exit_code': record.get('exit_code'),
                      'finished_at': record.get('finished_at')}
    return out


def _changes(now):
    """The part of a status that changes as a run goes (what a watcher prints)."""
    return json.dumps({'shots': {s: [len(v['passed']), len(v['failed']), v['renders']] for s, v in now['shots'].items()},
                       'notes': now['decision_notes'], 'ended': (now.get('run') or {}).get('ended')}, sort_keys=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('project')
    parser.add_argument('--log-dir')
    parser.add_argument('--watch', action='store_true', help='print a line on every change; stop when the run has ended')
    parser.add_argument('--every', type=float, default=30.0)
    args = parser.parse_args()
    if not args.watch:
        print(json.dumps(status(args.project, args.log_dir), ensure_ascii=False))
        return 0
    if not args.log_dir:
        parser.error('--watch needs --log-dir (to know when the run ends)')
    seen = None
    while True:
        now = status(args.project, args.log_dir)
        key = _changes(now)
        if key != seen:
            print(time.strftime('%H:%M:%S'), json.dumps(now, ensure_ascii=False), flush=True)
            seen = key
        if now['run']['ended']:
            return 0
        time.sleep(args.every)


if __name__ == '__main__':
    raise SystemExit(main())
