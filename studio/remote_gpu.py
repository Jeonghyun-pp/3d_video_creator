"""Rented GPU servers (RunPod Pods) for renders: started when a render is queued for one, deleted the moment the queue
is empty, never left running.

Why (2026-10-08): the Mac (M4, 10-core GPU, 16 GB) renders a 30 s reel's final in about two hours and overloads when
builds and renders overlap. The user chose RunPod Pods, RTX 5090 first (4090 next), a monthly cap of $15, and "start
it when a render needs it, delete it as soon as it is done".

Three guards against a server that keeps billing:
  1. the remote worker deletes the server when no remote job is queued or running (finally, after success or failure);
  2. inside the server a watchdog deletes it after IDLE_S without work or MAX_POD_HOURS in total - it needs nothing from
     this Mac (the Mac may sleep or lose its network);
  3. every render command first sweeps our named servers that no live job owns.
Money: every server's start, end and price per hour go to .studio/gpu_ledger.jsonl; a server is not created when this
month's spend plus the job's estimate would pass the cap. Settings live outside the repository
(~/.config/studio/remote_gpu.json) and are turned on only with the user's words. The API key is read from the
environment (or the export line in ~/.zshrc) and never written anywhere.
"""
from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .common import REPO, StudioError, lock, now

API = os.environ.get('STUDIO_RUNPOD_API', 'https://rest.runpod.io/v1')   # the rehearsal points this at a fake server
CONFIG = Path(os.environ.get('STUDIO_REMOTE_GPU_CONFIG', Path.home() / '.config' / 'studio' / 'remote_gpu.json'))
LEDGER = Path(os.environ.get('STUDIO_GPU_LEDGER', REPO / '.studio' / 'gpu_ledger.jsonl'))
SSH_KEY = Path(os.environ.get('STUDIO_RUNPOD_SSH_KEY', Path.home() / '.ssh' / 'studio_runpod'))
NAME_PREFIX = 'studio-render-'
DEFAULTS = {'enabled': False, 'monthly_cap_usd': 15.0, 'gpu_types': ['NVIDIA GeForce RTX 5090', 'NVIDIA GeForce RTX 4090'],
            'cloud_type': 'SECURE', 'image': 'runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404', 'container_disk_gb': 40,
            'max_pod_hours': 2.0, 'idle_minutes': 10.0, 'blender_version': '5.2.2'}
BOOT_TIMEOUT_S = int(os.environ.get('STUDIO_REMOTE_BOOT_TIMEOUT', 900))
# A boot gives up when nothing it can observe changes for this long. Provisional, from one sample (boot probe
# 2026-10-21: RunPod's pod record shows only publicIp and portMappings changing - no runtime or uptime fields - and the
# longest unchanged stretch was ~20 s, from create to address and ssh); x3 that is under the floor of 120 s. Every boot
# that gives up records its observations in the ledger ('boot' rows): those are the next samples.
BOOT_STALL_S = int(os.environ.get('STUDIO_REMOTE_BOOT_STALL', 120))
SSH_TIMEOUT_S = 120   # one short command; longer steps (bootstrap) pass their own
WORKDIR = '/workspace/studio'


# ---- settings, key, ledger -------------------------------------------------------------------------------------------

def settings():
    data = json.loads(CONFIG.read_text()) if CONFIG.is_file() else {}
    return {**DEFAULTS, **data}


def enable(user_words, monthly_cap_usd=None):
    """Turn remote renders on, in the user's words (recorded with the cap)."""
    from .generative.review import check_user_words
    words = check_user_words(user_words, 'turn on remote GPU renders')
    data = {**settings(), 'enabled': True, 'enabled_by': {'user_words': words, 'at': now()}}
    if monthly_cap_usd is not None:
        data['monthly_cap_usd'] = float(monthly_cap_usd)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return {k: v for k, v in data.items() if k != 'image'}


