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


@contextmanager
def _beating(job_path, every=BEAT_S):
    """The job's heartbeat kept fresh while the worker waits on a step that reports nothing - the server's boot, the
    upload, the download - so the job is not judged dead (jobs.worker_alive) and its server not deleted under it by
    another job ending (live_pod_ids). Why (2026-10-19): a shot rendered in full was marked interrupted while its
    1.3 GB of frames came home, the last other job's end deleted the server, and the download failed."""
    stop = threading.Event()

    def beat():
        while not stop.wait(every):
            job = read_json(job_path)
            job['heartbeat_unix'] = time.time()
            write_json(job_path, job)
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


def estimate_hours(job):
    """Server hours a job needs: its frames at the profile's guessed speed, plus a boot."""
    frames = len(job.get('missing_frames') or job['frames'])
    return frames * SECONDS_PER_FRAME.get(job['profile'], 1.0) / 3600 + BOOT_HOURS


def estimate_usd(job, cost_per_hr=1.0):
    return round(estimate_hours(job) * cost_per_hr, 4)


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
    from .jobs import worker_alive
    live, data = set(), _registry()
    for job_path, pod_id in list(data.items()):
        path = Path(job_path)
        if str(path) == str(exclude) or not path.is_file():
            continue
        job = read_json(path)
        if job.get('status') in ('queued', 'running') and worker_alive(job, path):
            live.add(pod_id)
    return live


# ---- the worker ------------------------------------------------------------------------------------------------------

def _project_files(project, job):
    """Local files the frozen worker reads on the server (status_project at its end reads every shot.json)."""
    shot_dir = project / 'shots' / job['shot_id']
    version = shot_dir / 'versions' / job['scene_version']
    files = [project / 'project.json', *sorted(project.glob('shots/*/shot.json')), version / 'scene.blend',
             version / 'shot.snapshot.json', version / 'dependencies.json', Path(job['cancel_path']).parent / 'job.json']
    run = Path(job['cancel_path']).parents[2] / 'run.json'
    files += [run] + ([project / 'style.json'] if (project / 'style.json').is_file() else [])
    files += sorted((Path(job['cancel_path']).parent / 'code').glob('*'))
    output = Path(job['output_dir'])
    if output.is_dir():   # frames an earlier attempt left: the worker reuses the ones whose hashes match
        files += sorted(output.rglob('*'))
    return [f for f in files if f.is_file()]


def _download(pod, remote_dir, local_dir, project, shot_id):
    """The server's render folder into the job's: fetched into a private temp folder first with no links, devices or
    permissions (rsync -rt), then only regular files are copied in, each to a place checked to run through no link -
    the server is not trusted to name what lands on this machine, nor the checkout to stay as it was checked
    (2026-10-18 review: a render folder swapped for a link mid-render, or links in the download)."""
    from . import remote_gpu
    from .jobs import render_output_ok
    with tempfile.TemporaryDirectory(prefix='remote-render-') as tmp:
        remote_gpu.rsync(pod, [f'{remote_dir}/'], tmp, upload=False)
        if not render_output_ok(project, shot_id, local_dir):
            raise StudioError('REMOTE_RENDER_UNVERIFIED', f'{local_dir} is no render folder of the job (or runs through a link)')
        local = Path(local_dir)
        local.mkdir(parents=True, exist_ok=True)
        for source in sorted(Path(tmp).rglob('*')):
            if source.is_symlink() or not source.is_file():
                continue
            target = local / source.relative_to(tmp)
            target.parent.mkdir(parents=True, exist_ok=True)
            if any(p.is_symlink() for p in [target, *target.parents] if p.is_relative_to(Path(project).resolve())):
                raise StudioError('REMOTE_RENDER_UNVERIFIED', f'{target} runs through a link')
            shutil.copyfile(source, target)


