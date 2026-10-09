"""Image -> 3D through fal (paid), registered as an AI-generated, review-only asset.

Generated meshes invent unseen sides (castings got an imagined back face in the 10-03 test),
so they are never cleared automatically: a human approves the turnaround before any final render.

Paid like a generated clip: `asset image3d-review` writes a sheet for exactly this request (image, endpoint,
arguments, cost); the user approves it in their own words; `asset image3d --review <id> --user-words "..."`
then sends that request and nothing else, inside the project's budget.
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from ..assets import fetch_trusted
from ..common import StudioError, check_id, file_hash, now, read_json, stable_hash, write_json
from ..project import load_project, project_dir
from .fal_client import estimate_usd, paid_call
from .review import check_user_words

MESH_SUFFIXES = ('.glb', '.gltf', '.obj', '.fbx')
DEFAULT_ENDPOINT = 'fal-ai/hyper3d/rodin/v2.5'


def _data_uri(image):
    image = Path(image)
    kind = mimetypes.guess_type(image.name)[0] or 'image/png'
    return f'data:{kind};base64,' + base64.b64encode(image.read_bytes()).decode()


def _request(image, asset_id, endpoint, real_dimension, extra_arguments):
    check_id(asset_id)
    image = Path(image).resolve()
    if not image.is_file():
        raise StudioError('INPUT_INVALID', f'Image not found: {image}')
    if not real_dimension or real_dimension.get('meters', 0) <= 0 or real_dimension.get('dimension') not in ('longest', 'x', 'y', 'z'):
        raise StudioError('INPUT_INVALID', 'Generated meshes need a real dimension, e.g. longest=0.30 (metres)')
    request = {'asset_id': asset_id, 'image': str(image), 'image_sha256': file_hash(image), 'endpoint': endpoint,
               'real_dimension': real_dimension, 'extra_arguments': extra_arguments or {}}
    return request, stable_hash(request)


def review(project, image, asset_id, *, endpoint=DEFAULT_ENDPOINT, real_dimension=None, extra_arguments=None):
    """Write the sheet the user approves: one request, its price, what happens to the result. No call is made."""
    path = project_dir(project)
    project_data = load_project(path)
    from ..routing import assert_sendable
    assert_sendable(path, image, 'image3d input')
    request, fingerprint = _request(image, asset_id, endpoint, real_dimension, extra_arguments)
    usd = estimate_usd(endpoint)
    budget = project_data.get('route_policy', {}).get('budget_usd', 0)
    review_id = fingerprint[:16]
    directory = path / 'reviews' / f'image3d_{review_id}'
    record = read_json(directory / 'review.json') if (directory / 'review.json').is_file() else \
        {'schema_version': 1, 'review_id': review_id, 'kind': 'image3d', 'created_at': now(), 'request': request,
         'fingerprint': fingerprint, 'est_usd': usd, 'approvals': []}
    write_json(directory / 'review.json', record)
    (directory / 'sheet.md').write_text('\n'.join([
        f'# Image to 3D — {asset_id}', '', f'- Image: `{request["image"]}` (sha256 {request["image_sha256"][:12]})',
        f'- Model: `{endpoint}`', f'- Real size: {real_dimension["dimension"]} = {real_dimension["meters"]} m',
        f'- Cost: about ${usd} per call (project budget ${budget})',
        '- Result: an AI-generated mesh, registered review_only; its unseen sides are invented, so a person approves the '
        'turnaround (front/back/side) before any final render.', '',
        f'Approve with: asset image3d --project {path} --review {review_id} --user-words "<the user\'s words>" --allow-paid --max-usd {usd}',
    ]) + '\n', encoding='utf-8')
    return {'review_id': review_id, 'est_usd': usd, 'budget_usd': budget, 'sheet': str(directory / 'sheet.md'),
            'artifacts': [str(directory / 'sheet.md'), str(directory / 'review.json')]}


def image_to_3d(project, review_id, user_words, *, allow_paid=False, max_usd=None, asset_root=None):
    """Send exactly the reviewed request, approved in the user's words, inside the project budget."""
    path = project_dir(project)
    project_data = load_project(path)
    review_path = path / 'reviews' / f'image3d_{check_id(review_id)}' / 'review.json'
    if not review_path.is_file():
        raise StudioError('ROUTE_REVIEW_MISSING', f'No image3d review {review_id}',
                          recovery='Run asset image3d-review, show the sheet, then pass its id with the user\'s words')
    record = read_json(review_path)
    request = record['request']
    current, fingerprint = _request(request['image'], request['asset_id'], request['endpoint'], request['real_dimension'],
                                    request['extra_arguments'])
    if fingerprint != record['fingerprint']:
        raise StudioError('ROUTE_REVIEW_STALE', f'The image changed after review {review_id}', recovery='Run asset image3d-review again')
    words = check_user_words(user_words)
    record['approvals'].append({'user_words': words, 'at': now()})
    write_json(review_path, record)
    budget = project_data.get('route_policy', {}).get('budget_usd', 0)
    request_dir = path / 'requests' / 'image3d' / fingerprint[:16]
    arguments = {'input_image_urls': [_data_uri(current['image'])], **current['extra_arguments']}
    paid = paid_call(current['endpoint'], arguments, request_dir, project_id=project_data['project_id'], allow_paid=allow_paid,
                     max_usd=max_usd, budget_usd=budget)
    meshes = [f for f in paid['files'] if Path(f['path']).suffix.lower() in MESH_SUFFIXES]
    if not meshes:
        raise StudioError('ASSET_NOT_SUITABLE', f"{current['endpoint']} returned no mesh file")
    candidate = {'asset_id': current['asset_id'], 'provider': 'fal', 'path': meshes[0]['path'], 'scale_basis': current['real_dimension']}
    trusted = {'model': current['endpoint'], 'request_id': paid['request_id'], 'estimated_usd': paid['estimated_usd'],
               'input_image_sha256': current['image_sha256'], 'use_status': 'review_only', 'ai_generated': True,
               'license_id': 'fal-output (see provider terms)', 'license_evidence': 'https://fal.ai/terms',
               'approval': {'review_id': review_id, 'user_words': words}}
    return fetch_trusted(candidate, trusted, asset_root)
