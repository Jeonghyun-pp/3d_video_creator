"""Where a command's time goes: every studio command run on a project leaves one row in the project's
runs/timings.jsonl ({id, at, kind: command, command, args, shots, seconds, ok}), and each Blender process and model call
it makes leaves a row nested under it (kind blender | model, parent = the command's id). `studio timings --project P`
sums them by command and by kind and lists the slowest. Why (2026-10-26): one 8-cut clay reel took 2-3 hours and no one
could say where the hours went.

The command now running is this process's (a CLI process runs one command) and is handed to the processes it starts
in the environment (ENV), so a render worker's Blender time nests under the command that asked for the render.
A row is bookkeeping: writing one never fails, slows or changes the command it measures."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
import time
import uuid

FILE = 'runs/timings.jsonl'
ENV = 'STUDIO_TIMING_PARENT'   # {"project": <dir>, "id": <command row id>} for processes a command starts
KINDS = ('blender', 'model', 'render')   # nested kinds (render: a clip drawn locally, v2's studio2/motion.py); a command's time not in them is "other"
_ARG_CHARS = 120
_WRITE = threading.Lock()   # rows from worker threads of one command are whole lines


def _now():
    from .common import now
    return now()


def current():
    """{project, id} of the command this process runs for, or None."""
    try:
        got = json.loads(os.environ.get(ENV) or 'null')
        return got if isinstance(got, dict) and got.get('project') and got.get('id') else None
    except ValueError:
        return None


def _write(project, row):
    try:
        file = Path(project) / FILE
        file.parent.mkdir(parents=True, exist_ok=True)
        with _WRITE, file.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
    except Exception:  # noqa: BLE001 - a row is bookkeeping; the command it measures goes on
        pass


def _shots(args):
    out = []
    for key in ('shot', 'shots'):
        value = getattr(args, key, None)
        out += [value] if isinstance(value, str) else list(value or [])
    return [str(s) for s in out]


def _args(args):
    skip = {'handler', 'command', 'project'}
    return {k: (v if isinstance(v, (bool, int, float)) or v is None else str(v)[:_ARG_CHARS])
            for k, v in sorted(vars(args).items()) if k not in skip and not k.endswith('_command')}


class command:
    """The row of one CLI command (studio/__main__.main): begun before its handler, written when it ends."""

    def __init__(self, args, operation):
        self.row, self.saved = None, os.environ.get(ENV)
        try:
            project = getattr(args, 'project', None)
            if not project or operation == 'timings':
                return
            from .project import project_dir
            path = project_dir(project)
            if not (path / 'project.json').is_file():
                return
            self.project = path
            self.row = {'id': uuid.uuid4().hex[:12], 'at': _now(), 'kind': 'command', 'command': operation, 'args': _args(args),
                        'shots': _shots(args)}
            self.started = time.monotonic()
            os.environ[ENV] = json.dumps({'project': str(path), 'id': self.row['id'], 'shots': self.row['shots']})
        except Exception:  # noqa: BLE001
            self.row = None

    def end(self, ok):
        if self.row is None:
            return
        try:
            if self.saved is None:
                os.environ.pop(ENV, None)
            else:
                os.environ[ENV] = self.saved
            _write(self.project, {**self.row, 'seconds': round(time.monotonic() - self.started, 3), 'ok': bool(ok)})
        except Exception:  # noqa: BLE001
            pass


_SHOT = threading.local()   # the shot a worker thread of a many-shot command works on (studio/pool.each_shot)


@contextmanager
def for_shot(shot_id):
    """Rows nested while this block runs (in this thread) are this shot's."""
    before = getattr(_SHOT, 'shots', None)
    _SHOT.shots = [shot_id]
    try:
        yield
    finally:
        _SHOT.shots = before


def record(kind, what, seconds, ok=True, shots=None, project=None, parent=None):
    """A nested row (a Blender process, a model call) under the command now running - or under `parent` in `project`
    (a render worker, from its job). Nothing is written where no command or project is known."""
    try:
        here = current() or {}
        project = project or here.get('project')
        if not project:
            return
        _write(project, {'id': uuid.uuid4().hex[:12], 'parent': parent or here.get('id'), 'at': _now(), 'kind': kind,
                         'command': str(what)[:_ARG_CHARS], 'shots': list(shots if shots is not None else getattr(_SHOT, 'shots', None) or here.get('shots') or []),
                         'seconds': round(float(seconds), 3), 'ok': bool(ok)})
    except Exception:  # noqa: BLE001
        pass


class timed:
    """with timed('model', 'judge_x.json'): ... - a nested row of how long the block took and whether it raised."""

    def __init__(self, kind, what, shots=None):
        self.kind, self.what, self.shots = kind, what, shots

    def __enter__(self):
        self.started = time.monotonic()
        return self

    def __exit__(self, kind, error, trace):
        record(self.kind, self.what, time.monotonic() - self.started, ok=error is None, shots=self.shots)
        return False


def rows(project):
    from .project import project_dir
    file = project_dir(project) / FILE
    out = []
    if not file.is_file():
        return out
    for line in file.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue   # a line cut short by a killed process
        if isinstance(row, dict) and row.get('kind') and isinstance(row.get('seconds'), (int, float)):
            out.append(row)
    return out


def summary(project, slowest=10, since=None):
    """By command (count, seconds, mean, failed), by kind (blender / model / other: a command's own time outside the
    Blender processes and model calls nested in it; nested rows running at once may sum past their command) and the
    slowest rows. since: only rows at or after this ISO time."""
    from .project import project_dir
    got = [r for r in rows(project) if not since or str(r.get('at')) >= since]
    commands = [r for r in got if r['kind'] == 'command']
    by_command = {}
    for r in commands:
        c = by_command.setdefault(r['command'], {'count': 0, 'seconds': 0.0, 'failed': 0})
        c['count'] += 1; c['seconds'] = round(c['seconds'] + r['seconds'], 3); c['failed'] += not r.get('ok', True)
    for c in by_command.values():
        c['mean'] = round(c['seconds'] / c['count'], 3)
    nested = {kind: round(sum(r['seconds'] for r in got if r['kind'] == kind), 3) for kind in KINDS}
    ids = {r['id'] for r in commands}
    inside = {}
    for r in got:
        if r['kind'] in KINDS and r.get('parent') in ids:
            inside[r['parent']] = inside.get(r['parent'], 0.0) + r['seconds']
    other = round(sum(max(0.0, r['seconds'] - inside.get(r['id'], 0.0)) for r in commands), 3)
    return {'total_seconds': round(sum(r['seconds'] for r in commands), 3),
            'by_command': dict(sorted(by_command.items(), key=lambda kv: -kv[1]['seconds'])),
            'by_kind': {**nested, 'other': other},
            'slowest': sorted(got, key=lambda r: -r['seconds'])[:slowest], 'file': str(project_dir(project) / FILE)}


def register_commands(subparsers):
    p = subparsers.add_parser('timings', help='where the time went: each command, Blender process and model call on the project, '
                                              'summed by command and kind, slowest first (runs/timings.jsonl)')
    p.add_argument('--project', required=True); p.add_argument('--slowest', type=int, default=10)
    p.add_argument('--since', help='only rows at or after this ISO time')
    p.set_defaults(handler=lambda a: summary(a.project, a.slowest, a.since))