def _upload(pod, job, mapping):
    from .remote_gpu import rsync, ssh, ssh_detached
    project = Path(job['project_dir'])
    remote = mapping[str(project)]
    ssh(pod, f'mkdir -p {REMOTE_REPO}/.studio {shlex.quote(remote)}; touch /workspace/.busy')
    ssh(pod, 'rm -f /workspace/.uploaded')
    ssh_detached(pod, 'while [ ! -f /workspace/.uploaded ]; do touch /workspace/.busy; sleep 10; done')
    rsync(pod, ['studio', 'schemas'], f'{REMOTE_REPO}/', cwd=CODE)   # the engine that runs here (sealed or not)
    rsync(pod, [str(f.relative_to(project)) for f in _project_files(project, job)], f'{remote}/', cwd=project)
    with tempfile.TemporaryDirectory() as tmp:   # the job as the server sees it
        job_file = Path(tmp) / str(Path(job['cancel_path']).parent.relative_to(project)) / 'job.json'
        job_file.parent.mkdir(parents=True)
        write_json(job_file, rewrite(job, mapping))
        rsync(pod, [str(job_file.relative_to(tmp))], f'{remote}/', cwd=tmp)
    ssh(pod, 'touch /workspace/.busy /workspace/.uploaded')   # the upload keep-alive stops; the relay keeps it fresh from here


def _remote_state(pod, remote_job, remote_progress):
    from .remote_gpu import ssh
    out = ssh(pod, f'touch /workspace/.busy; cat {shlex.quote(remote_job)}; echo; echo ---; cat {shlex.quote(remote_progress)} 2>/dev/null || true',
              timeout=60).stdout
    job_text, _, progress_text = out.partition('\n---\n')
    try:
        state = json.loads(job_text)
    except ValueError:   # the server's job file is missing or half written: say what the server's worker said
        log = ssh(pod, f'tail -c 1500 {shlex.quote(remote_job)}.worker.log 2>/dev/null', check=False, timeout=60).stdout
        raise StudioError('REMOTE_RENDER_FAILED', f'server job file unreadable ({remote_job}); worker log: {log.strip()[-1200:]}') from None
    try:
        progress = json.loads(progress_text) if progress_text.strip() else None
    except ValueError:   # written between our reads: the next poll has it
        progress = None
    return state, progress


