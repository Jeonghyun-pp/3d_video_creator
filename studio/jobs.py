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
from .common import REPO, StudioError, blender_binary, check_id, file_hash, h264_args, lock, now, read_json, safe_path, stable_hash, write_json
from .project import load_project, project_dir, shot_path

PROFILES = {'layout': (360, 640, 16), 'look': (720, 1280, 64), 'review': (720, 1280, 64), 'final': (1080, 1920, 128)}
# Below 64 spp the measured frame-to-frame flicker rose 13-17 % (photoreal research 01, 2026-10-03).
ANIMATION_MIN_SAMPLES = 64
# Copied next to each job and executed from there, so a running job never sees code edits.
FROZEN = ('render_frames.py', 'scene_tools.py', 'render_profile.py')


def render_settings(project, shot, profile, frames, samples_override=None, env=None):
    """Pure: every value that changes pixels, so all of it lands in the render fingerprint."""
    env = os.environ if env is None else env
    width, height, samples = PROFILES[profile]
    width = max(2, round(project['output']['width'] * height / project['output']['height'] / 2) * 2)
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
    return {'width': width, 'height': height, 'fps': project['output']['fps'], 'samples': samples, 'engine': shot['render'].get('engine'),
            'device': env.get('STUDIO_RENDER_DEVICE', 'GPU'), 'png_depth': '16' if profile == 'final' else '8',
            'adaptive_threshold': .01 if profile == 'final' else .02, 'profile': profile, 'animation': animation}


def find_job(path, job_id):
    matches = list(project_dir(path).glob(f'runs/*/jobs/{check_id(job_id)}/job.json'))
    if len(matches) != 1:
        raise StudioError('INPUT_INVALID', f'Job not found or ambiguous: {job_id}')
    return matches[0]


def worker_alive(job, job_path):
    pid = job.get('pid')
    if not pid:
        return False
    result = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True)
    return result.returncode == 0 and '_worker' in result.stdout and str(job_path) in result.stdout and job.get('worker_token', '') in result.stdout


def start_worker(job_path):
    job = read_json(job_path)
    job['worker_token'] = uuid.uuid4().hex
    job['status'] = 'queued'; job['error'] = None; job['updated_at'] = now()
    job.pop('render_started_at', None); job.pop('heartbeat_unix', None)
    write_json(job_path, job)
    with (job_path.parent / 'worker.log').open('ab') as log:
        process = subprocess.Popen([sys.executable, '-m', 'studio', '_worker', str(job_path), job['worker_token']], cwd=REPO, stdout=log, stderr=log, start_new_session=True)
    # Worker sets PID itself. Do not overwrite a fast worker's final state.
    return process.pid


def submit_render(path, shot_id, version, profile='layout', frames=None, samples_override=None):
    path = project_dir(path)
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
    from .routing import assert_route
    assert_route(shot, 'render')
    if profile == 'final':
        _require_turnaround(path, project, shot, version)
    settings = render_settings(project, shot, profile, frames, samples_override)
    fingerprint = stable_hash({'scene': dependencies, 'settings': settings, 'frames': frames,
                               'renderer': file_hash(REPO / 'studio/blender_ops/render_frames.py'), 'scene_tools': file_hash(REPO / 'studio/blender_ops/scene_tools.py'),
                               'render_profile': file_hash(REPO / 'studio/blender_ops/render_profile.py'), 'worker': file_hash(Path(__file__))})[:24]
    output = shot_path(path, shot_id).parent / 'renders' / fingerprint
    with lock(path / '.project.lock', blocking=False):
        for existing in path.glob('runs/*/jobs/*/job.json'):
            old = read_json(existing)
            if old.get('fingerprint') == fingerprint and old.get('shot_id') == shot_id:
                status = job_status(path, old['job_id'])
                if status['status'] in ('queued', 'running'):
                    return {**status, 'cache_hit': True}
                if status['status'] == 'complete' and verify_render(old):
                    return {**status, 'cache_hit': True}
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
               'cancel_path': str(job_path.parent / 'cancel.request'), 'progress_path': str(job_path.parent / 'progress.json')}
        freeze_renderer(job, job_path)
        write_json(job_path, job)
        with lock(run_path.parent / '.run.lock'):
            run = read_json(run_path)
            run['pending_jobs'].append(job_id); run['updated_at'] = now(); run['status'] = 'running'
            write_json(run_path, run)
        start_worker(job_path)
    return {'project_id': project['project_id'], 'run_id': run['run_id'], 'job_id': job_id, 'status': 'queued', 'cache_hit': False,
            'artifacts': [str(job_path), str(output)]}



