"""Library sounds: music beds and sound effects a reel uses by id (project.audio.music, shot.sfx), registered once with
their licence (library/sounds/<id>/<version>/sound.json: file, sha256, duration, kind, source).

Why (2026-10-08): the edit mixed the voice only; reference reels carry a music bed under the narration and sounds on
the events (a cut opening, a tag appearing), and silence reads as unfinished. Sounds are data like characters: a
licence the clip can be published under (CC0, or CC-BY with attribution in sources.md), a hash, and a measured
duration; the edit (studio/edit.py) mixes them.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from .common import REPO, StudioError, check_id, now, read_json, stable_hash, write_json

ROOT = REPO / 'library' / 'sounds'
LICENCES = ('CC0-1.0', 'CC-BY-4.0')
KINDS = ('music', 'sfx')


def _duration(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)],
                         capture_output=True, text=True, timeout=60)
    try:
        return round(float(out.stdout.strip()), 3)
    except ValueError:
        raise StudioError('INPUT_INVALID', f'{path} is not an audio file ffprobe can read') from None


def register(sound_id, file, kind, licence, source_url, author=None, licence_file=None, tags=(), generated=None):
    sound_id = check_id(sound_id)
    if kind not in KINDS:
        raise StudioError('INPUT_INVALID', f'kind one of {KINDS}')
    if licence == 'fal-output' and not generated:   # a generated sound comes only through generate (sheet + the user's words)
        raise StudioError('INPUT_INVALID', 'fal-output sounds are registered by sound generate, with the approval')
    if licence not in LICENCES and licence != 'fal-output':
        raise StudioError('INPUT_INVALID', f'licence {licence!r}: only {LICENCES} are registered (the reel is published)')
    file = Path(file).resolve()
    duration = _duration(file)
    versions = sorted((ROOT / sound_id).glob('v*')) if (ROOT / sound_id).is_dir() else []
    dest = ROOT / sound_id / f'v{len(versions) + 1:04d}'
    dest.mkdir(parents=True)
    target = dest / file.name
    shutil.copy2(file, target)
    if licence_file:
        shutil.copy2(licence_file, dest / 'LICENSE.txt')
    manifest = {'schema_version': 1, 'sound_id': sound_id, 'version': dest.name, 'kind': kind, 'file': target.name,
                'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'duration_s': duration, 'tags': list(tags),
                'source': {'url': source_url, 'license_id': licence, 'author': author, 'retrieved_at': now(), 'use_status': 'cleared',
                           **({'ai_generated': True, **generated} if generated else {})}}
    write_json(dest / 'sound.json', manifest)
    return {'sound_id': sound_id, 'manifest': str(dest / 'sound.json'), 'duration_s': duration}


def load(sound):
    """(manifest, file path) of a sound id or id@version, the file checked against its sha256."""
    name, _, version = str(sound).partition('@')
    folder = ROOT / check_id(name)
    versions = sorted(folder.glob('v*')) if folder.is_dir() else []
    if not versions:
        known = sorted(p.name for p in ROOT.iterdir()) if ROOT.is_dir() else []
        raise StudioError('INPUT_INVALID', f'no sound {name!r} in the library (has {known})')
    chosen = folder / version if version else versions[-1]
    manifest = read_json(chosen / 'sound.json')
    path = chosen / manifest['file']
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest['sha256']:
        raise StudioError('INPUT_INVALID', f'sound {name}: {manifest["file"]} does not match its sha256')
    return manifest, path


def listing():
    rows = []
    for folder in sorted(ROOT.iterdir()) if ROOT.is_dir() else []:
        try:
            manifest, _ = load(folder.name)
            rows.append({k: manifest[k] for k in ('sound_id', 'kind', 'duration_s', 'tags')} | {'licence': manifest['source']['license_id']})
        except StudioError:
            continue
    return {'sounds': rows}


ENDPOINTS = {'sfx': 'fal-ai/elevenlabs/sound-effects/v2', 'music': 'fal-ai/elevenlabs/music'}
AUDIO_SUFFIXES = ('.mp3', '.wav', '.ogg', '.m4a')


def _request(sound_id, kind, prompt, seconds):
    check_id(sound_id)
    if kind not in KINDS:
        raise StudioError('INPUT_INVALID', f'kind one of {KINDS}')
    if not isinstance(prompt, str) or len(prompt.strip()) < 8:
        raise StudioError('INPUT_INVALID', 'describe the sound (8+ characters)')
    low, high = (0.5, 22.0) if kind == 'sfx' else (10.0, 300.0)
    if not low <= float(seconds) <= high:
        raise StudioError('INPUT_INVALID', f'{kind}: {low}-{high} seconds')
    request = {'sound_id': sound_id, 'kind': kind, 'prompt': prompt.strip(), 'seconds': float(seconds), 'endpoint': ENDPOINTS[kind]}
    return request, stable_hash(request)


def generate_review(project, sound_id, kind, prompt, seconds):
    """The sheet for one generated sound (no call): when no library sound fits, the user approves this request."""
    from .generative.fal_client import estimate_usd
    from .project import load_project, project_dir
    path = project_dir(project)
    request, fingerprint = _request(sound_id, kind, prompt, seconds)
    usd = estimate_usd(request['endpoint'], duration_seconds=request['seconds'] if kind == 'music' else None)
    review_id = fingerprint[:16]
    directory = path / 'reviews' / f'sound_{review_id}'
    record = read_json(directory / 'review.json') if (directory / 'review.json').is_file() else \
        {'schema_version': 1, 'review_id': review_id, 'kind': 'sound', 'created_at': now(), 'request': request, 'fingerprint': fingerprint,
         'est_usd': usd, 'approvals': []}
    write_json(directory / 'review.json', record)
    budget = load_project(path).get('route_policy', {}).get('budget_usd', 0)
    (directory / 'sheet.md').write_text('\n'.join([
        f'# Generated {kind} - {sound_id}', '', f'- Prompt: {request["prompt"]}', f'- Length: {request["seconds"]} s',
        f'- Model: `{request["endpoint"]}`', f'- Cost: about ${usd} (project budget ${budget})',
        '- Result: an AI-generated sound registered in library/sounds with this prompt and your approval.', '',
        f'Approve with: sound generate --project {path} --review {review_id} --user-words "<the user\'s words>" --allow-paid --max-usd {usd}',
    ]) + '\n', encoding='utf-8')
    return {'review_id': review_id, 'est_usd': usd, 'sheet': str(directory / 'sheet.md')}


def generate(project, review_id, user_words, *, allow_paid=False, max_usd=None):
    """Send exactly the reviewed request in the user's words, then register the sound (licence: the provider's terms
    for its output, with the approval)."""
    from .generative.fal_client import paid_call
    from .generative.review import check_user_words
    from .project import load_project, project_dir
    path = project_dir(project)
    review_path = path / 'reviews' / f'sound_{check_id(review_id)}' / 'review.json'
    if not review_path.is_file():
        raise StudioError('ROUTE_REVIEW_MISSING', f'No sound review {review_id}')
    record = read_json(review_path)
    request = record['request']
    if _request(request['sound_id'], request['kind'], request['prompt'], request['seconds'])[1] != record['fingerprint']:
        raise StudioError('ROUTE_REVIEW_STALE', f'sound review {review_id} no longer matches its request')
    words = check_user_words(user_words, 'generate a sound')
    record['approvals'].append({'user_words': words, 'at': now()})
    write_json(review_path, record)
    arguments = ({'text': request['prompt'], 'duration_seconds': request['seconds'], 'output_format': 'mp3_44100_128'} if request['kind'] == 'sfx'
                 else {'prompt': request['prompt'], 'music_length_ms': int(request['seconds'] * 1000), 'force_instrumental': True})
    project_data = load_project(path)
    paid = paid_call(request['endpoint'], arguments, path / 'requests' / 'sound' / record['fingerprint'][:16], project_id=project_data['project_id'],
                     allow_paid=allow_paid, max_usd=max_usd, budget_usd=project_data.get('route_policy', {}).get('budget_usd', 0),
                     duration_seconds=request['seconds'] if request['kind'] == 'music' else None)
    files = [f for f in paid['files'] if Path(f['path']).suffix.lower() in AUDIO_SUFFIXES]
    if not files:
        raise StudioError('ASSET_NOT_SUITABLE', f"{request['endpoint']} returned no audio")
    return register(request['sound_id'], files[0]['path'], request['kind'], 'fal-output', f"fal:{request['endpoint']}",
                    tags=['generated'], generated={'prompt': request['prompt'], 'approval': words, 'request_id': paid['request_id']})


def register_commands(subparsers):
    parser = subparsers.add_parser('sound', help='Library sounds: music beds and effects a reel uses by id')
    commands = parser.add_subparsers(dest='sound_command', required=True)
    p = commands.add_parser('register')
    p.add_argument('--id', required=True); p.add_argument('--file', required=True); p.add_argument('--kind', required=True, choices=KINDS)
    p.add_argument('--licence', required=True, choices=LICENCES); p.add_argument('--source-url', required=True); p.add_argument('--author')
    p.add_argument('--licence-file'); p.add_argument('--tag', action='append', default=[])
    p.set_defaults(handler=lambda a: register(a.id, a.file, a.kind, a.licence, a.source_url, a.author, a.licence_file, a.tag))
    p = commands.add_parser('list')
    p.set_defaults(handler=lambda a: listing())
    p = commands.add_parser('generate-review', help='Sheet for one generated sound when no library sound fits (no call)')
    p.add_argument('--project', required=True); p.add_argument('--id', required=True); p.add_argument('--kind', required=True, choices=KINDS)
    p.add_argument('--prompt', required=True); p.add_argument('--seconds', type=float, required=True)
    p.set_defaults(handler=lambda a: generate_review(a.project, a.id, a.kind, a.prompt, a.seconds))
    p = commands.add_parser('generate', help="PAID: the reviewed sound, approved in the user's words, into library/sounds")
    p.add_argument('--project', required=True); p.add_argument('--review', required=True); p.add_argument('--user-words', required=True)
    p.add_argument('--allow-paid', action='store_true'); p.add_argument('--max-usd', type=float)
    p.set_defaults(handler=lambda a: generate(a.project, a.review, a.user_words, allow_paid=a.allow_paid, max_usd=a.max_usd))
