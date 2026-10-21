"""Collect licensed reference photos of a real subject from an allow-listed source, with their provenance.

Why (2026-10-11, rocket_landing): the booster was modelled from text sources only - its spec report says "no licensed
drawing, reference mask or calibrated photographic view was supplied" - and the board read as "a grey model shaped
like a rocket", not a Falcon 9. The photo match tools were there (reference view add, reference_fit_camera,
photo_views); nothing let the agent get a photo: its sandbox and the broker have no network. This module is the one way
in: a job the broker starts outside the sandbox (broker.NETWORK_ENTRIES '_reference_worker', frozen group
network_worker), reaching only the hosts of SOURCES, keeping only files whose licence the source itself states as one
of LICENCE_PATTERNS, writing only inside <project>/references/photos with an index row per file.

A collected photo is for comparison here (licence local_only on its reference view): it never goes to a generation
model without the user's words, whatever its licence.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .common import StudioError, file_hash, now, read_json, write_json

PHOTOS_DIR = 'references/photos'   # project-relative; the only place this module writes
INDEX = 'index.json'
THUMB_WIDTH = 1600                 # enough to mark landmarks and compare a silhouette; the original can be 50 MB
MAX_BYTES = 20 * 1024 * 1024       # one file
MAX_FILES = 50                     # one job (titles or search hits)
PAUSE_S = 1.0                      # between downloads: measured 2026-10-11, Commons answers 429 to back-to-back fetches
USER_AGENT = 'studio-reference-collect/1.0 (local shape comparison; no redistribution)'
IMAGE_TYPES = ('image/jpeg', 'image/png')

# Sources the network worker may reach: the API host and the file hosts. Default deny - a source not listed here is
# refused, and every URL (redirects included) is checked against its own source's hosts.
SOURCES = {
    'wikimedia_commons': {'api': 'https://commons.wikimedia.org/w/api.php',
                          'hosts': ('commons.wikimedia.org', 'upload.wikimedia.org', 'thumb.wikimedia.org'),
                          'prefix': 'commons'},
}

# Licences a collected photo may carry, matched against the licence name the source states (never the agent's word).
# Free licences only: public domain, CC0, CC BY and CC BY-SA in any version or port. NC / ND are refused because a reel
# is published; an unknown or missing licence is refused.
LICENCE_PATTERNS = (r'cc0( 1\.0)?', r'public domain', r'pd(-[a-z0-9.-]+)?', r'no restrictions',
                    r'cc by(-sa)? \d(\.\d)?( [a-z]+)?')
LICENCE_REFUSE = re.compile(r'\b(nc|nd)\b|non-?commercial|no ?deriv', re.I)


def licence_ok(name):
    """True for a licence name the source states that LICENCE_PATTERNS allow."""
    text = re.sub(r'\s+', ' ', str(name or '')).strip().lower()
    if not text or LICENCE_REFUSE.search(text):
        return False
    return any(re.fullmatch(p, text) for p in LICENCE_PATTERNS)


def allowed_url(url, source):
    """True when url is https on one of the source's hosts."""
    parts = urllib.parse.urlparse(str(url))
    return parts.scheme == 'https' and parts.hostname in SOURCES[source]['hosts']


def _strip(html):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', str(html or ''))).strip()


def rows_from_api(data, source='wikimedia_commons'):
    """(candidates, refused) from a Commons API answer with prop=imageinfo: one candidate per file whose licence and
    type pass, each refused file with why. Pure: the tests feed it a recorded answer."""
    pages = sorted(((data or {}).get('query') or {}).get('pages', {}).values(), key=lambda p: (p.get('index', 0), p.get('title', '')))
    candidates, refused = [], []
    for page in pages:
        title = page.get('title', '')
        info = (page.get('imageinfo') or [None])[0]
        if not info:
            refused.append({'title': title, 'why': 'missing' if 'missing' in page else 'no image information'})
            continue
        meta = info.get('extmetadata') or {}
        licence = _strip((meta.get('LicenseShortName') or {}).get('value'))
        if not licence_ok(licence):
            refused.append({'title': title, 'licence': licence or None, 'why': 'licence not allowed (free licences only: PD, CC0, CC BY, CC BY-SA)'})
            continue
        if info.get('mime') not in IMAGE_TYPES:
            refused.append({'title': title, 'licence': licence, 'why': f"not a photo ({info.get('mime')})"})
            continue
        if not isinstance(page.get('pageid'), int):   # it names the stored file
            refused.append({'title': title, 'why': f"no numeric page id ({page.get('pageid')!r})"})
            continue
        url = info.get('thumburl') or info.get('url')
        if not allowed_url(url, source):
            refused.append({'title': title, 'licence': licence, 'why': f'file host not allowed: {url}'})
            continue
        candidates.append({'source': source, 'page_id': page.get('pageid'), 'title': title, 'url': url,
                           'page': info.get('descriptionurl'), 'licence': licence,
                           'licence_url': _strip((meta.get('LicenseUrl') or {}).get('value')) or None,
                           'artist': _strip((meta.get('Artist') or {}).get('value'))[:300] or None,
                           'description': _strip((meta.get('ImageDescription') or {}).get('value'))[:300] or None,
                           'taken': _strip((meta.get('DateTimeOriginal') or {}).get('value'))[:40] or None,
                           'size': [info.get('width'), info.get('height')]})
    return candidates, refused


