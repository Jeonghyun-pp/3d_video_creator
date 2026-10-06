"""Generated backdrops (paid, fal): a still image placed far behind a Blender shot, blurred - the mood around a subject
whose geometry stays exact (generation owns atmosphere, Blender owns the mechanism; routing rule #1).

Paid like every other request: `generate backdrop-review` writes a sheet for exactly this request (prompt, model, count,
size, cost); the user approves it in their own words; `generate backdrop --review <id> --user-words "..."` sends that
request and nothing else, inside the project's budget, and keeps each image with its provenance under backdrops/<id>/.
Place one with shot.scene.backdrop {image: "backdrops/<id>/0.png", blur_px, strength} (blender_ops/backdrop.py).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..common import StudioError, check_id, now, read_json, stable_hash, write_json
from ..project import load_project, project_dir, validate_schema
from .fal_client import estimate_usd, paid_call
from .review import check_user_words

DEFAULT_ENDPOINT = 'fal-ai/nano-banana-pro'
ASPECTS = ('9:16', '16:9', '1:1', '3:4', '4:3')
IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.webp')


def _request(backdrop_id, prompt, endpoint, count, aspect):
    from ..routing import NO_TEXT
    check_id(backdrop_id)
    if not isinstance(prompt, str) or len(prompt.strip()) < 12:
        raise StudioError('INPUT_INVALID', 'backdrop: describe the place (12+ characters)')
    if not NO_TEXT.search(prompt):
        raise StudioError('INPUT_INVALID', 'backdrop: generated images must contain no text - say so in the prompt ("no text, no logos")')
    if aspect not in ASPECTS or not 1 <= int(count) <= 4:
        raise StudioError('INPUT_INVALID', f'backdrop: aspect one of {ASPECTS}, count 1-4')
    request = {'backdrop_id': backdrop_id, 'prompt': prompt.strip(), 'endpoint': endpoint, 'count': int(count), 'aspect': aspect}
    return request, stable_hash(request)


SIDES = {'left': 'right', 'right': 'left'}


def compose_prompt(shot, place, aspect):
    """The backdrop prompt from the shot's backdrop relation (docs/BACKDROP_STAGING_PLAN.md) and the place described in
    words: the view, the light and the empty centre come from the same data the Blender scene is built from, so the
    picture and the scene agree; `place` says what is there."""
    relation = ((shot.get('scene') or {}).get('backdrop') or {}).get('relation') or {}
    orientation = 'Vertical' if aspect in ('9:16', '3:4') else 'Horizontal' if aspect in ('16:9', '4:3') else 'Square'
    parts = [f'{orientation} {aspect} background plate photograph for a product shot; a subject will be composited in the centre later, '
             'so the image is only the environment behind it.']
    support = (relation.get('support') or {}).get('kind')
    view = relation.get('view')
    if view:
        height = {'bench': 'workbench height', 'floor': 'just above the floor'}.get(support, 'subject height')
        parts.append(f"Taken from {height}, looking {'down' if view['elevation_deg'] >= 0 else 'up'} about {abs(round(view['elevation_deg']))} degrees; "
                     'perspective lines consistent with that view.')
    parts.append(place.strip().rstrip('.') + '.')
    light = relation.get('light')
    if light:
        key = light.get('key_side', 'left')
        parts.append(f"Light: the brightest light from the {key}, about {round(light.get('key_kelvin', 5600))} K; softer light from the "
                     f"{SIDES[key]}, about {round(light.get('fill_kelvin', 4000))} K.")
    parts.append('Everything strongly out of focus, soft round bokeh, no sharp edges or readable detail. Keep the centre calm and about one '
                 'stop darker than the edges. No people in focus, no text, no letters, no signs, no logos, no watermark.')
    return ' '.join(parts)


def review(project, backdrop_id, prompt=None, *, count=3, aspect='9:16', endpoint=DEFAULT_ENDPOINT, shot_id=None, place=None):
    """The sheet the user approves: one request, its price, what is made. No call is made. With shot_id and place the prompt
    is composed from the shot's backdrop relation (compose_prompt) instead of written whole."""
    path = project_dir(project)
    project_data = load_project(path)
    if shot_id:
        from ..project import load_shot
        if not place:
            raise StudioError('INPUT_INVALID', 'backdrop-review --shot needs --place (what is there, in words)')
        prompt = compose_prompt(load_shot(path, shot_id), place, aspect)
    request, fingerprint = _request(backdrop_id, prompt, endpoint, count, aspect)
    usd = round(estimate_usd(endpoint) * request['count'], 2)
    budget = project_data.get('route_policy', {}).get('budget_usd', 0)
    review_id = fingerprint[:16]
    directory = path / 'reviews' / f'backdrop_{review_id}'
    record = read_json(directory / 'review.json') if (directory / 'review.json').is_file() else \
        {'schema_version': 1, 'review_id': review_id, 'kind': 'backdrop', 'created_at': now(), 'request': request,
         'fingerprint': fingerprint, 'est_usd': usd, 'approvals': []}
    write_json(directory / 'review.json', record)
    (directory / 'sheet.md').write_text('\n'.join([
        f'# Backdrop — {backdrop_id}', '', f'- Prompt: {request["prompt"]}', f'- Model: `{endpoint}`, {request["count"]} image(s), {aspect}',
        f'- Cost: about ${usd} (project budget ${budget})',
        '- Result: AI-generated still images, kept under backdrops/ with their provenance; one is placed far behind the shot '
        'and blurred. The mechanism in front stays Blender geometry.', '',
        f'Approve with: generate backdrop --project {path} --review {review_id} --user-words "<the user\'s words>" --allow-paid --max-usd {usd}'
        ' [--budget-usd N]',
    ]) + '\n', encoding='utf-8')
    return {'review_id': review_id, 'est_usd': usd, 'budget_usd': budget, 'sheet': str(directory / 'sheet.md'),
            'artifacts': [str(directory / 'sheet.md'), str(directory / 'review.json')]}


