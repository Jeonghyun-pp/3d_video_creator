"""The remote render worker: runs a queued render job on a rented GPU server (studio/remote_gpu.py) and leaves the
same files a local render leaves - frames, render.json, clip - under the job's output folder.

The frozen worker (render_worker.py, part of every render fingerprint) is not changed and not reimplemented: it runs on
the server, unmodified, on a copy of the project laid out the same way. This module only moves files and relays state:
  upload   the engine code (studio/, schemas/ - the same commit, so the fingerprinted code hashes are the same), the
           project files the worker reads (project.json, style.json, every shot.json, the run's run.json, the version's
           scene.blend / shot.snapshot.json / dependencies.json, the job folder with its frozen render code, and frames
           an earlier attempt left), with the job's absolute paths rewritten to the server's;
  run      `python -m studio _worker` there, detached; every POLL_S relay the server job's status, heartbeat and
           progress to the local job (so job status, cancel and resume work as for a local job) and touch the server's
           busy file (its watchdog deletes an idle server);
  download the output folder, paths in render.json rewritten back, then verify_render (frame sha256) locally;
  finally  delete the server when no other remote job is queued or running.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .common import CODE, REPO, StudioError, lock, now, read_json, write_json

POLL_S = 5
BEAT_S = 10   # the heartbeat through a step that polls nothing (well inside jobs.HEARTBEAT_STALE_S)


BUSY_EVERY_S = 30   # the server's busy file, touched by its owners only (its idle watchdog: remote_gpu.watchdog_command)


@contextmanager
def _beating(job_path, every=BEAT_S, server=None):
    """The job's heartbeat, kept fresh for the remote worker's whole life by a thread of its own: the heartbeat says the
    job's owner - this process - is alive and holds the job, whatever step it is in (a boot, an upload, a slow ssh read,
    a download). Whether the server's worker is alive is judged on the server (_remote_state, server_verdict), never
    from this heartbeat. Why: 2026-10-19 a shot rendered in full was marked interrupted while its 1.3 GB of frames came
    home and the server was deleted under it; 2026-10-21 one 58 s status read made a live job read as interrupted."""
    from .jobs import update_job
    stop = threading.Event()

    def beat():
        touched = 0.0
        while not stop.wait(every):
            update_job(job_path, {'heartbeat_unix': time.time()})
            # while this owner lives, its server is busy: one keep-alive, the owner's, for every step (boot, install,
            # upload, render, download) - a loop on the server outlived its owner (2026-10-21: a failed upload left
            # one touching the busy file forever, so the idle watchdog could never fire)
            if server and server.get('pod') and time.monotonic() - touched >= BUSY_EVERY_S:
                from .remote_gpu import ssh
                ssh(server['pod'], 'touch /workspace/.busy', check=False, timeout=20)
                touched = time.monotonic()
    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
TERMINAL = ('complete', 'failed', 'cancelled', 'interrupted')
REGISTRY = Path(os.environ.get('STUDIO_REMOTE_REGISTRY', REPO / '.studio' / 'remote_jobs.json'))
REMOTE_ROOT = '/workspace/studio'
REMOTE_REPO = f'{REMOTE_ROOT}/repo'
SECONDS_PER_FRAME = {'layout': 0.05, 'look': 0.4, 'review': 0.4, 'final': 5.0}   # final measured 10-19 (1080x1920, 128 samples: 4.8 s/frame); the rest still guesses; boot ~5 min
BOOT_HOURS = 5 / 60


def remote_project(project_id):
    return f'{REMOTE_ROOT}/projects/{project_id}'


def rewrite(value, mapping):
    """Every string in a JSON value with path prefixes replaced (longest first), so a job reads the same anywhere."""
    if isinstance(value, dict):
        return {k: rewrite(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [rewrite(v, mapping) for v in value]
    if isinstance(value, str):
        for old, new in sorted(mapping.items(), key=lambda kv: -len(kv[0])):
            if value == old or value.startswith(old.rstrip('/') + '/'):
                return new + value[len(old):]
    return value


def estimate_hours(job, boot=True):
    """Server hours a job needs: its frames at the profile's speed, plus a boot when it starts a server of its own."""
    frames = len(job.get('missing_frames') or job['frames'])
    return frames * SECONDS_PER_FRAME.get(job['profile'], 1.0) / 3600 + (BOOT_HOURS if boot else 0.0)


