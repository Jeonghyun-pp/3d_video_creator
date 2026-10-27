"""generate clip / select: one approved generative or hybrid shot -> a 30 fps BT.709 clip the edit can use.

The request (model, prompt bytes, input hashes, seed, take) is the cache key, so re-running never
pays twice; bumping `take` is the only way to ask for a new paid generation of the same request.
"""
from __future__ import annotations

import base64
import json
import math
import mimetypes
import tempfile
from copy import deepcopy
from pathlib import Path

from ..common import BT709_CHAIN, StudioError, check_id, file_hash, h264_encoder_args, lock, now, read_json, safe_path, source_matrix, stable_hash, write_json
from ..project import load_project, load_shot, project_dir, shot_path, validate_shot
from ..routing import assert_route
from .fal_client import paid_call

from ..routing import MODELS
ENDPOINTS = {(model, operation): endpoint for model, entry in MODELS.items() for operation, endpoint in entry['operations'].items()}


def _uri(path):
    kind = mimetypes.guess_type(Path(path).name)[0] or 'application/octet-stream'
    return f'data:{kind};base64,' + base64.b64encode(Path(path).read_bytes()).decode()


def _inputs(path, spec, kind):
    return [path / i['path'] for i in spec.get('inputs', []) if i['kind'] == kind]


SEEDANCE_ASPECTS = ('21:9', '16:9', '4:3', '1:1', '3:4', '9:16')   # fal seedance-2.5 reference-to-video, 2026-10-07
ASPECT_MATCH = 0.03   # a named ratio this close to the output is asked for; farther, the model picks ('auto') and it is said


def nearest_aspect(width, height, allowed):
    """(ratio name, relative error) of the allowed ratio closest to width:height (log distance)."""
    want = width / height
    def ratio(name):
        a, b = (float(x) for x in name.split(':'))
        return a / b
    best = min(allowed, key=lambda n: abs(math.log(ratio(n) / want)))
    return best, abs(ratio(best) / want - 1)


def _seedance_aspect(project):
    name, error = nearest_aspect(project['output']['width'], project['output']['height'], SEEDANCE_ASPECTS)
    return name if error <= ASPECT_MATCH else 'auto'


# The provider's own prompt rewriter, switched off for every model that has one: the prompt the user approved is the
# prompt sent (fal schemas read 2026-10-27, no paid call: veo3.1 text-to-video auto_fix defaults to true, image-to-video
# and first-last-frame to false; both Wan VACE endpoints' enable_prompt_expansion to false; seedance-2.5 reference,
# kling o1 edit and luma ray-2 modify have no such field). A model added with such a field is a row here.
PROMPT_REWRITE_OFF = {
    'veo-3.1': {'auto_fix': False},
    'wan-2.2-vace': {'enable_prompt_expansion': False},
    'wan-vace-inpaint': {'enable_prompt_expansion': False},
}
# The models whose endpoint takes what to avoid as its own field (negative_prompt; the same schemas). A spec that carries
# one for a model without the field is refused, never dropped: the request would not say what the user approved.
NEGATIVE_FIELD = {'veo-3.1': 'negative_prompt', 'wan-2.2-vace': 'negative_prompt', 'wan-vace-inpaint': 'negative_prompt'}


def build_arguments(path, project, spec, prompt, uri=None):
    """Endpoint-specific arguments from one route spec (a table of adapters, not per-shot code), the model's prompt
    rewriter off (PROMPT_REWRITE_OFF) and the spec's negative prompt in the model's own field (NEGATIVE_FIELD).
    uri: how a file becomes an argument (data URI when sending; its hash when fingerprinting the request)."""
    args = {**_adapter_arguments(path, project, spec, prompt, uri), **PROMPT_REWRITE_OFF.get(spec['model'], {})}
    if spec.get('negative_prompt'):
        field = NEGATIVE_FIELD.get(spec['model'])
        if field is None:
            raise StudioError('INPUT_INVALID', f"{spec['model']} takes no negative prompt: say what holds as a positive sentence in the prompt")
        args[field] = spec['negative_prompt']
    return args


def _adapter_arguments(path, project, spec, prompt, uri=None):
    _uri = uri or globals()['_uri']
    model, operation = spec['model'], spec['operation']
    portrait = project['output']['height'] > project['output']['width']
    aspect = '9:16' if portrait else '16:9'
    seconds = spec['duration_seconds'] + spec.get('trim_start_seconds', 0)
    previs = _inputs(path, spec, 'previs')
    references = _inputs(path, spec, 'reference_image')
    first = _inputs(path, spec, 'first_frame')
    common = {'prompt': prompt}
    if spec.get('seed') is not None:
        common['seed'] = spec['seed']
    if model == 'veo-3.1':
        allowed = [4, 6, 8]
        duration = next((d for d in allowed if d >= seconds), None)
        if duration is None:
            raise StudioError('INPUT_INVALID', 'Veo clips are at most 8 s; split the shot')
        args = {**common, 'duration': f'{duration}s', 'resolution': '1080p', 'aspect_ratio': aspect, 'generate_audio': False}
        if operation == 'image_to_video':
            if not first:
                raise StudioError('ROUTE_INPUT_MISSING', 'image_to_video needs a first_frame input')
            args['image_url'] = _uri(first[0])
        return args
    if not previs:
        raise StudioError('ROUTE_INPUT_MISSING', f'{model} needs the Blender motion pass as a previs input')
    if model == 'seedance-2.5':
        return {**common, 'task': 'reference', 'video_urls': [_uri(previs[0])], 'image_urls': [_uri(r) for r in references[:4]],
                'duration': str(min(8, max(4, math.ceil(seconds)))), 'resolution': '720p', 'generate_audio': False,
                # the output's own ratio when the model has it (2026-10-07: 'auto' chose 4:3 for a 3:2 shot and the crop
                # to 3:2 moved the framing); otherwise the model picks and generate_clip records the crop
                'aspect_ratio': _seedance_aspect(project)}
    if model == 'wan-2.2-vace':
        control = _inputs(path, spec, 'control')
        source = control[0] if control else previs[0]
        return {**common, 'video_url': _uri(source), 'preprocess': not control, 'match_input_num_frames': True, 'resolution': '720p'}
    if model == 'wan-vace-inpaint':
        mask = _inputs(path, spec, 'mask')
        if not mask:
            raise StudioError('ROUTE_INPUT_MISSING', 'wan-vace-inpaint needs the mask video of screen.generate_only (generate inputs)')
        return {**common, 'video_url': _uri(previs[0]), 'mask_video_url': _uri(mask[0]), 'match_input_num_frames': True,
                'match_input_frames_per_second': True, 'resolution': '720p',
                **({'ref_image_urls': [_uri(r) for r in references[:4]]} if references else {})}
    if model == 'kling-o1-edit':
        return {**common, 'video_url': _uri(previs[0]), **({'image_urls': [_uri(r) for r in references[:4]]} if references else {})}
    if model == 'luma-ray-modify':
        return {**common, 'video_url': _uri(previs[0]), 'mode': 'adhere_1', **({'image_url': _uri(references[0])} if references else {})}
    raise StudioError('ROUTE_MODEL_UNKNOWN', f'No fal adapter for {model}')