def api_key():
    key = os.environ.get('RUNPOD_API_KEY')
    if not key:   # the user's shell profile holds it (added without printing); read only that line
        profile = Path.home() / '.zshrc'
        lines = [l for l in profile.read_text().splitlines() if l.startswith('export RUNPOD_API_KEY=')] if profile.is_file() else []
        key = lines[-1].split('=', 1)[1].strip().strip('"\'') if lines else None
    if not key:
        raise StudioError('REMOTE_GPU_NO_KEY', 'RUNPOD_API_KEY is not set', recovery='add it to ~/.zshrc without printing it (see the RunPod setup notes)')
    return key


def _ledger_rows():
    return [json.loads(l) for l in LEDGER.read_text().splitlines() if l.strip()] if LEDGER.is_file() else []


def _record(row):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with lock(LEDGER.parent / '.gpu_ledger.lock'):
        with LEDGER.open('a') as out:
            out.write(json.dumps({'at': now(), **row}) + '\n')


def month_spend(rows=None, month=None):
    """USD spent this month: every ended server (seconds x price), plus running ones up to now."""
    month = month or datetime.now(timezone.utc).strftime('%Y-%m')
    rows = _ledger_rows() if rows is None else rows
    started = {r['pod_id']: r for r in rows if r['event'] == 'start'}
    ended = {r['pod_id']: r for r in rows if r['event'] == 'end'}
    total = 0.0
    for pod_id, start in started.items():
        if not start['at'].startswith(month):
            continue
        t0 = datetime.fromisoformat(start['at'])
        t1 = datetime.fromisoformat(ended[pod_id]['at']) if pod_id in ended else datetime.now(timezone.utc)
        total += max(0.0, (t1 - t0).total_seconds()) / 3600 * float(start['cost_per_hr'])
    return round(total, 4)


def price_of(pod):
    """USD per hour RunPod bills this server at (what the ledger records), or None when it does not say."""
    price = (pod or {}).get('costPerHr') or (pod or {}).get('adjustedCostPerHr')
    return float(price) if price else None


def known_price():
    """The highest price a server of ours was billed at (the ledger), else $1/h: the guess before RunPod names one."""
    return max([float(r['cost_per_hr']) for r in _ledger_rows() if r['event'] == 'start' and r.get('cost_per_hr')] or [1.0])


def over_cap(cap=None):
    """GPU_BUDGET_REACHED text when this month's spend - running servers counted up to now - has reached the cap, else
    None: read while a job runs, so a render that takes longer than its estimate stops at the cap, not after it."""
    cap = settings()['monthly_cap_usd'] if cap is None else cap
    spent = month_spend()
    return f'this month ${spent:.2f} has reached the ${cap:.2f} cap' if spent >= cap else None


def check_budget(estimate_usd, cap=None):
    cap = settings()['monthly_cap_usd'] if cap is None else cap
    spent = month_spend()
    if spent + estimate_usd > cap:
        raise StudioError('GPU_BUDGET_EXCEEDED', f'this month ${spent:.2f} + this job about ${estimate_usd:.2f} would pass the ${cap:.2f} cap',
                          recovery='render locally, or ask the user to raise the cap (studio gpu enable --monthly-cap N with their words)')
    return {'spent_usd': spent, 'estimate_usd': round(estimate_usd, 4), 'cap_usd': cap}


# ---- RunPod REST ------------------------------------------------------------------------------------------------------

USER_AGENT = 'studio-remote-gpu/1.0'   # RunPod's Cloudflare refuses Python's default urllib agent (403, code 1010; first real call 2026-10-09)


def _call(method, path, body=None, timeout=60):
    request = urllib.request.Request(f'{API}{path}', method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Authorization': f'Bearer {api_key()}', 'Content-Type': 'application/json', 'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:   # the body says why; the key never appears in it
        raise StudioError('REMOTE_GPU_API', f'{method} {path}: {error.code} {error.read()[:300].decode(errors="replace")}', retryable=error.code >= 500) from None
    except urllib.error.URLError as error:
        raise StudioError('REMOTE_GPU_API', f'{method} {path}: {error.reason}', retryable=True) from None


def our_pods():
    return [p for p in (_call('GET', '/pods') or []) if str(p.get('name', '')).startswith(NAME_PREFIX)
            and p.get('desiredStatus') != 'TERMINATED']


def _public_key():
    if not SSH_KEY.is_file():
        SSH_KEY.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'studio-runpod', '-f', str(SSH_KEY)], check=True)
    return Path(f'{SSH_KEY}.pub').read_text().strip()


