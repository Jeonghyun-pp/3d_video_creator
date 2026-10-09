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
from datetime import datetime, timezone
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
            out.write(json.dumps({**row, 'at': now()}) + '\n')


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


def watchdog_command(cfg):
    """The server's start command: sshd as the image provides it, plus a loop that deletes the server itself after
    idle_minutes without /workspace/.busy being touched or after max_pod_hours, using the pod-scoped key RunPod injects
    (RUNPOD_API_KEY, RUNPOD_POD_ID) - nothing from this Mac."""
    idle, life = int(cfg['idle_minutes'] * 60), int(cfg['max_pod_hours'] * 3600)
    api = os.environ.get('STUDIO_RUNPOD_API_IN_POD', API)
    loop = (f'touch /workspace/.busy; start=$(date +%s); while true; do sleep 15; now=$(date +%s); '
            f'idle=$(( now - $(stat -c %Y /workspace/.busy) )); '
            f'if [ $idle -gt {idle} ] || [ $(( now - start )) -gt {life} ]; then '
            f'curl -s -X DELETE -H "Authorization: Bearer $RUNPOD_API_KEY" {api}/pods/$RUNPOD_POD_ID; fi; done')
    return ['bash', '-c', f'mkdir -p /workspace; (/start.sh >/dev/null 2>&1 &); {loop}']


def create_pod(estimate_usd):
    cfg = settings()
    if not cfg['enabled']:
        raise StudioError('REMOTE_GPU_DISABLED', 'remote GPU renders are off', recovery="studio gpu enable --user-words \"<the user's words>\"")
    check_budget(estimate_usd)
    body = {'name': f'{NAME_PREFIX}{socket.gethostname().split(".")[0].lower()[:40]}', 'imageName': cfg['image'], 'gpuTypeIds': cfg['gpu_types'],
            'gpuTypePriority': 'custom', 'gpuCount': 1, 'cloudType': cfg['cloud_type'], 'containerDiskInGb': cfg['container_disk_gb'],
            'volumeInGb': 0, 'ports': ['22/tcp'], 'supportPublicIp': True, 'interruptible': False,
            'env': {'PUBLIC_KEY': _public_key()}, 'dockerStartCmd': watchdog_command(cfg)}
    pod = _call('POST', '/pods', body)
    _record({'event': 'start', 'pod_id': pod['id'], 'cost_per_hr': pod.get('costPerHr') or pod.get('adjustedCostPerHr') or 0,
             'gpu': (pod.get('gpu') or {}).get('displayName')})
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


def wait_ready(pod_id, timeout=BOOT_TIMEOUT_S, sleep=time.sleep):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pod = _call('GET', f'/pods/{pod_id}')
        host, port = endpoint(pod or {})
        if host and ssh(pod, ['true'], check=False, timeout=20).returncode == 0:
            return pod
        sleep(5)
    raise StudioError('REMOTE_GPU_TIMEOUT', f'server {pod_id} did not answer SSH within {timeout} s')


POD_LOCK = REPO / '.studio' / 'remote_gpu.lock'   # one decision at a time: create or reuse, and delete or keep


def ensure_pod(estimate_usd, on_chosen=None):
    """A ready server of ours: the running one, or a new one (inside the budget). on_chosen(pod) runs under the same
    lock the delete decision takes, so a job that has chosen a server is never left without it."""
    POD_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with lock(POD_LOCK):
        running = [p for p in our_pods() if p.get('desiredStatus') == 'RUNNING']
        pod = running[0] if running else create_pod(estimate_usd)
        if on_chosen:
            on_chosen(pod)
    pod = wait_ready(pod['id'])   # a failure here is the caller's to clean up: it deletes the server unless another job holds it
    bootstrap(pod)
    return pod


def sweep(live_pod_ids=()):
    """Delete our servers no live job owns. Returns the ids deleted."""
    gone = []
    for pod in our_pods():
        if pod['id'] not in live_pod_ids:
            terminate(pod['id'], 'sweep')
            gone.append(pod['id'])
    return gone