def _probe(video):
    from ..audio import run_media
    data = json.loads(run_media(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-count_frames', '-show_entries',
                                 'stream=width,height,avg_frame_rate,nb_read_frames,color_space:format=duration', '-of', 'json', str(video)]))
    stream = data['streams'][0]
    num, den = stream['avg_frame_rate'].split('/')
    return {'width': stream['width'], 'height': stream['height'], 'fps': float(num) / float(den), 'frames': int(stream['nb_read_frames']),
            'duration': float(data['format']['duration']), 'color_space': stream.get('color_space')}


RETIME_DEFAULT = 'interpolate'


def retime(raw, out, frame_count, width, height, trim=0.0, method=RETIME_DEFAULT):
    """Any source fps -> exactly frame_count frames at 30 fps, project size, BT.709 tagged.

    interpolate (default): motion-interpolated frames at the exact 30 fps instants (RIFE, _interpolate_frames).
    duplicate: nearest source frame - a 24 fps take repeats every fourth frame (judder); only for a deliberately
    choppy look. A take with exactly frame_count frames (Wan match_input_num_frames) is mapped frame to frame.
    Why (2026-10-08): 'duplicate' was the default and every 24 fps take stuttered; ffmpeg's minterpolate smeared edges."""
    from ..audio import run_media
    source = _probe(raw)
    if source['frames'] == frame_count and not trim:
        # Frame-matched output (e.g. Wan match_input_num_frames at its own fps): map frame to frame, not by time.
        method, raw_rate = 'frame_match', f'setpts=N/(30*TB),fps=30'
    else:
        raw_rate = None
    if raw_rate is None and source['duration'] - trim + 1e-3 < frame_count / 30:
        raise StudioError('GENERATION_TOO_SHORT', f"Generated {source['duration']:.2f}s (trim {trim}s) < shot {frame_count / 30:.2f}s")
    if method not in ('interpolate', 'duplicate', 'frame_match'):
        raise StudioError('INPUT_INVALID', f'retime {method!r}: interpolate or duplicate')
    fit = (f"scale={width}:{height}:force_original_aspect_ratio=increase:in_color_matrix={source_matrix(source['color_space'])}:in_range=tv,"
           f"crop={width}:{height},setsar=1,{BT709_CHAIN}")
    if method == 'interpolate':
        with tempfile.TemporaryDirectory(prefix='retime-') as tmp:
            frames = _interpolate_frames(raw, source, Path(tmp), frame_count, trim)
            run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-framerate', '30', '-start_number', '1', '-i', str(frames / '%08d.png'),
                       '-vf', fit.replace(f"in_color_matrix={source_matrix(source['color_space'])}:in_range=tv", 'in_range=pc'),
                       '-frames:v', str(frame_count), *h264_encoder_args(), '-movflags', '+faststart', str(out)])
    else:
        rate = raw_rate or 'fps=30:round=near'
        head = '' if raw_rate else f'trim=start={trim},setpts=PTS-STARTPTS,'
        run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(raw), '-an', '-vf', f'{head}{rate},{fit}', '-frames:v', str(frame_count),
                   *h264_encoder_args(), '-movflags', '+faststart', str(out)])
    result = _probe(out)
    if result['frames'] != frame_count:
        raise StudioError('MISSING_FRAMES', f"Retimed clip has {result['frames']} frames, expected {frame_count}")
    return source


def _interpolate_frames(raw, source, tmp, frame_count, trim):
    """RGB frames 00000001.png... at the 30 fps instants of the shot, interpolated by RIFE from the source frames.
    RIFE's -n N puts output i at source position i * M / N; M source frames are taken so that M * 30 / fps is whole
    (24 fps: a multiple of 4), so every output lands exactly on a 30 fps instant."""
    from ..audio import run_media
    from ..common import rife_binary
    binary, model = rife_binary()
    fps = source['fps']
    first = round(trim * fps)
    available = source['frames'] - first
    need = math.ceil(frame_count * fps / 30) + 1
    count = next((m for m in range(need, need + 8) if m <= available and abs(m * 30 / fps - round(m * 30 / fps)) < 1e-6), min(need, available))
    target = round(count * 30 / fps)
    if target < frame_count:
        raise StudioError('GENERATION_TOO_SHORT', f'{count} source frames give {target} at 30 fps, fewer than the shot\'s {frame_count}')
    src, dst = tmp / 'src', tmp / 'dst'
    src.mkdir(); dst.mkdir()
    run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(raw), '-an', '-vf',
               f"trim=start_frame={first}:end_frame={first + count},setpts=PTS-STARTPTS,"
               f"scale=in_color_matrix={source_matrix(source['color_space'])}:in_range=tv,format=rgb24",
               '-fps_mode', 'passthrough', '-start_number', '1', str(src / '%08d.png')])
    run_media([str(binary), '-i', str(src), '-o', str(dst), '-m', str(model), '-n', str(target), '-f', '%08d.png'])
    if len(list(dst.glob('*.png'))) < frame_count:
        raise StudioError('RETIME_FAILED', f'RIFE wrote {len(list(dst.glob("*.png")))} frames, expected {target}')
    return dst


def _padded(path, spec, directory, seconds):
    """A copy of the spec whose video inputs hold their last frame for `seconds` more (models with a minimum output
    length); retime keeps only the shot's frames, so the generated tail is dropped."""
    if seconds <= 0:
        return spec
    from ..audio import run_media
    directory.mkdir(parents=True, exist_ok=True)
    send = deepcopy(spec)
    for item in send.get('inputs', []):
        if item['kind'] in ('previs', 'control', 'mask'):
            out = directory / Path(item['path']).name
            run_media(['ffmpeg', '-v', 'error', '-y', '-i', str(path / item['path']), '-vf', f'tpad=stop_mode=clone:stop_duration={seconds}',
                       '-c:v', 'libx264', '-crf', '16', '-pix_fmt', 'yuv420p', '-an', str(out)])
            item['path'] = str(out)   # absolute: path / absolute == absolute
    return send


def _billed_output_seconds(spec):
    from .review import MIN_INPUT_SECONDS
    """Seconds the provider generates (and bills): its duration options round up."""
    seconds = spec['duration_seconds'] + spec.get('trim_start_seconds', 0)
    if spec['model'] == 'seedance-2.5':
        return min(8, max(4, math.ceil(seconds)))
    if spec['model'] == 'veo-3.1':
        return next((d for d in (4, 6, 8) if d >= seconds), 8)
    if spec['model'] == 'wan-vace-inpaint':   # billed per 16 frames, and it keeps the input's 30 fps frame count
        return round(max(seconds, MIN_INPUT_SECONDS[spec['model']]) * 30 / 16, 3)
    return seconds


