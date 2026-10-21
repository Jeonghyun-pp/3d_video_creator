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
import time

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
# Also `gpu status` (2026-10-19): the rented-GPU servers live behind the network a brokered command never reaches, so an
# unattended run could not see whether a server was left billing. Answered here only: reads, spends nothing, records
# nothing, returns no key. Each command here has one exact shape (HERE); gpu sweep / enable stay refused or wrapped.
# The user: "원격 GPU 세 파일 동결 해제 승인".
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
    """The exact shape of a command the broker answers in its own process (HERE), as it will run - else None, and the
    command is refused or wrapped as any other."""
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args) or tuple(args[:2]) not in HERE:
        return None
    return HERE[tuple(args[:2])][0](args)


def _contrib_check_shape(args):
    """contrib check on a project of this repository and an entry id, nothing else."""
    if len(args) not in (6, 8):
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


def _check_here(plain):
    """contrib check, in this process (the sealed engine's broker): the CLI's JSON result and exit code."""
    from .contrib import check_entry
    given = dict(zip(plain[2::2], plain[3::2]))
    try:
        result = check_entry(given['--project'], given['--name'], given.get('--version'))
    except StudioError as exc:
        return {'ok': False, 'operation': 'contrib.check', 'status': 'failed', 'error': exc.as_dict(), 'exit_code': 1 if exc.retryable else 2}
    return {'operation': 'contrib.check', **result, 'status': 'complete' if result.get('ok') else 'failed',
            'exit_code': 0 if result.get('ok') else 2, 'checked_by': 'broker'}


def _gpu_status_here(plain):
    """gpu status, in this process: our servers as RunPod lists them (a GET), the month's spend from the ledger."""
    from .remote_gpu import status
    try:
        result = status()
    except StudioError as exc:
        return {'ok': False, 'operation': 'gpu.status', 'status': 'failed', 'error': exc.as_dict(), 'exit_code': 1 if exc.retryable else 2}
    if result['running'] is None:   # the broker could not reach RunPod either: not a way to point at again
        result['warnings'] = [w.split(';')[0] + '; from the broker too: check the network and the RunPod key' for w in result['warnings']]
    return {'ok': True, 'operation': 'gpu.status', **result, 'status': 'complete', 'exit_code': 0, 'checked_by': 'broker'}


# command -> (its one shape: the args as they run, or None; the handler in this process)
HERE = {('contrib', 'check'): (_contrib_check_shape, _check_here),
        ('gpu', 'status'): (lambda args: list(args) if args == ['gpu', 'status'] else None, _gpu_status_here)}
UNWRAPPED = tuple(HERE)


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
    plain = unwrapped(args)
    if plain is not None:   # answered here (HERE), never in a new interpreter
        return HERE[tuple(plain[:2])][1](plain)
    env = {**blender_env(), BROKERED: '1', 'PYTHONPATH': '', 'STUDIO_DATA_ROOT': str(REPO)}
    if os.environ.get('STUDIO_RENDER_DEVICE'):
        env['STUDIO_RENDER_DEVICE'] = os.environ['STUDIO_RENDER_DEVICE']
    # Jobs the command leaves waiting are started while it runs, not after: a command may wait for its own job
    # (reel clay --remote waits for its clips), and a job started only once the command returns is one that command
    # can never see finish (remote jobs audit 2026-10-21: every brokered remote clay failed after 10 minutes).
    # Output goes to files, not pipes, so a long command never blocks on a full pipe while we wait.
    dispatched = []
    with tempfile.TemporaryFile('w+') as out, tempfile.TemporaryFile('w+') as err:
        process = subprocess.Popen(command(args), cwd=REPO, env=env, stdout=out, stderr=err, text=True)
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if time.monotonic() > deadline:
                process.kill(); process.wait()
                dispatch_waiting()   # a job the command left waiting still starts
                raise StudioError('TIMEOUT', f'studio {" ".join(args[:3])} ran over {timeout} s', retryable=True)
            dispatched += dispatch_waiting()
            time.sleep(DISPATCH_EVERY_S)
        out.seek(0); err.seek(0)
        done = subprocess.CompletedProcess(process.args, process.returncode, out.read(), err.read())
    lines = [l for l in done.stdout.splitlines() if l.startswith('{')]
    try:
        result = json.loads(lines[-1]) if lines else None
    except ValueError:
        result = None
    if result is None:
        result = {'ok': False, 'error': {'code': 'COMMAND_FAILED', 'message': (done.stdout + done.stderr)[-2000:]}}
    dispatched += dispatch(result.get('artifacts') or []) + dispatch_waiting()
    return {**result, 'exit_code': done.returncode, **({'dispatched': dispatched} if dispatched else {})}


DISPATCH_EVERY_S = 2


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
    from .common import lock, read_json
    from .jobs import spawn_worker, update_job
    started, frozen = [], None
    for item in artifacts:
        path = Path(str(item))
        if path.name != 'job.json' or not path.is_file() or not path.resolve().is_relative_to(Path(REPO).resolve()):
            continue
        with lock(path.parent / '.lock', reentrant=True):   # one dispatcher per job, even with two brokers (update_job takes it too)
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
                update_job(path, {'status': 'failed', 'error': {'code': 'DISPATCH_REFUSED', 'message': '; '.join(problems)}})
                continue
            from .common import now
            update_job(path, {'status': 'queued', 'updated_at': now()})
            spawn_worker(path, entry, job['worker_token'])
        started.append({'job': str(path), 'entry': entry})
    return started


def in_agent_sandbox():
    """True inside the codex sandbox, where Blender cannot start (and not inside the broker, which runs outside it)."""
    return bool(os.environ.get('CODEX_SANDBOX')) and not os.environ.get(BROKERED)
