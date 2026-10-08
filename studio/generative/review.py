"""generate review: the human-in-the-loop sheet shown before any paid generation, and the request fingerprint that
binds an approval to exactly what will be sent.

request_fingerprint(path, shot) hashes everything fal would receive - endpoint, prompt bytes, every input file hash,
seed, take, durations, trim/retime/pad and the adapter arguments (with file contents replaced by their hashes). The
generation cache key is derived from the same value, so "what the user approved" and "what is paid for" are one
thing; editing the prompt, an input, the model or any argument after approval stops generation with
ROUTE_APPROVAL_STALE.

The sheet (reviews/gen_<id>/sheet.png + sheet.md + review.json) shows per shot: three frames of the Blender input
(start/middle/end), reference images, role, model, estimated cost, the structured prompt items and the final prompt.
The user adds or changes items in their own words; the agent edits route.generative.prompt_spec, re-assembles the
prompt and shows a new sheet (history keeps the user's words per version). Approval quotes the user verbatim.
"""
from __future__ import annotations

from pathlib import Path

from ..common import StudioError, file_hash, now, read_json, stable_hash, write_json
from ..project import load_project, load_shot, project_dir
from .clip import RETIME_DEFAULT

THUMB_H = 360
FRAMES = (0.0, 0.5, 1.0)   # positions in the shot shown on the sheet
AGENT_MARKERS = ('user (', 'user:', 'approved', '— ', 'agent:')   # wrappers an agent adds around a quote


def pad_seconds(spec):
    """Seconds the input video is held on its last frame before sending (0 = none)."""
    target = spec.get('pad_to_seconds')
    if target is None and spec.get('model') == 'seedance-2.5' and spec['operation'] == 'video_to_video':
        target = 4.0   # Seedance generates >= 4 s; a shorter input was squeezed (samsung A/B s02: 4 s -> 2.9 s)
    return round(max(0.0, (target or 0.0) - spec['duration_seconds']), 3)


def request_fingerprint(path, shot):
    """{'fingerprint', 'request', 'endpoint', 'prompt_sha256', 'est_usd'} of the paid request this shot would send."""
    from .clip import ENDPOINTS, _billed_output_seconds, build_arguments
    from .fal_client import estimate_usd
    path = project_dir(path)
    project = load_project(path)
    spec = shot['route']['generative']
    endpoint = ENDPOINTS.get((spec['model'], spec['operation']))
    if endpoint is None:
        raise StudioError('ROUTE_MODEL_MISMATCH', f"No endpoint for {spec['model']} {spec['operation']}")
    prompt_file = path / spec['prompt_ref']
    prompt = prompt_file.read_text(encoding='utf-8').strip() if prompt_file.is_file() else ''
    inputs = []
    for item in spec.get('inputs', []):
        source = path / item['path']
        inputs.append({'kind': item['kind'], 'sha256': file_hash(source) if source.is_file() else None,
                       **{k: item[k] for k in ('start_s', 'end_s') if k in item}})
    try:
        arguments = build_arguments(path, project, spec, prompt, uri=lambda p: 'sha256:' + file_hash(p))
    except StudioError:
        arguments = None   # missing inputs: the gate reports them; the fingerprint still identifies the request
    pad = pad_seconds(spec)
    request = {'endpoint': endpoint, 'prompt_sha256': stable_hash(prompt), 'seed': spec.get('seed'), 'take': spec.get('take', 1),
               'inputs': inputs, 'duration_seconds': spec['duration_seconds'], 'output': project['output'],
               'trim_start_seconds': spec.get('trim_start_seconds', 0), 'retime': spec.get('retime', RETIME_DEFAULT), 'pad_seconds': pad,
               'arguments_sha256': stable_hash(arguments)}
    videos = [path / i['path'] for i in spec.get('inputs', []) if i['kind'] in ('previs', 'control') and (path / i['path']).is_file()]
    input_seconds = 0.0
    if videos and spec['operation'] == 'video_to_video':
        from .clip import _probe
        try:
            input_seconds = _probe(videos[0])['duration'] + pad
        except StudioError:
            input_seconds = None   # unreadable input: no estimate; the gate reports the input
    if input_seconds is None:
        input_seconds = 0.0
    try:
        est = estimate_usd(endpoint, _billed_output_seconds(spec), input_seconds)
    except StudioError:
        est = None
    return {'fingerprint': stable_hash(request), 'request': request, 'endpoint': endpoint, 'prompt_sha256': request['prompt_sha256'],
            'prompt': prompt, 'est_usd': est}


