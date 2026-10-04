"""generate clip / select: one approved generative or hybrid shot -> a 30 fps BT.709 clip the edit can use.

The request (model, prompt bytes, input hashes, seed, take) is the cache key, so re-running never
pays twice; bumping `take` is the only way to ask for a new paid generation of the same request.
"""
from __future__ import annotations

import base64
import json
import math
import mimetypes
from copy import deepcopy
from pathlib import Path

from ..common import BT709_CHAIN, StudioError, file_hash, h264_encoder_args, lock, now, read_json, source_matrix, stable_hash, write_json
from ..project import load_project, load_shot, project_dir, shot_path, validate_shot
from ..routing import assert_route
from .fal_client import paid_call

ENDPOINTS = {
    ('veo-3.1', 'image_to_video'): 'fal-ai/veo3.1/image-to-video',
    ('veo-3.1', 'text_to_video'): 'fal-ai/veo3.1',
    ('seedance-2.5', 'video_to_video'): 'bytedance/seedance-2.5/reference-to-video',
    ('wan-2.2-vace', 'video_to_video'): 'fal-ai/wan-22-vace-fun-a14b/depth',
    ('kling-o1-edit', 'video_to_video'): 'fal-ai/kling-video/o1/video-to-video/edit',
    ('luma-ray-modify', 'video_to_video'): 'fal-ai/luma-dream-machine/ray-2/modify',
}


def _uri(path):
    kind = mimetypes.guess_type(Path(path).name)[0] or 'application/octet-stream'
    return f'data:{kind};base64,' + base64.b64encode(Path(path).read_bytes()).decode()


def _inputs(path, spec, kind):
    return [path / i['path'] for i in spec.get('inputs', []) if i['kind'] == kind]


def build_arguments(path, project, spec, prompt):
    """Endpoint-specific arguments from one route spec (a table of adapters, not per-shot code)."""
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
                'duration': str(min(8, max(4, math.ceil(seconds)))), 'resolution': '720p', 'generate_audio': False, 'aspect_ratio': 'auto'}
    if model == 'wan-2.2-vace':
        control = _inputs(path, spec, 'control')
        source = control[0] if control else previs[0]
        return {**common, 'video_url': _uri(source), 'preprocess': not control, 'match_input_num_frames': True, 'resolution': '720p'}
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


def retime(raw, out, frame_count, width, height, trim=0.0, method='duplicate'):
    """Any source fps -> exactly frame_count frames at 30 fps, project size, BT.709 tagged."""
    from ..audio import run_media
    source = _probe(raw)
    if source['frames'] == frame_count and not trim:
        # Frame-matched output (e.g. Wan match_input_num_frames at its own fps): map frame to frame, not by time.
        method, raw_rate = 'frame_match', f'setpts=N/(30*TB),fps=30'
    else:
        raw_rate = None
    if raw_rate is None and source['duration'] - trim + 1e-3 < frame_count / 30:
        raise StudioError('GENERATION_TOO_SHORT', f"Generated {source['duration']:.2f}s (trim {trim}s) < shot {frame_count / 30:.2f}s")
    rate = raw_rate or ('minterpolate=fps=30:mi_mode=mci' if method == 'minterpolate' else 'fps=30:round=near')
    head = '' if raw_rate else f'trim=start={trim},setpts=PTS-STARTPTS,'
    chain = (f"{head}{rate},"
             f"scale={width}:{height}:force_original_aspect_ratio=increase:in_color_matrix={source_matrix(source['color_space'])}:in_range=tv,"
             f"crop={width}:{height},setsar=1,{BT709_CHAIN}")
    run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(raw), '-an', '-vf', chain, '-frames:v', str(frame_count),
               *h264_encoder_args(), '-movflags', '+faststart', str(out)])
    result = _probe(out)
    if result['frames'] != frame_count:
        raise StudioError('MISSING_FRAMES', f"Retimed clip has {result['frames']} frames, expected {frame_count}")
    return source


def _billed_output_seconds(spec):
    """Seconds the provider generates (and bills): its duration options round up."""
    seconds = spec['duration_seconds'] + spec.get('trim_start_seconds', 0)
    if spec['model'] == 'seedance-2.5':
        return min(8, max(4, math.ceil(seconds)))
    if spec['model'] == 'veo-3.1':
        return next((d for d in (4, 6, 8) if d >= seconds), 8)
    return seconds


def _input_seconds(path, spec):
    videos = _inputs(path, spec, 'previs') + _inputs(path, spec, 'control')
    return _probe(videos[0])['duration'] if videos and spec['operation'] == 'video_to_video' else 0.0


