from __future__ import annotations

import fcntl
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time
import uuid

from PIL import Image
from .common import CODE, REPO, StudioError, check_id, file_hash, lock, now, read_json, safe_path, stable_hash, write_json
from .project import load_project, load_shot, project_dir, shot_path

# A job in one of these is still on its way: a run waits for it, a render request is answered by it (2026-10-12 audit:
# a job waiting for the broker was dropped from the run's pending jobs).
ACTIVE_STATES = ('queued', 'running', 'awaiting_dispatch', 'handed_over')
TERMINAL_STATES = ('complete', 'failed', 'cancelled', 'interrupted')
PROFILES = {'layout': (360, 640, 16), 'look': (720, 1280, 64), 'review': (720, 1280, 64), 'final': (1080, 1920, 128)}
# Below 64 spp the measured frame-to-frame flicker rose 13-17 % (photoreal research 01, 2026-10-03).
ANIMATION_MIN_SAMPLES = 64
# Copied next to each job and executed from there, so a running job never sees code edits: the render script and every
# blender_ops module it imports, at any depth (code_closure - the same answer the look's input hash uses). A list went
# stale: scene_tools imports scene_roles for label occlusion, the copy lacked it and every labelled render failed
# (floor_noise s02, 2026-10-09).
from .blender_ops.code_closure import module_closure
FROZEN = module_closure('render_frames.py')


def render_compute(remote=False, env=None):
    """The GPU backend a render runs on: the rented server's (OptiX) or this machine's (Metal on macOS, CUDA elsewhere).
    It changes pixels (noise and denoise differ by backend), so it is a render setting: a local and a remote render of the
    same scene never answer for each other."""
    env = os.environ if env is None else env
    if remote:
        return env.get('STUDIO_REMOTE_COMPUTE', 'optix')
    return env.get('STUDIO_RENDER_COMPUTE', 'metal' if sys.platform == 'darwin' else 'cuda')


def render_settings(project, shot, profile, frames, samples_override=None, env=None, remote=False):
    """Pure: every value that changes pixels, so all of it lands in the render fingerprint."""
    env = os.environ if env is None else env
    width, height, samples = PROFILES[profile]
    # the profile's long side is the output's long side (a horizontal project previews as small as a vertical one; a
    # vertical one gets the same numbers as before - 2026-10-12 audit: horizontal previews were ~3x the pixels)
    out_w, out_h = project['output']['width'], project['output']['height']
    scale = max(width, height) / max(out_w, out_h)
    width, height = (max(2, round(side * scale / 2) * 2) for side in (out_w, out_h))
    if profile == 'final':
        width, height = project['output']['width'], project['output']['height']
    if width % 2 or height % 2:
        raise StudioError('INPUT_INVALID', 'H.264 output dimensions must be even')
    if samples_override is not None and (not isinstance(samples_override, int) or samples_override < 1):
        raise StudioError('INPUT_INVALID', 'Render samples must be a positive integer')
    explicit = samples_override or shot['render'].get('profile_samples', {}).get(profile) or shot['render'].get('samples')
    animation = len(frames) > 1 and frames == list(range(frames[0], frames[0] + len(frames)))
    if explicit:
        samples = explicit
    elif animation and profile in ('review', 'final'):
        samples = max(samples, ANIMATION_MIN_SAMPLES)
    engine = 'BLENDER_WORKBENCH' if profile == 'layout' else shot['render'].get('engine')   # blocking and motion: ~0.02 s/frame
    return {'width': width, 'height': height, 'fps': project['output']['fps'], 'samples': samples, 'engine': engine,
            'device': env.get('STUDIO_RENDER_DEVICE', 'GPU'), 'compute': render_compute(remote, env), 'png_depth': '16' if profile == 'final' else '8',
            'adaptive_threshold': .01 if profile == 'final' else .02, 'profile': profile, 'animation': animation}


def find_job(path, job_id):
    matches = list(project_dir(path).glob(f'runs/*/jobs/{check_id(job_id)}/job.json'))
    if len(matches) != 1:
        raise StudioError('INPUT_INVALID', f'Job not found or ambiguous: {job_id}')
    return matches[0]


HEARTBEAT_STALE_S = 60   # a running worker writes heartbeat_unix every 0.5 s (render_worker._run_process)
NEVER_STARTED_S = 30     # a queued job no worker claimed (start_worker writes queued just before it spawns one)


# ---- who may judge and change a job (remote jobs audit, 2026-10-21) -------------------------------------------------
# A job is judged only by the machine whose process owns it, and changed only through update_job. Why: readers on any
# machine marked jobs interrupted from evidence that was not theirs - a rented server signalled this Mac's pid and
# failed queued jobs another job's end happened to look at (submarine, 2026-10-20: 6 times), and a status poll here
# marked a live remote job interrupted during one slow ssh read - while writers overwrote each other's fields.