# The ways a server may end itself with the pod-scoped key RunPod injects, tried in this order until one takes. Why
# (watchdog probe 2026-10-21): the REST delete with that key answers HTTP 403, so the idle watchdog never deleted a
# server; RunPod's own in-pod tool (runpodctl) speaks GraphQL. A way that is refused falls through to the next; the stop
# at least ends the GPU billing (a stopped server is then deleted by the next sweep). Each answer goes to the log, so a
# server that outlives its watchdog says why.
WATCHDOG_LOG = '/workspace/.watchdog.log'
GRAPHQL_IN_POD = os.environ.get('STUDIO_RUNPOD_GRAPHQL_IN_POD', 'https://api.runpod.io/graphql')


def self_end_ways(rest_api):
    """bash commands, one per way, in the order tried; each prints its answer (no key: only responses are printed)."""
    auth = '-H "Authorization: Bearer $RUNPOD_API_KEY"'
    terminate = '\'{"query":"mutation { podTerminate(input: {podId: \\"\'"$RUNPOD_POD_ID"\'\\"}) }"}\''
    return [('graphql podTerminate', f"curl -s -m 20 -w ' HTTP %{{http_code}}' -X POST -H 'Content-Type: application/json' {auth} "
                                     f"-d {terminate} {GRAPHQL_IN_POD}"),
            ('runpodctl remove', 'command -v runpodctl >/dev/null && runpodctl remove pod "$RUNPOD_POD_ID" 2>&1 || echo "no runpodctl"'),
            ('runpodctl stop', 'command -v runpodctl >/dev/null && runpodctl stop pod "$RUNPOD_POD_ID" 2>&1 || echo "no runpodctl"'),
            ('rest delete', f"curl -s -m 20 -w ' HTTP %{{http_code}}' -X DELETE {auth} {rest_api}/pods/$RUNPOD_POD_ID")]


def self_end_script(rest_api, log=WATCHDOG_LOG):
    """bash that tries each of self_end_ways once, appending every answer to `log`."""
    return ' '.join(f'echo "$(date -u +%FT%TZ) {name}: $( ( {command} ) 2>&1 | tail -c 300)" >> {log};'
                    for name, command in self_end_ways(rest_api))


def watchdog_command(cfg):
    """The server's start command: sshd as the image provides it, plus a loop that ends the server itself after
    idle_minutes without /workspace/.busy being touched or after max_pod_hours, using the pod-scoped key RunPod injects
    (RUNPOD_API_KEY, RUNPOD_POD_ID) - nothing from this Mac - by each of self_end_ways in turn, every answer logged."""
    idle, life = int(cfg['idle_minutes'] * 60), int(cfg['max_pod_hours'] * 3600)
    api = os.environ.get('STUDIO_RUNPOD_API_IN_POD', API)
    tries = self_end_script(api)
    loop = (f'touch /workspace/.busy; start=$(date +%s); while true; do sleep 15; now=$(date +%s); '
            f'idle=$(( now - $(stat -c %Y /workspace/.busy) )); '
            f'if [ $idle -gt {idle} ] || [ $(( now - start )) -gt {life} ]; then {tries} fi; done')
    return ['bash', '-c', f'mkdir -p /workspace; (/start.sh >/dev/null 2>&1 &); {loop}']