def run_remote_worker(job_path, token):
    from . import remote_gpu
    from .jobs import record_render_time, verify_render
    job_path = Path(job_path).resolve()
    job = read_json(job_path)
    if job.get('worker_token') != token:
        raise StudioError('REVISION_CONFLICT', 'Worker token does not match job')
    job['pid'] = os.getpid(); job['updated_at'] = now(); write_json(job_path, job)

    def stop(signum, frame):   # job cancel / kill: still reach `finally` and delete the server
        raise StudioError('CANCELLED', f'remote worker stopped by signal {signum}')
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    pod = None
    try:
        remote_gpu.locked_sweep(exclude=job_path)   # a server nobody holds is deleted before we pay for another
        def chosen_one(chosen):
            nonlocal pod
            pod = chosen
            _register(job_path, chosen['id'])
        with _beating(job_path):
            pod = remote_gpu.ensure_pod(estimate_hours(job), on_chosen=chosen_one)
        job['remote'] = {'pod_id': pod['id'], 'gpu': (pod.get('gpu') or {}).get('displayName'), 'cost_per_hr': pod.get('costPerHr')}
        job['heartbeat_unix'] = time.time(); write_json(job_path, job)
        project = Path(job['project_dir'])
        from .jobs import RENDER_DEVICES, render_job_problems, render_output_ok
        refused = render_job_problems(project, job, job_path)   # checked again by the worker itself, not only by the dispatcher
        if refused:
            raise StudioError('DISPATCH_REFUSED', '; '.join(refused))
        # both spellings of a path map (macOS: /var/... is /private/var/...), back to the one the job was written with
        mapping = {str(project): remote_project(job['project_id']), str(project.resolve()): remote_project(job['project_id']),
                   str(REPO): REMOTE_REPO, str(REPO.resolve()): REMOTE_REPO}
        back = {remote_project(job['project_id']): str(project), REMOTE_REPO: str(REPO)}
        with _beating(job_path):
            _upload(pod, job, mapping)
        remote_job = rewrite(str(job_path), mapping)
        remote_progress = rewrite(job['progress_path'], mapping)
        remote_cancel = rewrite(job['cancel_path'], mapping)
        env = f'STUDIO_BLENDER=/opt/blender/blender STUDIO_RENDER_DEVICE={shlex.quote(job["render_settings"]["device"])}'
        remote_gpu.ssh_detached(pod, f'cd {REMOTE_REPO} && {env} exec python3 -m studio _worker {shlex.quote(remote_job)} {shlex.quote(token)}',
                                log=f'{remote_job}.worker.log')
        while True:
            time.sleep(1 if Path(job['cancel_path']).exists() else POLL_S)
            if Path(job['cancel_path']).exists():   # the frozen worker reads its own cancel file: create it there
                remote_gpu.ssh(pod, f'touch {shlex.quote(remote_cancel)}', check=False)
            reached = remote_gpu.over_cap()   # the estimate is a guess: the cap holds while the render runs, not only before
            if reached:
                remote_gpu.ssh(pod, f'touch {shlex.quote(remote_cancel)}', check=False)
                raise StudioError('GPU_BUDGET_REACHED', f'{reached}: the render stopped and its server is deleted',
                                  recovery='ask the user to raise the cap (studio gpu enable --monthly-cap N with their words), or render locally')
            state, progress = _remote_state(pod, remote_job, remote_progress)
            job = read_json(job_path)
            job.update({k: state[k] for k in ('error', 'render_started_at', 'missing_frames', 'warnings') if k in state})
            # the local job stays running until the frames are home and verified: another job finishing meanwhile must
            # see this one still holding the server (rehearsal 2026-10-08: the server was deleted mid-download)
            job['status'] = 'running' if state.get('status') in TERMINAL or state.get('status') == 'running' else job['status']
            job['heartbeat_unix'] = time.time(); job['updated_at'] = now()
            write_json(job_path, job)
            if progress:
                write_json(Path(job['progress_path']), progress)
            if state.get('status') in TERMINAL:
                break
        remote_output = rewrite(job['output_dir'], mapping)
        with _beating(job_path):
            _download(pod, remote_output, job['output_dir'], project, job['shot_id'])
        if state.get('fingerprint') != job['fingerprint']:   # a CPU fallback renders under its own fingerprint and folder
            job['fallback_from'], job['fingerprint'] = state.get('fallback_from'), state['fingerprint']
            local = rewrite(state['output_dir'], back)
            if not render_output_ok(project, job['shot_id'], local) or (state.get('render_settings') or {}).get('device') not in RENDER_DEVICES:
                raise StudioError('REMOTE_RENDER_UNVERIFIED', f"the server reported a fallback folder or device the job may not use: {local}")
            job['output_dir'], job['render_settings'] = local, state['render_settings']
            with _beating(job_path):
                _download(pod, state['output_dir'], job['output_dir'], project, job['shot_id'])
        job['status'] = state['status']   # now final: the server's verdict, checked below against the frames we hold
        manifest_path = Path(job['output_dir']) / 'render.json'
        if manifest_path.is_file():
            write_json(manifest_path, rewrite(read_json(manifest_path), back))
        if job['status'] == 'complete' and not verify_render(job):
            job['status'], job['error'] = 'failed', {'code': 'REMOTE_RENDER_UNVERIFIED', 'message': 'downloaded frames do not match render.json'}
        if job.get('render_started_at'):
            record_render_time(job_path, job, max(0.0, job['heartbeat_unix'] - job['render_started_at']))
    except Exception as exc:
        error = exc if isinstance(exc, StudioError) else StudioError('REMOTE_RENDER_FAILED', str(exc), retryable=True)
        job = read_json(job_path)
        job['status'], job['error'] = ('cancelled' if Path(job['cancel_path']).exists() else 'failed'), error.as_dict()
    finally:
        job['updated_at'] = now()
        write_json(job_path, job)
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
