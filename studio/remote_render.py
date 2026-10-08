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
import signal
import tempfile
import time
from pathlib import Path

from .common import REPO, StudioError, lock, now, read_json, write_json

POLL_S = 5
TERMINAL = ('complete', 'failed', 'cancelled', 'interrupted')
REGISTRY = Path(os.environ.get('STUDIO_REMOTE_REGISTRY', REPO / '.studio' / 'remote_jobs.json'))
REMOTE_ROOT = '/workspace/studio'
REMOTE_REPO = f'{REMOTE_ROOT}/repo'
SECONDS_PER_FRAME = {'layout': 0.05, 'look': 0.4, 'review': 0.4, 'final': 1.0}   # RTX 5090 guess until measured; boot ~5 min
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


def estimate_usd(job, cost_per_hr=1.0):
    frames = len(job.get('missing_frames') or job['frames'])
    return round((frames * SECONDS_PER_FRAME.get(job['profile'], 1.0) / 3600 + BOOT_HOURS) * cost_per_hr, 4)


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


def _upload(pod, job, mapping):
    from .remote_gpu import rsync, ssh, ssh_detached
    project = Path(job['project_dir'])
    remote = mapping[str(project)]
    ssh(pod, f'mkdir -p {REMOTE_REPO}/.studio {shlex.quote(remote)}; touch /workspace/.busy')
    ssh(pod, 'rm -f /workspace/.uploaded')
    ssh_detached(pod, 'while [ ! -f /workspace/.uploaded ]; do touch /workspace/.busy; sleep 10; done')
    rsync(pod, ['studio', 'schemas'], f'{REMOTE_REPO}/', cwd=REPO)
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
        remote_gpu.sweep(live_pod_ids(exclude=job_path))   # a server nobody holds is deleted before we pay for another
        def chosen_one(chosen):
            nonlocal pod
            pod = chosen
            _register(job_path, chosen['id'])
        pod = remote_gpu.ensure_pod(estimate_usd(job), on_chosen=chosen_one)
        job['remote'] = {'pod_id': pod['id'], 'gpu': (pod.get('gpu') or {}).get('displayName'), 'cost_per_hr': pod.get('costPerHr')}
        job['heartbeat_unix'] = time.time(); write_json(job_path, job)
        project = Path(job['project_dir'])
        # both spellings of a path map (macOS: /var/... is /private/var/...), back to the one the job was written with
        mapping = {str(project): remote_project(job['project_id']), str(project.resolve()): remote_project(job['project_id']),
                   str(REPO): REMOTE_REPO, str(REPO.resolve()): REMOTE_REPO}
        back = {remote_project(job['project_id']): str(project), REMOTE_REPO: str(REPO)}
        _upload(pod, job, mapping)
        remote_job = rewrite(str(job_path), mapping)
        remote_progress = rewrite(job['progress_path'], mapping)
        remote_cancel = rewrite(job['cancel_path'], mapping)
        env = f'STUDIO_BLENDER=/opt/blender/blender STUDIO_RENDER_DEVICE={job["render_settings"]["device"]}'
        remote_gpu.ssh_detached(pod, f'cd {REMOTE_REPO} && {env} exec python3 -m studio _worker {shlex.quote(remote_job)} {token}',
                                log=f'{remote_job}.worker.log')
        while True:
            time.sleep(1 if Path(job['cancel_path']).exists() else POLL_S)
            if Path(job['cancel_path']).exists():   # the frozen worker reads its own cancel file: create it there
                remote_gpu.ssh(pod, f'touch {shlex.quote(remote_cancel)}', check=False)
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
        Path(job['output_dir']).mkdir(parents=True, exist_ok=True)
        remote_gpu.rsync(pod, [f'{remote_output}/'], job['output_dir'], upload=False)
        if state.get('fingerprint') != job['fingerprint']:   # a CPU fallback renders under its own fingerprint and folder
            job['fallback_from'], job['fingerprint'] = state.get('fallback_from'), state['fingerprint']
            job['output_dir'], job['render_settings'] = rewrite(state['output_dir'], back), state['render_settings']
            Path(job['output_dir']).mkdir(parents=True, exist_ok=True)
            remote_gpu.rsync(pod, [f'{state["output_dir"]}/'], job['output_dir'], upload=False)
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
