"""The only path to a paid fal request: budget-checked, ledgered, never re-POSTed.

Rules:
- A request costs money once it is POSTed, so the POST happens at most once per request
  directory. Re-running resumes by GET (free) from the saved submit receipt; an ambiguous
  state stops with GENERATION_REQUEST_UNKNOWN instead of paying again.
- The caller must pass allow_paid=True and max_usd; the global ledger and the project's
  route_policy.budget_usd are checked under a lock before the POST.
- The key is read from FAL_KEY or ~/.config/fal/api_key at call time, sent only to
  queue.fal.run, and never written anywhere. Result files are downloaded without it.
- Endpoints are an allow-list with a price quote; unknown endpoints and licence-excluded
  models are refused.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ..common import REPO, StudioError, file_hash, lock, now, read_json, write_json

QUEUE = 'https://queue.fal.run/'
LEDGER = REPO / '.studio' / 'paid_ledger.jsonl'
# Quotes from 2026-10-03/04 research; per_second prices multiply by the request duration.
PRICING = {
    'fal-ai/veo3.1/image-to-video': {'usd_per_second': 0.20},
    'fal-ai/veo3.1': {'usd_per_second': 0.20},
    # fal llms.txt 2026-10-04: 720p with video inputs ~= $0.2838 per second of (input + output) video.
    'bytedance/seedance-2.5/reference-to-video': {'usd_per_second_in_out': 0.2838},
    # fal pricing API 2026-10-04: $0.10 per output second.
    'fal-ai/wan-22-vace-fun-a14b/depth': {'usd_per_second': 0.10},
    'fal-ai/kling-video/o1/video-to-video/edit': {'usd_per_call_max': None},
    'fal-ai/luma-dream-machine/ray-2/modify': {'usd_per_call_max': None},
    'fal-ai/hyper3d/rodin/v2.5': {'usd_per_call_max': 0.40},
    'fal-ai/nano-banana-pro/edit': {'usd_per_call_max': 0.15},
}
# Tencent Hunyuan 3D open-weight licence excludes South Korea; never route Korean production through it.
BLOCKED_PREFIXES = ('fal-ai/hunyuan',)


def _key():
    stored = Path.home() / '.config/fal/api_key'
    key = os.environ.get('FAL_KEY') or (stored.read_text().strip() if stored.is_file() else None)
    if not key:
        raise StudioError('ASSET_ACCESS_REQUIRED', 'No fal key: set FAL_KEY or ~/.config/fal/api_key')
    return key


def _json(url, data=None, auth=True, method=None):
    if auth and not url.startswith(QUEUE):
        raise StudioError('INPUT_INVALID', 'The fal key is only ever sent to queue.fal.run')
    headers = {'Content-Type': 'application/json', 'User-Agent': 'TechnicalReelStudio/0.1'}
    if auth:
        headers['Authorization'] = 'Key ' + _key()
    request = Request(url, data=json.dumps(data).encode() if data is not None else None, headers=headers, method=method)
    with urlopen(request, timeout=120) as response:
        return json.load(response)


def estimate_usd(endpoint, duration_seconds=None, input_seconds=0.0):
    if endpoint.startswith(BLOCKED_PREFIXES):
        raise StudioError('INPUT_INVALID', f'{endpoint} is licence-excluded for Korean production')
    if endpoint not in PRICING:
        raise StudioError('ROUTE_MODEL_UNKNOWN', f'{endpoint} is not in the fal allow-list', recovery='Add it to PRICING with price evidence')
    price = PRICING[endpoint]
    if price.get('usd_per_second_in_out') is not None:
        if not duration_seconds:
            raise StudioError('ROUTE_ESTIMATE_MISSING', f'{endpoint} is priced on input+output seconds; pass duration_seconds')
        return round(price['usd_per_second_in_out'] * (duration_seconds + (input_seconds or 0)), 2)
    if price.get('usd_per_second') is not None:
        if not duration_seconds:
            raise StudioError('ROUTE_ESTIMATE_MISSING', f'{endpoint} is priced per second; pass duration_seconds')
        return round(price['usd_per_second'] * duration_seconds, 2)
    if price.get('usd_per_call_max') is None:
        raise StudioError('ROUTE_ESTIMATE_MISSING', f'No price quote recorded for {endpoint}')
    return price['usd_per_call_max']


def _ledger_rows():
    if not LEDGER.is_file():
        return []
    return [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]


def ledger_total(project_id=None):
    rows = {}
    for row in _ledger_rows():  # last entry per request_dir wins (reserved -> released/charged)
        rows[row['request_dir']] = row
    return round(sum(r['usd'] for r in rows.values() if r['state'] in ('reserved', 'charged') and (project_id is None or r['project_id'] == project_id)), 2)


def _append(row):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open('a') as handle:
        handle.write(json.dumps({**row, 'at': now()}) + '\n')


def _download(url, destination):
    if urlparse(url).scheme != 'https':
        raise StudioError('INPUT_INVALID', 'Result URL must be https')
    request = Request(url, headers={'User-Agent': 'TechnicalReelStudio/0.1'})  # never the fal key
    with urlopen(request, timeout=300) as response, destination.open('wb') as out:
        for chunk in iter(lambda: response.read(1 << 20), b''):
            out.write(chunk)


def _files(value, trail=''):
    if isinstance(value, dict):
        if isinstance(value.get('url'), str) and value['url'].startswith('https://'):
            yield trail, value
        for key, child in value.items():
            yield from _files(child, f'{trail}.{key}' if trail else key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _files(child, f'{trail}[{index}]')


def paid_call(endpoint, arguments, dest, *, project_id, allow_paid=False, max_usd=None, budget_usd=None,
              duration_seconds=None, input_seconds=0.0, poll_timeout_s=1800, poll_interval_s=5):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    response_path, submit_path = dest / 'response.json', dest / 'submit.json'
    if response_path.is_file():
        return read_json(response_path)
    state_path = dest / 'state.json'
    state = read_json(state_path) if state_path.is_file() else {}
    if state.get('state') == 'submitted' and not submit_path.is_file():
        raise StudioError('GENERATION_REQUEST_UNKNOWN', f'{dest}: a POST may have been sent but no receipt was saved; check the fal dashboard before retrying')
    if not submit_path.is_file():
        usd = estimate_usd(endpoint, duration_seconds, input_seconds)
        if not allow_paid:
            raise StudioError('BUDGET_EXCEEDED', f'Paid call ${usd} needs --allow-paid', recovery='Get user approval and pass --allow-paid --max-usd')
        if max_usd is None or usd > max_usd:
            raise StudioError('BUDGET_EXCEEDED', f'Estimated ${usd} exceeds --max-usd {max_usd}')
        with lock(LEDGER.parent / '.ledger.lock'):
            if budget_usd is not None and ledger_total(project_id) + usd > budget_usd:
                raise StudioError('BUDGET_EXCEEDED', f'Project ledger ${ledger_total(project_id)} + ${usd} > budget ${budget_usd}')
            _append({'state': 'reserved', 'usd': usd, 'endpoint': endpoint, 'project_id': project_id, 'request_dir': str(dest)})
        write_json(dest / 'request.json', {'endpoint': endpoint, 'arguments': arguments, 'estimated_usd': usd})
        write_json(state_path, {'state': 'submitted', 'at': now()})  # written before the POST: a crash can never re-POST
        try:
            receipt = _json(QUEUE + endpoint, arguments)
        except HTTPError as error:
            if 400 <= error.code < 500:
                _append({'state': 'released', 'usd': 0, 'endpoint': endpoint, 'project_id': project_id, 'request_dir': str(dest)})
                write_json(state_path, {'state': 'rejected', 'http': error.code, 'at': now()})
                raise StudioError('GENERATION_REJECTED', f'fal rejected the request (HTTP {error.code}); nothing was charged')
            raise StudioError('GENERATION_REQUEST_UNKNOWN', f'HTTP {error.code} after POST; check the fal dashboard before retrying') from error
        write_json(submit_path, {k: receipt.get(k) for k in ('request_id', 'status_url', 'response_url')})
    receipt = read_json(submit_path)
    deadline = time.monotonic() + poll_timeout_s
    while True:
        status = _json(receipt['status_url'])
        if status.get('status') == 'COMPLETED':
            break
        if time.monotonic() > deadline:
            raise StudioError('GENERATION_PENDING', f"Request {receipt['request_id']} still running; re-run to resume polling (free)", retryable=True)
        time.sleep(poll_interval_s)
    result = _json(receipt['response_url'])
    files = []
    for trail, item in _files(result):
        name = (item.get('file_name') or item['url'].rsplit('/', 1)[-1]).replace('/', '_')
        target = dest / 'files' / f"{trail.replace('[', '_').replace(']', '').replace('.', '_')}__{name}"
        target.parent.mkdir(exist_ok=True)
        _download(item['url'], target)
        files.append({'trail': trail, 'path': str(target), 'sha256': file_hash(target), 'bytes': target.stat().st_size})
    usd = read_json(dest / 'request.json')['estimated_usd']
    _append({'state': 'charged', 'usd': usd, 'endpoint': endpoint, 'project_id': project_id, 'request_dir': str(dest), 'request_id': receipt['request_id']})
    record = {'endpoint': endpoint, 'request_id': receipt['request_id'], 'estimated_usd': usd, 'result': result, 'files': files, 'completed_at': now()}
    write_json(response_path, record)
    write_json(state_path, {'state': 'complete', 'at': now()})
    return record
