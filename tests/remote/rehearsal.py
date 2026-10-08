"""Remote render rehearsal: the whole remote path against a fake RunPod (tests/remote/fake_runpod.py) whose servers are
local Docker containers - no money, no GPU. What it proves, on real SSH, rsync, a real Linux Blender and the unchanged
frozen render worker:
  1. two jobs submitted together share one server, render, come back with verified frames, and the server is deleted
     the moment both are done (one create, one delete, ledger start and end);
  2. a remote worker killed mid-render (SIGKILL: no clean-up runs) - the server's watchdog deletes the server itself;
  3. a cancelled job - the server is deleted at once;
  4. a server that never answers SSH - the job fails and the server is deleted;
  5. a server nobody holds - the sweep deletes it.
What it cannot prove (the first real use checks these): RunPod's exact responses, public IP assignment, OptiX on an RTX
5090 and its speed, whether the pod-scoped key may delete its own pod.
Run: .venv/bin/python tests/remote/rehearsal.py   (Docker running; about 20-40 min under x86 emulation)
"""
from pathlib import Path
import json
import os
import signal
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
CACHE = Path(os.environ.get('STUDIO_REHEARSAL_BLENDER_CACHE', ''))
if not (CACHE / 'blender-5.2.2-linux-x64.tar.xz').is_file():
    sys.exit('set STUDIO_REHEARSAL_BLENDER_CACHE to a folder holding blender-5.2.2-linux-x64.tar.xz and blender-5.2.2.sha256')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests' / 'remote'))
from fake_runpod import Fake  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix='remote-rehearsal-'))
USER_KEY = 'rpa_rehearsal_' + os.urandom(8).hex()
fake = Fake(USER_KEY, CACHE, log_file=os.environ.get('STUDIO_REHEARSAL_FAKE_LOG'))
os.environ.update({'RUNPOD_API_KEY': USER_KEY, 'STUDIO_RUNPOD_API': fake.url, 'STUDIO_RUNPOD_API_IN_POD': fake.url_in_pod,
                   'STUDIO_REMOTE_GPU_CONFIG': str(TMP / 'remote_gpu.json'), 'STUDIO_GPU_LEDGER': str(TMP / 'ledger.jsonl'),
                   'STUDIO_REMOTE_REGISTRY': str(TMP / 'registry.json'), 'STUDIO_RUNPOD_SSH_KEY': str(TMP / 'id_studio'),
                   'STUDIO_BLENDER_URL': f'http://host.docker.internal:{fake.port}/blender/blender-5.2.2-linux-x64.tar.xz',
                   'STUDIO_BLENDER_SHA_URL': f'http://host.docker.internal:{fake.port}/blender/blender-5.2.2.sha256',
                   'STUDIO_REMOTE_BOOT_TIMEOUT': '120'})
(TMP / 'remote_gpu.json').write_text(json.dumps({'enabled': True, 'enabled_by': 'rehearsal (fake RunPod)', 'monthly_cap_usd': 15,
                                                 'idle_minutes': 0.75, 'max_pod_hours': 1}))

from studio import remote_gpu, remote_render  # noqa: E402
from studio.blender import build_shot  # noqa: E402
from studio.common import read_json, write_json  # noqa: E402
from studio.jobs import cancel_job, find_job, job_status, submit_render, verify_render  # noqa: E402
from studio.project import init_project, shot_path  # noqa: E402

SCENE = {'world': {'kind': 'blockout', 'color': [0.35, 0.36, 0.4], 'strength': 0.8, 'samples': 8},
         'materials': {'m': {'color': [0.7, 0.4, 0.2]}},
         'primitives': [{'id': 'block', 'shape': 'box', 'size': [1, 1, 1], 'at': [0, 0, 0.5], 'material': 'm'},
                        {'id': 'floor', 'shape': 'box', 'size': [10, 10, 0.2], 'at': [0, 0, -0.1], 'material': 'm'}]}
checks, started = [], time.monotonic()


