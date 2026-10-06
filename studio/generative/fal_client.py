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
from urllib.error import HTTPError, URLError
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
    for row in _ledger_rows():  # last entry per request_dir wins (reserved -> released/charged/unknown)
        rows[row['request_dir']] = row
    # 'unknown' (failed remotely, billing not yet reconciled) counts as spent until a person settles it
    return round(sum(r['usd'] for r in rows.values() if r['state'] in ('reserved', 'charged', 'unknown') and (project_id is None or r['project_id'] == project_id)), 2)


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


RETRY_ATTEMPTS, RETRY_BACKOFF_S = 5, (2, 5, 10, 20, 30)   # free GETs only (status, result); never the POST
REMOTE_FAILED = ('FAILED', 'ERROR', 'CANCELLED')


def _transient(error):
    return isinstance(error, (URLError, TimeoutError, ConnectionError)) and not (isinstance(error, HTTPError) and error.code < 500)


def _get(url, sleep=time.sleep):
    """A free GET with retries on network errors and 5xx. Raises the last error when all attempts fail."""
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return _json(url)
        except Exception as error:   # noqa: BLE001 - classified below
            if not _transient(error) or attempt == RETRY_ATTEMPTS - 1:
                raise
            sleep(RETRY_BACKOFF_S[min(attempt, len(RETRY_BACKOFF_S) - 1)])


def _remote_failed(dest, endpoint, project_id, request_id, detail):
    """fal ran the job but it failed. Whether it was billed is only on the fal dashboard: keep the reservation
    (counted as spent) and mark the ledger row 'unknown' until a person reconciles it (generate reconcile)."""
    write_json(dest / 'state.json', {'state': 'remote_failed', 'detail': str(detail)[:500], 'at': now()})
    _append({'state': 'unknown', 'usd': read_json(dest / 'request.json')['estimated_usd'], 'endpoint': endpoint, 'project_id': project_id,
             'request_dir': str(dest), 'request_id': request_id})
    raise StudioError('GENERATION_REMOTE_FAILED', f'fal request {request_id} failed remotely: {str(detail)[:200]}',
                      recovery='Check the fal dashboard for this request, then record what the user saw with generate reconcile')


def paid_call(endpoint, arguments, dest, *, project_id, allow_paid=False, max_usd=None, budget_usd=None,
              duration_seconds=None, input_seconds=0.0, poll_timeout_s=1800, poll_interval_s=5, sleep=time.sleep):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with lock(dest / '.lock', blocking=False):  # one process per request: no double POST, no double charge
            return _paid_call(endpoint, arguments, dest, project_id=project_id, allow_paid=allow_paid, max_usd=max_usd,
                              budget_usd=budget_usd, duration_seconds=duration_seconds, input_seconds=input_seconds,
                              poll_timeout_s=poll_timeout_s, poll_interval_s=poll_interval_s, sleep=sleep)
    except StudioError as error:
        if error.code == 'BUSY':
            raise StudioError('GENERATION_IN_PROGRESS', f'{dest}: another process is handling this request', retryable=True) from error
        raise