class _SameSource(urllib.request.HTTPRedirectHandler):
    """A redirect stays on the source's hosts."""
    def __init__(self, source):
        self.source = source

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(newurl, self.source):
            raise StudioError('REFERENCE_FETCH_REFUSED', f'redirect off the allowed hosts: {newurl}')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _get(url, source, limit=MAX_BYTES):
    """(bytes, content type) of an allowed URL, at most limit bytes; 429/5xx are retried with a growing pause."""
    if not allowed_url(url, source):
        raise StudioError('REFERENCE_FETCH_REFUSED', f'host not allowed for {source}: {url}')
    opener = urllib.request.build_opener(_SameSource(source))
    for attempt in range(5):
        try:
            with opener.open(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=60) as answer:
                body = answer.read(limit + 1)
                if len(body) > limit:
                    raise StudioError('REFERENCE_FETCH_REFUSED', f'larger than {limit} bytes: {url}')
                return body, (answer.headers.get('Content-Type') or '').split(';')[0].strip()
        except urllib.error.HTTPError as error:
            if error.code in (429, 500, 502, 503, 504) and attempt < 4:
                time.sleep(5 * (attempt + 1))
                continue
            raise StudioError('REFERENCE_FETCH_FAILED', f'{error.code} from {url}') from None
        except urllib.error.URLError as error:
            raise StudioError('REFERENCE_FETCH_FAILED', f'{url}: {error.reason}') from None
    raise StudioError('REFERENCE_FETCH_FAILED', f'{url}: retries exhausted')


def _api(source, params):
    query = {'action': 'query', 'format': 'json', 'prop': 'imageinfo', 'iiprop': 'url|size|mime|extmetadata',
             'iiurlwidth': THUMB_WIDTH, 'iiextmetadatafilter': 'LicenseShortName|LicenseUrl|Artist|ImageDescription|DateTimeOriginal',
             **params}
    body, _ = _get(SOURCES[source]['api'] + '?' + urllib.parse.urlencode(query), source, limit=MAX_BYTES)
    return json.loads(body)


def store_image(body, content_type, path):
    """Decode the download as an image and write it re-encoded as JPEG (nothing but pixels is kept)."""
    from PIL import Image
    if content_type not in IMAGE_TYPES:
        raise StudioError('REFERENCE_FETCH_REFUSED', f'not an image ({content_type})')
    try:
        with Image.open(io.BytesIO(body)) as image:
            image.load()
            pixels = image.convert('RGB')
    except Exception as exc:   # noqa: BLE001 - any decode failure: not an image we keep
        raise StudioError('REFERENCE_FETCH_REFUSED', f'does not decode as an image: {exc}') from None
    path.parent.mkdir(parents=True, exist_ok=True)
    pixels.save(path, 'JPEG', quality=92)
    return list(pixels.size)


def index_rows(project):
    path = Path(project) / PHOTOS_DIR / INDEX
    return read_json(path)['photos'] if path.is_file() else []


def provenance(project, image):
    """The index row of a collected photo (project-relative path), or None."""
    return next((r for r in index_rows(project) if r['file'] == str(image)), None)