def machine_id():
    """This machine's id (one per data root, made once): a pid in a job is judged only where it was written."""
    path = REPO / '.studio' / 'machine_id'
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'w') as out:
            out.write(uuid.uuid4().hex)
    for _ in range(50):   # another process may have just created it and not yet written it
        text = path.read_text().strip()
        if text:
            return text
        time.sleep(0.01)
    raise StudioError('ENVIRONMENT_MISSING', f'{path} stays empty')


def owned_here(job):
    """The job's pid was written on this machine. No owner_machine: written where the file is (the local worker writes
    only its pid; a job sent to another machine has its pid and owner cleared on the way, remote_render._upload)."""
    return job.get('owner_machine') in (None, machine_id())


def create_job(job_path, job):
    """A new job file (nobody else knows the job yet)."""
    if Path(job_path).exists():
        raise StudioError('REVISION_CONFLICT', f'{job_path} already exists')
    write_json(job_path, job)


def update_job(job_path, change, reopen=False):
    """The one way a job file changes: under the job's lock, the file as it is now, `change` (a dict of fields, or a
    function of the job returning the new job) merged in. A finished job stays finished unless `reopen` (a resume)."""
    with lock(Path(job_path).parent / '.lock', reentrant=True):
        job = read_json(job_path)
        new = change(dict(job)) if callable(change) else {**job, **change}
        if job.get('status') in TERMINAL_STATES and not reopen:
            new['status'] = job['status']
        write_json(job_path, new)
        return new


def _process_running(pid):
    """The process exists and has not exited. A worker this process started is asked through waitpid (an exited child
    stays a zombie until reaped, and signal 0 would still find it; reaping it is right). Any other process is asked
    with signal 0 (its parent - launchd for a detached worker - reaps it)."""
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        return done == 0
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)   # existence only, nothing is sent
    except ProcessLookupError:
        return False
    except PermissionError:
        # EPERM means the process exists but may not be signalled: a detached worker seen from inside the studio sandbox
        # (archcut3, 2026-10-08: a rendering job was marked interrupted). A reused PID is caught by worker_alive's heartbeat.
        return True
    return True


def _renders(job):
    """A render job (the worker that writes frames and a heartbeat). Other jobs share runs/*/jobs (a reference fetch,
    2026-10-11) and carry none of a render's fields: status, liveness and progress are read by kind."""
    return job.get('operation', 'render') == 'render'


def worker_alive(job, job_path, now_unix=None):
    """The worker that claimed this job is still working - or, for a worker of another machine, not known to be dead
    (only its own machine can tell: owned_here). The worker writes its own PID after checking the job's token
    (render_worker.run_worker), so the PID names our worker; while it renders it refreshes heartbeat_unix every 0.5 s, so a
    running job with a stale heartbeat is dead or hung even if its PID was reused. No external tools (ps cannot run inside
    the studio sandbox, studio/broker.py): the same rule everywhere."""
    pid = job.get('pid')
    if not pid:
        return False
    if not owned_here(job):
        return True
    if not _process_running(int(pid)):
        return False
    if job.get('status') == 'running' and _renders(job):   # only the render worker keeps a heartbeat
        beat = job.get('heartbeat_unix') or job.get('render_started_at')
        return beat is not None and (now_unix if now_unix is not None else time.time()) - float(beat) <= HEARTBEAT_STALE_S
    return True                # queued: waiting for the GPU lock, before the first heartbeat


def effective_status(job, job_path, now_unix=None):
    """(status, evidence): the job's status as its stored state and its worker's liveness say, without writing - a
    reader on any machine may call it. A worker found dead reads as 'interrupted' with the evidence; the file keeps its
    state until the job's owner acts on it (resume, cancel)."""
    status, pid = job['status'], job.get('pid')
    now_unix = time.time() if now_unix is None else now_unix
    if status in ('queued', 'running') and pid and owned_here(job) and not worker_alive(job, job_path, now_unix):
        beat = job.get('heartbeat_unix') or job.get('render_started_at')
        alive = _process_running(int(pid))
        return 'interrupted', (f'worker pid {pid} has no fresh heartbeat ({now_unix - float(beat):.0f} s old)' if alive and beat
                               else f'worker pid {pid} is not running on this machine')
    if status == 'queued' and not pid and owned_here(job) and job.get('updated_at'):
        waited = (datetime.now(timezone.utc) - datetime.fromisoformat(job['updated_at'])).total_seconds()
        if waited > NEVER_STARTED_S:
            return 'interrupted', f'no worker claimed it in {waited:.0f} s'
    if status == 'awaiting_dispatch':
        return status, f"not dispatched since {job.get('updated_at')}"   # still dispatchable: a wait on it gives up on no progress
    return status, None


