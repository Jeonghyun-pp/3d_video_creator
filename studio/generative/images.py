"""Generated still images (paid, fal) beyond backdrops - the narrow uses of generation that leave structure to Blender:
a tileable surface texture, a plate (a sky, a distant city) or an edit of one of the project's images (a plate at dusk
instead of noon). Same gates as every paid request: `generate image-review` writes a sheet for exactly one request,
the user approves it in their own words, `generate image` sends that request inside the budget, and every image keeps
its provenance (ai_generated, model, prompt, the approval).

Why (2026-10-08): the only generation path was a whole-frame video restyle, and every measured one failed the structure
test; realism then fell back on ten catalog materials. A texture or a plate is generation where it is strong, inside
geometry and a camera Blender still owns.

A generated texture becomes a material only through `generate register-texture` with the user's words: the image is
copied into library/assets as a texture set (roughness and height derived from its luminance, so the catalog's three
maps exist) and any catalog kind uses it with `catalog_overrides: {texset: <asset id>}`.
"""
from __future__ import annotations

import base64
import hashlib
import shutil
from pathlib import Path

from ..common import REPO, StudioError, check_id, now, read_json, safe_path, stable_hash, write_json
from ..project import load_project, project_dir, validate_schema
from .fal_client import estimate_usd, paid_call
from .review import check_user_words

ENDPOINTS = {'texture': 'fal-ai/nano-banana-pro', 'plate': 'fal-ai/nano-banana-pro', 'edit': 'fal-ai/nano-banana-pro/edit'}
ASPECTS = ('9:16', '16:9', '1:1', '3:4', '4:3')
IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.webp')
TEXTURE_PREFIX = ('Seamless tileable square texture, flat orthographic top-down view, even diffuse light, no shadows, no perspective, '
                  'no vignette; the pattern repeats on all edges. ')


def _request(image_id, purpose, prompt, count, aspect, source=None):
    from ..routing import NO_TEXT
    check_id(image_id)
    if purpose not in ENDPOINTS:
        raise StudioError('INPUT_INVALID', f'image purpose {purpose!r}: one of {sorted(ENDPOINTS)}')
    if not isinstance(prompt, str) or len(prompt.strip()) < 12:
        raise StudioError('INPUT_INVALID', 'image: describe what to make (12+ characters)')
    if not NO_TEXT.search(prompt):   # words never live in generated pixels (SKILL #2): a sign's words are a decal we draw
        raise StudioError('INPUT_INVALID', 'image: generated images contain no text - say so in the prompt ("no text, no logos")')
    if purpose == 'edit' and not source:
        raise StudioError('INPUT_INVALID', 'image edit: give --source, the project image to edit')
    if aspect not in ASPECTS or not 1 <= int(count) <= 4:
        raise StudioError('INPUT_INVALID', f'image: aspect one of {ASPECTS}, count 1-4')
    prompt = (TEXTURE_PREFIX if purpose == 'texture' else '') + prompt.strip()
    request = {'image_id': image_id, 'purpose': purpose, 'prompt': prompt, 'endpoint': ENDPOINTS[purpose], 'count': int(count),
               'aspect': '1:1' if purpose == 'texture' else aspect, **({'source': source} if source else {})}
    return request, stable_hash(request)


