"""The render worker: one process per job, frozen render code, frames and the clip. Its source is part of the render
fingerprint (jobs.RENDER_CODE); job bookkeeping and gates stay in jobs.py, so editing those never re-renders."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time

from .common import REPO, StudioError, blender_binary, file_hash, h264_args, lock, now, read_json, stable_hash, write_json
from .jobs import freeze_renderer, good_frame, job_status, record_render_time

# A renderer that crashed (headless EEVEE on macOS, a Metal device abort) gets one homogeneous retry on Cycles CPU.
# A Python error in the scene or the render script is not a renderer failure: the same error would come back.
SCRIPT_ERROR = 'Traceback (most recent call last)'


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
                settings = job['render_settings']
                renderer = str(settings.get('engine', '')).startswith('BLENDER_EEVEE') or settings.get('device') == 'GPU'
                log = (job_path.parent / 'blender.log').read_text(errors='replace')
                if error.code != 'RENDER_FAILED' or not renderer or SCRIPT_ERROR in log[-20000:] or settings.get('engine') == 'BLENDER_WORKBENCH':
                    raise
                # A new homogeneous Cycles CPU sequence, under its own fingerprint: these are not the requested pixels,
                # so they never answer for the requested settings (submit reports them as a fallback).
                fallback = {**settings, 'engine': 'CYCLES', 'device': 'CPU'}
                job['fallback_from'] = job['fingerprint']
                job['fingerprint'] = stable_hash({'fallback_from': job['fingerprint'], 'settings': fallback})[:24]
                output = output.parent / job['fingerprint']; output.mkdir(parents=True, exist_ok=True)
                job['output_dir'] = str(output)
                job['warnings'] = ['Requested renderer failed; retried entire sequence with Cycles CPU under its own fingerprint. See blender.log.']
                job['render_settings'] = fallback
                job['missing_frames'] = list(job['frames'])
                write_json(worker_input, job); write_json(job_path, job)
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