def queued_remote_ahead(path):
    """How many remote render jobs of this project are still on their way (a clip wait's sign the queue moves)."""
    return sum(1 for f in project_dir(path).glob('runs/*/jobs/*/job.json')
               if (job := read_json(f)).get('executor') == 'runpod' and effective_status(job, f)[0] in ACTIVE_STATES)


def start_worker(job_path):
    """The local frozen worker, or - for a job submitted with remote - the remote worker that runs the same frozen worker
    on a rented GPU server (studio/remote_render.py)."""
    def queued(job):
        job.update(worker_token=uuid.uuid4().hex, status='queued', error=None, updated_at=now())
        for stale in ('render_started_at', 'heartbeat_unix', 'owner_machine', 'evidence'):
            job.pop(stale, None)
        return job
    job = update_job(job_path, queued, reopen=True)
    from .broker import BROKERED, NETWORK_ENTRIES, worker_entry
    entry = worker_entry(job)
    if entry in NETWORK_ENTRIES and os.environ.get(BROKERED):
        # Inside the broker's sandbox there is no network: the job waits for the broker to start this entry outside it
        # (broker.dispatch, an allow-list). Why (floor_noise, 2026-10-09): the remote worker inherited the sandbox and
        # its first RunPod call failed with EPERM.
        update_job(job_path, {'status': 'awaiting_dispatch'})
        return None
    return spawn_worker(job_path, entry, job['worker_token'])


def spawn_worker(job_path, entry, token, env=None):
    with (Path(job_path).parent / 'worker.log').open('ab') as log:
        # the engine this process runs (the sealed one in the broker), isolated from the checkout (studio/_launch.py)
        from .common import CODE
        from .seal import launch_argv
        process = subprocess.Popen(launch_argv('studio', entry, str(job_path), token, engine=CODE), cwd=REPO, stdout=log, stderr=log,
                                   start_new_session=True, env={**(os.environ if env is None else env), 'STUDIO_DATA_ROOT': str(REPO)})
    # Worker sets PID itself. Do not overwrite a fast worker's final state.
    return process.pid


def _prepare_render(path, shot_id, version, profile, frames, samples_override, remote):
    """What a render of this shot version would be - its gates passed, settings, fingerprint, output folder - before any
    job exists: shared by render submit and render quote, so a quote is the submit's own numbers."""
    project = load_project(path)
    if profile not in PROFILES:
        raise StudioError('INPUT_INVALID', f'Unknown render profile {profile}')
    directory = safe_path(shot_path(path, shot_id).parent, f'versions/{check_id(version)}')
    shot = read_json(directory / 'shot.snapshot.json')
    dependencies = read_json(directory / 'dependencies.json')
    scene = directory / 'scene.blend'
    if file_hash(scene) != dependencies['scene_sha256']:
        raise StudioError('REVISION_CONFLICT', 'Scene was modified after snapshot')
    frames = sorted(set(frames if frames is not None else (shot['render']['look_frames'] if profile == 'look' else range(shot['duration_frames']))))
    if not frames or frames[0] < 0 or frames[-1] >= shot['duration_frames']:
        raise StudioError('TIMING_CONFLICT', 'Render frames outside snapshot duration')
    render_gates(path, project, shot, version, profile)
    warnings = []
    current = load_shot(path, shot_id)
    if current.get('scene_version') == version:   # the shot's own version, judged against the shot as it is now (an older
        from .routing import scene_current     # version asked for by name, or a queued job resumed, renders what it is)
        warnings += [w for w in [scene_current(path, current)] if w]
    if remote:   # a rented GPU server: on only in the user's words (studio gpu enable)
        from .remote_gpu import settings as gpu_settings
        if not gpu_settings()['enabled']:
            raise StudioError('REMOTE_GPU_DISABLED', 'remote GPU renders are off', recovery="studio gpu enable --user-words \"<the user's words>\"")
    settings = render_settings(project, shot, profile, frames, samples_override, remote=remote)
    fingerprint = render_fingerprint(dependencies, settings, frames)
    return {'project': project, 'shot': shot, 'dependencies': dependencies, 'scene': scene, 'frames': frames, 'settings': settings,
            'fingerprint': fingerprint, 'output': shot_path(path, shot_id).parent / 'renders' / fingerprint, 'warnings': warnings}


def _cached_render(path, shot_id, fingerprint):
    """The job already rendering or rendered with this fingerprint (its status, cache_hit), else None."""
    for existing in path.glob('runs/*/jobs/*/job.json'):
        old = read_json(existing)
        if fingerprint in (old.get('fingerprint'), old.get('fallback_from')) and old.get('shot_id') == shot_id:
            status = job_status(path, old['job_id'])
            fallback = {'warnings': [f"RENDERED_WITH_FALLBACK: {old['job_id']} rendered with {old['render_settings']['engine']} "
                                     f"{old['render_settings']['device']} after the requested renderer failed"]} if old.get('fallback_from') == fingerprint else {}
            if status['status'] in ACTIVE_STATES:   # the broker starts a waiting one from this result
                return {**status, 'cache_hit': True, **fallback}
            if status['status'] == 'complete' and verify_render(old):
                return {**status, 'cache_hit': True, **fallback}
    return None