def estimate_usd(job, cost_per_hr=1.0):
    return round(estimate_hours(job) * cost_per_hr, 4)


def queued_remote_jobs(root=None):
    """Remote render jobs of this checkout's projects the queue's server will still run: waiting to start, or with a
    worker that has not died (a job whose worker is gone runs again only when resumed)."""
    from .jobs import ACTIVE_STATES, effective_status
    out = []
    for path in sorted(Path(root or REPO).glob('projects/**/runs/*/jobs/*/job.json')):
        job = read_json(path)
        if job.get('executor') == 'runpod' and effective_status(job, path)[0] in ACTIVE_STATES:
            out.append(job)
    return out


def queue_estimate(adding=(), root=None):
    """The remote queue's cost - the jobs waiting plus `adding` - on the one server the queue shares (ensure_pod reuses
    the running one): every job's frames, one boot, at the highest price billed so far, against the month's cap. Why
    (2026-10-19): the sum of per-job estimates counted a boot per job, $2.48 for a render billed $1.73, and each job was
    checked alone, so a queue past the cap was submitted and stopped half rendered. Local ledger only: no network."""
    from . import remote_gpu
    queued = queued_remote_jobs(root)
    jobs = [*queued, *adding]
    hours = sum(estimate_hours(job, boot=False) for job in jobs) + (BOOT_HOURS if jobs else 0.0)
    price = remote_gpu.known_price()
    spent, cap = remote_gpu.month_spend(), remote_gpu.settings()['monthly_cap_usd']
    usd = round(hours * price, 4)
    # the server's own watchdog deletes it after max_pod_hours whoever still holds it - the one delete that asks no
    # holder: a queue is accepted only inside that life, so the guard never cuts a render the queue was promised
    life = float(remote_gpu.settings()['max_pod_hours'])
    return {'queued_jobs': [job['job_id'] for job in queued], 'adding': [job.get('job_id') for job in adding],
            'frames': sum(len(job.get('missing_frames') or job['frames']) for job in jobs), 'hours': round(hours, 4),
            'price_per_hr': price, 'usd': usd, 'spent_usd': spent, 'cap_usd': cap, 'remaining_usd': round(cap - spent, 4),
            'max_pod_hours': life, 'fits_pod_life': hours <= life, 'fits': spent + usd <= cap}


# ---- which servers are in use --------------------------------------------------------------------------------------

def _registry():
    return read_json(REGISTRY) if REGISTRY.is_file() else {}


def _register(job_path, pod_id):
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    with lock(REGISTRY.parent / '.remote_jobs.lock'):
        data = _registry()
        if pod_id is None:
            data.pop(str(job_path), None)
        else:
            data[str(job_path)] = pod_id
        write_json(REGISTRY, data)


def live_pod_ids(exclude=None):
    """Servers held by remote jobs whose worker is still alive (stale entries are dropped)."""
    from .jobs import effective_status
    live, data = set(), _registry()
    for job_path, pod_id in list(data.items()):
        path = Path(job_path)
        if str(path) == str(exclude) or not path.is_file():
            continue
        if effective_status(read_json(path), path)[0] in ('queued', 'running'):
            live.add(pod_id)
    return live


# ---- the worker ------------------------------------------------------------------------------------------------------

def _project_files(project, job):
    """Local files the frozen worker reads on the server (status_project at its end reads every shot.json)."""
    shot_dir = project / 'shots' / job['shot_id']
    version = shot_dir / 'versions' / job['scene_version']
    # not the job file: the server gets the handed-over copy (handover), never this machine's own
    files = [project / 'project.json', *sorted(project.glob('shots/*/shot.json')), version / 'scene.blend',
             version / 'shot.snapshot.json', version / 'dependencies.json']
    run = Path(job['cancel_path']).parents[2] / 'run.json'
    files += [run] + ([project / 'style.json'] if (project / 'style.json').is_file() else [])
    files += sorted((Path(job['cancel_path']).parent / 'code').glob('*'))
    output = Path(job['output_dir'])
    if output.is_dir():   # frames an earlier attempt left: the worker reuses the ones whose hashes match
        files += sorted(output.rglob('*'))
    return [f for f in files if f.is_file()]


FETCH_STALL_S = int(os.environ.get('STUDIO_REMOTE_FETCH_STALL', 300))   # a download gives up after this long with no new file home
FETCH_LIMIT_S = 3 * 3600