# ---- SSH ---------------------------------------------------------------------------------------------------------------

def _ssh_args(pod):
    host, port = endpoint(pod)
    return ['-i', str(SSH_KEY), '-p', str(port), '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null',
            '-o', 'LogLevel=ERROR', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15'], host


def ssh(pod, command, check=True, timeout=3600, capture=True):
    args, host = _ssh_args(pod)
    result = subprocess.run(['ssh', *args, f'root@{host}', ' '.join(command) if isinstance(command, list) else command],
                            capture_output=capture, text=True, timeout=timeout)
    if check and result.returncode:
        raise StudioError('REMOTE_GPU_SSH', f'{command if isinstance(command, str) else command[0]} exited {result.returncode}: {(result.stderr or "")[-800:]}')
    return result


def ssh_detached(pod, command, log='/dev/null'):
    """Start `command` on the server and return at once. The whole command runs in its own session with stdin, stdout
    and stderr away from the ssh channel: a background job that keeps any of them (e.g. `cd x && nohup y > log &`,
    whose list subshell keeps ssh's pipes) holds ssh open until it exits - the relay stalled behind a running render
    and the idle watchdog deleted the server (rehearsal 2026-10-08)."""
    return ssh(pod, f'nohup setsid bash -c {shlex.quote(command)} > {shlex.quote(log)} 2>&1 < /dev/null &', timeout=60)


def rsync(pod, sources, destination, upload=True, timeout=3600, cwd=None):
    """Upload `sources` (paths relative to `cwd`, kept as relative paths under `destination`) or download one remote
    folder. Relative paths from a working folder, not the '/./' marker: macOS's rsync (openrsync) ignores the marker
    and recreated the Mac's absolute paths on the server (rehearsal, 2026-10-08)."""
    args, host = _ssh_args(pod)
    shell = 'ssh ' + ' '.join(args)
    remote = lambda p: f'root@{host}:{p}'  # noqa: E731
    command = ['rsync', '-a', '--relative', '-e', shell, *[str(s) for s in sources], remote(destination)] if upload else \
        ['rsync', '-a', '-e', shell, remote(sources[0]), str(destination)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if result.returncode:
        raise StudioError('REMOTE_GPU_SYNC', f'rsync exited {result.returncode}: {result.stderr[-800:]}')


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
    # the install is work: keep the server's busy file fresh while it runs, or its idle watchdog deletes the server
    # mid-download (rehearsal 2026-10-08, a 380 MB download under emulation outlasted a short idle limit)
    packages = ' '.join(SERVER_PACKAGES)
    script = (f'set -e; (while true; do touch /workspace/.busy; sleep 10; done) & keep=$!; trap "kill $keep" EXIT; '
              f'exec 9>/tmp/studio-bootstrap.lock; flock 9; '
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
    pods = our_pods()
    cfg = settings()
    return {'enabled': cfg['enabled'], 'monthly_cap_usd': cfg['monthly_cap_usd'], 'spent_this_month_usd': month_spend(),
            'running': [{'id': p['id'], 'status': p.get('desiredStatus'), 'cost_per_hr': p.get('costPerHr'), 'gpu': (p.get('gpu') or {}).get('displayName')}
                        for p in pods]}


def register_commands(subparsers):
    parser = subparsers.add_parser('gpu', help='Rented GPU servers for renders: status, sweep, enable (in the user\'s words)')
    commands = parser.add_subparsers(dest='gpu_command', required=True)
    commands.add_parser('status').set_defaults(handler=lambda a: status())
    commands.add_parser('sweep', help='delete our servers no running job owns').set_defaults(handler=lambda a: {'deleted': sweep(_live_pods())})
    p = commands.add_parser('enable')
    p.add_argument('--user-words', required=True); p.add_argument('--monthly-cap', type=float)
    p.set_defaults(handler=lambda a: enable(a.user_words, a.monthly_cap))


def _live_pods():
    """Server ids that queued or running remote jobs hold (studio/remote_render.py records them on the job)."""
    from .remote_render import live_pod_ids
    return live_pod_ids()