def create_pod(hours):
    """A new server for `hours` of work, inside the month's cap at the price RunPod bills it - known only once it exists
    (2026-10-18: checked at $1/h while the server cost $1.19/h): checked first at the highest price we were billed,
    then at its own; over the cap, or no price given, it is deleted at once and nothing runs on it."""
    cfg = settings()
    if not cfg['enabled']:
        raise StudioError('REMOTE_GPU_DISABLED', 'remote GPU renders are off', recovery="studio gpu enable --user-words \"<the user's words>\"")
    check_budget(hours * known_price())
    body = {'name': f'{NAME_PREFIX}{socket.gethostname().split(".")[0].lower()[:40]}', 'imageName': cfg['image'], 'gpuTypeIds': cfg['gpu_types'],
            'gpuTypePriority': 'custom', 'gpuCount': 1, 'cloudType': cfg['cloud_type'], 'containerDiskInGb': cfg['container_disk_gb'],
            'volumeInGb': 0, 'ports': ['22/tcp'], 'supportPublicIp': True, 'interruptible': False,
            'env': {'PUBLIC_KEY': _public_key()}, 'dockerStartCmd': watchdog_command(cfg)}
    pod = _call('POST', '/pods', body)
    price = price_of(pod)
    _record({'event': 'start', 'pod_id': pod['id'], 'cost_per_hr': price or 0,
             'gpu': (pod.get('gpu') or {}).get('displayName'), 'max_pod_hours': cfg['max_pod_hours']})
    try:
        if price is None:
            raise StudioError('REMOTE_GPU_PRICE_UNKNOWN', f"RunPod named no price for server {pod['id']}: it is deleted unused")
        check_budget(hours * price)
    except StudioError:
        terminate(pod['id'], 'over the cap at its own price' if price else 'no price')
        raise
    return pod


def terminate(pod_id, reason):
    try:
        _call('DELETE', f'/pods/{pod_id}')
    except StudioError as error:
        if '404' not in error.message:   # already gone (the watchdog may have been first)
            raise
    if not any(r['event'] == 'end' and r['pod_id'] == pod_id for r in _ledger_rows()):
        _record({'event': 'end', 'pod_id': pod_id, 'reason': reason})


def endpoint(pod):
    """(host, port) of the server's SSH once RunPod has mapped it."""
    mapping = pod.get('portMappings') or {}
    port = mapping.get('22') or mapping.get(22)
    return (pod.get('publicIp'), int(port)) if pod.get('publicIp') and port else (None, None)


def wait_ready(pod_id, timeout=BOOT_TIMEOUT_S, sleep=time.sleep, record=None, stall_s=None):
    """The server once it answers SSH. What RunPod and ssh report is observed and recorded (the ledger's 'boot' rows,
    and record(row) when given): a boot that gives up says what it last saw (studio/waiting.py)."""
    from .waiting import wait_until

    def observe():
        try:
            pod = _call('GET', f'/pods/{pod_id}') or {}
        except StudioError as error:   # one failed read is an observation, not the end of the boot
            return None, {'api': error.message[:120]}
        host, port = endpoint(pod)
        seen = {'desiredStatus': pod.get('desiredStatus'), 'publicIp': bool(host), 'port': port}
        if host:
            seen['ssh'] = ssh(pod, ['true'], check=False, timeout=20, retries=0).returncode
        return (pod if seen.get('ssh') == 0 else None), seen

    def note(row):
        _record({'event': 'boot', 'pod_id': pod_id, **row})
        if record:
            record(row)
    return wait_until(observe, limit_s=timeout, stall_s=BOOT_STALL_S if stall_s is None else stall_s, record=note,
                      what=f'boot of server {pod_id}', code='REMOTE_GPU_TIMEOUT', sleep=sleep)


POD_LOCK = REPO / '.studio' / 'remote_gpu.lock'   # one decision at a time: create or reuse, and delete or keep


def ensure_pod(hours, on_chosen=None, boot_hours=0.0, record=None):
    """A ready server of ours for `hours` of work: the running one or a new one, either inside the month's cap at its
    own price (the running one was taken unchecked before 2026-10-18). A new server also bills its boot (boot_hours);
    the running one has booted. on_chosen(pod) runs under the same lock the delete decision takes, so a job that has
    chosen a server is never left without it."""
    POD_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with lock(POD_LOCK):
        running = [p for p in our_pods() if p.get('desiredStatus') == 'RUNNING']
        if running:
            check_budget(hours * (price_of(running[0]) or known_price()))
        pod = running[0] if running else create_pod(hours + boot_hours)
        if on_chosen:
            on_chosen(pod)
    pod = wait_ready(pod['id'], record=record)   # a failure here is the caller's to clean up: it deletes the server unless another job holds it
    bootstrap(pod)
    return pod