def _incoming(job_path, local_dir):
    """Where the server's frames land before they are checked: the job's own folder (it outlives this process, so a
    download that stopped half way is kept for the next try and for a resume - 2026-10-21: a dropped download deleted
    the server with every rendered frame, and the temp folder threw away the ones already home)."""
    folder = Path(job_path).parent / '.incoming' / Path(local_dir).name
    if any(p.is_symlink() for p in [folder, *folder.parents][:3]):
        raise StudioError('REMOTE_RENDER_UNVERIFIED', f'{folder} runs through a link')
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _download(pod, remote_dir, local_dir, project, shot_id, job_path, record=None):
    """The server's render folder into the job's: fetched into the job's incoming folder first with no links, devices or
    permissions (rsync -rt; a retry fetches only what is missing), then only regular files are copied in, each to a
    place checked to run through no link - the server is not trusted to name what lands on this machine, nor the
    checkout to stay as it was checked (2026-10-18 review: a render folder swapped for a link mid-render, or links in the
    download). Retried while files keep coming home (studio/waiting.py); the server is held all that time."""
    from . import remote_gpu
    from .jobs import render_output_ok
    from .waiting import wait_until
    incoming = _incoming(job_path, local_dir)

    def fetch():
        try:
            remote_gpu.rsync(pod, [f'{remote_dir}/'], str(incoming), upload=False)
            return True, {'home': sum(1 for f in incoming.rglob('*') if f.is_file()), 'done': True}
        except StudioError as error:
            if not error.retryable:
                raise
            return None, {'home': sum(1 for f in incoming.rglob('*') if f.is_file())}
    wait_until(fetch, limit_s=FETCH_LIMIT_S, stall_s=FETCH_STALL_S, record=record, what=f'download of {remote_dir}', code='REMOTE_GPU_SYNC',
               every_s=10)
    if not render_output_ok(project, shot_id, local_dir):
        raise StudioError('REMOTE_RENDER_UNVERIFIED', f'{local_dir} is no render folder of the job (or runs through a link)')
    local = Path(local_dir)
    local.mkdir(parents=True, exist_ok=True)
    for source in sorted(incoming.rglob('*')):
        if source.is_symlink() or not source.is_file():
            continue
        target = local / source.relative_to(incoming)
        target.parent.mkdir(parents=True, exist_ok=True)
        if any(p.is_symlink() for p in [target, *target.parents] if p.is_relative_to(Path(project).resolve())):
            raise StudioError('REMOTE_RENDER_UNVERIFIED', f'{target} runs through a link')
        shutil.copyfile(source, target)
    shutil.rmtree(incoming)


def _reuse_incoming(job_path, job):
    """Frames an earlier attempt brought half way home (its incoming folder) join the output folder before the upload,
    when they are whole PNGs of the job's size: the server's worker then renders only the rest (it reuses good frames)."""
    from .jobs import good_frame, render_output_ok
    folder = Path(job_path).parent / '.incoming' / Path(job['output_dir']).name / 'frames'
    if not folder.is_dir() or not render_output_ok(job['project_dir'], job['shot_id'], job['output_dir']):
        return 0
    size = (job['render_settings']['width'], job['render_settings']['height'])
    frames = Path(job['output_dir']) / 'frames'
    kept = 0
    for frame in job['frames']:
        name = f'frame_{frame:06d}.png'
        if (folder / name).is_file() and not (folder / name).is_symlink() and not (frames / name).exists() and good_frame(folder / name, size):
            frames.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(folder / name, frames / name)
            kept += 1
    return kept


def handover(job, mapping):
    """The job as the server holds it: its paths the server's, and no owner - this machine's pid and id mean nothing
    there; the server's worker claims it (its own pid) first thing. Every copy of a job sent to another machine is made
    here, so a pid in a job file was always written on the machine that holds it (jobs.owned_here)."""
    from .jobs import ACTIVE_STATES
    out = rewrite(job, mapping)
    for key in ('pid', 'owner_machine', 'heartbeat_unix', 'evidence'):
        out.pop(key, None)
    out.update(status='handed_over', updated_at=now())
    assert out['status'] in ACTIVE_STATES
    return out