def contact_sheet(project, rows, out):
    from PIL import Image, ImageDraw, ImageOps
    cell, cols = 300, 6
    sheet = Image.new('RGB', (cols * cell, max(1, -(-len(rows) // cols)) * (cell + 24)), '#111418')
    draw = ImageDraw.Draw(sheet)
    for i, row in enumerate(rows):
        x, y = (i % cols) * cell, (i // cols) * (cell + 24)
        with Image.open(Path(project) / row['file']) as src:
            thumb = ImageOps.contain(src.convert('RGB'), (cell - 8, cell - 8))
        sheet.paste(thumb, (x + 4, y + 24))
        draw.text((x + 4, y + 5), f"{Path(row['file']).name}  {row['licence']}", fill='white')
    sheet.save(out, quality=88)
    return str(out)


def collect(job):
    """Run one reference_fetch job: look up its titles or query, download the files that pass, index them."""
    project = Path(job['project_dir'])
    source = job['source']
    if source not in SOURCES:
        raise StudioError('REFERENCE_FETCH_REFUSED', f'source {source!r} is not allowed (allowed: {sorted(SOURCES)})')
    out = project / PHOTOS_DIR
    if Path(job['output_dir']).resolve() != out.resolve():
        raise StudioError('REFERENCE_FETCH_REFUSED', f'output_dir must be {out}')
    if job.get('titles'):
        titles = [t if t.startswith('File:') else 'File:' + t for t in job['titles']][:MAX_FILES]
        candidates, refused = [], []
        for offset in range(0, len(titles), 25):
            c, r = rows_from_api(_api(source, {'titles': '|'.join(titles[offset:offset + 25])}), source)
            candidates += c; refused += r
    else:
        candidates, refused = rows_from_api(_api(source, {'generator': 'search', 'gsrnamespace': 6, 'gsrsearch': job['query'],
                                                          'gsrlimit': min(int(job.get('limit') or 30), MAX_FILES)}), source)
    rows = index_rows(project)
    known = {(r['source'], r['page_id']): r for r in rows}
    collected = []
    for item in candidates:
        key = (source, item['page_id'])
        if key in known and (project / known[key]['file']).is_file() and file_hash(project / known[key]['file']) == known[key]['sha256']:
            collected.append(known[key]); continue
        name = f"{SOURCES[source]['prefix']}_{item['page_id']}.jpg"
        try:
            body, kind = _get(item['url'], source)
            size = store_image(body, kind, out / name)
        except StudioError as error:
            refused.append({'title': item['title'], 'licence': item['licence'], 'why': error.message}); continue
        row = {'file': f'{PHOTOS_DIR}/{name}', 'sha256': file_hash(out / name), **{k: v for k, v in item.items() if k != 'size'},
               'pixels': size, 'subject_id': job.get('subject_id'), 'query': job.get('query'), 'retrieved_at': now(),
               'use': 'compare'}   # local comparison only; a generation input needs the user's words
        known[key] = row
        collected.append(row)
        time.sleep(PAUSE_S)
    write_json(out / INDEX, {'schema_version': 1, 'photos': sorted(known.values(), key=lambda r: r['file'])})
    sheet = contact_sheet(project, collected, out / f"contact_{job['job_id']}.jpg") if collected else None
    return {'collected': [{k: r[k] for k in ('file', 'title', 'licence', 'artist', 'page')} for r in collected],
            'refused': refused, 'contact_sheet': sheet, 'index': str(out / INDEX)}


def run_reference_worker(job_path, token):
    """The network entry the broker starts outside the sandbox (or `reference collect` starts directly outside it)."""
    job_path = Path(job_path).resolve()
    job = read_json(job_path)
    if job.get('worker_token') != token:
        raise StudioError('REVISION_CONFLICT', 'Worker token does not match job')
    from .jobs import machine_id, update_job   # the one way a job file changes (jobs.update_job)
    job = update_job(job_path, {'status': 'running', 'pid': os.getpid(), 'owner_machine': machine_id(), 'updated_at': now()})
    try:
        result = collect(job)
        job.update({'status': 'complete', 'result': result, 'error': None})
    except StudioError as error:
        job.update({'status': 'failed', 'error': {'code': error.code, 'message': error.message}})
    except Exception as error:   # noqa: BLE001 - recorded on the job, never lost
        job.update({'status': 'failed', 'error': {'code': 'REFERENCE_FETCH_FAILED', 'message': f'{type(error).__name__}: {error}'}})
    job = update_job(job_path, {k: job.get(k) for k in ('status', 'result', 'error')} | {'updated_at': now()})
    return {'job_id': job['job_id'], 'status': job['status'], **({'result': job['result']} if job.get('result') else {}),
            **({'error': job['error']} if job.get('error') else {})}