def _require_turnaround(path, project, shot, version):
    """Final renders of unreviewed generated/caller-licensed assets or specific real subjects need a human look first."""
    missing = []
    for instance in shot.get('asset_instances', []):
        manifest_path = REPO / 'library' / 'assets' / instance['asset_id'] / instance.get('asset_version', 'v0001') / 'asset.json'
        if not manifest_path.is_file():
            continue
        manifest = read_json(manifest_path)
        source = manifest.get('source', {})
        if (source.get('ai_generated') or source.get('use_status') != 'cleared') and (manifest.get('approval') or {}).get('decision') != 'approved':
            missing.append(f"asset {instance['asset_id']} ({'ai_generated' if source.get('ai_generated') else source.get('use_status')})")
    policy = project.get('route_policy', {})
    if policy.get('turnaround_required', True) and project['brief'].get('subject_mode') == 'specific_real':
        approved = any(r.get('turnaround_approved') is True and r.get('reviewer_kind') == 'human' and r.get('shot_id') == shot['shot_id']
                       and r.get('scene_version') == version for r in (read_json(f) for f in path.glob('reviews/*.json')))
        if not approved:
            missing.append(f"shot {shot['shot_id']} {version} turnaround (specific real subject)")
    if missing:
        raise StudioError('TURNAROUND_APPROVAL_REQUIRED', 'Human turnaround approval missing: ' + '; '.join(missing),
                          recovery='Show front/back/side previews to the user, then record asset approve or review record --turnaround_approved')


def freeze_renderer(job, job_path):
    code = job_path.parent / 'code'
    code.mkdir(parents=True, exist_ok=True)
    hashes = {}
    # A job created before render_profile.py existed keeps its original two-file freeze on resume.
    names = tuple(job['execution_code_hashes']) if job.get('execution_code_hashes') else FROZEN
    for name in names:
        destination = code / name
        if not destination.exists():
            shutil.copy2(REPO / 'studio/blender_ops' / name, destination)
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
    job_path = find_job(path, job_id)
    job = read_json(job_path)
    never_started = job['status'] == 'queued' and not job.get('pid') and (datetime.now(timezone.utc) - datetime.fromisoformat(job['updated_at'])).total_seconds() > 30
    if never_started or (job['status'] in ('queued', 'running') and job.get('pid') and not worker_alive(job, job_path)):
        if job.get('render_started_at'):
            record_render_time(job_path, job, max(0, job.get('heartbeat_unix', time.time()) - job['render_started_at'] + 1))
        job['status'] = 'interrupted'; job['error'] = {'code': 'WORKER_INTERRUPTED', 'message': 'Worker is no longer running; resume preserves valid frames'}
        write_json(job_path, job)
    progress_path = Path(job.get('progress_path', ''))
    progress = read_json(progress_path) if progress_path.is_file() else {'completed_frames': 0, 'total_frames': len(job['frames'])}
    return {'job_id': job['job_id'], 'run_id': job['run_id'], 'project_id': job['project_id'], 'status': job['status'],
            'progress': progress, 'error': job.get('error'), 'attempt': job['attempt'], 'output_dir': job['output_dir'],
            'artifacts': [str(job_path), str(Path(job['output_dir']) / 'render.json')]}


def cancel_job(path, job_id):
    job_path = find_job(path, job_id)
    with lock(job_path.parent / '.lock'):
        job = read_json(job_path)
        if job['status'] == 'complete':
            return job_status(path, job_id)
        Path(job['cancel_path']).touch()
        if worker_alive(job, job_path) and job['status'] == 'running':
            # Let the worker terminate Blender and account elapsed time before releasing its lock.
            deadline = time.monotonic() + 7
            while time.monotonic() < deadline and worker_alive(job, job_path):
                current = read_json(job_path)
                if current['status'] in ('cancelled', 'failed', 'complete'):
                    return job_status(path, job_id)
                time.sleep(.1)
        if worker_alive(job, job_path):
            # Queued or unresponsive worker: verify its dedicated process group before termination.
            try:
                os.killpg(job['pid'], signal.SIGTERM)
            except ProcessLookupError:
                pass
        job = read_json(job_path)
        if job.get('render_started_at'):
            record_render_time(job_path, job, max(0, time.time() - job['render_started_at']))
        job['status'] = 'cancelled'; job['updated_at'] = now()
        write_json(job_path, job)
    return job_status(path, job_id)