def _upload(pod, job, mapping):
    from .remote_gpu import rsync, ssh
    project = Path(job['project_dir'])
    remote = mapping[str(project)]
    ssh(pod, f'mkdir -p {REMOTE_REPO}/.studio {shlex.quote(remote)}; touch /workspace/.busy')
    rsync(pod, ['studio', 'schemas'], f'{REMOTE_REPO}/', cwd=CODE)   # the engine that runs here (sealed or not)
    rsync(pod, [str(f.relative_to(project)) for f in _project_files(project, job)], f'{remote}/', cwd=project)
    with tempfile.TemporaryDirectory() as tmp:   # the job as the server sees it
        job_file = Path(tmp) / str(Path(job['cancel_path']).parent.relative_to(project)) / 'job.json'
        job_file.parent.mkdir(parents=True)
        from .jobs import create_job
        create_job(job_file, handover(job, mapping))
        rsync(pod, [str(job_file.relative_to(tmp))], f'{remote}/', cwd=tmp)
    ssh(pod, 'touch /workspace/.busy')   # kept fresh by this job's owner from here as before (_beating)


def _remote_state(pod, remote_job, remote_progress):
    """The server's job, its progress, and the server's own judgement of its worker, in one ssh: whether the worker's
    process runs there (the pid it wrote before it started) and the server's clock to age its heartbeat by. The process
    is asked first, so a worker that ends between the two reads has already written its final state."""
    from .remote_gpu import ssh
    pidfile = shlex.quote(f'{remote_job}.worker.pid')
    out = ssh(pod, f'touch /workspace/.busy; p=$(cat {pidfile} 2>/dev/null); '
                   f'if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then w="alive $p"; else w="gone $p"; fi; '
                   f'cat {shlex.quote(remote_job)}; echo; echo ---; cat {shlex.quote(remote_progress)} 2>/dev/null || true; '
                   f'echo; echo ---; echo "$w"; date +%s', timeout=60).stdout
    job_text, _, rest = out.partition('\n---\n')
    progress_text, _, server_text = rest.partition('\n---\n')
    words = server_text.split()
    server = {'worker': words[0] if words else None, 'pid': words[1] if len(words) > 2 else None,
              'now': float(words[-1]) if words and words[-1].isdigit() else None}
    try:
        state = json.loads(job_text)
    except ValueError:   # the server's job file is missing or half written: say what the server's worker said
        log = ssh(pod, f'tail -c 1500 {shlex.quote(remote_job)}.worker.log 2>/dev/null', check=False, timeout=60).stdout
        raise StudioError('REMOTE_RENDER_FAILED', f'server job file unreadable ({remote_job}); worker log: {log.strip()[-1200:]}') from None
    try:
        progress = json.loads(progress_text) if progress_text.strip() else None
    except ValueError:   # written between our reads: the next poll has it
        progress = None
    return state, progress, server


def server_verdict(state, server):
    """Why the server's worker is dead, on the server's own evidence - or None while it lives or has finished. Its
    process is gone without a final state, or it runs with a heartbeat (written by the server's clock) gone stale."""
    from .jobs import HEARTBEAT_STALE_S
    if state.get('status') in TERMINAL:
        return None
    if server.get('worker') == 'gone':
        return f"the server's worker (pid {server.get('pid') or 'never written'}) is not running on the server"
    beat = state.get('heartbeat_unix')
    if state.get('status') == 'running' and beat and server.get('now') and server['now'] - float(beat) > HEARTBEAT_STALE_S:
        return f"the server's worker has written no heartbeat for {server['now'] - float(beat):.0f} s (server clock)"
    return None