def generate_clip(path, shot_id, allow_paid=False, max_usd=None):
    path = project_dir(path)
    project = load_project(path)
    shot = load_shot(path, shot_id)
    route = assert_route(shot, 'generate', path)
    spec = route['generative']
    endpoint = ENDPOINTS.get((spec['model'], spec['operation']))
    if endpoint is None:
        raise StudioError('ROUTE_MODEL_MISMATCH', f"No endpoint for {spec['model']} {spec['operation']}")
    prompt = (path / spec['prompt_ref']).read_text(encoding='utf-8').strip()
    request = {'endpoint': endpoint, 'prompt_sha256': stable_hash(prompt), 'seed': spec.get('seed'), 'take': spec.get('take', 1),
               'inputs': [{'kind': i['kind'], 'sha256': file_hash(path / i['path'])} for i in spec.get('inputs', [])],
               'duration_seconds': spec['duration_seconds'], 'output': project['output']}
    key = stable_hash(request)[:16]
    directory = shot_path(path, shot_id).parent / 'generated' / key
    if (directory / 'clip.json').is_file():
        return {**read_json(directory / 'clip.json'), 'reused': True}
    arguments = build_arguments(path, project, spec, prompt)
    record = paid_call(endpoint, arguments, directory / 'request', project_id=project['project_id'], allow_paid=allow_paid,
                       max_usd=max_usd, budget_usd=project.get('route_policy', {}).get('budget_usd'),
                       duration_seconds=_billed_output_seconds(spec), input_seconds=_input_seconds(path, spec))
    videos = [f for f in record['files'] if Path(f['path']).suffix.lower() in ('.mp4', '.mov', '.webm')]
    if not videos:
        raise StudioError('GENERATION_REJECTED', f'{endpoint} returned no video file')
    raw = directory / 'raw' / Path(videos[0]['path']).name
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(Path(videos[0]['path']).read_bytes())
    clip = directory / 'clip.mp4'
    source = retime(raw, clip, shot['duration_frames'], project['output']['width'], project['output']['height'],
                    spec.get('trim_start_seconds', 0.0), spec.get('retime', 'duplicate'))
    native = source['height'] >= project['output']['height'] or source['width'] >= project['output']['width']
    manifest = {'schema_version': 1, 'status': 'complete', 'shot_id': shot_id, 'route': route['mode'], 'key': key, 'request': request,
                'scene_version': shot['scene_version'] if route['mode'] == 'hybrid' else None,
                'profile': 'final' if native else 'preview', 'frame_count': shot['duration_frames'], 'fps': 30,
                'clip_path': str(clip), 'clip_sha256': file_hash(clip), 'raw_sha256': file_hash(raw),
                'source': {k: source[k] for k in ('width', 'height', 'fps', 'frames', 'duration')}, 'retime': spec.get('retime', 'duplicate'),
                'endpoint': endpoint, 'request_id': record['request_id'], 'estimated_usd': record['estimated_usd'],
                'ai_generated': True, 'use_status': 'review_only', 'created_at': now()}
    if route['mode'] == 'hybrid':
        # Structure must match the Blender motion pass; failing is the default and sends the shot back to Blender.
        from ..qa_generative import structure
        previs = _inputs(path, spec, 'previs')[0]
        control = sorted((shot_path(path, shot_id).parent / 'control').glob('*/control.json'))
        anchors = None
        if control and read_json(control[-1]).get('anchors_path'):
            anchors = read_json(read_json(control[-1])['anchors_path']).get('frames')
        report = structure(previs, clip, anchors=anchors)
        write_json(directory / 'structure.json', report)
        manifest['structure_qa'] = {k: report[k] for k in ('passed', 'reasons')}
        if report['passed'] and report.get('anchors_2d'):
            write_json(directory / 'anchors_2d.json', {'schema_version': 1, 'frames': report['anchors_2d']})
    write_json(directory / 'clip.json', manifest)
    return {**manifest, 'reused': False, 'artifacts': [str(clip), str(directory / 'clip.json')]}


# How a builder's output reads in a clay previs; the prompt maps these shapes to what they are.
SHAPE_WORDS = {'loft': 'long rounded body', 'wing': 'thin wing-shaped surface', 'revolve': 'round turned part', 'sweep': 'tube',
               'box': 'block', 'profile': 'straight beam', 'wall': 'flat panel', 'mirror': 'mirrored copy', 'asset': 'detailed part'}


def _shape(spec, part_id):
    builder = next(b for b in spec['builders'] if b['part_id'] == part_id)
    if builder['builder'] == 'array':
        count = builder['params'].get('count') or 1
        for n in builder['params'].get('counts', []):
            count *= n
        return f"set of {count} identical {SHAPE_WORDS.get(builder['params']['item']['builder'], 'part')}s"
    if builder['builder'] == 'mirror':
        return _shape(spec, builder['params']['source'])
    return SHAPE_WORDS.get(builder['builder'], 'part')