def generate(project, review_id, user_words, *, allow_paid=False, max_usd=None, budget_usd=None):
    """Send exactly the reviewed request, approved in the user's words, inside the project budget (a budget the user
    names in the same words is recorded first)."""
    path = project_dir(project)
    review_path = path / 'reviews' / f'backdrop_{check_id(review_id)}' / 'review.json'
    if not review_path.is_file():
        raise StudioError('ROUTE_REVIEW_MISSING', f'No backdrop review {review_id}',
                          recovery="Run generate backdrop-review, show the sheet, then pass its id with the user's words")
    record = read_json(review_path)
    request = record['request']
    _, fingerprint = _request(request['backdrop_id'], request['prompt'], request['endpoint'], request['count'], request['aspect'])
    if fingerprint != record['fingerprint']:
        raise StudioError('ROUTE_REVIEW_STALE', f'backdrop review {review_id} no longer matches its request', recovery='Run backdrop-review again')
    words = check_user_words(user_words, 'generate backdrop')
    project_data = load_project(path)
    if budget_usd is not None:   # the user's budget, in the same words as the approval
        project_data['route_policy'] = {**project_data.get('route_policy', {}), 'budget_usd': float(budget_usd)}
        project_data['revision'] += 1
        validate_schema(project_data, 'project'); write_json(path / 'project.json', project_data)
    record['approvals'].append({'user_words': words, 'at': now(), **({'budget_usd': float(budget_usd)} if budget_usd is not None else {})})
    write_json(review_path, record)
    budget = project_data.get('route_policy', {}).get('budget_usd', 0)
    per_call = estimate_usd(request['endpoint'])
    out_dir = path / 'backdrops' / review_id
    out_dir.mkdir(parents=True, exist_ok=True)
    images = []
    for i in range(request['count']):
        dest = path / 'requests' / 'backdrop' / f'{fingerprint[:16]}_{i}'
        paid = paid_call(request['endpoint'], {'prompt': request['prompt'], 'num_images': 1, 'aspect_ratio': request['aspect'],
                                               'output_format': 'png'},
                         dest, project_id=project_data['project_id'], allow_paid=allow_paid, max_usd=max_usd if max_usd is None else min(max_usd, per_call),
                         budget_usd=budget)
        files = [f for f in paid['files'] if Path(f['path']).suffix.lower() in IMAGE_SUFFIXES]
        if not files:
            raise StudioError('ASSET_NOT_SUITABLE', f"{request['endpoint']} returned no image")
        target = out_dir / f'{i}{Path(files[0]["path"]).suffix.lower()}'
        shutil.copy2(files[0]['path'], target)
        images.append({'image': str(target.relative_to(path)), 'request_id': paid['request_id'], 'estimated_usd': paid['estimated_usd']})
    write_json(out_dir / 'provenance.json', {'schema_version': 1, 'review_id': review_id, 'ai_generated': True, 'model': request['endpoint'],
                                            'prompt': request['prompt'], 'approval': {'user_words': words}, 'images': images,
                                            'license_id': 'fal-output (see provider terms)', 'license_evidence': 'https://fal.ai/terms',
                                            'created_at': now()})
    return {'review_id': review_id, 'images': [i['image'] for i in images], 'artifacts': [str(path / i['image']) for i in images]}


def register(commands):
    p = commands.add_parser('backdrop-review', help='Sheet for a generated backdrop image request (no call is made)')
    p.add_argument('--project', required=True); p.add_argument('--id', required=True); p.add_argument('--prompt')
    p.add_argument('--shot', help="compose the prompt from this shot's backdrop relation (view, light, centre) plus --place")
    p.add_argument('--place', help='what is there, in words (with --shot)')
    p.add_argument('--count', type=int, default=3); p.add_argument('--aspect', default='9:16', choices=ASPECTS)
    p.set_defaults(handler=lambda a: review(a.project, a.id, a.prompt, count=a.count, aspect=a.aspect, shot_id=a.shot, place=a.place))
    p = commands.add_parser('backdrop', help="PAID: generate the reviewed backdrop images, approved in the user's words")
    p.add_argument('--project', required=True); p.add_argument('--review', required=True); p.add_argument('--user-words', required=True)
    p.add_argument('--allow-paid', action='store_true'); p.add_argument('--max-usd', type=float); p.add_argument('--budget-usd', type=float)
    p.set_defaults(handler=lambda a: generate(a.project, a.review, a.user_words, allow_paid=a.allow_paid, max_usd=a.max_usd, budget_usd=a.budget_usd))