def review(project, image_id, purpose, prompt, *, count=2, aspect='9:16', source=None):
    """The sheet the user approves: one request, its price, what is made. No call is made."""
    path = project_dir(project)
    if source:
        source_file = safe_path(path, source)
        if not source_file.is_file():
            raise StudioError('INPUT_INVALID', f'image edit: {source} is not a file in the project')
        from ..routing import assert_sendable
        assert_sendable(path, source_file, 'image edit source')
        source = {'path': source, 'sha256': hashlib.sha256(source_file.read_bytes()).hexdigest()}
    request, fingerprint = _request(image_id, purpose, prompt, count, aspect, source)
    usd = round(estimate_usd(request['endpoint']) * request['count'], 2)
    budget = load_project(path).get('route_policy', {}).get('budget_usd', 0)
    review_id = fingerprint[:16]
    directory = path / 'reviews' / f'image_{review_id}'
    record = read_json(directory / 'review.json') if (directory / 'review.json').is_file() else \
        {'schema_version': 1, 'review_id': review_id, 'kind': 'image', 'created_at': now(), 'request': request,
         'fingerprint': fingerprint, 'est_usd': usd, 'approvals': []}
    write_json(directory / 'review.json', record)
    use = {'texture': 'a tileable surface texture; it becomes a material only after generate register-texture with your words',
           'plate': 'a still placed far behind a shot (shot.scene.backdrop) - mood around exact Blender geometry',
           'edit': f"an edited copy of {source['path'] if source else ''}"}[purpose]
    (directory / 'sheet.md').write_text('\n'.join([
        f'# Generated image — {image_id} ({purpose})', '', f'- Prompt: {request["prompt"]}',
        f'- Model: `{request["endpoint"]}`, {request["count"]} image(s), {request["aspect"]}', f'- Cost: about ${usd} (project budget ${budget})',
        f'- Use: {use}. AI-generated; kept under images/ with provenance.', '',
        f'Approve with: generate image --project {path} --review {review_id} --user-words "<the user\'s words>" --allow-paid --max-usd {usd}',
    ]) + '\n', encoding='utf-8')
    return {'review_id': review_id, 'est_usd': usd, 'budget_usd': budget, 'sheet': str(directory / 'sheet.md'),
            'artifacts': [str(directory / 'sheet.md'), str(directory / 'review.json')]}


def arguments(request, path):
    """The fal arguments for a reviewed request (the source image inline, as every input is: no upload API)."""
    args = {'prompt': request['prompt'], 'num_images': 1, 'aspect_ratio': request['aspect'], 'output_format': 'png'}
    if request['purpose'] == 'edit':
        source = safe_path(path, request['source']['path'])
        blob = source.read_bytes()
        if hashlib.sha256(blob).hexdigest() != request['source']['sha256']:
            raise StudioError('ROUTE_REVIEW_STALE', f"image edit: {request['source']['path']} changed since the review")
        mime = 'image/png' if source.suffix.lower() == '.png' else 'image/jpeg'
        args['image_urls'] = [f'data:{mime};base64,' + base64.b64encode(blob).decode()]
    return args


FAILED_STATES = ('remote_failed', 'reconciled')   # fal_client: the provider failed it, or the user settled it from the dashboard


def attempt_dest(root, key, retry_failed=False):
    """The request folder of one image slot: `key`, then `key_r2`, `key_r3`... A new attempt only when the user asked for
    one (retry_failed, in their words) and every earlier attempt of the slot ended failed (FAILED_STATES); a slot that
    succeeded, is in flight or is unknown always answers from its own folder (free, or refused as before).
    Why (2026-10-12 audit): one failed image blocked its whole review for good - the folder is the review's fingerprint."""
    attempt = 1
    while True:
        dest = Path(root) / (key if attempt == 1 else f'{key}_r{attempt}')
        state = read_json(dest / 'state.json').get('state') if (dest / 'state.json').is_file() else None
        if not retry_failed or (dest / 'response.json').is_file() or state not in FAILED_STATES:
            return dest
        attempt += 1