def submit_render(path, shot_id, version, profile='layout', frames=None, samples_override=None, remote=False):
    path = project_dir(path)
    from .freeze import require_code_frozen
    require_code_frozen()   # the renderer and its fingerprint are frozen code: refuse to render on an unapproved change
    prepared = _prepare_render(path, shot_id, version, profile, frames, samples_override, remote)
    from .broker import BROKERED
    if remote and not os.environ.get(BROKERED):   # outside the broker the worker starts here: say now, not after a job exists, if the API is unreachable
        from .remote_gpu import our_pods
        try:
            our_pods()
        except StudioError as error:
            raise StudioError('REMOTE_GPU_UNREACHABLE', f'the rented-GPU API cannot be reached from here: {error.message}',
                              recovery='run it through studio_run (the broker starts remote workers outside the sandbox) or check the network') from None
    project, shot, dependencies, scene, frames, settings, fingerprint, output = (prepared[k] for k in (
        'project', 'shot', 'dependencies', 'scene', 'frames', 'settings', 'fingerprint', 'output'))
    estimate = None
    with lock(path / '.project.lock', blocking=False):
        cached = _cached_render(path, shot_id, fingerprint)
        if cached:
            return cached
        if remote:   # the whole queue on one server must fit the month's cap before this job joins it (2026-10-19)
            from .remote_render import queue_estimate
            estimate = queue_estimate(adding=[{'job_id': 'this job', 'frames': frames, 'profile': profile}])
            if not estimate['fits']:
                raise StudioError('GPU_BUDGET_EXCEEDED', f"the remote queue ({len(estimate['queued_jobs'])} jobs waiting + this one, "
                                  f"{estimate['frames']} frames, about ${estimate['usd']:.2f}) would bring this month's "
                                  f"${estimate['spent_usd']:.2f} past the ${estimate['cap_usd']:.2f} cap",
                                  recovery='studio render quote to see the queue; render fewer shots, or ask the user to raise the cap '
                                           '(studio gpu enable --monthly-cap N in their words)')
            if not estimate['fits_pod_life']:
                raise StudioError('GPU_QUEUE_TOO_LONG', f"the remote queue ({len(estimate['queued_jobs'])} jobs waiting + this one, about "
                                  f"{estimate['hours']:.2f} h) outlasts a server's {estimate['max_pod_hours']:.2f} h life (its watchdog deletes it then)",
                                  recovery='render fewer shots at once (submit the rest when these are done), or render locally')
        runs = sorted(path.glob('runs/*/run.json'))
        if not runs:
            raise StudioError('INPUT_INVALID', 'Project has no run; initialize through project init')
        run_path = runs[-1]
        run = read_json(run_path)
        job_id = 'job_' + uuid.uuid4().hex[:12]
        job_path = run_path.parent / 'jobs' / job_id / 'job.json'
        job = {'schema_version': 1, 'job_id': job_id, 'run_id': run['run_id'], 'project_id': project['project_id'], 'project_dir': str(path),
               'operation': 'render', 'shot_id': shot_id, 'scene_version': version, 'profile': profile, 'fingerprint': fingerprint,
               'scene_path': str(scene), 'scene_sha256': dependencies['scene_sha256'], 'shot': shot, 'render_settings': settings,
               'frames': frames, 'output_dir': str(output), 'status': 'queued', 'pid': None, 'created_at': now(), 'updated_at': now(),
               'attempt': 1, 'render_wall_seconds': project['limits']['render_wall_minutes'] * 60, 'error': None,
               'cancel_path': str(job_path.parent / 'cancel.request'), 'progress_path': str(job_path.parent / 'progress.json'),
               **({'executor': 'runpod'} if remote else {})}
        freeze_renderer(job, job_path)
        create_job(job_path, job)
        with lock(run_path.parent / '.run.lock'):
            run = read_json(run_path)
            run['pending_jobs'].append(job_id); run['updated_at'] = now(); run['status'] = 'running'
            write_json(run_path, run)
        start_worker(job_path)
    return {'project_id': project['project_id'], 'run_id': run['run_id'], 'job_id': job_id, 'status': 'queued', 'cache_hit': False,
            **({'remote_queue': estimate} if estimate else {}), **({'warnings': prepared['warnings']} if prepared['warnings'] else {}),
            'artifacts': [str(job_path), str(output)]}


