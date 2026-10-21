"""Rented GPU servers without renting one: the create request, the monthly cap, the sweep, path rewriting both ways,
and that the API key never lands in a request body, a ledger row or a job."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from studio import remote_gpu, remote_render
from studio.common import StudioError

KEY = 'rpa_TESTKEY_never_written_anywhere_0123456789abcdef'


class RemoteGpuTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [mock.patch.object(remote_gpu, 'CONFIG', root / 'remote_gpu.json'), mock.patch.object(remote_gpu, 'LEDGER', root / 'ledger.jsonl'),
                        mock.patch.object(remote_gpu, 'SSH_KEY', root / 'id'), mock.patch.dict(os.environ, {'RUNPOD_API_KEY': KEY}),
                        mock.patch.object(remote_render, 'REGISTRY', root / 'registry.json')]
        for p in self.patches:
            p.start()
        self.root = root

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_off_until_the_user_turns_it_on(self):
        with self.assertRaises(StudioError) as error:
            remote_gpu.create_pod(0.1)
        self.assertEqual(error.exception.code, 'REMOTE_GPU_DISABLED')
        with self.assertRaises(StudioError):
            remote_gpu.enable('ok')                                   # not the user's words
        cfg = remote_gpu.enable('원격 GPU 렌더 켜도 돼', 15)
        self.assertTrue(cfg['enabled'] and cfg['monthly_cap_usd'] == 15)

    def test_create_request_and_ledger(self):
        remote_gpu.enable('원격 GPU 렌더 켜도 돼', 15)
        sent = {}

        def call(method, path, body=None, timeout=60):
            sent.update(method=method, path=path, body=body)
            return {'id': 'pod1', 'costPerHr': 0.99, 'gpu': {'displayName': 'RTX 5090'}}
        with mock.patch.object(remote_gpu, '_call', side_effect=call):
            pod = remote_gpu.create_pod(0.2)
        body = sent['body']
        self.assertEqual((sent['method'], sent['path'], pod['id']), ('POST', '/pods', 'pod1'))
        self.assertEqual(body['gpuTypeIds'][0], 'NVIDIA GeForce RTX 5090')
        self.assertEqual((body['cloudType'], body['ports'], body['volumeInGb'], body['supportPublicIp']), ('SECURE', ['22/tcp'], 0, True))
        self.assertTrue(body['name'].startswith('studio-render-'))
        self.assertIn('ssh-ed25519', body['env']['PUBLIC_KEY'])
        start = ' '.join(body['dockerStartCmd'])
        self.assertIn('$RUNPOD_POD_ID', start); self.assertIn('/workspace/.busy', start)   # the server deletes itself when idle
        self.assertNotIn(KEY, json.dumps(body))
        rows = [json.loads(l) for l in (self.root / 'ledger.jsonl').read_text().splitlines()]
        self.assertEqual((rows[0]['event'], rows[0]['cost_per_hr']), ('start', 0.99))
        self.assertNotIn(KEY, (self.root / 'ledger.jsonl').read_text())

    def test_requests_carry_a_user_agent(self):
        """RunPod's Cloudflare answers Python's default agent with 403 / 1010 (first real call, 2026-10-09)."""
        seen = {}

        class Response:
            def __init__(self, request): seen['agent'] = request.get_header('User-agent')
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'[]'
        with mock.patch('urllib.request.urlopen', side_effect=lambda request, timeout=60: Response(request)):
            self.assertEqual(remote_gpu._call('GET', '/pods'), [])
        self.assertTrue(seen['agent'] and not seen['agent'].startswith('Python-urllib'))

    def test_monthly_cap(self):
        remote_gpu.enable('원격 GPU 렌더 켜도 돼', 1.0)
        an_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        rows = [{'event': 'start', 'pod_id': 'a', 'cost_per_hr': 0.9, 'at': an_hour_ago},
                {'event': 'end', 'pod_id': 'a', 'at': datetime.now(timezone.utc).isoformat()}]
        (self.root / 'ledger.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
        self.assertAlmostEqual(remote_gpu.month_spend(), 0.9, places=2)
        with self.assertRaises(StudioError) as error:
            remote_gpu.check_budget(0.2)                               # 0.9 + 0.2 > 1.0
        self.assertEqual(error.exception.code, 'GPU_BUDGET_EXCEEDED')
        self.assertEqual(remote_gpu.check_budget(0.05)['cap_usd'], 1.0)
        self.assertIsNone(remote_gpu.over_cap())                        # $0.90 of $1.00
        self.assertIn('reached', remote_gpu.over_cap(cap=0.9))

    def test_a_server_gone_without_an_end_stops_counting(self):
        """2026-10-12 audit: a server whose delete failed (or whose worker was killed) had no end row and was billed as
        running until the month ended, refusing every later job. A sweep closes servers RunPod no longer lists, at the
        latest the watchdog's limit after their start - never billed less than they could have run."""
        remote_gpu.enable('원격 GPU 렌더 켜도 돼', 2.0)
        start = datetime.now(timezone.utc) - timedelta(hours=10)
        (self.root / 'ledger.jsonl').write_text(json.dumps({'event': 'start', 'pod_id': 'ghost', 'cost_per_hr': 0.9, 'at': start.isoformat()}) + '\n')
        self.assertGreater(remote_gpu.month_spend(), 8.0)   # 10 h running, as the ledger stands
        with mock.patch.object(remote_gpu, '_call', side_effect=lambda m, p, b=None, timeout=60: [] if m == 'GET' else None):
            remote_gpu.sweep()
        hours = remote_gpu.settings()['max_pod_hours'] + remote_gpu.BOOT_TIMEOUT_S / 3600
        self.assertAlmostEqual(remote_gpu.month_spend(), round(hours * 0.9, 4), places=2)   # closed at the watchdog's limit
        ends = [json.loads(l) for l in (self.root / 'ledger.jsonl').read_text().splitlines() if '"end"' in l]
        self.assertEqual([e['reason'] for e in ends], ['gone'])

    def test_a_held_or_booting_server_is_never_closed_by_its_absence(self):
        """2026-10-12 second re-audit: one listing without a server a live job holds (or one just created, not listed
        yet) recorded an end while it ran on."""
        remote_gpu.enable('원격 GPU 렌더 켜도 돼', 2.0)
        now = datetime.now(timezone.utc)
        rows = [{'event': 'start', 'pod_id': 'held', 'cost_per_hr': 0.9, 'at': (now - timedelta(hours=1)).isoformat()},
                {'event': 'start', 'pod_id': 'new', 'cost_per_hr': 0.9, 'at': (now - timedelta(seconds=30)).isoformat()}]
        (self.root / 'ledger.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
        with mock.patch.object(remote_gpu, '_call', side_effect=lambda m, p, b=None, timeout=60: [] if m == 'GET' else None):
            remote_gpu.sweep(live_pod_ids={'held'})
        self.assertNotIn('"end"', (self.root / 'ledger.jsonl').read_text())

    def test_sweep_deletes_only_ours_that_nobody_holds(self):
        pods = [{'id': 'a', 'name': 'studio-render-mac', 'desiredStatus': 'RUNNING'}, {'id': 'b', 'name': 'studio-render-mac', 'desiredStatus': 'RUNNING'},
                {'id': 'c', 'name': 'someone-else', 'desiredStatus': 'RUNNING'}]
        deleted = []
        with mock.patch.object(remote_gpu, '_call', side_effect=lambda m, p, b=None, timeout=60: pods if m == 'GET' else deleted.append(p)):
            gone = remote_gpu.sweep(live_pod_ids={'b'})
        self.assertEqual((gone, deleted), (['a'], ['/pods/a']))

    def test_the_start_sweep_waits_for_the_server_choice(self):
        """2026-10-12 audit: the sweep at a job's start ran outside the lock another job holds while choosing a server, so it
        could delete a server that job had just chosen."""
        from studio.common import lock
        run = self.root / 'run'
        job_path = run / 'jobs' / 'j1' / 'job.json'
        job_path.parent.mkdir(parents=True)
        (run / 'run.json').write_text(json.dumps({'pending_jobs': ['j1'], 'completed_operations': []}))
        job_path.write_text(json.dumps({'job_id': 'j1', 'worker_token': 't', 'project_dir': str(self.root), 'cancel_path': str(self.root / 'cancel')}))
        held = []

        def sweep(live):
            try:
                with lock(self.root / 'pod.lock', blocking=False):
                    held.append(False)
            except StudioError:
                held.append(True)
            raise StudioError('STOP', 'stop after the sweep')
        with mock.patch.object(remote_gpu, 'POD_LOCK', self.root / 'pod.lock'), mock.patch.object(remote_gpu, 'sweep', side_effect=sweep), \
                mock.patch('studio.jobs.job_status', return_value={}), mock.patch('signal.signal'):
            remote_render.run_remote_worker(job_path, 't')
        self.assertEqual(held, [True])
        self.assertEqual(json.loads(job_path.read_text())['error']['code'], 'STOP')

    def _deletes(self):
        calls = []

        def call(method, path, body=None, timeout=60):
            calls.append((method, path))
            if method == 'POST':
                return dict(self.created)
            return [] if method == 'GET' else None
        return calls, call

    def test_a_server_is_checked_at_the_price_it_is_billed(self):
        """2026-10-18: the cap was checked at $1/h while the server cost $1.19/h. Over the cap at its own price, or with no
        price, the server is deleted before anything runs on it."""
        remote_gpu.enable('원격 GPU 렌더 켜도 돼', 1.0)
        calls, call = self._deletes()
        self.created = {'id': 'pod1', 'costPerHr': 1.5}
        with mock.patch.object(remote_gpu, '_call', side_effect=call):
            with self.assertRaises(StudioError) as refused:
                remote_gpu.create_pod(0.8)                               # $0.80 at $1/h, $1.20 at its own $1.50/h
        self.assertEqual(refused.exception.code, 'GPU_BUDGET_EXCEEDED')
        self.assertIn(('DELETE', '/pods/pod1'), calls)
        rows = [json.loads(l) for l in (self.root / 'ledger.jsonl').read_text().splitlines()]
        self.assertEqual([r['event'] for r in rows], ['start', 'end'])
        calls, call = self._deletes()
        self.created = {'id': 'pod2'}
        with mock.patch.object(remote_gpu, '_call', side_effect=call):
            with self.assertRaises(StudioError) as refused:
                remote_gpu.create_pod(0.01)
        self.assertEqual(refused.exception.code, 'REMOTE_GPU_PRICE_UNKNOWN')
        self.assertIn(('DELETE', '/pods/pod2'), calls)
        with self.assertRaises(StudioError):                              # the next guess is the highest price billed
            with mock.patch.object(remote_gpu, '_call', side_effect=AssertionError('no server is made')):
                remote_gpu.create_pod(0.7)                                 # 0.7 x 1.5 > 1.0 before any request

    def test_a_running_server_is_taken_only_inside_the_cap(self):
        remote_gpu.enable('원격 GPU 렌더 켜도 돼', 1.0)
        running = {'id': 'pod9', 'name': 'studio-render-x', 'desiredStatus': 'RUNNING', 'costPerHr': 2.0}
        with mock.patch.object(remote_gpu, 'POD_LOCK', self.root / 'pod.lock'), mock.patch.object(remote_gpu, 'our_pods', return_value=[running]), \
                mock.patch.object(remote_gpu, 'create_pod', side_effect=AssertionError('reused, not made')):
            with self.assertRaises(StudioError) as refused:
                remote_gpu.ensure_pod(0.6)                                # 0.6 h at its $2/h
        self.assertEqual(refused.exception.code, 'GPU_BUDGET_EXCEEDED')

    def test_a_running_server_bills_no_boot_and_a_new_one_does(self):
        remote_gpu.enable('원격 GPU 렌더 켜도 돼', 1.5)
        running = {'id': 'pod9', 'name': 'studio-render-x', 'desiredStatus': 'RUNNING', 'costPerHr': 2.0}
        with mock.patch.object(remote_gpu, 'POD_LOCK', self.root / 'pod.lock'), mock.patch.object(remote_gpu, 'our_pods', return_value=[running]), \
                mock.patch.object(remote_gpu, 'wait_ready', side_effect=lambda pod_id, **k: running), mock.patch.object(remote_gpu, 'bootstrap'):
            self.assertEqual(remote_gpu.ensure_pod(0.6, boot_hours=0.5)['id'], 'pod9')   # 0.6 h x $2 = 1.2 <= 1.5: no boot billed
        made = []
        with mock.patch.object(remote_gpu, 'POD_LOCK', self.root / 'pod.lock'), mock.patch.object(remote_gpu, 'our_pods', return_value=[]), \
                mock.patch.object(remote_gpu, 'create_pod', side_effect=lambda hours: made.append(hours) or running), \
                mock.patch.object(remote_gpu, 'wait_ready', side_effect=lambda pod_id, **k: running), mock.patch.object(remote_gpu, 'bootstrap'):
            remote_gpu.ensure_pod(0.6, boot_hours=0.5)
        self.assertEqual(made, [1.1])                                    # a new server: the work and its boot

    def _job(self, name, status, frames, pid=None, executor='runpod'):
        path = self.root / 'projects' / 'p' / 'runs' / 'r' / 'jobs' / name / 'job.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'job_id': name, 'status': status, 'frames': list(range(frames)), 'profile': 'final',
                                    'pid': pid, **({'executor': executor} if executor else {})}))

    def test_the_queue_is_priced_on_one_server(self):
        """2026-10-19: per-job estimates counted a boot each ($2.48 for a render billed $1.73) and each job was checked alone."""
        remote_gpu.enable('원격 GPU 렌더 켜도 돼', 3.0)
        self._job('waiting', 'awaiting_dispatch', 100)
        self._job('starting', 'queued', 50)                                   # no worker yet: it will run
        self._job('dead', 'running', 400, pid=999999)                         # its worker is gone: it runs only if resumed
        self._job('done', 'complete', 400)
        self._job('local', 'queued', 400, executor=None)                      # a Mac render bills nothing
        adding = [{'job_id': 'new', 'frames': list(range(150)), 'profile': 'final'}]
        quote = remote_render.queue_estimate(adding=adding, root=self.root)
        hours = 300 * remote_render.SECONDS_PER_FRAME['final'] / 3600 + remote_render.BOOT_HOURS   # one boot for the queue
        self.assertEqual((sorted(quote['queued_jobs']), quote['frames']), (['starting', 'waiting'], 300))
        self.assertAlmostEqual(quote['hours'], hours, places=3)
        self.assertAlmostEqual(quote['usd'], hours * remote_gpu.known_price(), places=3)
        self.assertTrue(quote['fits'])
        remote_gpu.enable('원격 GPU 렌더 켜도 돼', 0.3)
        self.assertFalse(remote_render.queue_estimate(adding=adding, root=self.root)['fits'])
        self.assertEqual(remote_render.queue_estimate(root=Path(self.tmp.name) / 'empty')['usd'], 0)

    def test_a_remote_submit_past_the_cap_makes_no_job(self):
        from studio import jobs
        remote_gpu.enable('원격 GPU 렌더 켜도 돼', 0.1)
        project = self.root / 'proj'
        prepared = {'project': {}, 'shot': {}, 'dependencies': {}, 'scene': None, 'frames': list(range(500)), 'settings': {},
                    'fingerprint': 'f', 'output': project / 'out'}
        with mock.patch.object(jobs, '_prepare_render', return_value=prepared), mock.patch.object(jobs, '_cached_render', return_value=None), \
                mock.patch('studio.freeze.require_code_frozen'), mock.patch.dict(os.environ, {'STUDIO_BROKERED': '1'}), \
                mock.patch.object(remote_render, 'REPO', self.root):
            project.mkdir()
            with self.assertRaises(StudioError) as refused:
                jobs.submit_render(project, 's', 'v0001', 'final', remote=True)
            self.assertEqual(refused.exception.code, 'GPU_BUDGET_EXCEEDED')
            self.assertIn('500 frames', refused.exception.message)
            self.assertEqual(list(project.glob('runs/*/jobs/*/job.json')), [])
            with mock.patch.object(jobs, '_cached_render', return_value={'job_id': 'old', 'status': 'complete', 'cache_hit': True}):
                self.assertTrue(jobs.submit_render(project, 's', 'v0001', 'final', remote=True)['cache_hit'])   # a cached render costs nothing

    def test_status_answers_from_the_ledger_where_the_servers_cannot_be_listed(self):
        remote_gpu.enable('원격 GPU 렌더 켜도 돼', 3.0)
        with mock.patch.object(remote_gpu, '_call', side_effect=StudioError('REMOTE_GPU_API', 'GET /pods: [Errno 1] Operation not permitted')):
            out = remote_gpu.status()
        self.assertEqual((out['running'], out['monthly_cap_usd']), (None, 3.0))
        self.assertTrue(out['warnings'][0].startswith('REMOTE_GPU_LIVE_UNAVAILABLE') and '"gpu", "status"' in out['warnings'][0])

    def test_a_render_stops_when_the_month_reaches_the_cap(self):
        """The estimate is a guess (seconds a frame): the cap is read again on every poll while the server renders."""
        run = self.root / 'run'
        job_path = run / 'jobs' / 'j1' / 'job.json'
        job_path.parent.mkdir(parents=True)
        (run / 'run.json').write_text(json.dumps({'pending_jobs': ['j1'], 'completed_operations': []}))
        job_path.write_text(json.dumps({'job_id': 'j1', 'worker_token': 't', 'project_dir': str(self.root), 'project_id': 'p',
                                        'cancel_path': str(self.root / 'cancel'), 'progress_path': str(self.root / 'progress.json'),
                                        'output_dir': str(self.root / 'out'), 'frames': [0, 1], 'profile': 'final',
                                        'render_settings': {'device': 'GPU'}}))
        pod = {'id': 'pod1', 'costPerHr': 1.19}
        sent, ended = [], []

        def ensure(hours, on_chosen=None, boot_hours=0.0, record=None):
            on_chosen(pod)
            return pod
        with mock.patch.object(remote_gpu, 'POD_LOCK', self.root / 'pod.lock'), mock.patch.object(remote_gpu, 'locked_sweep'), \
                mock.patch.object(remote_gpu, 'ensure_pod', side_effect=ensure), mock.patch('studio.jobs.render_job_problems', return_value=[]), \
                mock.patch.object(remote_render, '_upload'), mock.patch.object(remote_gpu, 'ssh_detached'), \
                mock.patch.object(remote_gpu, 'ssh', side_effect=lambda pod, command, **k: sent.append(command)), \
                mock.patch.object(remote_gpu, 'over_cap', return_value='this month $3.00 has reached the $3.00 cap'), \
                mock.patch.object(remote_render, '_remote_state', side_effect=AssertionError('stopped before another poll')), \
                mock.patch.object(remote_gpu, 'terminate', side_effect=lambda pod_id, reason: ended.append(pod_id)), \
                mock.patch('time.sleep'), mock.patch('studio.jobs.job_status', return_value={}), mock.patch('signal.signal'):
            remote_render.run_remote_worker(job_path, 't')
        job = json.loads(job_path.read_text())
        self.assertEqual((job['status'], job['error']['code']), ('failed', 'GPU_BUDGET_REACHED'))
        self.assertTrue(any('touch' in c and 'cancel' in c for c in sent))   # the server's render is told to stop
        self.assertEqual(ended, ['pod1'])                                       # and the server is deleted

    def test_the_heartbeat_holds_through_a_long_download(self):
        """2026-10-19: a finished render was judged dead while its frames came home, and its server was deleted."""
        import time as clock
        from studio.jobs import worker_alive
        job_path = self.root / 'job.json'
        job_path.write_text(json.dumps({'status': 'running', 'pid': os.getpid(), 'heartbeat_unix': clock.time() - 120}))
        with mock.patch('studio.jobs.HEARTBEAT_STALE_S', 1.0):
            self.assertFalse(worker_alive(json.loads(job_path.read_text()), job_path))   # stale before the step
            with remote_render._beating(job_path, every=0.05):
                clock.sleep(0.3)                                                          # a download reporting nothing
                self.assertTrue(worker_alive(json.loads(job_path.read_text()), job_path))

    def test_paths_rewrite_both_ways(self):
        job = {'project_dir': '/Users/me/p', 'scene_path': '/Users/me/p/shots/s/versions/v1/scene.blend', 'frames': [0, 1],
               'renderer_script': '/Users/me/p/runs/r/jobs/j/code/render_frames.py', 'other': '/Users/me/p2/x', 'profile': 'final'}
        mapping = {'/Users/me/p': '/workspace/studio/projects/p'}
        there = remote_render.rewrite(job, mapping)
        self.assertEqual(there['scene_path'], '/workspace/studio/projects/p/shots/s/versions/v1/scene.blend')
        self.assertEqual(there['other'], '/Users/me/p2/x')             # a sibling folder is not a prefix match
        self.assertEqual(remote_render.rewrite(there, {v: k for k, v in mapping.items()}), job)

    def test_estimate(self):
        job = {'frames': list(range(900)), 'profile': 'final'}
        hours = 900 * remote_render.SECONDS_PER_FRAME['final'] / 3600 + 5 / 60
        self.assertAlmostEqual(remote_render.estimate_hours(job), hours, places=6)
        self.assertAlmostEqual(remote_render.estimate_usd(job, 0.99), hours * 0.99, places=3)

    def test_key_from_the_profile_line(self):
        home = self.root / 'home'; home.mkdir()
        (home / '.zshrc').write_text(f'alias x=y\nexport RUNPOD_API_KEY={KEY}\n')
        with mock.patch.dict(os.environ, {'RUNPOD_API_KEY': ''}), mock.patch.object(Path, 'home', return_value=home):
            self.assertEqual(remote_gpu.api_key(), KEY)


if __name__ == '__main__':
    unittest.main()