def _input_seconds(path, spec):
    videos = _inputs(path, spec, 'previs') + _inputs(path, spec, 'control')
    return _probe(videos[0])['duration'] if videos and spec['operation'] == 'video_to_video' else 0.0


def _parts_and_light(path, shot, control, baseline, clip):
    """Per part (declared key parts and kept parts): did the take keep its clay edges (qa_generative.parts, masks from
    generative/keep.py); and the light: the angle between the light fitted on the Blender look render and on the take
    (qa_generative.light_change, needs the control 'normal' kind). Free; what cannot be measured is said, not guessed."""
    from ..qa_generative import PART_SAMPLES, WIDTH, light_change, parts, sample_frames
    from .inputs import latest_complete_render
    from .keep import build_keep_masks
    ids = list(dict.fromkeys([k['id'] for k in shot.get('key_parts', [])] + list((shot.get('screen') or {}).get('keep') or [])))
    part_report = {'parts': {}, 'lost': [], 'note': None}
    if ids:
        try:   # one Workbench pass: every part its own colour, only the frames parts() samples, at about its width
            out = load_project(path)['output']
            masks = build_keep_masks(path, shot['shot_id'], ids, frames=sample_frames(shot['duration_frames'], PART_SAMPLES), split=True,
                                     height=max(16, round(WIDTH * out['height'] / out['width'])))['patterns']
            part_report['parts'] = parts(baseline, clip, masks)
            part_report['lost'] = sorted(i for i, row in part_report['parts'].items() if row['lost'])
        except StudioError as error:
            part_report['note'] = f'not measured: {error.message[:200]}'
    else:
        part_report['note'] = 'no key or kept parts declared'
    normal = (control or {}).get('files', {}).get('normal', {}).get('path')
    render = latest_complete_render(path, shot)
    if normal and Path(normal).is_file() and render:
        light = light_change(normal, render['clip_path'], clip)
    else:
        light = {'angle_deg': None, 'note': 'needs the control normal pass (generate control --kinds depth,clay,normal) and a look render'}
    return part_report, light


def generate_clip(path, shot_id, allow_paid=False, max_usd=None):
    path = project_dir(path)
    project = load_project(path)
    shot = load_shot(path, shot_id)
    route = assert_route(shot, 'generate', path)
    spec = route['generative']
    endpoint = ENDPOINTS.get((spec['model'], spec['operation']))
    if endpoint is None:
        raise StudioError('ROUTE_MODEL_MISMATCH', f"No endpoint for {spec['model']} {spec['operation']}")
    prompt = safe_path(path, spec['prompt_ref']).read_text(encoding='utf-8').strip()
    from .review import pad_seconds, request_fingerprint
    fp = request_fingerprint(path, shot)   # the same value the user's approval is bound to (routing.assert_route)
    request = fp['request']
    key = fp['fingerprint'][:16]
    directory = shot_path(path, shot_id).parent / 'generated' / key
    if (directory / 'clip.json').is_file():
        return {**read_json(directory / 'clip.json'), 'reused': True}
    send = _padded(path, spec, directory / 'request' / 'padded', pad_seconds(spec))
    arguments = build_arguments(path, project, send, prompt)
    record = paid_call(endpoint, arguments, directory / 'request', project_id=project['project_id'], allow_paid=allow_paid,
                       max_usd=max_usd, budget_usd=project.get('route_policy', {}).get('budget_usd'),
                       duration_seconds=_billed_output_seconds(spec), input_seconds=_input_seconds(path, send))
    videos = [f for f in record['files'] if Path(f['path']).suffix.lower() in ('.mp4', '.mov', '.webm')]
    if not videos:
        raise StudioError('GENERATION_REJECTED', f'{endpoint} returned no video file')
    raw = directory / 'raw' / Path(videos[0]['path']).name
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(Path(videos[0]['path']).read_bytes())
    clip = directory / 'clip.mp4'
    source = retime(raw, clip, shot['duration_frames'], project['output']['width'], project['output']['height'],
                    spec.get('trim_start_seconds', 0.0), spec.get('retime', RETIME_DEFAULT))
    native = source['height'] >= project['output']['height'] or source['width'] >= project['output']['width']
    crop = abs((source['width'] / source['height']) / (project['output']['width'] / project['output']['height']) - 1)
    aspect_qa = {'source': [source['width'], source['height']], 'output': [project['output']['width'], project['output']['height']],
                 'crop_share': round(crop, 4), 'warnings': [] if crop <= ASPECT_MATCH else [
                     f"ASPECT_CROPPED: the take is {source['width']}x{source['height']}, the shot {project['output']['width']}x"
                     f"{project['output']['height']}; retime cropped {crop:.0%} and the framing moved - compare the take, not only the crop"]}
    manifest = {'schema_version': 1, 'status': 'complete', 'shot_id': shot_id, 'route': route['mode'], 'key': key, 'request': request,
                'scene_version': shot['scene_version'] if route['mode'] == 'hybrid' else None,
                'profile': 'final' if native else 'preview', 'frame_count': shot['duration_frames'], 'fps': 30,
                'clip_path': str(clip), 'clip_sha256': file_hash(clip), 'raw_sha256': file_hash(raw),
                'source': {k: source[k] for k in ('width', 'height', 'fps', 'frames', 'duration')}, 'retime': spec.get('retime', RETIME_DEFAULT),
                'endpoint': endpoint, 'request_id': record['request_id'], 'estimated_usd': record['estimated_usd'],
                'ai_generated': True, 'use_status': 'review_only', 'created_at': now()}
    if route['mode'] == 'hybrid':   # a take paid on an older build than the shot, softened by the user: said with the take
        from ..routing import scene_current
        softened = scene_current(path, shot)
        if softened:
            manifest.setdefault('warnings', []).append(softened)
    if route['mode'] == 'hybrid':
        # Structure must match the Blender motion pass; failing is the default and sends the shot back to Blender.
        from ..qa_generative import structure
        previs = _inputs(path, spec, 'previs')[0]
        from .control import latest_control
        control = latest_control(path, shot)
        anchors = None
        if control and control.get('anchors_path'):
            anchors = read_json(control['anchors_path']).get('frames')
        # Judge structure against the uniform grey clay of this scene version (the gate was calibrated on it);
        # the role-coloured previs is the fallback when no control pass exists.
        clay = control['files'].get('clay', {}).get('path') if control else None
        baseline = clay if clay and Path(clay).is_file() else previs
        report = structure(baseline, clip, anchors=anchors)
        report['baseline'] = 'control_clay' if baseline == clay else 'previs'
        write_json(directory / 'structure.json', report)
        manifest['structure_qa'] = {k: report[k] for k in ('passed', 'reasons')}
        manifest['structure_qa'].update({k: report['iou'].get(k) for k in ('preservation', 'extra')})
        if report['passed'] and report.get('anchors_2d'):
            write_json(directory / 'anchors_2d.json', {'schema_version': 1, 'frames': report['anchors_2d']})
        manifest['parts_qa'], manifest['light_qa'] = _parts_and_light(path, shot, control, baseline, clip)
    # warnings for the person who picks the take; whether the take may be used is the role's call (policy.judge)
    from ..qa_generative import flicker, judder, morph, text
    from .policy import judge, policy_for
    manifest['qa'] = {'structure': manifest.get('structure_qa'), 'parts': manifest.get('parts_qa'), 'light': manifest.get('light_qa'), 'aspect': aspect_qa,
                      **{name: {k: v for k, v in check(clip).items() if k != 'frames'} for name, check in
                         (('flicker', flicker), ('morph', morph), ('text', text), ('judder', judder))}}
    from ..look_style import check as look_check, style_for
    style_name = style_for(shot, read_json(path / 'style.json') if (path / 'style.json').is_file() else {})
    if style_name:   # density/brightness against the project's look style: a warning for the person choosing the take
        try:
            manifest['qa']['look_style'] = look_check(style_name, clip)
        except StudioError as error:
            manifest['qa']['look_style'] = {'error': error.code, 'warnings': []}
    from .policy import project_policy
    manifest['policy'] = judge(manifest, policy_for(shot, project_policy(path)))
    write_json(directory / 'clip.json', manifest)
    artifacts = [str(clip), str(directory / 'clip.json')]
    if route['mode'] == 'hybrid' or (shot.get('screen') or {}).get('generate_only') or (shot.get('scene') or {}).get('decals'):
        # Blender pixels go back in now (free), only where they must: lost parts, declared keeps, labelled parts, decals
        # (keep.auto_keep); a missing look render is said
        from .keep import auto_keep
        try:
            result = auto_keep(path, shot_id, key)
            manifest = read_json(directory / 'clip.json')
            if result.get('kept'):
                artifacts.append(result['kept']['path'])
        except StudioError as error:
            manifest.setdefault('warnings', []).append(f'KEEP_NOT_APPLIED: {error.message}')
            write_json(directory / 'clip.json', manifest)
    return {**manifest, 'reused': False, 'artifacts': artifacts}