def generate(project, review_id, user_words, *, allow_paid=False, max_usd=None, budget_usd=None, retry_failed=False):
    """Send exactly the reviewed request, approved in the user's words, inside the project budget. retry_failed: a slot
    whose earlier attempts failed at the provider is tried again (attempt_dest); slots already made are reused."""
    path = project_dir(project)
    review_path = path / 'reviews' / f'image_{check_id(review_id)}' / 'review.json'
    if not review_path.is_file():
        raise StudioError('ROUTE_REVIEW_MISSING', f'No image review {review_id}', recovery="Run generate image-review, show the sheet, then pass its id with the user's words")
    record = read_json(review_path)
    request = record['request']
    _, fingerprint = _request(request['image_id'], request['purpose'], request['prompt'][len(TEXTURE_PREFIX):] if request['purpose'] == 'texture' else request['prompt'],
                              request['count'], request['aspect'], request.get('source'))
    if fingerprint != record['fingerprint']:
        raise StudioError('ROUTE_REVIEW_STALE', f'image review {review_id} no longer matches its request', recovery='Run image-review again')
    words = check_user_words(user_words, 'generate image')
    project_data = load_project(path)
    if budget_usd is not None:
        project_data['route_policy'] = {**project_data.get('route_policy', {}), 'budget_usd': float(budget_usd)}
        project_data['revision'] += 1
        validate_schema(project_data, 'project'); write_json(path / 'project.json', project_data)
    record['approvals'].append({'user_words': words, 'at': now(), **({'budget_usd': float(budget_usd)} if budget_usd is not None else {}),
                                **({'retry_failed': True} if retry_failed else {})})
    write_json(review_path, record)
    budget = project_data.get('route_policy', {}).get('budget_usd', 0)
    per_call = estimate_usd(request['endpoint'])
    out_dir = path / 'images' / review_id
    out_dir.mkdir(parents=True, exist_ok=True)
    images = []
    for i in range(request['count']):
        dest = attempt_dest(path / 'requests' / 'image', f'{fingerprint[:16]}_{i}', retry_failed)
        paid = paid_call(request['endpoint'], arguments(request, path), dest, project_id=project_data['project_id'], allow_paid=allow_paid,
                         max_usd=max_usd if max_usd is None else min(max_usd, per_call), budget_usd=budget)
        files = [f for f in paid['files'] if Path(f['path']).suffix.lower() in IMAGE_SUFFIXES]
        if not files:
            raise StudioError('ASSET_NOT_SUITABLE', f"{request['endpoint']} returned no image")
        target = out_dir / f'{i}{Path(files[0]["path"]).suffix.lower()}'
        shutil.copy2(files[0]['path'], target)
        images.append({'image': str(target.relative_to(path)), 'request_id': paid['request_id'], 'estimated_usd': paid['estimated_usd']})
    write_json(out_dir / 'provenance.json', {'schema_version': 1, 'review_id': review_id, 'purpose': request['purpose'], 'ai_generated': True,
                                            'model': request['endpoint'], 'prompt': request['prompt'], 'approval': {'user_words': words},
                                            'images': images, 'license_id': 'fal-output (see provider terms)',
                                            'license_evidence': 'https://fal.ai/terms', 'created_at': now()})
    return {'review_id': review_id, 'images': [i['image'] for i in images], 'artifacts': [str(path / i['image']) for i in images]}


def derived_maps(image_path, out_dir):
    """Roughness and height from a colour texture's luminance (a single generated image has neither): height = the
    luminance softened (lighter is higher), roughness = 0.55-0.95 from the inverse luminance (darker grooves rougher)."""
    from PIL import Image, ImageFilter, ImageOps
    colour = Image.open(image_path).convert('RGB')
    lum = ImageOps.grayscale(colour)
    height = lum.filter(ImageFilter.GaussianBlur(2))
    rough = ImageOps.invert(lum).point(lambda v: int(140 + v * (242 - 140) / 255))
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {'base_color': out_dir / 'color.png', 'roughness': out_dir / 'roughness.png', 'displacement': out_dir / 'height.png'}
    colour.save(paths['base_color']); rough.save(paths['roughness']); height.save(paths['displacement'])
    return paths


LOCAL_TEXTURE_SOURCES = {   # an image made without a paid call: who made it and on what terms (recorded with the user's words)
    'codex_image_gen': {'ai_generated': True, 'license_id': 'model-output', 'license_evidence': "the image tool's terms give the output to the user"},
    'photo': {'ai_generated': False, 'license_id': 'user-owned', 'license_evidence': "the user's own photograph, asserted in their words"},
}