def quote_render(path, shots, profile='final', samples_override=None):
    """What submitting these shot versions remotely would cost, as submit itself counts it (render quote): each shot's
    frames or its cached render, and the queue on one server - the jobs already waiting plus these, one boot - against
    the month's cap. Nothing is created; reads the local ledger only, so it answers inside the agent's sandbox."""
    path = project_dir(path)
    from .remote_render import queue_estimate
    rows, adding = [], []
    for shot_id, version in shots:
        prepared = _prepare_render(path, shot_id, version, profile, None, samples_override, True)
        cached = _cached_render(path, shot_id, prepared['fingerprint'])
        rows.append({'shot_id': shot_id, 'version': version, 'frames': len(prepared['frames']), 'cache_hit': bool(cached),
                     **({'job_id': cached['job_id'], 'status': cached['status']} if cached else {}),
                     **({'warnings': prepared['warnings']} if prepared['warnings'] else {})})
        if not cached:
            adding.append({'job_id': f'{shot_id}@{version}', 'frames': prepared['frames'], 'profile': profile})
    return {'profile': profile, 'shots': rows, 'queue': queue_estimate(adding=adding)}


def _shot_versions(text):
    pairs = [item.split(':') for item in text.split(',') if item]
    if not pairs or any(len(p) != 2 for p in pairs):
        raise StudioError('INPUT_INVALID', '--shots takes shot:version pairs, e.g. s01:v0008,s02:v0019')
    return [(check_id(s), check_id(v)) for s, v in pairs]



RENDER_CODE = (*(f'studio/blender_ops/{name}' for name in FROZEN),
               'studio/render_worker.py')   # everything that runs between the scene and the frames; any change re-renders


def render_fingerprint(dependencies, settings, frames):
    return stable_hash({'scene': dependencies, 'settings': settings, 'frames': frames,
                        'code': {name: file_hash(CODE / name) for name in RENDER_CODE}})[:24]


def render_gates(path, project, shot, version, profile):
    """Every gate a render passes, at submit and at resume alike: the route (and the approved fill brief), subject
    fidelity for anything past blocking, and the human turnaround for a final."""
    from .routing import assert_route
    assert_route(shot, 'render', path)
    from .decisions import require
    require(path, 'render_final' if profile == 'final' else 'render_look' if profile in ('look', 'review') else 'render')
    if profile in ('look', 'review', 'final'):
        from .storyboard import require as storyboard_kept   # the pictures the user agreed (decision ladder projects)
        storyboard_kept(path, shot, version)
        from .fidelity import require_fidelity
        require_fidelity(path, shot, version, f'{profile} render')
        _require_direction(path)
        from .quality_gate import require as quality_kept   # measured, picked, watched, floors and reasons read (2026-10-24)
        quality_kept(path, shot)
    if profile == 'final':
        _require_turnaround(path, project, shot, version)
        from .decisions import envelope, gated, adopted
        if adopted(path) and gated(path, 'cut'):   # a final renders the version the user watched in the rough cut
            seen = ((envelope(path, 'cut') or {}).get('body') or {}).get('versions', {}).get(shot['shot_id'])
            if seen != version:
                raise StudioError('DECISION_DRIFT', f"final render of {shot['shot_id']} {version}: the approved rough cut showed {seen}",
                                  recovery='render the version the cut showed, or build a new rough cut and approve it')


def _require_direction(path):
    """A coloured render waits until the reel's direction findings are answered (studio/reel_flow.py): the blocking ones
    fixed, the others fixed or departed with why. Only a project with a shot list (decisions/shotlist.json, settled or
    left a draft by its findings) - the reel the findings read. Why (2026-10-19): the bike reel was rendered on RunPod with six findings of its clay
    open and its key change too small to see; a render is where it starts to cost."""
    from .decisions import envelope
    if envelope(path, 'shotlist') is None:   # any shot list, settled or a draft: a draft is what an open finding leaves
        return
    from .reel_flow import built
    flow = built(path)
    if flow['errors']:
        raise StudioError('DIRECTION_UNRESOLVED', 'the reel flow has open findings: ' + ' | '.join(e[:200] for e in flow['errors']),
                          recovery='studio reel flow --project P: fix each, or depart with why (shot screen.pattern_departs, studio reel depart); '
                                   'the blocking ones (HOOK_WEAK, KEY_CHANGE_*) are fixed in the shot list or the camera')