def check_user_words(words, what='approval'):
    """The user's own words only: refuse text that is obviously an agent's wrapper around a quote."""
    if not isinstance(words, str) or len(words.strip()) < 4:
        raise StudioError('INPUT_INVALID', f"{what} needs the user's own words (--user-words)")
    if words.strip().lower().startswith(AGENT_MARKERS):
        raise StudioError('INPUT_INVALID', f"{what}: pass only what the user said, verbatim - not a summary or a 'User (...)' wrapper; "
                                           'put your own notes in --agent-note')
    return words.strip()


def _frames(video, duration_frames):
    from ..qa_generative import _probe as probe, _rgb_frames
    from PIL import Image
    try:
        width, height, count = probe(video)
        frames = _rgb_frames(video, max(2, round(width * THUMB_H / height / 2) * 2), THUMB_H, 1)
    except StudioError:
        return []   # unreadable input: the sheet shows no frames and the gate refuses the input
    if not frames:
        return []
    return [frames[min(len(frames) - 1, round(f * (len(frames) - 1)))] for f in FRAMES]


def _thumb(image_path):
    from PIL import Image, UnidentifiedImageError
    try:
        image = Image.open(image_path).convert('RGB')
    except (OSError, UnidentifiedImageError):
        return Image.new('RGB', (THUMB_H, THUMB_H), (60, 20, 20))
    image.thumbnail((THUMB_H, THUMB_H))
    return image


def _sheet(path, rows, out):
    """One row per shot: Blender input frames, reference thumbnails, then role / model / cost text."""
    from PIL import Image, ImageDraw
    from ..edit import pinned_font
    style = read_json(path / 'style.json') if (path / 'style.json').is_file() else {}
    font, _ = pinned_font(style, path, 22)
    small, _ = pinned_font(style, path, 18)
    tiles = []
    for row in rows:
        images = row['frames'] + row['refs']
        width = sum(i.width for i in images) + 8 * (len(images) + 1) + 520
        canvas = Image.new('RGB', (max(width, 900), THUMB_H + 70), (24, 24, 28))
        draw = ImageDraw.Draw(canvas)
        x = 8
        for i, image in enumerate(images):
            canvas.paste(image, (x, 52))
            label = ('Blender ' + ('start', 'middle', 'end')[i]) if i < len(row['frames']) else f'ref {i - len(row["frames"]) + 1}'
            draw.text((x, 30), label, font=small, fill=(170, 170, 180))
            x += image.width + 8
        draw.text((8, 4), f"{row['shot_id']}  ·  {row['role']}  ·  {row['model']}  ·  ${row['est_usd']}", font=font, fill=(240, 240, 245))
        y = 52
        for line in row['summary']:
            draw.text((x + 12, y), line[:60], font=small, fill=(220, 220, 225)); y += 26
        tiles.append(canvas)
    if not tiles:
        return
    sheet = Image.new('RGB', (max(t.width for t in tiles), sum(t.height for t in tiles)), (24, 24, 28))
    y = 0
    for t in tiles:
        sheet.paste(t, (0, y)); y += t.height
    sheet.save(out)