def register_texture(project, image, asset_id, tile_m, user_words, source_kind=None, prompt=None):
    """A texture into the library as a cleared texture set, in the user's words; the catalog then reads it by asset id.
    A paid generation (images/<review>/<n>.png) carries its provenance; an image made without a paid call - Codex's
    built-in image tool, or the user's own photograph - names its source (LOCAL_TEXTURE_SOURCES) and, for a generated
    one, its prompt. Why (2026-10-10): the free path to a real surface was closed - only paid images could become
    materials, so floor layers stayed one grey."""
    path = project_dir(project)
    asset_id = check_id(asset_id)
    words = check_user_words(user_words, 'register a texture')
    source = safe_path(path, image)
    if not source.is_file():
        raise StudioError('INPUT_INVALID', f'no image {image}')
    provenance_file = source.parent / 'provenance.json'
    if source_kind:
        if source_kind not in LOCAL_TEXTURE_SOURCES:
            raise StudioError('INPUT_INVALID', f'source {source_kind}: one of {sorted(LOCAL_TEXTURE_SOURCES)} (a paid image carries its own provenance)')
        if source_kind == 'codex_image_gen' and not (prompt and prompt.strip()):
            raise StudioError('INPUT_INVALID', 'a generated texture records its prompt (--prompt)')
        terms = LOCAL_TEXTURE_SOURCES[source_kind]
        provenance = {'model': source_kind, 'prompt': prompt, 'license_id': terms['license_id'], 'license_evidence': terms['license_evidence'],
                      'created_at': now(), 'provider': source_kind, 'ai_generated': terms['ai_generated']}
    else:
        if not provenance_file.is_file():
            raise StudioError('INPUT_INVALID', f'{image} is not a generated image with provenance (images/<review>/<n>.png); '
                                               f'an image made without a paid call names --source {"|".join(sorted(LOCAL_TEXTURE_SOURCES))}')
        provenance = {**read_json(provenance_file), 'provider': 'fal', 'ai_generated': True}
        if provenance.get('purpose') != 'texture':
            raise StudioError('INPUT_INVALID', f"{image} was generated as a {provenance.get('purpose')}, not a texture")
    if not 0.05 <= float(tile_m) <= 20:
        raise StudioError('INPUT_INVALID', 'tile_m: the real size one tile covers, 0.05-20 m')
    dest = REPO / 'library' / 'assets' / asset_id / 'v0001'
    if dest.exists():
        raise StudioError('REVISION_CONFLICT', f'library asset {asset_id} exists')
    maps = derived_maps(source, dest / 'original')
    files = [{'path': f'original/{p.name}', 'relative_path': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
              'bytes': p.stat().st_size, 'role': role} for role, p in maps.items()]
    write_json(dest / 'asset.json', {'schema_version': 1, 'asset_id': asset_id, 'version': 'v0001', 'name': asset_id, 'tags': ['generated', 'texture'],
                                     'source': {'provider': provenance['provider'], 'model': provenance['model'], 'prompt': provenance['prompt'], 'license_id': provenance['license_id'],
                                                'license_evidence': provenance['license_evidence'], 'use_status': 'cleared', 'ai_generated': provenance['ai_generated'],
                                                'license_asserted_by': 'user', 'approval': {'user_words': words, 'at': now()},
                                                'retrieved_at': provenance['created_at']},
                                     'files': files,
                                     'texset': {'asset_id': asset_id, 'tile_m': float(tile_m), 'size_source': 'declared at registration',
                                                'maps': {'color': 'base_color', 'rough': 'roughness', 'height': 'displacement'}}})
    return {'asset_id': asset_id, 'manifest': str(dest / 'asset.json'), 'use': f"catalog_overrides: {{'texset': '{asset_id}'}}"}


def register(commands):
    p = commands.add_parser('image-review', help='Sheet for a generated still (texture, plate, or an edit of a project image); no call')
    p.add_argument('--project', required=True); p.add_argument('--id', required=True)
    p.add_argument('--purpose', required=True, choices=sorted(ENDPOINTS)); p.add_argument('--prompt', required=True)
    p.add_argument('--count', type=int, default=2); p.add_argument('--aspect', default='9:16', choices=ASPECTS); p.add_argument('--source')
    p.set_defaults(handler=lambda a: review(a.project, a.id, a.purpose, a.prompt, count=a.count, aspect=a.aspect, source=a.source))
    p = commands.add_parser('image', help="PAID: generate the reviewed images, approved in the user's words")
    p.add_argument('--project', required=True); p.add_argument('--review', required=True); p.add_argument('--user-words', required=True)
    p.add_argument('--allow-paid', action='store_true'); p.add_argument('--max-usd', type=float); p.add_argument('--budget-usd', type=float)
    p.add_argument('--retry-failed', action='store_true', help="a new attempt for images the provider failed (the user's words say so)")
    p.set_defaults(handler=lambda a: generate(a.project, a.review, a.user_words, allow_paid=a.allow_paid, max_usd=a.max_usd, budget_usd=a.budget_usd,
                                              retry_failed=a.retry_failed))
    p = commands.add_parser('register-texture', help="A generated texture into the library as a texture set, in the user's words")
    p.add_argument('--project', required=True); p.add_argument('--image', required=True); p.add_argument('--id', required=True)
    p.add_argument('--tile-m', type=float, required=True); p.add_argument('--user-words', required=True)
    p.add_argument('--source', choices=sorted(LOCAL_TEXTURE_SOURCES), help='an image made without a paid call (copy it into the project first)')
    p.add_argument('--prompt', help='the prompt a generated image was made with')
    p.set_defaults(handler=lambda a: register_texture(a.project, a.image, a.id, a.tile_m, a.user_words, a.source, a.prompt))
