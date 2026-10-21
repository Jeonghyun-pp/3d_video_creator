"""Remote render fault injection, free and local (no Docker, no GPU, no money): a fake RunPod API in this process, a fake
server that is a folder reached through a fake `ssh` (with its own process table: a server cannot see this machine's
processes), and a fake Blender that writes frames. The engine runs unchanged - render submit, the remote worker, the
frozen render worker on the "server", job status, the broker's dispatch. Only three things differ from a real server:
where the server keeps its files, Blender's install (bootstrap is skipped: it uses apt), and `setsid` (macOS lacks it: a
two-line stand-in that does the same, a new session).

Each scenario injects one fault and checks the principles (docs/ENGINE_PROGRESS.md, remote jobs):
  queue12       12 jobs on one server with real ssh latency     no job interrupted, one server, one delete
  two_at_once   two jobs finish together                        both complete, no server left
  die1 / die2   the server's worker is killed mid-render        the job ends interrupted with evidence, server deleted
  upload2/5     the upload drops 2 / 5 times                    2: complete; 5: failed with the reason, no orphan loop
  download2/5   the download drops 2 / 5 times                  complete (it is retried while files come home)
  download_forever  every download drops                        failed with the reason, frames kept, server deleted
  cmddrop       the worker's start runs but its answer drops    complete, one worker (the start is retried safely)
  never_boots   the server never gets an address                failed, the boot's observations recorded, server deleted
  stall         one status read hangs 58 s mid-render           never reported interrupted
  abandon       a clip wait gives up on a render that never ends  the job is cancelled and its server deleted
  brokered      a remote clip waited for inside the broker      complete (dispatched while the command runs)

Run: .venv/bin/python tests/remote/faults.py --from <a project with built shots> [scenario ...] [--json out.json]
(about 15 min for all; the project is copied, never touched).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
PY = os.path.abspath(sys.executable)
SCENARIOS = ('queue12', 'two_at_once', 'die1', 'die2', 'upload2', 'upload5', 'download2', 'download5', 'download_forever', 'cmddrop',
             'never_boots', 'stall', 'abandon', 'brokered')

FAKE_SSH = r'''#!/usr/bin/python3
import fcntl, json, os, random, subprocess, sys, time
SRV = os.environ['FI_SRV']
args = sys.argv[1:]
i = 0
while i < len(args) and not args[i].startswith('root@'):
    i += 2 if args[i] in ('-i', '-p', '-o', '-l') else 1
cmd = ' '.join(args[i + 1:])
def fault(name, take=False, match=None):
    path = SRV + '/.faults.json'
    with open(path, 'r+') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        data = json.load(f)
        value = data.get(name, 0)
        if match is not None and value and data.get(name + '_match', '') not in match:
            return 0
        if isinstance(value, int) and value > 0 and name.startswith('drop'):
            data[name] = value - 1; f.seek(0); f.truncate(); json.dump(data, f)
        elif take and value:
            data.pop(name); f.seek(0); f.truncate(); json.dump(data, f)
        return value
with open(SRV + '/.ssh_log', 'a') as log:
    log.write(json.dumps([time.time(), cmd[:200]]) + '\n')
lat = fault('ssh_latency_s')
if lat:
    time.sleep(lat)
if fault('ssh_down'):
    sys.stderr.write('ssh: connect to host port 22: Connection refused\n'); sys.exit(255)
cmd = cmd.replace('/workspace/', SRV + '/').replace('/opt/blender/blender', os.environ['FI_BLENDER']) \
         .replace('python3 ', os.environ['FI_PY'] + ' ')
env = {'PATH': os.path.dirname(os.path.abspath(__file__)) + ':/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin', 'HOME': SRV + '/home', 'PYTHONPATH': os.environ['FI_SITE'],
       'FI_PIDS': SRV + '/.pids', 'FI_SRV': SRV}
upload = 'rsync --server' in cmd and '--sender' not in cmd
download = 'rsync --server' in cmd and '--sender' in cmd
if upload and fault('upload_jitter_s'):
    time.sleep(random.uniform(0, fault('upload_jitter_s')))
if (upload and fault('drop_upload')) or (download and fault('drop_download')):
    p = subprocess.Popen(['bash', '-c', cmd], env=env)
    time.sleep(0.4); p.kill(); p.wait()
    sys.stderr.write('client_loop: send disconnect: Broken pipe\n'); sys.exit(255)
once = fault('fail_once_on')
if once and once in cmd and fault('fail_once_on', take=True):
    sys.stderr.write('Connection to host closed by remote host.\n'); sys.exit(255)
after = fault('fail_after_once_on')   # the command runs, then the link drops: was it done? (a retry must be safe)
if after and after in cmd and fault('fail_after_once_on', take=True):
    subprocess.run(['bash', '-c', cmd], env=env)
    sys.stderr.write('Connection to host closed by remote host.\n'); sys.exit(255)
if fault('worker_wont_start') and ' _worker ' in cmd:
    cmd = cmd.replace(' -m studio _worker', ' -c "import sys; sys.exit(1)" _worker')
if 'cat ' in cmd and '---' in cmd:
    hang = fault('hang_state_s', take=True, match=cmd)
    if hang:
        time.sleep(hang)
sys.exit(subprocess.run(['bash', '-c', cmd], env=env).returncode)
'''

# The fake server's process table: a python started there registers its pid; signal 0 to any other pid answers "no such
# process", as a real server (its own pid namespace) answers for a pid of this machine.
SITE = r'''import os
_P = os.environ.get('FI_PIDS')
if _P:
    with open(_P, 'a') as f:
        f.write(f'{os.getpid()}\n')
    _kill = os.kill
    def _k(pid, sig):
        if sig == 0 and str(pid) not in open(_P).read().split():
            raise ProcessLookupError(3, 'No such process (not on this server)')
        return _kill(pid, sig)
    os.kill = _k
'''

FAKE_BLENDER = r'''#!{py}
import json, os, sys, time
from PIL import Image
job = json.load(open(sys.argv[-1]))
srv = os.environ.get('FI_SRV', '')
faults = json.load(open(srv + '/.faults.json')) if os.path.exists(srv + '/.faults.json') else {{}}
out = job['output_dir']; os.makedirs(out + '/frames', exist_ok=True)
w, h = job['render_settings']['width'], job['render_settings']['height']
for i, f in enumerate(job['missing_frames']):
    time.sleep(faults.get('frame_s', 0.1))
    Image.new('RGB', (w, h), (i * 7 % 255, 80, 120)).save(f"{{out}}/frames/frame_{{f:06d}}.png")
    json.dump({{'completed_frames': i + 1, 'total_frames': len(job['missing_frames'])}}, open(job['progress_path'], 'w'))
json.dump({{'engine': 'FAKE'}}, open(out + '/renderer_actual.json', 'w'))
'''

# The remote worker as it runs, pointed at the fake server.
RELAY = r'''import json, os, sys
sys.path.insert(0, os.environ['FI_CODE'])
from studio import remote_gpu, remote_render
remote_render.REMOTE_ROOT = os.environ['FI_SRV'] + '/studio'
remote_render.REMOTE_REPO = os.environ['FI_SRV'] + '/studio/repo'
remote_gpu.bootstrap = lambda pod: 'Blender 5.2.2 STUDIO_RENDER_OK'
print(json.dumps(remote_render.run_remote_worker(sys.argv[1], sys.argv[2])))
'''


def child(name, source, work):
    """One scenario, in its own process (the engine reads its roots at import)."""
    W = Path(work)
    MAC, SRV, KIT = W / 'mac', W / 'srv', W / 'kit'
    for d in (MAC / 'projects', SRV / 'home', KIT / 'bin', KIT / 'site'):
        d.mkdir(parents=True, exist_ok=True)
    (KIT / 'bin/ssh').write_text(FAKE_SSH); (KIT / 'bin/ssh').chmod(0o755)
    (KIT / 'bin/setsid').write_text(f'#!{PY}\nimport os, sys\nos.setsid()\nos.execvp(sys.argv[1], sys.argv[1:])\n'); (KIT / 'bin/setsid').chmod(0o755)
    (KIT / 'site/sitecustomize.py').write_text(SITE)
    (KIT / 'blender').write_text(FAKE_BLENDER.format(py=PY)); (KIT / 'blender').chmod(0o755)
    (KIT / 'relay.py').write_text(RELAY)
    subprocess.run(['cp', '-cR', str(source), str(MAC / 'projects/')], check=True)
    (MAC / 'library').symlink_to(ROOT / 'library')   # the data root's library, as the checkout's (read only here)
    project = MAC / 'projects' / Path(source).name
    for stale in [*project.glob('runs/*/jobs'), *project.glob('shots/*/renders')]:
        shutil.rmtree(stale, ignore_errors=True)
    for clip in project.glob('shots/*/versions/*/animatic.mp4'):
        clip.unlink()
    events, t0 = [], time.time()

    def ev(*row):
        events.append([round(time.time() - t0, 2), *row])

    pods, boot = {}, {'mode': 'ok'}

    class API(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body=None):
            raw = json.dumps(body).encode() if body is not None else b''
            self.send_response(code); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(raw)))
            self.end_headers(); self.wfile.write(raw)

        def do_GET(self):
            if self.path == '/v1/pods':
                return self._send(200, list(pods.values()))
            pod = pods.get(self.path.rsplit('/', 1)[-1])
            return self._send(200, pod) if pod else self._send(404, {'error': 'pod not found'})

        def do_POST(self):
            json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            pid = uuid.uuid4().hex[:14]
            ok = boot['mode'] == 'ok'
            pods[pid] = {'id': pid, 'name': 'studio-render-fi', 'desiredStatus': 'RUNNING', 'costPerHr': 0.5, 'gpu': {'displayName': 'FAKE'},
                         'publicIp': '127.0.0.1' if ok else None, 'portMappings': {'22': 22} if ok else {}}
            ev('create', pid); self._send(200, pods[pid])

        def do_DELETE(self):
            pid = self.path.rsplit('/', 1)[-1]
            ev('delete', pid, pid in pods)
            if pods.pop(pid, None) is None:
                return self._send(404, {'error': 'pod not found'})
            self._send(200, None)

    api = ThreadingHTTPServer(('127.0.0.1', 0), API)
    threading.Thread(target=api.serve_forever, daemon=True).start()
    (W / 'remote_gpu.json').write_text(json.dumps({'enabled': True, 'monthly_cap_usd': 15, 'idle_minutes': 10, 'max_pod_hours': 2}))
    os.environ.update({'STUDIO_DATA_ROOT': str(MAC), 'STUDIO_RUNPOD_API': f'http://127.0.0.1:{api.server_address[1]}/v1',
                       'RUNPOD_API_KEY': 'fake-not-a-key', 'STUDIO_REMOTE_GPU_CONFIG': str(W / 'remote_gpu.json'),
                       'STUDIO_GPU_LEDGER': str(W / 'ledger.jsonl'), 'STUDIO_REMOTE_REGISTRY': str(W / 'registry.json'),
                       'STUDIO_RUNPOD_SSH_KEY': str(W / 'id_fi'), 'STUDIO_REMOTE_BOOT_TIMEOUT': '40' if name == 'never_boots' else '900',
                       'PATH': f"{KIT / 'bin'}:{os.environ['PATH']}", 'FI_SRV': str(SRV), 'FI_CODE': str(ROOT), 'FI_PY': PY,
                       'FI_BLENDER': str(KIT / 'blender'), 'FI_SITE': str(KIT / 'site')})

    def faults(**kw):
        with open(SRV / '.faults.json', 'a+') as f:
            fcntl.flock(f, fcntl.LOCK_EX); f.seek(0)
            data = json.loads(f.read() or '{}'); data.update(kw)
            f.seek(0); f.truncate(); json.dump(data, f)
    faults()
    sys.path.insert(0, str(ROOT))
    from studio import jobs
    from studio.common import read_json
    relays = {}

    def spawn(job_path, entry, token, env=None):
        log = open(Path(job_path).parent / 'worker.log', 'ab')
        process = subprocess.Popen([PY, str(KIT / 'relay.py'), str(job_path), token], cwd=ROOT, stdout=log, stderr=log,
                                   start_new_session=True, env={**os.environ, **(env or {})})
        relays[Path(job_path).parent.name] = process
        return process.pid
    jobs.spawn_worker = spawn
    shots = [s['shot_id'] for s in read_json(project / 'project.json')['shots']
             if read_json(project / 'shots' / s['shot_id'] / 'shot.json').get('scene_version')]

    def version(s):
        return read_json(project / 'shots' / s / 'shot.json')['scene_version']

    def submit(shot, frames=None):
        return jobs.submit_render(project, shot, version(shot), profile='layout', frames=frames, remote=True)['job_id']

    def local(job_id):
        return read_json(next(project.glob(f'runs/*/jobs/{job_id}/job.json')))

    def server(job_id):
        found = list(SRV.glob(f'studio/projects/*/runs/*/jobs/{job_id}/job.json'))
        try:
            return read_json(found[0]) if found else {}
        except Exception:   # noqa: BLE001 - half written: the next look has it
            return {}

    seen, seen_server = {}, {}

    def watch(ids, seconds, poll_status=True, extra=None):
        """job status on every poll, as a clip wait and every command's status refresh do; every status any reader saw."""
        end = time.time() + seconds
        while time.time() < end:
            for j in ids:
                status = jobs.job_status(project, j)['status'] if poll_status else local(j)['status']
                if seen.setdefault(j, []) == [] or seen[j][-1] != status:
                    seen[j].append(status); ev('status', j, status)
                there = server(j).get('status')   # what the server's copy says (another job's end may rewrite it)
                if there and (seen_server.setdefault(j, []) == [] or seen_server[j][-1] != there):
                    seen_server[j].append(there); ev('server status', j, there)
            if extra:
                extra()
            if relays and all(p.poll() is not None for p in relays.values()):
                break
            time.sleep(0.25)

    ids, notes = [], {}
    try:
        if name in ('queue12', 'two_at_once'):
            faults(frame_s=0.02, **({'ssh_latency_s': 1.0, 'upload_jitter_s': 20} if name == 'queue12' else {}))
            ids = [submit(s, None if name == 'queue12' else list(range(8))) for s in shots[:12 if name == 'queue12' else 2]]
            watch(ids, 420)
        elif name in ('die1', 'die2'):
            faults(frame_s=0.5)
            ids = [submit(s, list(range(40))) for s in shots[:1 if name == 'die1' else 2]]
            killed = {}

            def killer():
                s = server(ids[0])
                if not killed and s.get('status') == 'running' and (s.get('heartbeat_unix') or 0) > (s.get('render_started_at') or 1e18) + 3:
                    pid = s['pid']   # the server worker's own pid (render_worker writes it)
                    os.kill(pid, signal.SIGKILL); killed['at'] = time.time(); ev('killed', ids[0], pid)
            watch(ids, 180, extra=killer)
            notes['killed_at'] = killed.get('at')
        elif name.startswith(('upload', 'download')):
            kind = 'upload' if name.startswith('upload') else 'download'
            if name == 'download_forever':
                os.environ['STUDIO_REMOTE_FETCH_STALL'] = '20'   # inherited by the remote worker
            faults(frame_s=0.05, **{f'drop_{kind}': 10_000 if name == 'download_forever' else int(name[-1])})
            ids = [submit(shots[0], list(range(8)))]
            watch(ids, 240)
        elif name == 'cmddrop':
            faults(frame_s=0.05, fail_after_once_on='studio _worker')   # the worker's start runs, its answer is lost
            ids = [submit(shots[0], list(range(8)))]
            watch(ids, 120)
        elif name == 'never_boots':
            boot['mode'] = 'never'; faults(ssh_down=1)
            ids = [submit(s, list(range(8))) for s in shots[:2]]
            watch(ids, 400)
        elif name == 'stall':
            faults(frame_s=2.0)
            ids = [submit(shots[0], list(range(60))), submit(shots[1], list(range(4)))]
            stalled = {}

            def stall():
                if not stalled and local(ids[0])['status'] == 'running':
                    stalled['at'] = time.time(); faults(hang_state_s=58, hang_state_s_match=ids[0]); ev('stall', ids[0])
            watch(ids, 300, extra=stall)
        elif name == 'abandon':
            faults(frame_s=1000)   # alive and beating, never done: only the clip wait's own limit ends it
            from studio import storyboard
            storyboard.ANIMATIC_REMOTE_WAIT_S = 25
            ids = [submit(shots[0])]
            try:
                storyboard._animatic(project, shots[0], version(shots[0]), remote=True, job={'job_id': ids[0]})
            except Exception as error:   # noqa: BLE001
                notes['gave_up'] = str(error)[:300]
            watch(ids, 60, poll_status=False)
        elif name == 'brokered':
            # reel clay --remote through the broker, as Astra runs it: the command (in the studio sandbox) submits and waits
            # for its clip; the broker must start the remote worker while the command still runs
            faults(frame_s=0.02)
            home_cfg = Path(os.environ['HOME']) / '.config/studio/remote_gpu.json'   # the sandboxed side reads its HOME's
            home_cfg.parent.mkdir(parents=True, exist_ok=True); home_cfg.write_text((W / 'remote_gpu.json').read_text())
            from studio import broker
            try:
                result = broker.run(['reel', 'clay', '--project', str(project), '--shots', shots[0], '--remote'], timeout=240)
                notes['broker'] = {k: result.get(k) for k in ('ok', 'exit_code', 'error', 'dispatched')}
            except Exception as error:   # noqa: BLE001 - said as the scenario's failure
                notes['broker'] = {'raised': str(error)[:300]}
            ids = [p.parent.name for p in project.glob('runs/*/jobs/*/job.json')]
            watch(ids, 30, poll_status=False)
    finally:
        for p in relays.values():
            if p.poll() is None:
                ev('relay still running', p.pid); os.killpg(p.pid, signal.SIGKILL)
        listed = subprocess.run(['pgrep', '-fl', str(SRV)], capture_output=True, text=True).stdout.splitlines()
        orphans = [line.split(' ', 1)[0] for line in listed]
        notes['orphans'] = [line[:160] for line in listed]
        for pid in orphans:
            try:
                os.kill(int(pid), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass
        api.shutdown()
    final = {j: {k: local(j).get(k) for k in ('status', 'error', 'evidence', 'waits')} for j in ids}
    ledger = [json.loads(l) for l in (W / 'ledger.jsonl').read_text().splitlines()] if (W / 'ledger.jsonl').is_file() else []
    notes['worker_starts'] = len(list(SRV.glob('studio/projects/*/runs/*/jobs/*/job.json.started.*')))
    notes['workers_run'] = sum(1 for line in (SRV / '.ssh_log').read_text().splitlines() if 'exec ' in line and '_worker' in line) if (SRV / '.ssh_log').is_file() else 0
    return {'scenario': name, 'final': final, 'seen': seen, 'seen_server': seen_server, 'pods_left': list(pods), 'orphans': len(orphans),
            'creates': sum(e[1] == 'create' for e in events), 'deletes': sum(e[1] == 'delete' and e[3] for e in events),
            'ledger': [(r['event'], r.get('reason')) for r in ledger], 'events': events, 'notes': notes,
            'kept_frames': sorted(p.name for p in project.glob('runs/*/jobs/*/.incoming/**/*.png'))[:3]}


def judge(r):
    """The principles' checks for one scenario: [] when it passes, else what failed."""
    n, final, seen = r['scenario'], r['final'], r['seen']
    statuses = [v['status'] for v in final.values()]
    ever = {s for v in seen.values() for s in v}
    bad = []
    if r['pods_left']:
        bad.append(f"servers left: {r['pods_left']}")
    if r['orphans']:
        bad.append(f"{r['orphans']} processes left on the server: {r['notes'].get('orphans')}")
    if n == 'brokered' and not final:
        bad.append(f"no job was made: {r['notes'].get('broker')}")
    if n in ('queue12', 'two_at_once', 'upload2', 'download2', 'download5', 'cmddrop', 'stall', 'brokered'):
        if set(statuses) != {'complete'}:
            bad.append(f'not all complete: {statuses}')
        if 'interrupted' in ever:
            bad.append('a reader saw interrupted')
        if any('interrupted' in v for v in r.get('seen_server', {}).values()):
            bad.append("a job's server copy was marked interrupted")
    if n in ('queue12', 'two_at_once') and r['creates'] != 1:
        bad.append(f"{r['creates']} servers created")
    if n in ('die1', 'die2'):
        first = final[next(iter(final))]
        if first['status'] != 'interrupted' or not first.get('evidence'):
            bad.append(f"killed job ended {first['status']} with evidence {first.get('evidence')}")
    if n in ('upload5', 'download_forever', 'never_boots'):
        if any(s != 'failed' for s in statuses) or not all((v['error'] or {}).get('message') for v in final.values()):
            bad.append(f'expected failed with a reason: {statuses}')
    if n == 'download_forever' and not r['kept_frames']:
        bad.append('the frames that came home were thrown away')
    if n == 'never_boots' and not all(v.get('waits') for v in final.values()):
        bad.append('the boot wait recorded no observations')
    if n == 'cmddrop' and r['notes'].get('worker_starts') != 1:
        bad.append(f"worker start markers: {r['notes'].get('worker_starts')}")
    if n == 'abandon' and statuses != ['cancelled']:
        bad.append(f"the abandoned job ended {statuses}")
    return bad


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('scenarios', nargs='*', default=list(SCENARIOS))
    parser.add_argument('--from', dest='source', required=True)
    parser.add_argument('--json')
    parser.add_argument('--child')
    parser.add_argument('--work')
    a = parser.parse_args()
    if a.child:
        print('FI_RESULT ' + json.dumps(child(a.child, a.source, a.work), default=str))
        return
    results = []
    for name in a.scenarios:
        work = tempfile.mkdtemp(prefix=f'faults-{name}-')
        done = subprocess.run([PY, __file__, '--child', name, '--from', a.source, '--work', work], capture_output=True, text=True,
                              env={**os.environ, 'HOME': work})   # no frozen baseline here: a warning, not a refusal
        line = [l for l in done.stdout.splitlines() if l.startswith('FI_RESULT ')]
        r = json.loads(line[-1][10:]) if line else {'scenario': name, 'crash': (done.stdout + done.stderr)[-1500:]}
        r['failed'] = judge(r) if line else ['crashed']
        results.append(r)
        print(f"{name:12} {'PASS' if not r['failed'] else 'FAIL'}  {'; '.join(r['failed'])[:300]}", flush=True)
        shutil.rmtree(work, ignore_errors=True)
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=1, default=str))
    sys.exit(1 if any(r['failed'] for r in results) else 0)


if __name__ == '__main__':
    main()