def resume_job(path, job_id):
    job_path = find_job(path, job_id)
    with lock(job_path.parent / '.lock'):
        job = read_json(job_path)
        if job['status'] != 'complete' and worker_alive(job, job_path):
            return job_status(path, job_id)
        if job['status'] == 'complete' and verify_render(job):
            return {**job_status(path, job_id), 'cache_hit': True}
        if file_hash(job['scene_path']) != job['scene_sha256']:
            raise StudioError('REVISION_CONFLICT', 'Cannot resume a modified scene snapshot')
        freeze_renderer(job, job_path)
        Path(job['cancel_path']).unlink(missing_ok=True)
        job['attempt'] += 1; job['pid'] = None
        job['render_wall_seconds'] = load_project(path)['limits']['render_wall_minutes'] * 60
        write_json(job_path, job)
        start_worker(job_path)
    return {'job_id': job_id, 'status': 'queued', 'attempt': job['attempt']}


def _run_process(command, job, job_path, deadline, logfile):
    with logfile.open('ab') as log:
        process = subprocess.Popen(command, stdout=log, stderr=log)
        while process.poll() is None:
            if Path(job['cancel_path']).exists() or time.monotonic() > deadline:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
                raise StudioError('CANCELLED' if Path(job['cancel_path']).exists() else 'BUDGET_EXCEEDED', 'Render stopped; completed frames remain reusable', retryable=True)
            job['updated_at'] = now(); job['heartbeat_unix'] = time.time(); write_json(job_path, job)
            time.sleep(.5)
        if process.returncode:
            tail = logfile.read_text(errors='replace')[-2500:]
            raise StudioError('RENDER_FAILED', f'Process exited {process.returncode}: {tail}', retryable=True)


