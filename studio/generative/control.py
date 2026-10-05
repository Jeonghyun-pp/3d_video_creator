"""Hybrid control pass: depth / clay / canny videos of the Blender motion pass for video-to-video restyle.

Hybrid principle (routing.py): Blender owns structure, camera and timing; the model only restyles the
look. These controls are the structure the model must follow, and the clay pass is the reference that
qa_generative.structure() measures the generated clip against.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from ..common import REPO, StudioError, blender_binary, check_id, file_hash, h264_args, h264_encoder_args, BT709_CHAIN, now, read_json, run_command, safe_path, stable_hash, write_json
from ..project import load_project, load_shot, project_dir, route_of, shot_path

KINDS = ('depth', 'clay', 'canny', 'normal', 'lines', 'id')
DEFAULT_KINDS = ('depth', 'clay', 'canny')
# Canny is derived from the clay render. normal / lines (Freestyle) / id ride along with the clay render as
# render passes - geometric structure that does not depend on shading; the other kinds are their own pass.
SOURCE_PASS = {'depth': 'depth', 'clay': 'clay', 'canny': 'clay', 'normal': 'normal', 'lines': 'lines', 'id': 'id'}
CANNY_FILTER = 'edgedetect=low=0.1:high=0.3'
CONTROL_HEIGHT = 1280   # 720x1280 for 9:16, the input size video-to-video models take
OPS = REPO / 'studio' / 'blender_ops'
FROZEN = ('control_pass.py', 'scene_tools.py', 'scene_roles.py', 'scene_geometry.py')   # run from a copy, so code edits never race a job


def _frame_count(video):
    probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-count_packets', '-show_entries', 'stream=nb_read_packets',
                            '-of', 'json', str(video)], capture_output=True, text=True)
    if probe.returncode:
        raise StudioError('QA_FAILED', probe.stderr[-1000:])
    return int(json.loads(probe.stdout)['streams'][0]['nb_read_packets'])


def _valid(directory, fingerprint):
    manifest = directory / 'control.json'
    if not manifest.is_file():
        return None
    data = read_json(manifest)
    if data.get('fingerprint') != fingerprint:
        return None
    for entry in data['files'].values():
        if not Path(entry['path']).is_file() or file_hash(entry['path']) != entry['sha256']:
            return None
    anchors = directory / 'anchors.json'
    if not anchors.is_file() or file_hash(anchors) != data.get('anchors_sha256'):
        return None
    return data


def latest_control(path, shot):
    """The newest control pass (by created_at) built from the shot's current scene version, or None.
    Fingerprint directory names are hashes: their order says nothing about which pass is newer."""
    rows = []
    for file in (shot_path(path, shot['shot_id']).parent / 'control').glob('*/control.json'):
        data = read_json(file)
        if data.get('scene_version') == shot['scene_version']:
            rows.append(data)
    return max(rows, key=lambda d: d.get('created_at', '')) if rows else None


def build_control(project, shot_id, kinds=DEFAULT_KINDS, version=None, height=CONTROL_HEIGHT):
    path = project_dir(project)
    meta = load_project(path)
    shot = load_shot(path, shot_id)
    kinds = sorted(set(kinds))
    if not kinds or set(kinds) - set(KINDS):
        raise StudioError('INPUT_INVALID', f'Control kinds must be a nonempty subset of {KINDS}: {kinds}')
    version = check_id(version or shot['scene_version'] or '')
    scene = safe_path(shot_path(path, shot_id).parent, f'versions/{version}/scene.blend')
    if not scene.is_file():
        raise StudioError('INPUT_INVALID', f'Scene version missing: {version}', recovery='Build the shot first (shot build).')
    if not isinstance(height, int) or height < 16:
        raise StudioError('INPUT_INVALID', 'Control height must be an integer >= 16')
    out = meta['output']
    width = max(2, round(out['width'] * height / out['height'] / 2) * 2)
    height = height // 2 * 2
    frames = shot['duration_frames']
    labels = [{k: label[k] for k in ('label_id', 'anchor', 'start_frame', 'end_frame')} for label in shot['labels']]
    passes = sorted({SOURCE_PASS[k] for k in kinds})
    # Everything that changes pixels or anchors: scene, code, kinds, size, timing, labels.
    fingerprint = stable_hash({'scene': file_hash(scene), 'kinds': kinds, 'control_pass': file_hash(OPS / 'control_pass.py'),
                               'scene_tools': file_hash(OPS / 'scene_tools.py'), 'scene_roles': file_hash(OPS / 'scene_roles.py'), 'scene_geometry': file_hash(OPS / 'scene_geometry.py'), 'frame_count': frames, 'fps': out['fps'],
                               'size': [width, height], 'labels': labels, 'canny': CANNY_FILTER})[:24]
    root = shot_path(path, shot_id).parent / 'control'
    directory = root / fingerprint
    warnings = [] if route_of(shot)['mode'] == 'hybrid' else [f"route is {route_of(shot)['mode']}, not hybrid; control pass built anyway"]
    cached = _valid(directory, fingerprint)
    if cached:
        return {'status': 'reused', 'shot_id': shot_id, **_result(cached, directory), 'warnings': warnings}
    if directory.exists():
        shutil.rmtree(directory)   # incomplete or tampered: never reuse a partial control
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.building-', dir=root))
    try:
        for name in FROZEN:
            shutil.copy2(OPS / name, staging / name)
        job = {'output_dir': str(staging), 'frame_count': frames, 'width': width, 'height': height, 'fps': out['fps'],
               'passes': passes, 'labels': labels}
        write_json(staging / 'job.json', job)
        run_command([blender_binary(), '--background', '--factory-startup', '--disable-autoexec', str(scene), '--python-exit-code', '1',
                     '--python', str(staging / 'control_pass.py'), '--', str(staging / 'job.json')], staging / 'control.log', timeout=7200)
        control_meta = read_json(staging / 'control_meta.json')
        for name in passes:
            rendered = len(list((staging / name).glob('frame_*.png')))
            if rendered != frames:
                raise StudioError('SCENE_INVALID', f'{name} pass rendered {rendered}/{frames} frames')
        files = {}
        for kind in kinds:
            pattern = str(staging / SOURCE_PASS[kind] / 'frame_%06d.png')
            target = staging / f'{kind}.mp4'
            codec = (['-vf', f'format=gray,{CANNY_FILTER},{BT709_CHAIN}', *h264_encoder_args()] if kind == 'canny' else h264_args())
            run_command(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-framerate', out['fps'], '-start_number', 0, '-i', pattern,
                         *codec, '-r', out['fps'], target], staging / f'{kind}.ffmpeg.log', timeout=1800)
            count = _frame_count(target)
            if count != frames:
                raise StudioError('QA_FAILED', f'{kind}.mp4 has {count}/{frames} frames')
            files[kind] = {'path': str(directory / target.name), 'sha256': file_hash(target), 'frames': count}
        data = {'schema_version': 1, 'fingerprint': fingerprint, 'shot_id': shot_id, 'scene_version': version, 'near': control_meta['near'],
                'far': control_meta['far'], 'width': width, 'height': height, 'fps': out['fps'], 'frames': frames, 'kinds': kinds,
                'depth_encoding': control_meta['depth_encoding'], 'canny_filter': CANNY_FILTER, 'files': files,
                'anchors_path': str(directory / 'anchors.json'), 'anchors_sha256': file_hash(staging / 'anchors.json'), 'created_at': now()}
        if 'clay' in kinds:  # how much structure the model will be handed (qa_generative.richness); fixed by the fingerprint
            from ..qa_generative import richness
            data['richness'] = {k: v for k, v in richness(staging / 'clay.mp4').items() if k != 'per_frame'}
        if 'lines' in kinds:  # the same measure on the geometric line pass (shading-independent structure)
            from ..qa_generative import richness
            data['richness_lines'] = {k: v for k, v in richness(staging / 'lines.mp4').items() if k != 'per_frame'}
        write_json(staging / 'control.json', data)
        staging.rename(directory)
    except BaseException:
        if staging.exists():   # keep logs for inspection; never promoted
            staging.rename(root / ('failed_' + staging.name.removeprefix('.building-')))
        raise
    return {'status': 'complete', 'shot_id': shot_id, **_result(data, directory), 'warnings': warnings}


def _result(data, directory):
    return {'scene_version': data['scene_version'], 'fingerprint': data['fingerprint'], 'control_dir': str(directory),
            'near': data['near'], 'far': data['far'], 'files': data['files'], 'anchors_path': data['anchors_path'],
            'artifacts': [str(directory / 'control.json')] + [entry['path'] for entry in data['files'].values()]}


def register_control(subparsers):
    """Add `control` under a `generate` parent: generate control --project P --shot S [--kinds depth,clay,canny]."""
    parser = subparsers.add_parser('control', help='Depth/clay/canny control videos of the Blender motion pass (hybrid shots)')
    parser.add_argument('--project', required=True)
    parser.add_argument('--shot', required=True)
    parser.add_argument('--kinds', default=','.join(DEFAULT_KINDS), help=f"Comma-separated subset of {','.join(KINDS)} (default {','.join(DEFAULT_KINDS)}; add lines,normal,id for geometric structure)")
    parser.add_argument('--version')
    parser.add_argument('--height', type=int, default=CONTROL_HEIGHT)
    parser.set_defaults(handler=lambda a: build_control(a.project, a.shot, [k.strip() for k in a.kinds.split(',') if k.strip()], a.version, a.height))