def build_review(project, shots=None, after=None, user_words=None):
    """Make the sheet for the given shots (default: every generative/hybrid shot). after/user_words: the previous sheet
    and what the user asked to change on it, kept as history."""
    from .policy import role_of
    path = project_dir(project)
    entries = [e['shot_id'] for e in load_project(path)['shots']]
    chosen = shots or [s for s in entries if (load_shot(path, s).get('route') or {}).get('mode') in ('generative', 'hybrid')]
    if not chosen:
        raise StudioError('INPUT_INVALID', 'No generative or hybrid shot to review')
    record, rows, md = {}, [], []
    for sid in chosen:
        shot = load_shot(path, sid)
        route = shot.get('route') or {}
        if route.get('mode') not in ('generative', 'hybrid'):
            raise StudioError('INPUT_INVALID', f'{sid} is not a generative or hybrid shot')
        spec = route['generative']
        fp = request_fingerprint(path, shot)
        previs = next((path / i['path'] for i in spec.get('inputs', []) if i['kind'] in ('previs', 'first_frame')), None)
        frames = _frames(previs, shot['duration_frames']) if previs and previs.suffix.lower() in ('.mp4', '.mov', '.webm') else \
            ([_thumb(previs)] if previs and previs.is_file() else [])
        refs = [_thumb(path / i['path']) for i in spec.get('inputs', []) if i['kind'] == 'reference_image' and (path / i['path']).is_file()]
        items = spec.get('prompt_spec') or {}
        summary = [f"look: {items.get('look', '-')}"] + [f'{k}: {v}' for k in ('keep', 'add', 'forbid') for v in items.get(k, [])]
        summary += [f"input {i['kind']}: {Path(i['path']).parent.parent.name}/{Path(i['path']).name}" for i in spec.get('inputs', [])
                    if i['kind'] in ('previs', 'control')]
        rows.append({'shot_id': sid, 'role': role_of(route), 'model': spec['model'], 'est_usd': fp['est_usd'], 'frames': frames,
                     'refs': refs, 'summary': summary})
        record[sid] = {'fingerprint': fp['fingerprint'], 'prompt_sha256': fp['prompt_sha256'], 'model': spec['model'],
                       'endpoint': fp['endpoint'], 'est_usd': fp['est_usd'], 'role': role_of(route), 'prompt_ref': spec['prompt_ref'],
                       'inputs': fp['request']['inputs']}
        md += [f"## {sid} — {role_of(route)} · {spec['model']} · ${fp['est_usd']}", '',
               '| item | value |', '|---|---|', f"| look | {items.get('look', '-')} |"]
        md += [f'| {k} | {v} |' for k in ('keep', 'add', 'forbid') for v in items.get(k, [])]
        md += ['', '| input | file | sha256 |', '|---|---|---|']
        md += [f"| {i['kind']} | {i['path']} | {(file_hash(path / i['path'])[:12] if (path / i['path']).is_file() else 'MISSING')} |"
               for i in spec.get('inputs', [])]
        md += ['', 'Final prompt:', '', '```',
               fp['prompt'], '```', '']
    review_id = stable_hash({'shots': {s: r['fingerprint'] for s, r in record.items()}})[:16]
    directory = path / 'reviews' / f'gen_{review_id}'
    directory.mkdir(parents=True, exist_ok=True)
    _sheet(path, rows, directory / 'sheet.png')
    total = round(sum(r['est_usd'] or 0 for r in record.values()), 2)
    header = [f'# Generation review {review_id}', '', f'Total estimate: ${total}. Nothing is generated until you approve this sheet.',
              'Tell me what to add, change or forbid (in your own words); I will update the prompt items and show a new sheet.', '']
    (directory / 'sheet.md').write_text('\n'.join(header + md), encoding='utf-8')
    history = []
    if after:
        previous = read_json(path / 'reviews' / f'gen_{after}' / 'review.json')
        history = previous.get('history', []) + [{'review_id': after, 'user_words': check_user_words(user_words, 'review change')}]
    data = {'schema_version': 1, 'review_id': review_id, 'created_at': now(), 'status': 'shown', 'shots': record, 'total_est_usd': total,
            'history': history, 'approvals': []}
    existing = directory / 'review.json'
    if existing.is_file():   # same requests again: keep approvals and history already recorded
        old = read_json(existing)
        data.update({k: old[k] for k in ('approvals', 'status', 'created_at') if k in old})
        data['history'] = old.get('history') or history
    write_json(existing, data)
    return {'status': 'shown', 'review_id': review_id, 'total_est_usd': total, 'sheet': str(directory / 'sheet.png'),
            'sheet_md': str(directory / 'sheet.md'), 'artifacts': [str(directory / 'sheet.png'), str(directory / 'sheet.md'), str(existing)]}


