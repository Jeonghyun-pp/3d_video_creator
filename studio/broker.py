"""Run a studio command where Blender can run: outside the agent's sandbox, inside the studio's own.

The codex sandbox (seatbelt) denies the GPU (IOKit AGXDeviceUserClient / IOSurfaceRootUserClient); Blender then dies at
Metal start-up (measured 2026-10-06, exit 139), and the frame probe and previews need the GPU anyway. So Blender never
runs in the agent's sandbox: the studio MCP server (studio/workbench_mcp.py, tool `studio_run`), which codex starts
outside its sandbox, runs the command here, under a profile of our own:
  - GPU allowed; network denied (paid generation is impossible from here, and no API keys are passed: blender_env);
  - writes only inside the repository and the temp folders;
  - only `python -m studio <args>` - not a shell.
Every gate of the command itself still runs (the author isolation, the frame probe, the frozen-code check, the user's
words for approvals): the broker gives Blender access, not authority.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from .common import REPO, StudioError, blender_env

BROKERED = 'STUDIO_BROKERED'
NO_BROKER = ('freeze', 'contrib')   # commands that judge or record the lines themselves are not run through the broker
# Worker entries that need the network (a rented GPU server's API, ssh, rsync). A brokered command never reaches the
# network itself: it leaves such a job 'awaiting_dispatch' and the broker - outside the sandbox - starts only these
# entries (default deny; anything else stays in the sandbox).
NETWORK_ENTRIES = {'_remote_worker': 'remote_render'}   # entry -> the studio module it runs (freeze.network_worker hashes it)


def profile():
    """The macOS sandbox profile a brokered command runs under."""
    roots = {str(Path(REPO).resolve()), str(Path(tempfile.gettempdir()).resolve()), '/private/var/folders', '/private/tmp'}
    roots.add(str((Path(sys.prefix) / 'lib').resolve()))   # __pycache__ of the studio venv - sys.prefix, not the resolved
    # interpreter: resolving the venv's python symlink lands in the system Python's stdlib and global site-packages, a write
    # root that every process outside the sandbox imports from (code review, 2026-10-09)
    writable = ' '.join(f'(subpath "{r}")' for r in sorted(roots))
    return ('(version 1)(allow default)(deny network*)(allow network* (remote unix-socket))(allow network* (local unix-socket))'
            f'(deny file-write*)(allow file-write* {writable} (literal "/dev/null") (regex #"^/dev/tty") (regex #"^/dev/dtracehelper"))')


def command(args):
    if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
        raise StudioError('INPUT_INVALID', 'studio_run takes args: a list of strings, e.g. ["shot", "build", "--project", "...", "--shot", "s01"]')
    if args[0].startswith(('-', '_')) or args[0] in NO_BROKER:   # internal entries (_worker, _remote_worker) only start from a job
        raise StudioError('INPUT_INVALID', f'studio_run does not run {args[0]!r}' + (' (run it in your own shell: it needs no Blender)' if args[0] in NO_BROKER else ''))
    base = [sys.executable, '-m', 'studio', *args]
    if sys.platform == 'darwin':
        return ['sandbox-exec', '-p', profile(), *base]
    return base


def run(args, timeout=3600):
    """The command's JSON result (ok or not), plus its exit code."""
    env = {**blender_env(), BROKERED: '1', 'PYTHONPATH': ''}
    if os.environ.get('STUDIO_RENDER_DEVICE'):
        env['STUDIO_RENDER_DEVICE'] = os.environ['STUDIO_RENDER_DEVICE']
    try:
        done = subprocess.run(command(args), cwd=REPO, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        dispatch_waiting()   # a job the command left waiting still starts
        raise StudioError('TIMEOUT', f'studio {" ".join(args[:3])} ran over {timeout} s', retryable=True) from exc
    lines = [l for l in done.stdout.splitlines() if l.startswith('{')]
    try:
        result = json.loads(lines[-1]) if lines else None
    except ValueError:
        result = None
    if result is None:
        result = {'ok': False, 'error': {'code': 'COMMAND_FAILED', 'message': (done.stdout + done.stderr)[-2000:]}}
    dispatched = dispatch(result.get('artifacts') or []) + dispatch_waiting()
    return {**result, 'exit_code': done.returncode, **({'dispatched': dispatched} if dispatched else {})}


def dispatch_waiting():
    """Every job in the repository's projects still 'awaiting_dispatch' (a command that timed out, a lost result)."""
    return dispatch(sorted(Path(REPO).glob('projects/**/runs/*/jobs/*/job.json')))


JOB_PATH_KEYS = ('project_dir', 'output_dir', 'cancel_path', 'progress_path', 'scene_path', 'renderer_script')


def job_problems(path, job):
    """Why a job may not leave the sandbox: it must be a render job of the project it sits in, every path it names inside
    that project. A job.json is data the sandboxed side could write; the network worker reads and writes these paths,
    so a job naming ~/.ssh as its output would carry it off (code review, 2026-10-09)."""
    path = Path(path).resolve()   # <project>/runs/<run>/jobs/<job>/job.json
    if len(path.parents) < 5 or path.parents[1].name != 'jobs' or path.parents[3].name != 'runs':
        return ['not at <project>/runs/<run>/jobs/<job>/job.json']
    project = path.parents[4]
    if not (project / 'project.json').is_file() or not project.is_relative_to((Path(REPO) / 'projects').resolve()):
        return [f'{project} is not a project of this repository']
    problems = []
    if Path(job.get('project_dir', '')).resolve() != project:
        problems.append('project_dir is not the project the job sits in')
    for key in JOB_PATH_KEYS[1:]:
        value = job.get(key)
        if value and not Path(value).resolve().is_relative_to(project):
            problems.append(f'{key} {value} lies outside the project')
    if not job.get('worker_token') or job.get('executor') != 'runpod':
        problems.append('not a remote render job')
    return problems


def dispatch(artifacts):
    """Start, outside the sandbox, the network worker of every valid job the command left 'awaiting_dispatch'."""
    from .common import lock, read_json, write_json
    from .jobs import spawn_worker
    started, frozen = [], None
    for item in artifacts:
        path = Path(str(item))
        if path.name != 'job.json' or not path.is_file() or not path.resolve().is_relative_to(Path(REPO).resolve()):
            continue
        with lock(path.parent / '.lock'):   # one dispatcher per job, even with two brokers
            job = read_json(path)
            entry = '_remote_worker' if job.get('executor') == 'runpod' else '_worker'
            if job.get('status') != 'awaiting_dispatch' or entry not in NETWORK_ENTRIES:
                continue
            problems = job_problems(path, job)
            if not problems:   # the code that would run outside the sandbox is the code the user approved
                if frozen is None:
                    from .freeze import check
                    frozen = [f'frozen code changed since the user approved it ({g}: {", ".join(f[:4])})' for g, f in check()['changed'].items()]
                problems = frozen
            if problems:
                job.update({'status': 'failed', 'error': {'code': 'DISPATCH_REFUSED', 'message': '; '.join(problems)}})
                write_json(path, job)
                continue
            from .common import now
            job['status'] = 'queued'; job['updated_at'] = now(); write_json(path, job)
            spawn_worker(path, entry, job['worker_token'])
        started.append({'job': str(path), 'entry': entry})
    return started


def in_agent_sandbox():
    """True inside the codex sandbox, where Blender cannot start (and not inside the broker, which runs outside it)."""
    return bool(os.environ.get('CODEX_SANDBOX')) and not os.environ.get(BROKERED)