def wait(predicate, timeout, what, step=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    raise AssertionError(f'timed out: {what}')


def done(p, job_id):
    status = job_status(p, job_id)
    return status if status['status'] in ('complete', 'failed', 'cancelled', 'interrupted') else None


def submit(p, version, frames):
    return submit_render(p, 's', version, 'review', frames, samples_override=4, remote=True)


try:
    p = Path(init_project('rehearsal', {'request': 'remote render rehearsal', 'shots': [{'shot_id': 's', 'frame_count': 12}]}, TMP)['project_path'])
    shot = read_json(shot_path(p, 's'))
    shot.update({'scene': SCENE, 'screen': {'subject': ['block']},
                 'camera': {'projection': 'perspective', 'movement': 'authored', 'target_anchor': None,
                            'keys': [{'frame': 0, 'location': [3, -5, 2.5], 'target': [0, 0, 0.5]}, {'frame': 11, 'location': [4, -4, 2.5], 'target': [0, 0, 0.5]}]}})
    write_json(shot_path(p, 's'), shot)
    version = build_shot(p, 's', None)['scene_version']

    # 1. two jobs together: one server, verified frames, deleted at the end
    a, b = submit(p, version, [0, 1]), submit(p, version, [2, 3])
    results = [wait(lambda j=j: done(p, j['job_id']), 2400, f"job {j['job_id']}") for j in (a, b)]
    assert all(r['status'] == 'complete' for r in results), results
    jobs = [read_json(find_job(p, r['job_id'])) for r in results]
    assert jobs[0]['remote']['pod_id'] == jobs[1]['remote']['pod_id'], [j['remote'] for j in jobs]
    assert all(verify_render(j) for j in jobs)
    actual = read_json(Path(jobs[0]['output_dir']) / 'render.json')
    assert actual['compute'] == 'optix' and actual['renderer_actual']['blender_version'].startswith('5.2.2'), actual['renderer_actual']
    assert actual['renderer_actual'].get('compute') == 'cpu', actual['renderer_actual']   # no GPU in the fake: it says so
    wait(lambda: not fake.pods, 60, 'server deleted after both jobs')
    posts = [e for e in fake.log if e[0] == 'POST']
    ledger = [json.loads(l) for l in (TMP / 'ledger.jsonl').read_text().splitlines()]
    assert len(posts) == 1 and [r['event'] for r in ledger] == ['start', 'end'], (posts, ledger)
    assert USER_KEY not in json.dumps(jobs) + (TMP / 'ledger.jsonl').read_text()
    checks.append(f'two_jobs_one_server_verified_and_deleted ({round(time.monotonic() - started)} s)')

    # 2. remote worker killed mid-render: the server deletes itself
    c = submit(p, version, [4, 5, 6, 7])
    job_c = wait(lambda: (lambda j: j if j.get('status') == 'running' and j.get('remote') else None)(read_json(find_job(p, c['job_id']))), 900, 'job c running')
    os.kill(job_c['pid'], signal.SIGKILL)
    wait(lambda: not fake.pods, 240, 'watchdog self-delete')
    assert any(e[0] == 'DELETE' and e[2] != 'user' for e in fake.log), fake.log[-5:]   # deleted by the server's own key
    assert job_status(p, c['job_id'])['status'] == 'interrupted'
    checks.append('killed_worker_server_deleted_itself')

    # 3. cancel: the server goes at once
    d = submit(p, version, [8, 9, 10, 11])
    wait(lambda: read_json(find_job(p, d['job_id'])).get('status') == 'running', 900, 'job d running')
    cancel_job(p, d['job_id'])
    wait(lambda: done(p, d['job_id']), 120, 'job d stopped')
    wait(lambda: not fake.pods, 60, 'server deleted after cancel')
    assert job_status(p, d['job_id'])['status'] == 'cancelled'
    checks.append('cancelled_job_server_deleted')

    # 4. a server that never answers: the job fails, the server is deleted
    fake.broken_next = True
    e = submit(p, version, [0, 1, 2])
    status = wait(lambda: done(p, e['job_id']), 400, 'job e stopped')
    assert status['status'] == 'failed' and status['error']['code'] == 'REMOTE_GPU_TIMEOUT', status
    wait(lambda: not fake.pods, 60, 'broken server deleted')
    checks.append('unreachable_server_job_failed_server_deleted')

    # 5. a server nobody holds: the sweep deletes it
    orphan = remote_gpu.create_pod(0.01)
    assert remote_gpu.sweep(remote_render.live_pod_ids()) == [orphan['id']] and not fake.pods
    checks.append('orphan_swept')
    print('STUDIO_REMOTE_REHEARSAL ' + json.dumps({'ok': True, 'checks': checks, 'minutes': round((time.monotonic() - started) / 60, 1),
                                                   'spent_fake_usd': remote_gpu.month_spend()}))
finally:
    fake.close()
