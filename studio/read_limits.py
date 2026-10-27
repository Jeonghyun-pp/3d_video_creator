"""How long a model read's asks before the last may go unanswered before it is taken for a stall and asked again: per kind of read
(the folder its answer goes to, without a picture-and-try prefix: locate, checks, separate, plan_read ...), `margin` times
the slowest answered read of that kind on record since the rule began, never below `floor_s` nor above the full limit.
A kind with fewer than `min_n` answered reads has no row and keeps the full limit. The last ask always has the full limit
(screen_grammar.READ_TIMEOUT_S, READ_ASKS), so a read that is slow but would answer loses at most the short asks.

The rows live in library/read_limits.json with their evidence (n, median, p99, slowest, stalls) and are measured again
from every project's runs/timings.jsonl by `studio read-limits --write`.
Why (2026-10-26/27): every stall on record was a locate read that hung to the 900 s limit - nine of them, 15 min each,
135 min over two reels - while no locate read since the reader cap took more than 64 s to answer (294 answered)."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import re

from .common import REPO, now, read_json, write_json

TABLE = REPO / 'library' / 'read_limits.json'
_PREFIX = re.compile(r'^[a-z]+_\d+_')   # <picture>_<try>_ (first_1_separate): the same kind of read on another picture


def kind_of(target):
    """The kind of read whose answer goes to `target`: its folder's name without a picture-and-try prefix."""
    return _PREFIX.sub('', Path(target).parent.name)


def table():
    try:
        return read_json(TABLE)
    except (OSError, ValueError):
        return {'kinds': {}}


def first_limit(target, full_s, got=None):
    """The first ask's limit for a read written to `target`: its kind's row, else the full limit."""
    row = ((got if got is not None else table()).get('kinds') or {}).get(kind_of(target))
    return min(full_s, row['limit_s']) if row else full_s


def _start(row):
    return datetime.fromisoformat(row['at']) - timedelta(seconds=row['seconds'])   # a nested row is written when it ends


def calibrate(rows, rule):
    """The table measured from timing rows: per kind {limit_s, n, median_s, p99_s, max_s, stalls} from the model reads
    (command 'model <folder>/<file>') that started at or after rule.since."""
    since = datetime.fromisoformat(rule['since'])
    answered, stalls = {}, {}
    for r in rows:
        command = str(r.get('command') or '')
        if r.get('kind') != 'model' or not command.startswith('model ') or '/' not in command:
            continue
        try:
            if _start(r) < since:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        kind = kind_of(command.split(' ', 1)[1])
        if r.get('ok'):
            answered.setdefault(kind, []).append(float(r['seconds']))
        elif r['seconds'] >= rule['full_s'] - 1:
            stalls[kind] = stalls.get(kind, 0) + 1
    kinds = {}
    for kind, seconds in sorted(answered.items()):
        if len(seconds) < rule['min_n']:
            continue
        seconds.sort()
        slowest = seconds[-1]
        kinds[kind] = {'limit_s': min(rule['full_s'], max(rule['floor_s'], math.ceil(rule['margin'] * slowest))),
                       'n': len(seconds), 'median_s': round(seconds[len(seconds) // 2], 1),
                       'p99_s': round(seconds[min(len(seconds) - 1, int(len(seconds) * 0.99))], 1),
                       'max_s': round(slowest, 1), 'stalls': stalls.get(kind, 0)}
    return {'rule': rule, 'kinds': kinds}


def measure(projects=None, write=False):
    """The table measured again from every project's runs/timings.jsonl (projects: the folder holding them); write: kept."""
    folder = Path(projects or REPO / 'projects')
    files = sorted(folder.glob('**/runs/timings.jsonl'))
    rows = []
    for file in files:
        for line in file.read_text(encoding='utf-8', errors='replace').splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    kept = table()
    got = calibrate(rows, kept['rule'])
    out = {**kept, **got, 'calibration': {'at': now(), 'files': len(files), 'rows': len(rows)}}
    if write:
        write_json(TABLE, out)
    return {'ok': True, 'written': bool(write), 'table': str(TABLE), **out,
            'changed': {k: [((kept.get('kinds') or {}).get(k) or {}).get('limit_s'), v['limit_s']] for k, v in got['kinds'].items()
                        if ((kept.get('kinds') or {}).get(k) or {}).get('limit_s') != v['limit_s']}}


def register_commands(subparsers):
    p = subparsers.add_parser('read-limits', help="a model read's first-ask limit per kind, measured from the answered reads on "
                                                  'record (library/read_limits.json)')
    p.add_argument('--projects', help='the folder holding the projects (default: projects/)')
    p.add_argument('--write', action='store_true', help='keep the measured table')
    p.set_defaults(handler=lambda a: measure(a.projects, a.write))
