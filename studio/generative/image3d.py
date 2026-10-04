"""Image -> 3D through fal (paid), registered as an AI-generated, review-only asset.

Generated meshes invent unseen sides (castings got an imagined back face in the 10-03 test),
so they are never cleared automatically: a human approves the turnaround before any final render.
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from ..assets import fetch_trusted
from ..common import REPO, StudioError, check_id, file_hash
from .fal_client import paid_call

MESH_SUFFIXES = ('.glb', '.gltf', '.obj', '.fbx')


def _data_uri(image):
    image = Path(image)
    kind = mimetypes.guess_type(image.name)[0] or 'image/png'
    return f'data:{kind};base64,' + base64.b64encode(image.read_bytes()).decode()


def image_to_3d(image, asset_id, *, endpoint='fal-ai/hyper3d/rodin/v2.5', real_dimension=None, allow_paid=False, max_usd=None,
                project_id='library', budget_usd=None, asset_root=None, extra_arguments=None):
    check_id(asset_id)
    image = Path(image).resolve()
    if not image.is_file():
        raise StudioError('INPUT_INVALID', f'Image not found: {image}')
    if not real_dimension or real_dimension.get('meters', 0) <= 0 or real_dimension.get('dimension') not in ('longest', 'x', 'y', 'z'):
        raise StudioError('INPUT_INVALID', 'Generated meshes need a real dimension, e.g. longest=0.30 (metres)')
    image_sha = file_hash(image)
    request_dir = REPO / '.studio' / 'requests' / 'image3d' / f'{asset_id}_{image_sha[:12]}'
    arguments = {'input_image_urls': [_data_uri(image)], **(extra_arguments or {})}
    record = paid_call(endpoint, arguments, request_dir, project_id=project_id, allow_paid=allow_paid, max_usd=max_usd, budget_usd=budget_usd)
    meshes = [f for f in record['files'] if Path(f['path']).suffix.lower() in MESH_SUFFIXES]
    if not meshes:
        raise StudioError('ASSET_NOT_SUITABLE', f'{endpoint} returned no mesh file')
    candidate = {'asset_id': asset_id, 'provider': 'fal', 'path': meshes[0]['path'], 'scale_basis': real_dimension}
    trusted = {'model': endpoint, 'request_id': record['request_id'], 'estimated_usd': record['estimated_usd'],
               'input_image_sha256': image_sha, 'use_status': 'review_only', 'ai_generated': True,
               'license_id': 'fal-output (see provider terms)', 'license_evidence': 'https://fal.ai/terms'}
    return fetch_trusted(candidate, trusted, asset_root)