def run_worker(job_path, token):
    job_path = Path(job_path).resolve()
    job = read_json(job_path)
    if job.get('worker_token') != token:
        raise StudioError('REVISION_CONFLICT', 'Worker token does not match job')
    try:
        freeze_renderer(job, job_path)
    except StudioError as error:
        job['status'] = 'failed'; job['error'] = error.as_dict(); write_json(job_path, job)
        return job_status(job['project_dir'], job['job_id'])
    job['pid'] = os.getpid(); job['updated_at'] = now(); write_json(job_path, job)
    output = Path(job['output_dir']); output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    active_started = None
    try:
        # ponytail: one global GPU lock; separate resource locks only when multiple GPUs exist.
        with lock(REPO / '.studio/gpu.lock'):
            if Path(job['cancel_path']).exists():
                raise StudioError('CANCELLED', 'Cancelled before GPU acquisition')
            if file_hash(job['scene_path']) != job['scene_sha256']:
                raise StudioError('REVISION_CONFLICT', 'Scene changed while render was queued')
            job['status'] = 'running'; job['updated_at'] = now(); write_json(job_path, job)
            active_started = time.monotonic()
            job['render_started_at'] = time.time(); job['heartbeat_unix'] = time.time(); write_json(job_path, job)
            run_path = job_path.parents[2] / 'run.json'
            consumed = read_json(run_path).get('elapsed', {}).get('render_wall_seconds', 0)
            remaining = job['render_wall_seconds'] - consumed
            if remaining <= 0:
                raise StudioError('BUDGET_EXCEEDED', 'Run render budget exhausted; increase project limit before resuming')
            deadline = time.monotonic() + remaining
            size = (job['render_settings']['width'], job['render_settings']['height'])
            prior = read_json(output / 'render.json') if (output / 'render.json').is_file() else {}
            def reusable(frame):
                frame_path = output / 'frames' / f'frame_{frame:06d}.png'
                return good_frame(frame_path, size) and (not prior or file_hash(frame_path) == prior.get('frame_sha256', {}).get(str(frame)))
            job['missing_frames'] = [f for f in job['frames'] if not reusable(f)]
            worker_input = job_path.parent / 'render.input.json'; write_json(worker_input, job)
            command = [blender_binary(), '--background', '--factory-startup', '--disable-autoexec', job['scene_path'], '--python-exit-code', '1', '--python', job['renderer_script'], '--', str(worker_input)]
            try:
                _run_process(command, job, job_path, deadline, job_path.parent / 'blender.log')
            except StudioError as error:
                if error.code != 'RENDER_FAILED' or not (str(job['render_settings'].get('engine', '')).startswith('BLENDER_EEVEE') or job['render_settings'].get('device') == 'GPU'):
                    raise
                # Headless EEVEE can abort on macOS. A new homogeneous Cycles sequence is required.
                job['warnings'] = ['Requested renderer failed; retried entire sequence with Cycles CPU. See blender.log.']
                job['render_settings'] = {**job['render_settings'], 'engine': 'CYCLES', 'device': 'CPU'}
                job['missing_frames'] = list(job['frames'])
                write_json(worker_input, job)
                _run_process(command, job, job_path, deadline, job_path.parent / 'blender_fallback.log')
            if not all(good_frame(output / 'frames' / f'frame_{f:06d}.png', size) for f in job['frames']):
                raise StudioError('RENDER_FAILED', 'Missing or invalid rendered PNG frames')
            full = job['frames'] == list(range(job['shot']['duration_frames']))
            clip = output / 'clip.mp4'
            if full:
                temporary = output / 'clip.pending.mp4'
                command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-framerate', str(job['render_settings']['fps']), '-start_number', '0', '-i', str(output / 'frames/frame_%06d.png'), '-frames:v', str(len(job['frames'])), *h264_args(), '-movflags', '+faststart', str(temporary)]
                _run_process(command, job, job_path, deadline, job_path.parent / 'encode.log')
                os.replace(temporary, clip)
            manifest = {'schema_version':1,'status':'complete','project_id':job['project_id'],'shot_id':job['shot_id'],'scene_version':job['scene_version'],
                        'profile':job['profile'],'scene_sha256':job['scene_sha256'],'fingerprint':job['fingerprint'],**job['render_settings'],
                        'frame_count':len(job['frames']),'frame_indices':job['frames'],'frame_sha256':{str(f):file_hash(output/'frames'/f'frame_{f:06d}.png') for f in job['frames']},'full_sequence':full,'frames_dir':str(output/'frames'),
                        'clip_path':str(clip) if full else None,'clip_sha256':file_hash(clip) if full else None,'anchors_path':str(output/'anchors.json'),
                        'execution_code_hashes':job['execution_code_hashes'],'renderer_actual':read_json(output/'renderer_actual.json'), 'warnings':job.get('warnings',[]),'elapsed_seconds':round(time.monotonic()-started,3),'rendered_frames':len(job['missing_frames']),'created_at':now()}
            write_json(output / 'render.json', manifest)
            job['status'] = 'complete'; job['error'] = None
    except Exception as exc:
        error = exc if isinstance(exc, StudioError) else StudioError('RENDER_FAILED', str(exc), retryable=True)
        job['status'] = 'cancelled' if error.code == 'CANCELLED' else 'failed'; job['error'] = error.as_dict()
    job['updated_at'] = now(); write_json(job_path, job)
    if active_started:
        record_render_time(job_path, job, time.monotonic()-active_started)
    run_path = job_path.parents[2] / 'run.json'
    with lock(run_path.parent / '.run.lock'):
        run = read_json(run_path)
        if job['status'] == 'complete':
            run['pending_jobs'] = [j for j in run['pending_jobs'] if j != job['job_id']]
            run['completed_operations'].append({'operation':'render','job_id':job['job_id'],'input_hash':job['scene_sha256'],'output_hash':file_hash(output/'render.json')})
        run['updated_at'] = now(); run['last_error'] = job.get('error'); write_json(run_path, run)
    from .project import status_project
    status_project(job['project_dir'])
    return job_status(job['project_dir'], job['job_id'])


def register_commands(subparsers):
    render = subparsers.add_parser('render').add_subparsers(dest='render_command', required=True).add_parser('submit')
    render.add_argument('--project', required=True); render.add_argument('--shot', required=True); render.add_argument('--version', required=True); render.add_argument('--profile', choices=list(PROFILES), default='layout'); render.add_argument('--frames', help='Comma-separated 0-based frame indices'); render.add_argument('--samples', type=int, help='Profile sample override, included in render fingerprint')
    render.set_defaults(handler=lambda a: submit_render(a.project,a.shot,a.version,a.profile,[int(x) for x in a.frames.split(',')] if a.frames else None, a.samples))
    jobs = subparsers.add_parser('job').add_subparsers(dest='job_command', required=True)
    for name, fn in [('status',job_status),('cancel',cancel_job),('resume',resume_job)]:
        parser = jobs.add_parser(name); parser.add_argument('--project',required=True); parser.add_argument('--job',required=True)
        parser.set_defaults(handler=lambda a,fn=fn: fn(a.project,a.job))