def _paid_call(endpoint, arguments, dest, *, project_id, allow_paid, max_usd, budget_usd, duration_seconds, input_seconds,
               poll_timeout_s, poll_interval_s, sleep):
    response_path, submit_path = dest / 'response.json', dest / 'submit.json'
    if response_path.is_file():
        return read_json(response_path)
    state_path = dest / 'state.json'
    state = read_json(state_path) if state_path.is_file() else {}
    if state.get('state') == 'submitted' and not submit_path.is_file():
        raise StudioError('GENERATION_REQUEST_UNKNOWN', f'{dest}: a POST may have been sent but no receipt was saved; check the fal dashboard before retrying',
                          recovery='Record what the user saw on the dashboard with generate reconcile')
    if state.get('state') in ('remote_failed', 'reconciled'):
        raise StudioError('GENERATION_REMOTE_FAILED', f"{dest}: request is {state['state']}; use a new take for another attempt")
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
            raise StudioError('GENERATION_REQUEST_UNKNOWN', f'HTTP {error.code} after POST; check the fal dashboard before retrying',
                              recovery='Record what the user saw on the dashboard with generate reconcile') from error
        except (URLError, TimeoutError, ConnectionError) as error:   # the POST may or may not have arrived
            raise StudioError('GENERATION_REQUEST_UNKNOWN', f'network error during POST ({error}); check the fal dashboard before retrying',
                              recovery='Record what the user saw on the dashboard with generate reconcile') from error
        write_json(submit_path, {k: receipt.get(k) for k in ('request_id', 'status_url', 'response_url')})
    receipt = read_json(submit_path)
    deadline = time.monotonic() + poll_timeout_s
    try:
        while True:
            status = _get(receipt['status_url'], sleep)
            if status.get('status') == 'COMPLETED':
                break
            if status.get('status') in REMOTE_FAILED:
                _remote_failed(dest, endpoint, project_id, receipt['request_id'], status)
            if time.monotonic() > deadline:
                raise StudioError('GENERATION_PENDING', f"Request {receipt['request_id']} still running; re-run to resume polling (free)", retryable=True)
            sleep(poll_interval_s)
        try:
            result = _get(receipt['response_url'], sleep)
        except HTTPError as error:   # COMPLETED but the result is an error after every retry: the job failed on fal
            if error.code >= 500:
                _remote_failed(dest, endpoint, project_id, receipt['request_id'], error.read()[:300] if hasattr(error, 'read') else error)
            raise
    except HTTPError as error:   # an HTTP answer, not a network error (HTTPError subclasses URLError, so it goes first)
        request_id = receipt['request_id']
        if error.code >= 500:
            raise StudioError('GENERATION_PENDING', f'HTTP {error.code} while polling {request_id}; re-run to resume (free)', retryable=True) from error
        if error.code in (401, 403):
            raise StudioError('ASSET_ACCESS_REQUIRED', f'fal refused the key while polling {request_id} (HTTP {error.code})',
                              recovery='Fix FAL_KEY, then re-run; polling is free and the request is not re-sent') from error
        if error.code in (404, 410):
            raise StudioError('GENERATION_REQUEST_UNKNOWN', f'fal no longer knows request {request_id} (HTTP {error.code}); check the fal dashboard',
                              recovery='Record what the user saw on the dashboard with generate reconcile') from error
        raise StudioError('GENERATION_POLL_REJECTED', f'fal answered HTTP {error.code} while polling {request_id}',
                          recovery='Check the request on the fal dashboard; the reservation stays until reconciled') from error
    except (URLError, TimeoutError, ConnectionError) as error:
        raise StudioError('GENERATION_PENDING', f"Network error while polling {receipt['request_id']} ({error}); re-run to resume (free)",
                          retryable=True) from error
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


def reconcile(dest, charged, user_words):
    """Settle a request whose billing only the fal dashboard knows (no receipt, or failed remotely), from what the user
    saw there - their words verbatim. charged=True keeps the cost, False releases it."""
    dest = Path(dest)
    state = read_json(dest / 'state.json') if (dest / 'state.json').is_file() else {}
    if state.get('state') not in ('submitted', 'remote_failed') or (dest / 'response.json').is_file():
        raise StudioError('INPUT_INVALID', f"{dest}: nothing to reconcile (state {state.get('state')})")
    if not isinstance(user_words, str) or len(user_words.strip()) < 4:
        raise StudioError('INPUT_INVALID', "reconcile needs the user's own words about the dashboard")
    request = read_json(dest / 'request.json')
    rows = [r for r in _ledger_rows() if r['request_dir'] == str(dest)]
    project_id = rows[-1]['project_id'] if rows else None
    with lock(dest / '.lock', blocking=False), lock(LEDGER.parent / '.ledger.lock'):
        _append({'state': 'charged' if charged else 'released', 'usd': request['estimated_usd'] if charged else 0,
                 'endpoint': request['endpoint'], 'project_id': project_id, 'request_dir': str(dest), 'reconciled': user_words.strip()})
        write_json(dest / 'state.json', {'state': 'reconciled', 'charged': bool(charged), 'user_words': user_words.strip(),
                                         'previous': state, 'at': now()})
    return {'status': 'reconciled', 'request_dir': str(dest), 'charged': bool(charged), 'usd': request['estimated_usd'] if charged else 0}