def sweep(live_pod_ids=()):
    """Delete our servers no live job owns, and close the ledger of servers RunPod no longer lists. Returns the ids deleted."""
    listed, gone = our_pods(), []
    for pod in listed:
        if pod['id'] not in live_pod_ids:
            terminate(pod['id'], 'sweep')
            gone.append(pod['id'])
    _close_unlisted({pod['id'] for pod in listed} | set(live_pod_ids))
    return gone


def _close_unlisted(present_ids):
    """A server with a start but no end (its delete failed, or its worker was killed) is billed as running until the month
    ends and refuses every later job. Once RunPod no longer lists it, its end is recorded at the latest moment its own
    watchdog could have deleted it (boot + max_pod_hours after the start, or now) - never less than it could have run.
    A server a live job holds, or one started within the boot time (it may not be listed yet), is never judged by one
    listing (2026-10-12 second re-audit)."""
    rows = _ledger_rows()
    ended = {r['pod_id'] for r in rows if r['event'] == 'end'}
    now_utc = datetime.now(timezone.utc)
    for start in rows:
        if start['event'] != 'start' or start['pod_id'] in ended or start['pod_id'] in present_ids:
            continue
        if (now_utc - datetime.fromisoformat(start['at'])).total_seconds() < BOOT_TIMEOUT_S:
            continue
        hours = float(start.get('max_pod_hours', settings()['max_pod_hours']))
        latest = datetime.fromisoformat(start['at']) + timedelta(seconds=BOOT_TIMEOUT_S, hours=hours)
        _record({'event': 'end', 'pod_id': start['pod_id'], 'reason': 'gone', 'at': min(latest, now_utc).isoformat()})


# ---- SSH ---------------------------------------------------------------------------------------------------------------