def _require_turnaround(path, project, shot, version):
    """Final renders of unreviewed generated/caller-licensed assets or specific real subjects need a human look first."""
    missing = []
    for instance in shot.get('asset_instances', []):
        manifest_path = REPO / 'library' / 'assets' / instance['asset_id'] / instance.get('asset_version', 'v0001') / 'asset.json'
        if not manifest_path.is_file():   # an asset no record says the source of is no cleared asset (2026-10-21: it was skipped)
            raise StudioError('ASSET_UNKNOWN', f"asset {instance['asset_id']} {instance.get('asset_version', 'v0001')} has no manifest "
                              f'({manifest_path}): its source, licence and approval are unknown',
                              recovery='acquire it through asset acquire/import (the manifest records source and licence), or drop it')
        manifest = read_json(manifest_path)
        source = manifest.get('source', {})
        if (source.get('ai_generated') or source.get('use_status') != 'cleared') and (manifest.get('approval') or {}).get('decision') != 'approved':
            missing.append(f"asset {instance['asset_id']} ({'ai_generated' if source.get('ai_generated') else source.get('use_status')})")
    policy = project.get('route_policy', {})
    # a real subject is the brief's, or a spec of a real thing the shot builds (its own subject_mode: the brief's default
    # is schematic, so a real subject spec alone asked for no turnaround - 2026-10-21)
    from .subjects import spec_path
    real = project['brief'].get('subject_mode') == 'specific_real' or any(
        spec_path(path, ref['subject_id']).is_file() and read_json(spec_path(path, ref['subject_id'])).get('subject_mode') == 'specific_real'
        for ref in shot.get('subjects') or [])
    real = real or any(spec_path(path, row['subject']).is_file() and read_json(spec_path(path, row['subject'])).get('subject_mode') == 'specific_real'
                       for row in (shot.get('scene') or {}).get('instances') or [] if 'subject' in row)
    if policy.get('turnaround_required', True) and real:
        from .project import recorded_reviews   # only reviews review record wrote, with the user's words
        approved = any(r.get('turnaround_approved') is True and r.get('reviewer_kind') == 'human' and r.get('shot_id') == shot['shot_id']
                       and r.get('scene_version') == version for r in recorded_reviews(path))
        if not approved:
            missing.append(f"shot {shot['shot_id']} {version} turnaround (specific real subject)")
    if missing:
        raise StudioError('TURNAROUND_APPROVAL_REQUIRED', 'Human turnaround approval missing: ' + '; '.join(missing),
                          recovery='Show front/back/side previews to the user, then record asset approve or review record --turnaround_approved')


RENDER_DEVICES = ('GPU', 'CPU')   # what render_settings.device may say (it reaches a remote shell)