def assemble_prompt(path, shot_id, name=None):
    """Hybrid/generative prompt built from the shot's subject specs (base + Scene + Look + Timing + Avoid).

    The spec is the single source of what the subject is; the prompt never adds camera moves or text.
    Hybrid prompts add the previs conventions: which clay shape is which real part, what the orientation
    tints and black placeholders mean, and when each reference image applies.
    """
    from ..subjects import load_spec
    from ..routing import lint_prompt, route_of
    path = project_dir(path)
    shot = load_shot(path, shot_id)
    if not shot.get('subjects'):
        raise StudioError('INPUT_INVALID', 'Prompt assembly needs shot.subjects with subject specs')
    route = route_of(shot)
    mode = route['mode']
    generative = route.get('generative') or {}
    previs = generative.get('previs') or {}
    fps = load_project(path)['output']['fps']
    scene, look, avoid, references, mapping, motion, deliberate = [], [], ['text', 'letters', 'captions', 'watermark', 'logos'], [], [], [], []
    sources = {}
    for ref in shot['subjects']:
        spec = load_spec(path, ref['subject_id'])
        sources.update({s['id']: s for s in spec['sources']})
        gen = spec.get('generative_look', {})
        features = '; '.join(f['description'] for f in spec['features'])
        scene.append(gen.get('scene') or f"{spec['identity']} ({features})")
        materials = '; '.join(m['description'] for m in spec.get('materials', []) if m.get('description'))
        look.append(' '.join(x for x in (gen.get('look', ''), materials) if x))
        avoid += [a for a in gen.get('avoid', []) if a not in avoid]
        references += [sources[r].get('path') for r in gen.get('references', []) if sources.get(r, {}).get('path')]
        if gen.get('motion'):
            motion.append(gen['motion'])
        for feature in spec['features']:
            shapes = sorted({_shape(spec, p) for p in feature['part_ids']})
            mapping.append(f"the {' and '.join(shapes)} = {feature['description']}")
        for deviation in spec.get('deviations', []):
            # recorded on purpose: the video model must not "correct" it back to real proportions
            deliberate.append(f"{deviation['reason'].rstrip('.')} (deliberate, keep it)")
    lines = ['Photorealistic film footage.', 'Scene: ' + ' '.join(scene), 'Look: ' + ' '.join(l for l in look if l)]
    if mode == 'hybrid':
        lines.append('Follow the input video camera, timing and positions exactly; keep every part shape, proportion and position as in the input video.')
        lines.append('The input video is a grey clay model; its shapes are: ' + '; '.join(mapping) + '.')
        if previs.get('orientation_colors'):
            lines.append('In the input video red-tinted faces point to the front of the subject and blue-tinted faces to its back; '
                         'these tints only mark direction and are not real colours.')
        for slot in previs.get('placeholders', []):
            lines.append(f"From {slot['start_frame'] / fps:.1f} s to {slot['end_frame'] / fps:.1f} s the black areas of the input video "
                         f"are where {slot['description']} appears; fill them with it.")
        if deliberate:
            lines.append('Intentional changes from the real object: ' + '; '.join(deliberate) + '.')
        if motion:
            lines.append('Secondary motion allowed: ' + '; '.join(motion) + '.')
    for i, item in enumerate([x for x in generative.get('inputs', []) if x['kind'] == 'reference_image'], 1):
        if 'start_s' in item or 'end_s' in item:
            lines.append(f"Reference image {i} applies from {item.get('start_s', 0):.1f} s to "
                         f"{item.get('end_s', generative.get('duration_seconds', 0)):.1f} s.")
    lines.append('No text, no letters, no captions. Avoid: ' + ', '.join(avoid) + '.')
    text = '\n'.join(lines) + '\n'
    problems = lint_prompt(text, mode)
    if problems:
        raise StudioError('GENERATION_PROMPT_INVALID', '; '.join(problems), recovery='Edit generative_look in the subject spec or the placeholder descriptions')
    target = path / 'prompts' / f"{name or shot_id}.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding='utf-8')
    return {'status': 'written', 'prompt_ref': str(target.relative_to(path)), 'text': text,
            'reference_images': references, 'note': 'Add reference_images to route.generative.inputs as kind reference_image'}


def select_take(path, shot_id, take_key):
    path = project_dir(path)
    shot = load_shot(path, shot_id)
    if not (shot_path(path, shot_id).parent / 'generated' / take_key / 'clip.json').is_file():
        raise StudioError('INPUT_INVALID', f'No generated take {take_key}')
    updated = deepcopy(shot)
    updated['route']['generative']['selected_take'] = take_key
    with lock(path / '.project.lock', blocking=False):
        if load_shot(path, shot_id)['revision'] != shot['revision']:
            raise StudioError('REVISION_CONFLICT', 'Shot changed during selection')
        updated['revision'] += 1
        validate_shot(updated)
        write_json(shot_path(path, shot_id), updated)
    return {'status': 'selected', 'shot_id': shot_id, 'selected_take': take_key, 'revision': updated['revision']}


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
    select = commands.add_parser('select')
    select.add_argument('--project', required=True); select.add_argument('--shot', required=True); select.add_argument('--take', required=True)
    select.set_defaults(handler=lambda a: select_take(a.project, a.shot, a.take))
    try:
        from .control import register_control
        register_control(commands)
    except ImportError:
        pass