def _ssh_args(pod):
    host, port = endpoint(pod)
    return ['-i', str(SSH_KEY), '-p', str(port), '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null',
            '-o', 'LogLevel=ERROR', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15'], host


# ssh exits that mean the link, not the command: 255 is ssh's own failure; 124 is ours for a command that outlived its
# timeout (a wait always ends, and says why: studio/waiting.py).
SSH_TRANSIENT = {255, 124}


def ssh(pod, command, check=True, timeout=SSH_TIMEOUT_S, capture=True, retries=None):
    """Run `command` on the server, bounded by `timeout`; a link failure is retried `retries` times (SYNC_RETRY_S)."""
    args, host = _ssh_args(pod)
    argv = ['ssh', *args, f'root@{host}', ' '.join(command) if isinstance(command, list) else command]
    for delay in (*(SYNC_RETRY_S if retries is None else SYNC_RETRY_S[:retries]), None):
        try:
            result = subprocess.run(argv, capture_output=capture, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            result = subprocess.CompletedProcess(argv, 124, '', f'no answer within {timeout} s')
        if result.returncode not in SSH_TRANSIENT or delay is None:
            break
        time.sleep(delay)
    if check and result.returncode:
        raise StudioError('REMOTE_GPU_SSH', f'{command if isinstance(command, str) else command[0]} exited {result.returncode}: {(result.stderr or "")[-800:]}',
                          retryable=result.returncode in SSH_TRANSIENT)
    return result


def ssh_detached(pod, command, log='/dev/null', pidfile=None, once=None):
    """Start `command` on the server and return at once. The whole command runs in its own session with stdin, stdout
    and stderr away from the ssh channel: a background job that keeps any of them (e.g. `cd x && nohup y > log &`,
    whose list subshell keeps ssh's pipes) holds ssh open until it exits - the relay stalled behind a running render
    and the idle watchdog deleted the server (rehearsal 2026-10-08). pidfile: the started process's pid is written
    there before this returns (setsid does not fork outside a process-group leader, and bash -c / exec keep the pid),
    so the server can be asked whether it runs from the first look on. once: a marker made atomically (mkdir) before the
    start - sending the same start again (a retry after a dropped link) starts nothing, so it may be retried like any
    other command."""
    keep = f' echo $! > {shlex.quote(pidfile)}' if pidfile else ''
    guard = f'mkdir {shlex.quote(once)} 2>/dev/null || exit 0; ' if once else ''
    return ssh(pod, f'{guard}nohup setsid bash -c {shlex.quote(command)} > {shlex.quote(log)} 2>&1 < /dev/null &{keep}', timeout=60)


# rsync exits that mean the link dropped, not that the transfer is wrong: 255 ssh/transport, 12 protocol stream,
# 23/24 partial transfer, 30/35 timeouts. A dropped upload failed one look job and deleted its server (2026-10-10).
# Every command retries the same way (ssh too): each is safe to send twice - the one that was not, a worker's start,
# is guarded (ssh_detached once). 2026-10-21: one dropped `touch` failed a job and deleted its server.
SYNC_TRANSIENT = {255, 12, 23, 24, 30, 35}
SYNC_RETRY_S = (5, 15, 30)


def rsync(pod, sources, destination, upload=True, timeout=3600, cwd=None):
    """Upload `sources` (paths relative to `cwd`, kept as relative paths under `destination`) or download one remote
    folder. Relative paths from a working folder, not the '/./' marker: macOS's rsync (openrsync) ignores the marker
    and recreated the Mac's absolute paths on the server (rehearsal, 2026-10-08)."""
    args, host = _ssh_args(pod)
    shell = 'ssh ' + ' '.join(args)
    remote = lambda p: f'root@{host}:{p}'  # noqa: E731
    # '--': a source named like an option (a project file '-e…') is a file, never an option (2026-10-18 review)
    command = ['rsync', '-a', '--relative', '-e', shell, '--', *[str(s) for s in sources], remote(destination)] if upload else \
        ['rsync', '-rt', '-e', shell, '--', remote(sources[0]), str(destination)]   # a download: no links, devices or modes
    for delay in (*SYNC_RETRY_S, None):
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        if not result.returncode:
            return
        if result.returncode not in SYNC_TRANSIENT or delay is None:
            break
        time.sleep(delay)   # rsync -a picks up where it stopped: a retry resends only what is missing
    raise StudioError('REMOTE_GPU_SYNC', f'rsync exited {result.returncode}: {result.stderr[-800:]}',
                      retryable=result.returncode in SYNC_TRANSIENT)


# Debian packages a server needs (dpkg names): Blender's runtime libraries - it dlopens EGL/GL even in background mode -
# plus what the worker and the transfer use.
SERVER_PACKAGES = ('ffmpeg', 'rsync', 'xz-utils', 'curl', 'libegl1', 'libgl1', 'libglx-mesa0', 'libxi6', 'libxxf86vm1', 'libxfixes3',
                   'libxrender1', 'libxkbcommon0', 'libsm6')
RENDER_CHECK = ("import bpy; s=bpy.context.scene; s.render.engine='BLENDER_WORKBENCH'; s.render.resolution_x=s.render.resolution_y=32; "
                "bpy.ops.object.camera_add(location=(0,-5,0), rotation=(1.5708,0,0)); s.camera=bpy.context.object; "
                "s.render.filepath='/tmp/studio_render_check.png'; bpy.ops.render.render(write_still=True); print('STUDIO_RENDER_OK')")


def bootstrap(pod):
    """Blender (the same version as the Mac's), ffmpeg and the Python packages the worker imports - once per server."""
    version = settings()['blender_version']
    major = '.'.join(version.split('.')[:2])
    url = os.environ.get('STUDIO_BLENDER_URL', f'https://download.blender.org/release/Blender{major}/blender-{version}-linux-x64.tar.xz')
    sums = os.environ.get('STUDIO_BLENDER_SHA_URL', f'https://download.blender.org/release/Blender{major}/blender-{version}.sha256')
    # one install per server even when two jobs arrive together (flock), the archive checked against blender.org's sums
    # the install is work: the server's busy file is kept fresh by the job that waits for it (remote_render._beating),
    # or its idle watchdog deletes the server mid-download (rehearsal 2026-10-08)
    packages = ' '.join(SERVER_PACKAGES)
    script = (f'set -e; exec 9>/tmp/studio-bootstrap.lock; flock 9; '
              # each package checked by name: an image that ships ffmpeg can still lack the GL/EGL libraries Blender loads
              # at start (first real pod, 2026-10-09: "Couldn't open libEGL.so.1", exit -6)
              f'missing=$(for p in {packages}; do dpkg-query -W -f="\\${{Status}}" $p 2>/dev/null | grep -q "install ok installed" || echo $p; done); '
              f'if [ -n "$missing" ]; then apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $missing >/dev/null; fi; '
              f'if [ ! -x /opt/blender/blender ]; then '
              f'curl -sSfL -o /tmp/blender.tar.xz {url}; '
              f'want=$(curl -sSfL {sums} | grep linux-x64.tar.xz | cut -d" " -f1); echo "$want  /tmp/blender.tar.xz" | sha256sum -c - >/dev/null; '
              f'mkdir -p /opt/blender && tar -xJf /tmp/blender.tar.xz -C /opt/blender --strip-components=1; rm /tmp/blender.tar.xz; fi; '
              f'python3 -c "import PIL, jsonschema" 2>/dev/null || python3 -m pip install -q --break-system-packages pillow jsonschema 2>/dev/null '
              f'|| python3 -m pip install -q pillow jsonschema; '
              f'/opt/blender/blender --version | head -1; '
              # the server is ready only when Blender renders: a version string does not load the GL libraries a render does
              f'/opt/blender/blender -b --factory-startup -noaudio --python-expr "{RENDER_CHECK}" 2>&1 | grep -E "STUDIO_RENDER_OK|Error|open|Abort|Segmentation" | tail -3')
    out = ssh(pod, script, timeout=1800).stdout.strip()
    if version not in out:
        raise StudioError('REMOTE_GPU_BOOTSTRAP', f'server Blender is {out!r}, not {version}')
    if 'STUDIO_RENDER_OK' not in out:
        raise StudioError('REMOTE_GPU_BOOTSTRAP', f'server Blender does not render: {out[-600:]!r}')
    return out


# ---- CLI ---------------------------------------------------------------------------------------------------------------

def status():
    """Settings, the month's spend (ledger) and our servers as RunPod lists them. Where the API cannot be reached (the
    agent's sandbox denies the network) the ledger part still answers, with running None and a warning naming the
    way that can see the servers - the broker answers studio_run gpu status outside the sandbox (broker.HERE)."""
    cfg = settings()
    out = {'enabled': cfg['enabled'], 'monthly_cap_usd': cfg['monthly_cap_usd'], 'spent_this_month_usd': month_spend()}
    try:
        pods = our_pods()
    except StudioError as error:
        return {**out, 'running': None, 'warnings': [f'REMOTE_GPU_LIVE_UNAVAILABLE: the servers cannot be listed from here ({error.message}); '
                                                     'call studio_run with ["gpu", "status"] - the broker lists them outside the sandbox']}
    return {**out, 'running': [{'id': p['id'], 'status': p.get('desiredStatus'), 'cost_per_hr': p.get('costPerHr'),
                                'gpu': (p.get('gpu') or {}).get('displayName')} for p in pods]}


def register_commands(subparsers):
    parser = subparsers.add_parser('gpu', help='Rented GPU servers for renders: status, sweep, enable (in the user\'s words)')
    commands = parser.add_subparsers(dest='gpu_command', required=True)
    commands.add_parser('status').set_defaults(handler=lambda a: status())
    commands.add_parser('sweep', help='delete our servers no running job owns').set_defaults(handler=lambda a: {'deleted': locked_sweep()})
    p = commands.add_parser('enable')
    p.add_argument('--user-words', required=True); p.add_argument('--monthly-cap', type=float)
    p.set_defaults(handler=lambda a: enable(a.user_words, a.monthly_cap))


def locked_sweep(exclude=None):
    """sweep under the lock a job holds while it chooses a server, with the live jobs read inside it: a server a job has
    just chosen is never deleted (2026-10-12 audit)."""
    from .remote_render import live_pod_ids
    POD_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with lock(POD_LOCK):
        return sweep(live_pod_ids(exclude=exclude))