def render_job_problems(project, job, job_path=None):
    """Why a render job may not be run by a worker outside the sandbox (2026-10-18 reviews). job.json is data the agent
    can write, and the remote worker puts its fields into paths, its upload, the server's shell and its download - so
    every field it reads is checked against what render submit makes (default deny), not taken from it: ids are ids,
    each path is the one submit derives from them, the device one of RENDER_DEVICES, the token submit's hex, the renderer
    code byte for byte this engine's, and no folder on those paths is a link."""
    import re
    from .common import ID_PATTERN
    project = Path(project).resolve()
    problems = []
    ident = lambda v: isinstance(v, str) and re.fullmatch(ID_PATTERN, v) is not None   # noqa: E731
    try:
        own = read_json(project / 'project.json').get('project_id')
    except StudioError:
        own = None
    if not ident(job.get('project_id')) or job.get('project_id') != own:
        problems.append(f"project_id {job.get('project_id')!r} is not this project's id")
    if not ident(job.get('shot_id')):
        problems.append(f"shot_id {job.get('shot_id')!r} is not an id")
    if not (isinstance(job.get('scene_version'), str) and re.fullmatch(r'(failed_)?v\d{3,6}', job['scene_version'])):
        problems.append(f"scene_version {job.get('scene_version')!r} is not a version")
    if not (isinstance(job.get('fingerprint'), str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', job['fingerprint'])):
        problems.append('fingerprint is not a fingerprint')
    if not (isinstance(job.get('worker_token'), str) and re.fullmatch(r'[0-9a-f]{32}', job['worker_token'])):
        problems.append('worker_token is not the token submit makes')
    if (job.get('render_settings') or {}).get('device') not in RENDER_DEVICES:
        problems.append(f"render device {(job.get('render_settings') or {}).get('device')!r} is not one of {RENDER_DEVICES}")
    frames = job.get('frames')
    if not (isinstance(frames, list) and all(isinstance(f, int) and not isinstance(f, bool) and f >= 0 for f in frames)):
        problems.append('frames is not a list of frame numbers')
    if problems:   # the paths below are made from the ids above
        return problems
    shot = project / 'shots' / job['shot_id']
    folder = Path(job_path).parent.resolve() if job_path else Path(job.get('cancel_path') or '').parent.resolve()
    expected = {'output_dir': shot / 'renders' / job['fingerprint'],
                'scene_path': shot / 'versions' / job['scene_version'] / 'scene.blend',
                'renderer_script': folder / 'code' / 'render_frames.py'}
    for key, want in expected.items():
        if job.get(key) and Path(job[key]).resolve() != want.resolve():
            problems.append(f"{key} {job.get(key)} is not {want}")
    for key in ('cancel_path', 'progress_path'):
        if job.get(key) and Path(job[key]).resolve().parent != folder:
            problems.append(f'{key} is not in the job\'s folder')
    for path in (shot / 'renders' / job['fingerprint'], folder):
        if any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(project)):
            problems.append(f'{path} runs through a link')
    code = folder / 'code'
    for name in (sorted(p.name for p in code.glob('*')) if code.is_dir() else []):
        engine = CODE / 'studio/blender_ops' / name
        if not engine.is_file() or (code / name).is_symlink() or file_hash(code / name) != file_hash(engine):
            problems.append(f'renderer code {name} differs from the engine\'s')
    return problems


def render_output_ok(project, shot_id, output):
    """`output` is <project>/shots/<shot_id>/renders/<one folder>, through no link."""
    try:
        rel = Path(output).resolve().relative_to(Path(project).resolve())
    except ValueError:
        return False
    import re
    from .common import ID_PATTERN
    return (len(rel.parts) == 4 and rel.parts[0] == 'shots' and rel.parts[1] == shot_id and re.fullmatch(ID_PATTERN, shot_id or '') is not None
            and rel.parts[2] == 'renders' and re.fullmatch(r'[A-Za-z0-9_-]+', rel.parts[3]) is not None
            and not any(p.is_symlink() for p in [Path(output), *Path(output).parents] if p.is_relative_to(Path(project).resolve())))


def freeze_renderer(job, job_path):
    code = job_path.parent / 'code'
    code.mkdir(parents=True, exist_ok=True)
    hashes = {}
    # A job created before render_profile.py existed keeps its original two-file freeze on resume.
    names = tuple(job['execution_code_hashes']) if job.get('execution_code_hashes') else FROZEN
    for name in names:
        destination = code / name
        if not destination.exists():
            shutil.copy2(CODE / 'studio/blender_ops' / name, destination)
        hashes[name] = file_hash(destination)
    if job.get('execution_code_hashes') and job['execution_code_hashes'] != hashes:
        raise StudioError('REVISION_CONFLICT', 'Frozen render scripts were modified')
    job['execution_code_hashes'] = hashes
    job['renderer_script'] = str(code / 'render_frames.py')


def good_frame(path, size):
    try:
        with Image.open(path) as image:
            if image.size != tuple(size):
                return False
            image.verify()
        return True
    except (OSError, ValueError):
        return False


def verify_render(job):
    output = Path(job['output_dir'])
    manifest_path = output / 'render.json'
    if not manifest_path.exists():
        return False
    manifest = read_json(manifest_path)
    if manifest.get('status') != 'complete' or manifest.get('scene_sha256') != job['scene_sha256']:
        return False
    size = (job['render_settings']['width'], job['render_settings']['height'])
    for frame in job['frames']:
        frame_path = output / 'frames' / f'frame_{frame:06d}.png'
        if not good_frame(frame_path, size) or file_hash(frame_path) != manifest.get('frame_sha256', {}).get(str(frame)):
            return False
    clip = manifest.get('clip_path')
    return not manifest.get('full_sequence') or bool(clip and Path(clip).is_file() and file_hash(clip) == manifest.get('clip_sha256'))



def record_render_time(job_path, job, seconds):
    """Deduplicate attempt accounting, including cancellation and interrupted workers."""
    if seconds <= 0:
        return
    run_path = job_path.parents[2] / 'run.json'
    with lock(run_path.parent / '.run.lock'):
        run = read_json(run_path)
        elapsed = run.setdefault('elapsed', {})
        attempts = elapsed.setdefault('render_attempt_seconds', {})
        key = f"{job['job_id']}/{job['attempt']}"
        previous = attempts.get(key, 0)
        attempts[key] = max(previous, seconds)
        elapsed['render_wall_seconds'] = elapsed.get('render_wall_seconds', 0) + attempts[key] - previous
        run['updated_at'] = now()
        write_json(run_path, run)


def job_status(path, job_id):
    """A job as it stands: reads only (effective_status), on any machine. A dead worker reads as interrupted with its
    evidence; the job file is left as it is for its owner (resume, cancel)."""
    job_path = find_job(path, job_id)
    job = read_json(job_path)
    status, evidence = effective_status(job, job_path)
    if status == 'interrupted' and job['status'] != 'interrupted':
        job = {**job, 'status': status, 'error': {'code': 'WORKER_INTERRUPTED', 'message': f'Worker is no longer running ({evidence}); '
                                                                                      'resume preserves valid frames'}}
    progress_path = Path(job.get('progress_path', ''))
    renders = _renders(job)
    progress = read_json(progress_path) if progress_path.is_file() else \
        ({'completed_frames': 0, 'total_frames': len(job['frames'])} if renders else {})
    return {'job_id': job['job_id'], 'run_id': job['run_id'], 'project_id': job['project_id'], 'status': job['status'],
            'operation': job.get('operation', 'render'), 'progress': progress, 'error': job.get('error'), 'attempt': job.get('attempt', 1),
            'output_dir': job['output_dir'], **({'evidence': evidence} if evidence else {}),
            'artifacts': [str(job_path)] + ([str(Path(job['output_dir']) / 'render.json')] if renders else [])}


def cancel_job(path, job_id):
    """Ask the worker to stop (the cancel file: it stops Blender and accounts its time), signal it only if it is a
    process of this machine and did not stop, then mark the job cancelled through update_job. The job's lock is not
    held while the worker is waited for: the worker's own last write must get through."""
    job_path = find_job(path, job_id)
    job = read_json(job_path)
    if job['status'] in TERMINAL_STATES:
        return job_status(path, job_id)
    Path(job['cancel_path']).touch()
    if job['status'] == 'running' and worker_alive(job, job_path):
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline and worker_alive(read_json(job_path), job_path):
            if read_json(job_path)['status'] in TERMINAL_STATES:
                return job_status(path, job_id)
            time.sleep(.1)
    job = read_json(job_path)
    if job['status'] not in TERMINAL_STATES and job.get('pid') and owned_here(job) and worker_alive(job, job_path):
        # Queued or unresponsive worker: verify its dedicated process group before termination.
        try:
            os.killpg(job['pid'], signal.SIGTERM)
        except (ProcessLookupError, PermissionError):   # gone, or (from inside a sandbox) not ours to signal: the cancel file stands
            pass
    job = read_json(job_path)
    if job['status'] not in TERMINAL_STATES:
        if job.get('render_started_at'):
            record_render_time(job_path, job, max(0, (job.get('heartbeat_unix') or time.time()) - job['render_started_at']))
        update_job(job_path, {'status': 'cancelled', 'updated_at': now()})
    return job_status(path, job_id)


def resume_job(path, job_id):
    job_path = find_job(path, job_id)
    with lock(job_path.parent / '.lock', reentrant=True):
        job = read_json(job_path)
        if job['status'] != 'complete' and worker_alive(job, job_path):
            return job_status(path, job_id)
        if job['status'] == 'complete' and verify_render(job):
            return {**job_status(path, job_id), 'cache_hit': True}
        if file_hash(job['scene_path']) != job['scene_sha256']:
            raise StudioError('REVISION_CONFLICT', 'Cannot resume a modified scene snapshot')
        render_gates(project_dir(path), load_project(path), job['shot'], job['scene_version'], job['profile'])
        if effective_status(job, job_path)[0] == 'interrupted' and job.get('render_started_at'):   # the dead attempt's time, once
            record_render_time(job_path, job, max(0, job.get('heartbeat_unix', time.time()) - job['render_started_at'] + 1))
        freeze_renderer(job, job_path)
        Path(job['cancel_path']).unlink(missing_ok=True)
        job = update_job(job_path, {'attempt': job['attempt'] + 1, 'pid': None, 'execution_code_hashes': job['execution_code_hashes'],
                                    'renderer_script': job['renderer_script'],
                                    'render_wall_seconds': load_project(path)['limits']['render_wall_minutes'] * 60}, reopen=True)
        start_worker(job_path)
    return {'job_id': job_id, 'status': read_json(job_path)['status'], 'attempt': job['attempt'], 'artifacts': [str(job_path)]}


def register_commands(subparsers):
    renders = subparsers.add_parser('render').add_subparsers(dest='render_command', required=True)
    quote = renders.add_parser('quote', help='what a remote render of these shots would cost, the queue on one server against the cap (creates nothing)')
    quote.add_argument('--project', required=True); quote.add_argument('--shots', required=True, help='shot:version pairs, e.g. s01:v0008,s02:v0019')
    quote.add_argument('--profile', choices=list(PROFILES), default='final'); quote.add_argument('--samples', type=int)
    quote.set_defaults(handler=lambda a: quote_render(a.project, _shot_versions(a.shots), a.profile, a.samples))
    render = renders.add_parser('submit')
    render.add_argument('--project', required=True); render.add_argument('--shot', required=True); render.add_argument('--version', required=True); render.add_argument('--profile', choices=list(PROFILES), default='layout'); render.add_argument('--frames', help='Comma-separated 0-based frame indices'); render.add_argument('--samples', type=int, help='Profile sample override, included in render fingerprint')
    render.add_argument('--remote', action='store_true', help='render on a rented GPU server (studio gpu enable first; deleted when the queue is empty)')
    render.set_defaults(handler=lambda a: submit_render(a.project,a.shot,a.version,a.profile,[int(x) for x in a.frames.split(',')] if a.frames else None, a.samples, a.remote))
    jobs = subparsers.add_parser('job').add_subparsers(dest='job_command', required=True)
    for name, fn in [('status',job_status),('cancel',cancel_job),('resume',resume_job)]:
        parser = jobs.add_parser(name); parser.add_argument('--project',required=True); parser.add_argument('--job',required=True)
        parser.set_defaults(handler=lambda a,fn=fn: fn(a.project,a.job))
