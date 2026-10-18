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
import re
from pathlib import Path
import subprocess
import sys
import tempfile

from .common import CODE, ID_PATTERN, REPO, StudioError, blender_env

BROKERED = 'STUDIO_BROKERED'
NO_BROKER = ('freeze', 'contrib')   # commands that judge or record the lines themselves are not run through the broker
# ... but one: `contrib check --project <a project here> --name <id>`. Its child runs the entry under an OS sandbox of its
# own, which cannot be made inside another (macOS: sandbox_apply refused in a sandboxed process), so from the agent's
# sandbox no entry could ever be checked nor a build use one (2026-10-18). The broker checks it in its own process - the
# sealed engine (studio/seal.py: the user's approved copy, run by its launcher; never code the agent can edit) - whose
# child makes its own sandbox (no network, no writes, no new processes) around the entry. It records only the checker's
# verdict (studio/contrib_probe.py), no approval and no user's words: promote, deprecate and freeze stay refused.
# The user: "contrib 검사 broker로 돌리게 동결 해제해 -> 이거 승인할게".
UNWRAPPED = (('contrib', 'check'),)
# Worker entries that need the network (a rented GPU server's API, ssh, rsync). A brokered command never reaches the
# network itself: it leaves such a job 'awaiting_dispatch' and the broker - outside the sandbox - starts only these
# entries (default deny; anything else stays in the sandbox).
NETWORK_ENTRIES = {'_remote_worker': 'remote_render',      # entry -> the studio module it runs (freeze.network_worker hashes it)
                   '_reference_worker': 'reference_fetch'}   # licensed reference photos from reference_fetch.SOURCES only


def worker_entry(job):
    """The studio entry that runs a job: the local frozen render worker, the remote render worker, or the reference
    photo fetcher. Decided here, in frozen code: which entries may leave the sandbox is NETWORK_ENTRIES' call."""
    if job.get('operation') == 'reference_fetch':
        return '_reference_worker'
    return '_remote_worker' if job.get('executor') == 'runpod' else '_worker'


def profile():
    """The macOS sandbox profile a brokered command runs under."""
    roots = {str(Path(REPO).resolve()), str(Path(tempfile.gettempdir()).resolve()), '/private/var/folders', '/private/tmp'}
    # not the studio venv: a brokered command runs -B (studio/_launch.py), writing no bytecode, and a writable venv let a
    # command plant a .pth that every later interpreter outside the sandbox ran (review 2026-10-18)
    writable = ' '.join(f'(subpath "{r}")' for r in sorted(roots))
    return ('(version 1)(allow default)(deny network*)(allow network* (remote unix-socket))(allow network* (local unix-socket))'
            f'(deny file-write*)(allow file-write* {writable} (literal "/dev/null") (regex #"^/dev/tty") (regex #"^/dev/dtracehelper"))')


def unwrapped(args):
    """The one shape of a command run outside the broker's sandbox (UNWRAPPED): contrib check on a project of this
    repository and an entry id, nothing else - else None, and the command is refused or wrapped as any other."""
    if not isinstance(args, list) or tuple(args[:2]) not in UNWRAPPED or len(args) not in (6, 8):
        return None
    given = dict(zip(args[2::2], args[3::2]))
    if set(given) - {'--version'} != {'--project', '--name'} or not re.fullmatch(ID_PATTERN, given['--name']) \
            or ('--version' in given and not re.fullmatch(r'v\d{3}', given['--version'])):
        return None
    project = Path(given['--project'])
    project = (project if project.is_absolute() else Path(REPO) / project).resolve()
    if not project.is_relative_to((Path(REPO) / 'projects').resolve()) or not (project / 'project.json').is_file():
        return None
    return ['contrib', 'check', '--project', str(project), '--name', given['--name'],
            *(['--version', given['--version']] if '--version' in given else [])]