# How a builder's output reads in a clay previs; the prompt maps these shapes to what they are.
SHAPE_WORDS = {'loft': 'long rounded body', 'wing': 'thin wing-shaped surface', 'revolve': 'round turned part', 'sweep': 'tube',
               'box': 'block', 'profile': 'straight beam', 'wall': 'flat panel', 'mirror': 'mirrored copy', 'asset': 'detailed part',
               'toothed_ring': 'toothed ring', 'casting': 'cast housing', 'subd': 'molded cover'}   # every builder has a word (tests/test_generative_clip: no silent 'part')
# A profile's shape decides what it looks like, not the builder: an extruded gear outline is a gear, not a beam.
PROFILE_WORDS = {'gear': 'gear', 'internal_tooth': 'gear tooth', 'wave_cam': 'oval disc', 'table': 'steel section',
                 'points': 'rounded bar', 'rounded_rect': 'rounded bar'}


def _shape(spec, part_id):
    builder = next(b for b in spec['builders'] if b['part_id'] == part_id)
    if builder['builder'] == 'array':
        count = builder['params'].get('count') or 1
        for n in builder['params'].get('counts', []):
            count *= n
        return f"set of {count} identical {SHAPE_WORDS.get(builder['params']['item']['builder'], 'part')}s"
    if builder['builder'] == 'mirror':
        return _shape(spec, builder['params']['source'])
    profile = (builder.get('params') or {}).get('profile')
    if isinstance(profile, dict) and 'contrib' in profile:   # a contrib entry names itself (its manifest words)
        return _contrib_word(profile['contrib'], SHAPE_WORDS['profile'])
    if builder['builder'].startswith('contrib:'):
        return _contrib_word(builder['builder'], 'part')
    if builder['builder'] == 'profile' and isinstance(profile, dict):
        return next((PROFILE_WORDS[k] for k in PROFILE_WORDS if k in profile), SHAPE_WORDS['profile'])
    return SHAPE_WORDS.get(builder['builder'], 'part')


def _contrib_word(ref, default):
    from ..contrib import words
    return (words(ref) or [default])[0]


PROMPT_WORD_LIMIT = 200   # a warning only: very long prompts dilute instructions (A/B 2026-10-05 at 120+ words)


def _short_identity(identity):
    return identity.split(':')[0].split(',')[0].strip()


MAPPING_WORDS = 60   # the clay-shape mapping's share of the prompt: what matters first; the rest is reported
DELIBERATE_WORDS = 30   # deliberate deviations' share: their first sentence, in order; the rest is reported
HAPPENS_WORDS = 40      # a generated cut's action (direction.action, else its goal), whole up to this many words
CLAUSE_WORDS = 14       # a spec description in the prompt: its first clause, at most this many words
DESCRIBED_WORDS = 40    # an element left to the model in words (built nowhere in the cut: world.described_in) is said whole up
                        # to this many words - where it is and how much of it ("a dense forest on the hill left of the lock")
                        # is all the model has to place it by, beside the picked picture
SCENE_WORDS = 90        # every element said in words shares this: each gets an equal part, never under its first clause


