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

    def test_sweep_deletes_only_ours_that_nobody_holds(self):
        pods = [{'id': 'a', 'name': 'studio-render-mac', 'desiredStatus': 'RUNNING'}, {'id': 'b', 'name': 'studio-render-mac', 'desiredStatus': 'RUNNING'},
                {'id': 'c', 'name': 'someone-else', 'desiredStatus': 'RUNNING'}]
        deleted = []
        with mock.patch.object(remote_gpu, '_call', side_effect=lambda m, p, b=None, timeout=60: pods if m == 'GET' else deleted.append(p)):
            gone = remote_gpu.sweep(live_pod_ids={'b'})
        self.assertEqual((gone, deleted), (['a'], ['/pods/a']))

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
        self.assertAlmostEqual(remote_render.estimate_usd(job, 0.99), (900 * 1.0 / 3600 + 5 / 60) * 0.99, places=3)

    def test_key_from_the_profile_line(self):
        home = self.root / 'home'; home.mkdir()
        (home / '.zshrc').write_text(f'alias x=y\nexport RUNPOD_API_KEY={KEY}\n')
        with mock.patch.dict(os.environ, {'RUNPOD_API_KEY': ''}), mock.patch.object(Path, 'home', return_value=home):
            self.assertEqual(remote_gpu.api_key(), KEY)


if __name__ == '__main__':
    unittest.main()