def run_remote_worker(job_path, token):
    from . import remote_gpu
    from .jobs import machine_id, record_render_time, update_job, verify_render
    from .waiting import wait_until
    job_path = Path(job_path).resolve()
    job = read_json(job_path)
    if job.get('worker_token') != token:
        raise StudioError('REVISION_CONFLICT', 'Worker token does not match job')
    # the claim: this process, of this machine, owns the job (jobs.owned_here); its heartbeat runs from here to the end
    job = update_job(job_path, {'pid': os.getpid(), 'owner_machine': machine_id(), 'updated_at': now(), 'heartbeat_unix': time.time()})

    def stop(signum, frame):   # job cancel / kill: still reach `finally` and delete the server
        raise StudioError('CANCELLED', f'remote worker stopped by signal {signum}')
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    pod, final, held = None, {}, {}
    beating = _beating(job_path, server=held)
    beating.__enter__()
    try:
        remote_gpu.locked_sweep(exclude=job_path)   # a server nobody holds is deleted before we pay for another
        def chosen_one(chosen):
            nonlocal pod
            pod = held['pod'] = chosen
            _register(job_path, chosen['id'])
        def waited(row):   # what each wait of this job observed, on the job (studio/waiting.py)
            update_job(job_path, lambda j: {**j, 'waits': [*(j.get('waits') or []), row][-50:]})
        pod = held['pod'] = remote_gpu.ensure_pod(estimate_hours(job, boot=False), on_chosen=chosen_one, boot_hours=BOOT_HOURS, record=waited)
        job = update_job(job_path, {'remote': {'pod_id': pod['id'], 'gpu': (pod.get('gpu') or {}).get('displayName'), 'cost_per_hr': pod.get('costPerHr')}})
        project = Path(job['project_dir'])
        from .jobs import RENDER_DEVICES, render_job_problems, render_output_ok
        refused = render_job_problems(project, job, job_path)   # checked again by the worker itself, not only by the dispatcher
        if refused:
            raise StudioError('DISPATCH_REFUSED', '; '.join(refused))
        # both spellings of a path map (macOS: /var/... is /private/var/...), back to the one the job was written with
        mapping = {str(project): remote_project(job['project_id']), str(project.resolve()): remote_project(job['project_id']),
                   str(REPO): REMOTE_REPO, str(REPO.resolve()): REMOTE_REPO}
        back = {remote_project(job['project_id']): str(project), REMOTE_REPO: str(REPO)}
        _reuse_incoming(job_path, job)
        _upload(pod, job, mapping)
        remote_job = rewrite(str(job_path), mapping)
        remote_progress = rewrite(job['progress_path'], mapping)
        remote_cancel = rewrite(job['cancel_path'], mapping)
        env = f'STUDIO_BLENDER=/opt/blender/blender STUDIO_RENDER_DEVICE={shlex.quote(job["render_settings"]["device"])}'
        # the worker's pid is kept on the server (ssh_detached pidfile), so the server can say whether it still runs
        remote_gpu.ssh_detached(pod, f'cd {REMOTE_REPO} && {env} exec python3 -m studio _worker {shlex.quote(remote_job)} {shlex.quote(token)}',
                                log=f'{remote_job}.worker.log', pidfile=f'{remote_job}.worker.pid', once=f'{remote_job}.started.{token}')
        seen_last = {}

        def relay():
            """One look at the server: its job relayed here, its worker judged there; done once its state is final."""
            if Path(job['cancel_path']).exists():   # the frozen worker reads its own cancel file: create it there
                remote_gpu.ssh(pod, f'touch {shlex.quote(remote_cancel)}', check=False)
            reached = remote_gpu.over_cap()   # the estimate is a guess: the cap holds while the render runs, not only before
            if reached:
                remote_gpu.ssh(pod, f'touch {shlex.quote(remote_cancel)}', check=False)
                raise StudioError('GPU_BUDGET_REACHED', f'{reached}: the render stopped and its server is deleted',
                                  recovery='ask the user to raise the cap (studio gpu enable --monthly-cap N with their words), or render locally')
            state, progress, server = _remote_state(pod, remote_job, remote_progress)
            verdict = server_verdict(state, server)
            if verdict:   # judged on the server: the job ends interrupted, with the server's evidence; what the worker
                # started (Blender) is stopped there with it - its session is its pid's (ssh_detached: setsid)
                if server.get('pid'):
                    remote_gpu.ssh(pod, f"kill -TERM -- -{int(server['pid'])} 2>/dev/null; true", check=False, timeout=60)
                state = {**state, 'status': 'interrupted', 'error': {'code': 'WORKER_INTERRUPTED', 'message': f'{verdict}; resume preserves valid frames'}}
            fields = {k: state[k] for k in ('error', 'render_started_at', 'missing_frames', 'warnings') if k in state}
            # the local job stays running until the frames are home and verified: another job finishing meanwhile must
            # see this one still holding the server (rehearsal 2026-10-08: the server was deleted mid-download)
            current = update_job(job_path, lambda j: {**j, **fields, **({'evidence': verdict} if verdict else {}), 'updated_at': now(),
                                                     'status': 'running' if state.get('status') in (*TERMINAL, 'running') else j['status']})
            if progress:
                write_json(Path(current['progress_path']), progress)
            seen_last.update(state=state, verdict=verdict, job=current, at=time.time())
            return state.get('status') in TERMINAL, {'status': state.get('status'), 'worker': server.get('worker')}
        # bounded like every wait (studio/waiting.py): the whole queue's estimate three times over plus a boot - the
        # server's worker is judged on every look, so this only ends a render that runs and runs
        limit = max(1800.0, (queue_estimate()['hours'] * 3 + BOOT_HOURS) * 3600)
        wait_until(relay, limit_s=limit, record=waited, what=f"render of {job['job_id']} on server {pod['id']}", code='REMOTE_RENDER_TIMEOUT',
                   on_give_up=lambda reason: remote_gpu.ssh(pod, f'touch {shlex.quote(remote_cancel)}', check=False), every_s=POLL_S)
        state, verdict, job, polled_at = seen_last['state'], seen_last['verdict'], seen_last['job'], seen_last['at']
        remote_output = rewrite(job['output_dir'], mapping)
        there = not verdict or remote_gpu.ssh(pod, f'test -d {shlex.quote(remote_output)}', check=False).returncode == 0
        try:   # a dead worker may have left no folder: then there is nothing to fetch
            if there:
                _download(pod, remote_output, job['output_dir'], project, job['shot_id'], job_path, record=waited)
        except StudioError:
            if not verdict:   # a dead worker's interruption is the job's story, not its download
                raise
        if state.get('fingerprint') and state['fingerprint'] != job['fingerprint']:   # a CPU fallback renders under its own fingerprint and folder
            job['fallback_from'], job['fingerprint'] = state.get('fallback_from'), state['fingerprint']
            local = rewrite(state['output_dir'], back)
            if not render_output_ok(project, job['shot_id'], local) or (state.get('render_settings') or {}).get('device') not in RENDER_DEVICES:
                raise StudioError('REMOTE_RENDER_UNVERIFIED', f"the server reported a fallback folder or device the job may not use: {local}")
            job['output_dir'], job['render_settings'] = local, state['render_settings']
            _download(pod, state['output_dir'], job['output_dir'], project, job['shot_id'], job_path, record=waited)
        job['status'] = state['status']   # now final: the server's verdict, checked below against the frames we hold
        manifest_path = Path(job['output_dir']) / 'render.json'
        if manifest_path.is_file():
            write_json(manifest_path, rewrite(read_json(manifest_path), back))
        if job['status'] == 'complete' and not verify_render(job):
            job['status'], job['error'] = 'failed', {'code': 'REMOTE_RENDER_UNVERIFIED', 'message': 'downloaded frames do not match render.json'}
        if job.get('render_started_at'):
            record_render_time(job_path, job, max(0.0, polled_at - job['render_started_at']))
        final = {k: job.get(k) for k in ('status', 'error', 'fallback_from', 'fingerprint', 'output_dir', 'render_settings') if k in job}
    except Exception as exc:
        error = exc if isinstance(exc, StudioError) else StudioError('REMOTE_RENDER_FAILED', str(exc), retryable=True)
        final = {'status': 'cancelled' if Path(job['cancel_path']).exists() else 'failed', 'error': error.as_dict()}
    finally:
        job = update_job(job_path, {**final, 'updated_at': now()})   # a job cancelled meanwhile stays cancelled
        beating.__exit__(None, None, None)
        with lock(remote_gpu.POD_LOCK):
            _register(job_path, None)
            if pod is not None and pod['id'] not in live_pod_ids(exclude=job_path):   # nobody else needs it: delete it now
                remote_gpu.terminate(pod['id'], f"done {job['job_id']} {job['status']}")
    _close_run(job_path, job)
    from .jobs import job_status
    return job_status(job['project_dir'], job['job_id'])


def _close_run(job_path, job):
    """The run bookkeeping the frozen worker does at its end, on the local run."""
    run_path = job_path.parents[2] / 'run.json'
    with lock(run_path.parent / '.run.lock'):
        run = read_json(run_path)
        if job['status'] == 'complete':
            from .common import file_hash
            run['pending_jobs'] = [j for j in run['pending_jobs'] if j != job['job_id']]
            run['completed_operations'].append({'operation': 'render', 'job_id': job['job_id'], 'input_hash': job['scene_sha256'],
                                                'output_hash': file_hash(Path(job['output_dir']) / 'render.json'), 'remote': job.get('remote')})
        run['updated_at'] = now(); run['last_error'] = job.get('error'); write_json(run_path, run)