def binding_for(path, shot, review_id, agent_note=None):
    """Approval binding of one shot to a shown sheet; refuses a sheet that no longer matches the request."""
    path = project_dir(path)
    file = path / 'reviews' / f'gen_{review_id}' / 'review.json'
    if not file.is_file():
        raise StudioError('ROUTE_REVIEW_MISSING', f'No generation review {review_id}', recovery='Run generate review and show the sheet to the user')
    review = read_json(file)
    entry = review['shots'].get(shot['shot_id'])
    if entry is None:
        raise StudioError('ROUTE_REVIEW_MISSING', f"Review {review_id} does not cover {shot['shot_id']}")
    current = request_fingerprint(path, shot)
    if current['fingerprint'] != entry['fingerprint']:
        raise StudioError('ROUTE_REVIEW_STALE', f"{shot['shot_id']}: the request changed after review {review_id} was shown",
                          recovery='Run generate review again and show the new sheet')
    return {'review_id': review_id, 'fingerprint': current['fingerprint'], 'prompt_sha256': current['prompt_sha256'],
            'model': shot['route']['generative']['model'], 'est_usd': current['est_usd'], 'agent_note': agent_note}


def record_approval(path, review_id, shot_id, user_words):
    file = project_dir(path) / 'reviews' / f'gen_{review_id}' / 'review.json'
    review = read_json(file)
    review['approvals'].append({'shot_id': shot_id, 'user_words': user_words, 'at': now()})
    if {a['shot_id'] for a in review['approvals']} >= set(review['shots']):
        review['status'] = 'approved'
    write_json(file, review)


def render_still(project, shot_id, frame, height=1280, samples=64):
    """stills/<shot>_<version>_f<frame>.png: one frame of the current version rendered as built - the look reference
    a reference-image model gets instead of third-party material (routing.reference_problems allows stills/)."""
    import os
    from ..common import REPO, blender_binary, run_command
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    if not shot.get('scene_version'):
        raise StudioError('INPUT_INVALID', f'{shot_id} has no built scene version')
    if not 0 <= frame < shot['duration_frames']:
        raise StudioError('INPUT_INVALID', f"frame {frame} outside the shot (0-{shot['duration_frames'] - 1})")
    scene = path / 'shots' / shot_id / 'versions' / shot['scene_version'] / 'scene.blend'
    out = path / 'stills' / f"{shot_id}_{shot['scene_version']}_f{frame:04d}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    job = out.with_suffix('.job.json')
    write_json(job, {'frame': frame, 'output': str(out), 'height': height, 'samples': samples,
                     'device': os.environ.get('STUDIO_RENDER_DEVICE', 'GPU')})
    run_command([blender_binary(), '--background', '--factory-startup', '--disable-autoexec', scene, '--python-exit-code', '1',
                 '--python', REPO / 'studio' / 'blender_ops' / 'render_still.py', '--', job], out.with_suffix('.log'), timeout=1800)
    if not out.is_file():
        raise StudioError('COMMAND_FAILED', f'No still written for {shot_id} frame {frame}')
    record = {'shot_id': shot_id, 'scene_version': shot['scene_version'], 'scene_sha256': file_hash(scene), 'frame': frame,
              'sha256': file_hash(out), 'created_at': now()}
    write_json(out.with_suffix('.json'), record)
    job.unlink()
    return {'status': 'complete', 'path': str(out.relative_to(path)), **record, 'artifacts': [str(out)]}


def register_review(commands):
    p = commands.add_parser('review', help='Sheet shown to the user before any paid generation (frames, prompt items, refs, cost)')
    p.add_argument('--project', required=True)
    p.add_argument('--shots', help='comma-separated shot ids (default: all generative/hybrid shots)')
    p.add_argument('--after', help='the previous sheet this one revises')
    p.add_argument('--user-words', help="what the user asked to change on the previous sheet, verbatim")
    p.set_defaults(handler=lambda a: build_review(a.project, a.shots.split(',') if a.shots else None, a.after, a.user_words))
    p = commands.add_parser('still', help='Render one frame of the built shot as a look reference image (stills/)')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.add_argument('--frame', type=int, required=True, help='0-based frame of the shot')
    p.add_argument('--height', type=int, default=1280); p.add_argument('--samples', type=int, default=64)
    p.set_defaults(handler=lambda a: render_still(a.project, a.shot, a.frame, a.height, a.samples))