def _said_whole(elements):
    """([words], [dropped ids]) for elements the model places from words alone: whole descriptions, each up to
    DESCRIBED_WORDS and an equal share of SCENE_WORDS (six elements at forty words made a 240-word scene), at least
    CLAUSE_WORDS; what still does not fit is dropped and reported (SCENE_LONG), never silently cut."""
    each = max(CLAUSE_WORDS, min(DESCRIBED_WORDS, SCENE_WORDS // max(1, len(elements))))
    return _within([(e['id'], ' '.join(e['description'].rstrip('.').split()[:each])) for e in elements], SCENE_WORDS)


def _scene_long(dropped):
    return [f"SCENE_LONG: {', '.join(dropped)} not said - the cut shows more things than {SCENE_WORDS} words describe; "
            'show fewer, or shorten their world descriptions'] if dropped else []


def _clause(text, limit=CLAUSE_WORDS):
    """The first sentence or clause of a description, at most `limit` words: a prompt line, not the spec's prose."""
    import re
    head = re.split(r'(?<=[.;:])\s|,\s', text.strip(), maxsplit=1)[0].rstrip('.;:,')
    words = head.split()
    return ' '.join(words[:limit]) + ('…' if len(words) > limit else '')


def _within(lines, budget):
    """(kept, dropped) of (name, line) pairs in order, within `budget` words (the first line always fits)."""
    kept, dropped, words = [], [], 0
    for name, line in lines:
        if kept and words + len(line.split()) > budget:
            dropped.append(name)
            continue
        kept.append(line); words += len(line.split())
    return kept, dropped


STRUCTURE_WORDS = 70    # the structure clause's share: the order and thickness of the parts the shot explains
BAND_ELONGATION = 3.0   # a part at least this many times longer than thick on screen is a band (a layer, a wall leaf)
THIN_SHARE = 0.03       # below this share of its stack a band is called "thin" rather than given a number


def colour_word(srgb):
    """A plain colour word for a material's sRGB colour (0..1): lightness first, then a hue only when it is clearly
    coloured. Measured from the spec, so a model told "black band" has the same thing in front of it."""
    import colorsys
    r, g, b = (max(0.0, min(1.0, float(c))) for c in srgb[:3])
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    tone = 'black' if l < 0.08 else 'dark' if l < 0.3 else 'light' if l > 0.7 else ''
    if s < 0.25 or max(r, g, b) - min(r, g, b) < 0.08:
        return 'white' if l > 0.85 else ('black' if tone == 'black' else f"{tone} grey".strip())
    hue = ('red', 'orange', 'yellow', 'green', 'cyan', 'blue', 'purple', 'red')[min(7, int((h * 360 + 22.5) // 45))]
    if hue in ('orange', 'yellow') and l < 0.5:
        hue = 'brown'
    return f'{tone} {hue}'.strip() if tone != 'black' else 'black'


def _band(row):
    """'h' (lies across the frame: stacked top to bottom), 'v' (stands: stacked left to right) or None (a piece)."""
    x0, y0, x1, y1 = row['bbox']
    along_h = (x1 - x0) * row['aspect'] / max(row['run_v'], 1e-6)   # length over thickness, both in frame-height units
    along_v = (y1 - y0) / max(row['run_h'] * row['aspect'], 1e-6)
    if max(along_h, along_v) < BAND_ELONGATION:
        return None
    return 'h' if along_h >= along_v else 'v'


def structure_clause(layout, names):
    """Prompt words saying how the explained parts sit on screen: every band (a part much longer than thick) in its
    stack - bands lying across the frame top to bottom, standing bands left to right - with each one's share of its
    stack's total thickness (median runs, so perspective does not inflate them). layout: keep.part_layout; names:
    {part: words}. Returns (line or None, info). Same rule for floor layers, a wall build-up meeting them, or a gearbox
    stack: measured on the frame, nothing about the subject is assumed."""
    seen = {p: r for p, r in layout.items() if r}
    info = {'stacks': {}, 'pieces': [], 'unseen': sorted(p for p, r in layout.items() if not r)}
    stacks = {'h': [], 'v': []}
    for part, row in seen.items():
        kind = _band(row)
        (stacks[kind] if kind else info['pieces']).append(part)
    sentences = []
    for kind, direction in (('h', 'from top to bottom'), ('v', 'from left to right')):
        bands = stacks[kind]
        if len(bands) < 2:
            info['pieces'] += bands
            continue
        axis, run = (1, 'run_v') if kind == 'h' else (0, 'run_h')
        bands.sort(key=lambda p: seen[p]['centre'][axis])
        total = sum(seen[p][run] for p in bands)
        words = []
        for p in bands:
            share = seen[p][run] / max(total, 1e-6)
            words.append(f"{names.get(p, p)}, {'thin' if share < THIN_SHARE else f'about {max(5, round(share * 20) * 5)} %'}")
        info['stacks'][direction] = bands
        sentences.append(f"{'layers' if kind == 'h' else 'standing layers'} {direction}: " + '; '.join(words))
    if not sentences:
        return None, info
    info['pieces'] = sorted(info['pieces'])
    line = ('Structure that must read clearly - ' + '. '.join(sentences) + ' (percent of each stack\'s thickness). '
            'Each layer is a distinct band with a crisp boundary and its own material; keep this order and these '
            'thickness ratios in every frame, even where you add texture, wear or light.')
    return line, info


def _structure_names(path, shot, parts):
    """{instance/part: 'part words (colour)'}: the part id in words and its material's colour word from the spec."""
    from ..subjects import load_spec
    names = {}
    for ref in shot.get('subjects') or []:
        spec = load_spec(path, ref['subject_id'])
        colours = {pid: m.get('color_srgb') for m in spec.get('materials', []) for pid in m.get('part_ids', [])}
        for instance in ref.get('instance_ids') or [ref['subject_id']]:
            for pid in [b['part_id'] for b in spec['builders']]:
                key = f'{instance}/{pid}'
                if key in parts:
                    colour = colours.get(pid)
                    names[key] = (f'{colour_word(colour)} ' if colour else '') + pid.replace('_', ' ')
    return names


def structure_parts(path, shot):
    """The parts the structure clause orders: the shot's focus when it names the structure (screen.subject - several
    parts, or anything that is not only its details), plus each label's anchored part. A focus that is only a detail
    (FOCUS_IS_DETAIL) widens to the spec features holding it - the structure the detail sits in. Never a whole feature
    a structural focus merely touches (2026-10-10: a focus naming the floor finish pulled the room's walls and window
    into the layer list)."""
    from ..patterns import focus_of
    from ..subjects import load_spec
    from .keep import label_parts, scene_ids
    focus, _ = focus_of(shot)
    details = {d['id'] for d in (shot.get('screen') or {}).get('details', [])}
    anchored = label_parts(shot, scene_ids(path, shot))
    if focus and not set(focus) <= details:
        return list(dict.fromkeys(list(focus) + anchored))
    seed = set(focus) | {k['id'] for k in shot.get('key_parts', [])}
    out = []
    for ref in shot.get('subjects') or []:
        spec = load_spec(path, ref['subject_id'])
        for instance in ref.get('instance_ids') or [ref['subject_id']]:
            for feature in spec['features']:
                ids = [f'{instance}/{p}' for p in feature['part_ids']]
                if seed & set(ids):
                    out += ids
    return list(dict.fromkeys(out + anchored))


def structure_still_review(project, shot_id):
    """Sheet (no call) for a structure reference still: the middle frame of the shot's look render, edited to photoreal
    with the measured structure clause, for a reference model to copy look and part order from (it copies its images
    more than its video). Paid only through generate image with the user's words; then generate inputs --reference."""
    from .images import ASPECTS, review
    from .inputs import latest_complete_render, look_keyframe
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    render = latest_complete_render(path, shot)
    if render is None:
        raise StudioError('INPUT_MISSING', f'{shot_id}: no look render to edit', recovery='render submit --profile review (the shot look)')
    key = look_keyframe(path, shot, render)
    parts = structure_parts(path, shot)
    clause = None
    if parts:
        from .keep import part_layout
        clause, _ = structure_clause(part_layout(path, shot_id, parts), _structure_names(path, shot, parts))
    look = ((shot.get('route') or {}).get('generative') or {}).get('prompt_spec', {}).get('look')
    prompt = ' '.join(x for x in (
        'Make this rendered frame a photorealistic photograph: real materials, real light and surface detail.',
        'Keep the camera, the composition and every part\'s position, size and outline exactly as in the image.',
        f'Look: {look}.' if look else '', clause or '', 'No text, no letters, no captions, no logos.') if x)
    out = load_project(path)['output']
    result = review(path, f'{shot_id}_structure', 'edit', prompt, count=1, aspect=nearest_aspect(out['width'], out['height'], ASPECTS)[0],
                    source=str(key.resolve().relative_to(path.resolve())))
    return {**result, 'prompt': prompt, 'structure_clause': clause}


def _brief_rank(shot):
    """{element: rank}: the fill brief's subject elements first, then identity (ambient is background - not named)."""
    brief = shot.get('fill_brief') or {}
    rank = {}
    for level in brief.get('levels', []):
        for item in level.get('items', []):
            if item['role'] in ('subject', 'identity'):
                key = item['element'].split(':', 1)[-1].rstrip('*').rstrip('_')
                rank[key] = min(rank.get(key, 9), 0 if item['role'] == 'subject' else 1)
    return rank


def _index_mapping(path, shot, overrides):
    """Clay-shape = part lines from versions/<v>/subjects_index.json for subjects on screen, grouped by identity, in
    order of what the shot is about (fill brief subject, identity, then screen time) within MAPPING_WORDS."""
    index = shot_path(path, shot['shot_id']).parent / 'versions' / (shot.get('scene_version') or '') / 'subjects_index.json'
    if not shot.get('scene_version') or not index.is_file():
        return [], []
    groups = {}
    for subject in read_json(index)['subjects']:
        if subject.get('on_screen_frames'):
            groups.setdefault(_short_identity(subject['identity']), []).append(subject)
    rank = _brief_rank(shot)

    def order(item):
        name, subjects = item
        ids = ' '.join(s.get('subject_id', '') for s in subjects)
        brief = min((r for key, r in rank.items() if key and key in ids), default=9)
        return (brief, -max(len(s.get('on_screen_frames') or []) for s in subjects), name)
    lines, words, dropped = [], 0, []
    for name, subjects in sorted(groups.items(), key=order):
        override = next((o['text'] for o in overrides if o['match'].lower() in name.lower()), None)
        if override:
            lines.append(override)
            continue
        first = subjects[0]
        parts = '; '.join(f"the {' and '.join(sorted({_shape(first, p) for p in f['part_ids']}))} = {f['description']}"
                          for f in first['features']) or 'detailed part'
        line = f"{len(subjects)} x {name} ({parts})" if len(subjects) > 1 else f'{name} ({parts})'
        if lines and words + len(line.split()) > MAPPING_WORDS:
            dropped.append(name)   # reported, never silently cut: the agent can add a short mapping_override
            continue
        lines.append(line); words += len(line.split())
    return lines, dropped


def assemble_prompt(path, shot_id, name=None):
    """Generation prompt: Look, Scene (which clay shape is which real part), Keep, Add, Avoid - short on purpose.

    Structure comes from the input video; these words own the look and what is added. Shape mapping comes from the
    shot's subject specs (shot.subjects) or, without them, from the version's subjects_index.json (every spec-built
    element on screen, e.g. exemplars). route.generative.prompt_spec carries look / keep / add / forbid; hybrid
    prompts add the previs conventions (orientation tints, placeholders, deliberate deviations).
    """
    from ..subjects import load_spec
    from ..routing import lint_prompt, route_of
    path = project_dir(path)
    shot = load_shot(path, shot_id)
    route = route_of(shot)
    mode = route['mode']
    generative = route.get('generative') or {}
    items = generative.get('prompt_spec') or {}
    previs = generative.get('previs') or {}
    fps = load_project(path)['output']['fps']
    scene, look, avoid, references, mapping, motion, deliberate = [], [], ['text', 'letters', 'captions', 'watermark', 'logos'], [], [], [], []
    sources = {}
    off_screen, notes = [], []
    for ref in shot.get('subjects') or []:
        spec = load_spec(path, ref['subject_id'])
        seen = None   # {feature id: share} - only what the camera shows is described (2026-10-07: an intact exterior's
        if shot.get('scene_version'):   # prompt listed pistons, cams and springs, inviting the model to draw them)
            from .keep import VISIBLE_SHARE, visible_features
            instances = ref.get('instance_ids') or [ref['subject_id']]
            try:
                share = visible_features(path, shot_id, {f['id']: [f'{i}/{p}' for i in instances for p in f['part_ids']] for f in spec['features']})
                seen = {f for f, v in share.items() if v >= VISIBLE_SHARE}
                off_screen += sorted(set(share) - seen)
            except StudioError as error:
                notes.append(f'VISIBILITY_UNMEASURED: {error.message[:160]}; every feature is described')
        sources.update({s['id']: s for s in spec['sources']})
        gen = spec.get('generative_look', {})
        features = '; '.join(_clause(f['description']) for f in spec['features'])
        # (with features, identity only): a hybrid prompt names each feature again in its shape mapping, so there the
        # Scene line keeps the identity (2026-10-10: the doubled list made a 253-word prompt)
        scene.append((gen.get('scene') or f"{spec['identity']} ({features})", gen.get('scene') or _clause(spec['identity'])))
        if not items.get('look'):   # the shot's own look owns it; the spec's look and material words are the fallback
            materials = '; '.join(m['description'] for m in spec.get('materials', []) if m.get('description'))
            look.append(' '.join(x for x in (gen.get('look', ''), materials) if x))
        avoid += [a for a in gen.get('avoid', []) if a not in avoid]
        references += [sources[r].get('path') for r in gen.get('references', []) if sources.get(r, {}).get('path')]
        if gen.get('motion'):
            motion.append(gen['motion'])
        for feature in spec['features']:
            if seen is not None and feature['id'] not in seen:
                continue
            shapes = sorted({_shape(spec, p) for p in feature['part_ids']})
            mapping.append((feature['id'], f"the {' and '.join(shapes)} = {_clause(feature['description'])}"))
        for deviation in spec.get('deviations', []):
            target = deviation.get('check', '')
            if seen is not None and target.startswith('feature:') and target.split(':', 1)[1] not in seen:
                continue   # a deliberate change of something the shot does not show
            # recorded on purpose: the video model must not "correct" it back to real proportions
            deliberate.append((deviation['id'], f"{_clause(deviation['reason'])} (deliberate, keep it)"))
    # Same rule for every source (2026-10-07: a spec's full feature prose and deviation notes made a 406-word prompt):
    # each part of the prompt has its word share, what does not fit is reported, never silently cut.
    mapping, dropped = _within(mapping, MAPPING_WORDS)
    deliberate, dropped_deviations = _within(deliberate, DELIBERATE_WORDS)
    if not shot.get('subjects'):
        mapping, dropped = _index_mapping(path, shot, items.get('mapping_overrides', []))
    if items.get('look'):
        look.insert(0, items['look'])
    else:   # the world's light, the same one the render reads (world.lit)
        from ..world import load as load_world, look_words
        words = look_words(load_world(path))
        if words:
            look.insert(0, words)
    avoid += [a for a in items.get('forbid', []) if a not in avoid]
    if mode == 'generative' and not shot.get('subjects'):
        # a cut the model makes from its picture (studio/maker.py): what is in it, in the world's own words (2026-10-20:
        # such a cut was described by the light alone)
        from ..world import load as load_world
        shown = set((shot.get('screen') or {}).get('elements') or [])
        said, unsaid = _said_whole([e for e in (load_world(path) or {}).get('elements', []) if e['id'] in shown and e['function'] != 'light'])
        scene += [(words, words) for words in said]
        notes += _scene_long(unsaid)
    happens = ' '.join(str((shot.get('direction') or {}).get('action') or shot.get('goal') or '').split()[:HAPPENS_WORDS]) \
        if mode == 'generative' else ''
    if not (scene or mapping or look or happens):
        raise StudioError('INPUT_INVALID', 'Nothing to describe: add shot.subjects, build spec elements, or set route.generative.prompt_spec.look')
    lines = ['Photorealistic film footage.']
    if look:
        lines.append('Look: ' + ' '.join(l for l in look if l))
    if scene:
        lines.append('Scene: ' + '; '.join(short if mode == 'hybrid' and mapping else full for full, short in scene))
    if mode == 'generative':
        if any(i['kind'] == 'first_frame' for i in generative.get('inputs', [])):
            lines.append('Start exactly from the given first image: keep its composition, framing and everything in it; '
                         'only what the action moves moves.')
        if happens:   # whole, not a first clause: "the gate opens, then the ship rises" is one action
            lines.append('What happens: ' + happens.rstrip('.') + '.')
    if mode == 'hybrid':
        lines.append('Follow the input video camera, timing and positions exactly; keep every part shape, proportion and position as in the input video.')
        from ..change_moves import kinds as change_kinds, words as change_words, changes_of
        made = changes_of(shot)   # what the lines explain happens in the input video: the model keeps it happening (2026-10-22)
        if made:
            book = change_kinds()
            lines.append('What changes in the input video, keep it changing exactly so: ' + '; '.join(change_words(c, book) for c in made) + '.')
        from .inputs import INPUTS_FOR
        from .policy import role_of
        from .inputs import _clay_fill
        if mapping and not _clay_fill(shot):   # a clay-filled shot names its regions from the world instead (below)
            shown = 'a grey clay model' if INPUTS_FOR.get(role_of(shot['route']), {}).get('previs') == 'control_clay' else 'a rendered model'
            lines.append(f'The input video is {shown}; its shapes are: ' + '; '.join(mapping) + '.')
        layout = None
        if role_of(shot['route']) == 'explain' and shot.get('scene_version'):
            # the parts the shot explains, in their measured screen order and thickness (2026-10-10: the model fills
            # 100 % of the frame; these words are what holds the structure, and only parts it still loses go back)
            parts = structure_parts(path, shot)
            if parts:
                from .keep import part_layout
                try:
                    clause, layout = structure_clause(part_layout(path, shot_id, parts), _structure_names(path, shot, parts))
                except StudioError as error:
                    clause = None
                    notes.append(f'STRUCTURE_UNMEASURED: {error.message[:160]}; the prompt says nothing about part order')
                if clause:
                    lines.append(clause)
                    if len(clause.split()) > STRUCTURE_WORDS:   # order breaks if a middle band is cut, so it is said, not cut
                        notes.append(f'STRUCTURE_LONG: the structure clause is {len(clause.split())} words (over {STRUCTURE_WORDS}); '
                                     'declare fewer key parts or a tighter screen.subject if the model ignores it')
        if _clay_fill(shot):   # the user's split: Blender keeps the core, clay stands in for what the model fills
            from ..world import load as load_world
            world = load_world(path) or {}
            shown = set((shot.get('screen') or {}).get('elements') or [])
            described = set((shot.get('screen') or {}).get('described') or [])   # left to the model in words (world.described_in)
            filled = [e for e in world.get('elements', []) if e['id'] in shown and e['function'] != 'light'
                      and (e['owner']['look'] == 'generative' or e['id'] in described)]
            stood = [e for e in filled if e['owner']['shape'] == 'blender' and e['id'] not in described]   # built clay stand-ins
            if stood:   # what is built is copied: only these keep their clay shape, size and place
                lines.append('Grey clay areas in the input video are stand-ins: make them what the reference images show, with the same '
                             'shape, size and place; keep the rendered, coloured parts exactly as they are.')
            else:
                lines.append('Keep the rendered, coloured parts exactly as they are.')
            if filled:   # what each region becomes, in the world's own words
                said = lambda e: ' '.join(e['description'].split('. ')[0].rstrip('.').split()[:CLAUSE_WORDS])   # noqa: E731 - lists stay whole
                lines.append('Make them: ' + '; '.join(said(e) for e in stood) + '.' if stood else '')
                around, unsaid = _said_whole([e for e in filled if e not in stood])
                notes += _scene_long(unsaid)
                if described & shown:   # nothing built stands for these: the model places and multiplies them as said, never from a grey shape
                    lines.append('Where nothing is built, add what is described - as many, as varied and where the words and the '
                                 'reference images say, not copied from any grey shape.')
                if '@background' in ((shot.get('screen') or {}).get('generate_only') or []):
                    from ..world import shell_built, shell_for
                    style_file = path / 'style.json'
                    shell = shell_for(shot, world, read_json(style_file) if style_file.is_file() else {})
                    pieces = ([f"flat {shell['ground']}"] if shell['ground'] != 'none' else []) + \
                             ([f"{shell['sky']} sky"] if shell['sky'] != 'none' else []) if shell_built(shell) else []
                    if pieces:   # the engine's ground and sky (env_shell.py) fix the horizon; the model paints them
                        lines.append(f"The {' and '.join(pieces)} shapes around it set the horizon and the layout: "
                                     'paint them' + (' as ' + '; '.join(around) if around else '') + ', as in the reference '
                                     'images, keeping the horizon where it is.')
                    elif around:
                        lines.append('The empty dark background is the world around it: fill it with ' + '; '.join(around) + ', as in the reference images.')
                lines = [l for l in lines if l]
        if previs.get('orientation_colors'):
            lines.append('In the input video red-tinted faces point to the front of the subject and blue-tinted faces to its back; '
                         'these tints only mark direction and are not real colours.')
        for slot in previs.get('placeholders', []):
            lines.append(f"From {slot['start_frame'] / fps:.1f} s to {slot['end_frame'] / fps:.1f} s the black areas of the input video "
                         f"are where {slot['description']} appears; fill them with it.")
        if deliberate:   # even on clay: a model that knows the real object (a P-51) "corrects" a stretched wing back
            lines.append('Intentional changes from the real object: ' + '; '.join(deliberate) + '.')
        if motion:
            lines.append('Secondary motion allowed: ' + '; '.join(motion) + '.')
    if items.get('keep'):
        lines.append('Keep: ' + '; '.join(items['keep']) + '.')
    if items.get('add'):
        lines.append('Add: ' + '; '.join(items['add']) + '.')
    for i, item in enumerate([x for x in generative.get('inputs', []) if x['kind'] == 'reference_image'], 1):
        if 'start_s' in item or 'end_s' in item:
            lines.append(f"Reference image {i} applies from {item.get('start_s', 0):.1f} s to "
                         f"{item.get('end_s', generative.get('duration_seconds', 0)):.1f} s.")
    lines.append('No text, no letters, no captions. Avoid: ' + ', '.join(avoid) + '.')
    text = '\n'.join(lines) + '\n'
    problems = lint_prompt(text, mode)
    if problems:
        raise StudioError('GENERATION_PROMPT_INVALID', '; '.join(problems), recovery='Edit route.generative.prompt_spec, generative_look in the subject spec or the placeholder descriptions')
    target = path / 'prompts' / f"{check_id(name or shot_id)}.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding='utf-8')
    return {'status': 'written', 'prompt_ref': str(target.relative_to(path)), 'text': text, 'words': len(text.split()),
            'reference_images': references, 'note': 'Add reference_images to route.generative.inputs as kind reference_image',
            'warnings': [f'MAPPING_DROPPED: {name} not named in the prompt (over {MAPPING_WORDS} mapping words); add a short '
                         'prompt_spec.mapping_overrides entry if the model must know it' for name in dropped]
                        + [f'DEVIATION_NOT_IN_PROMPT: {name} (over {DELIBERATE_WORDS} words of deliberate changes); it stays in the '
                           'spec and the fidelity report, and the input video still carries its shape' for name in dropped_deviations] + notes,
            'off_screen': off_screen, 'structure': layout if mode == 'hybrid' else None}


def select_take(path, shot_id, take_key, user_words=None, additions=None):
    """The take the edit uses. The person who looked at it chooses: their words are recorded verbatim with whether
    each requested addition (route.generative.prompt_spec.add) is present. Takes the shot's role does not allow
    are refused (policy.judge)."""
    from .policy import judge, policy_for
    from .review import check_user_words
    path = project_dir(path)
    shot = load_shot(path, shot_id)
    manifest_path = shot_path(path, shot_id).parent / 'generated' / check_id(take_key) / 'clip.json'
    if not manifest_path.is_file():
        raise StudioError('INPUT_INVALID', f'No generated take {take_key}')
    manifest = read_json(manifest_path)
    from .policy import project_policy
    policy = policy_for(shot, project_policy(path))
    before = judge(manifest, policy)
    verdict = judge(manifest, policy, picked=user_words is not None)
    if not verdict['usable']:
        hint = ' - the user may still pick it in their own words (--user-words): this explain shot has no labels or graphics' \
            if before['pickable'] else ''
        raise StudioError('GENERATION_NOT_USABLE', f"Take {take_key} is not usable for a {verdict['role']} shot: {'; '.join(verdict['reasons'])}{hint}")
    spec = shot['route']['generative']
    if spec.get('prompt_spec', {}).get('add') or shot['route'].get('approval_binding'):
        if user_words is None:
            raise StudioError('INPUT_INVALID', 'Show the take to the user and pass their words (--user-words)')
    judged = dict(additions or {})
    unknown = sorted(set(judged) - set(spec.get('prompt_spec', {}).get('add', [])))
    if unknown:
        raise StudioError('INPUT_INVALID', f'additions not requested in prompt_spec.add: {unknown}')
    missing = [a for a in spec.get('prompt_spec', {}).get('add', []) if a not in judged]
    updated = deepcopy(shot)
    updated['route']['generative']['selected_take'] = take_key
    if user_words is not None:
        updated['route']['generative']['selection'] = {'take': take_key, 'user_words': check_user_words(user_words, 'selection'),
                                                       'additions': judged, 'at': now(),
                                                       **({'override': {'structure': before['reasons']}} if before['reasons'] else {})}
    with lock(path / '.project.lock', blocking=False):
        if load_shot(path, shot_id)['revision'] != shot['revision']:
            raise StudioError('REVISION_CONFLICT', 'Shot changed during selection')
        updated['revision'] += 1
        validate_shot(updated)
        write_json(shot_path(path, shot_id), updated)
    warnings = verdict['warnings'] + [f'addition not judged: {a}' for a in missing] + \
        [f'requested addition absent: {a}' for a, v in judged.items() if v == 'absent']
    return {'status': 'selected', 'shot_id': shot_id, 'selected_take': take_key, 'revision': updated['revision'], 'warnings': warnings}


def _additions(text):
    """'present:wet floor,absent:three more workers' -> {item: present|absent}."""
    out = {}
    for part in filter(None, (x.strip() for x in (text or '').split(','))):
        verdict, _, item = part.partition(':')
        if verdict not in ('present', 'absent') or not item.strip():
            raise StudioError('INPUT_INVALID', f'--additions entries are present:<item> or absent:<item>, got {part!r}')
        out[item.strip()] = verdict
    return out


def register_commands(subparsers):
    parser = subparsers.add_parser('generate', help='PAID generation for approved generative/hybrid shots')
    commands = parser.add_subparsers(dest='generate_command', required=True)
    clip = commands.add_parser('clip')
    clip.add_argument('--project', required=True); clip.add_argument('--shot', required=True)
    clip.add_argument('--allow-paid', action='store_true'); clip.add_argument('--max-usd', type=float)
    clip.set_defaults(handler=lambda a: generate_clip(a.project, a.shot, a.allow_paid, a.max_usd))
    prompt = commands.add_parser('prompt', help='Assemble the generation prompt from the shot subject specs')
    prompt.add_argument('--project', required=True); prompt.add_argument('--shot', required=True); prompt.add_argument('--name')
    prompt.set_defaults(handler=lambda a: assemble_prompt(a.project, a.shot, a.name))
    select = commands.add_parser('select', help='Record the take the user chose, in their words, and which requested additions are present')
    select.add_argument('--project', required=True); select.add_argument('--shot', required=True); select.add_argument('--take', required=True)
    select.add_argument('--user-words', help="the user's choice, verbatim"); select.add_argument('--additions', help='present:<item>,absent:<item>')
    select.set_defaults(handler=lambda a: select_take(a.project, a.shot, a.take, a.user_words, _additions(a.additions)))
    from .review import register_review
    register_review(commands)
    from .backdrop import register as register_backdrop
    register_backdrop(commands)
    from .inputs import register_inputs
    register_inputs(commands)
    from .keep import register_keep
    register_keep(commands)
    from .images import register as register_images
    register_images(commands)
    still = commands.add_parser('structure-still-review', help='Sheet for a photoreal edit of the look render middle frame with the '
                                'measured part order (a reference image for the take); no call')
    still.add_argument('--project', required=True); still.add_argument('--shot', required=True)
    still.set_defaults(handler=lambda a: structure_still_review(a.project, a.shot))
    rec = commands.add_parser('reconcile', help="Settle a request only the fal dashboard can answer, from the user's own words")
    rec.add_argument('--request', required=True, help='the request directory (generated/<key>/request)')
    rec.add_argument('--charged', required=True, choices=('yes', 'no'))
    rec.add_argument('--user-words', required=True, help='what the user said they saw on the dashboard, verbatim')
    rec.set_defaults(handler=lambda a: __import__('studio.generative.fal_client', fromlist=['reconcile']).reconcile(
        a.request, a.charged == 'yes', a.user_words))
    try:
        from .control import register_control
        register_control(commands)
    except ImportError:
        pass