def _check_here(args):
    """contrib check, in this process (the sealed engine's broker): the CLI's JSON result and exit code."""
    from .contrib import check_entry
    plain = unwrapped(args)
    given = dict(zip(plain[2::2], plain[3::2]))
    try:
        result = check_entry(given['--project'], given['--name'], given.get('--version'))
    except StudioError as exc:
        return {'ok': False, 'operation': 'contrib.check', 'status': 'failed', 'error': exc.as_dict(), 'exit_code': 1 if exc.retryable else 2}
    return {'operation': 'contrib.check', **result, 'status': 'complete' if result.get('ok') else 'failed',
            'exit_code': 0 if result.get('ok') else 2, 'checked_by': 'broker'}


def command(args):
    if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
        raise StudioError('INPUT_INVALID', 'studio_run takes args: a list of strings, e.g. ["shot", "build", "--project", "...", "--shot", "s01"]')
    if args[0].startswith(('-', '_')) or args[0] in NO_BROKER:   # internal entries (_worker, _remote_worker) only start from a job
        raise StudioError('INPUT_INVALID', f'studio_run does not run {args[0]!r}' + (' (run it in your own shell: it needs no Blender)' if args[0] in NO_BROKER else '')
                          + (' - of contrib it runs only: contrib check --project <a project> --name <entry>' if args[0] == 'contrib' else ''))
    from .seal import launch_argv
    base = launch_argv('studio', *args, engine=CODE)   # the engine the broker itself runs (the sealed one, from Astra's launcher)
    if sys.platform == 'darwin':
        return ['sandbox-exec', '-p', profile(), *base]
    return base


def run(args, timeout=3600):
    """The command's JSON result (ok or not), plus its exit code."""
    if unwrapped(args) is not None:   # the one contrib command: checked here, never in a new interpreter
        return _check_here(args)
    env = {**blender_env(), BROKERED: '1', 'PYTHONPATH': '', 'STUDIO_DATA_ROOT': str(REPO)}
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
    """Why a job may not leave the sandbox: it must sit in a project of this repository, every path it names inside that
    project, and be a kind of job a network entry runs. A job.json is data the sandboxed side could write; the network
    worker reads and writes these paths, so a job naming ~/.ssh as its output would carry it off (code review, 2026-10-09)."""
    path = Path(path).resolve()   # <project>/runs/<run>/jobs/<job>/job.json
    if len(path.parents) < 5 or path.parents[1].name != 'jobs' or path.parents[3].name != 'runs':
        return ['not at <project>/runs/<run>/jobs/<job>/job.json']
    project = path.parents[4]
    if not (project / 'project.json').is_file() or not project.is_relative_to((Path(REPO) / 'projects').resolve()):
        return [f'{project} is not a project of this repository']
    problems = []
    if job.get('job_id') != path.parent.name or not re.fullmatch(ID_PATTERN, path.parent.name):   # workers name files from it
        problems.append(f"job_id {job.get('job_id')!r} is not the id of the folder the job sits in")
    if Path(job.get('project_dir', '')).resolve() != project:
        problems.append('project_dir is not the project the job sits in')
    for key in JOB_PATH_KEYS[1:]:
        value = job.get(key)
        if value and not Path(value).resolve().is_relative_to(project):
            problems.append(f'{key} {value} lies outside the project')
    if not job.get('worker_token'):
        problems.append('no worker token')
    if job.get('executor') == 'runpod':   # what the remote worker writes to and ships, as submit made it (jobs.py)
        from .jobs import render_job_problems
        problems += render_job_problems(project, job, path)
    if job.get('operation') == 'reference_fetch':
        from .reference_fetch import MAX_FILES, PHOTOS_DIR, SOURCES
        if Path(job.get('output_dir', '')).resolve() != (project / PHOTOS_DIR).resolve():
            problems.append(f'a reference fetch writes only to {PHOTOS_DIR}')
        if job.get('source') not in SOURCES:
            problems.append(f"source {job.get('source')!r} is not an allowed source")
        if len(job.get('titles') or []) > MAX_FILES or not (job.get('titles') or job.get('query')):
            problems.append(f'a reference fetch names 1-{MAX_FILES} titles or one query')
    elif job.get('executor') != 'runpod':
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
            try:
                job = read_json(path)
            except StudioError as error:   # one half-written job never stops the others
                started.append({'job': str(path), 'skipped': f'unreadable job.json: {error.message}'})
                continue
            entry = worker_entry(job)
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
