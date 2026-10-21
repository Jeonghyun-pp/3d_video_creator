"""A command to the server is retried when the link fails, because every command is safe to send twice (remote jobs
audit 2026-10-21: one dropped `touch` failed a job and deleted its server); the worker's start is made so (once)."""
import subprocess
import unittest
from unittest import mock

from studio import remote_gpu

POD = {'publicIp': '1.2.3.4', 'portMappings': {'22': 2222}}


class SshRetryTest(unittest.TestCase):
    def run_with(self, codes, **kw):
        calls = []

        def fake(argv, **k):
            calls.append(argv)
            code = codes[min(len(calls), len(codes)) - 1]
            if code == 'timeout':
                raise subprocess.TimeoutExpired(argv, k.get('timeout'))
            return subprocess.CompletedProcess(argv, code, '', 'err')
        with mock.patch.object(remote_gpu.subprocess, 'run', side_effect=fake), mock.patch.object(remote_gpu.time, 'sleep'):
            result = remote_gpu.ssh(POD, 'touch /workspace/.busy', check=False, **kw)
        return result.returncode, len(calls)

    def test_a_dropped_link_is_retried(self):
        self.assertEqual(self.run_with([255, 255, 0]), (0, 3))
        self.assertEqual(self.run_with(['timeout', 0]), (0, 2))          # an answer that never came is a dropped link too

    def test_a_failing_command_is_not(self):
        self.assertEqual(self.run_with([1]), (1, 1))

    def test_retries_end_and_say_so(self):
        code, calls = self.run_with([255])
        self.assertEqual((code, calls), (255, len(remote_gpu.SYNC_RETRY_S) + 1))
        with mock.patch.object(remote_gpu.subprocess, 'run', return_value=subprocess.CompletedProcess([], 255, '', 'gone')), \
                mock.patch.object(remote_gpu.time, 'sleep'):
            with self.assertRaises(remote_gpu.StudioError) as caught:
                remote_gpu.ssh(POD, 'true')
        self.assertTrue(caught.exception.retryable)

    def test_a_workers_start_is_guarded(self):
        sent = []
        with mock.patch.object(remote_gpu, 'ssh', side_effect=lambda pod, command, **k: sent.append(command)):
            remote_gpu.ssh_detached(POD, 'exec python3 -m studio _worker j t', pidfile='/w/j.pid', once='/w/j.started.t')
        self.assertTrue(sent[0].startswith("mkdir /w/j.started.t 2>/dev/null || exit 0; nohup setsid bash -c"))
        self.assertTrue(sent[0].endswith('& echo $! > /w/j.pid'))


class WatchdogTest(unittest.TestCase):
    """The server ends itself through each way the pod-scoped key may use, in order, every answer logged (watchdog
    probe 2026-10-21: the REST delete answers 403 to that key, so the idle watchdog never deleted a server)."""

    def test_each_way_is_tried_in_order_and_logged_with_a_valid_request(self):
        import json
        import tempfile
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        bodies = []

        class Fake(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self, code):
                raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
                bodies.append((self.command, self.path, self.headers.get('Authorization'), raw.decode()))
                self.send_response(code); self.send_header('Content-Length', '2'); self.end_headers(); self.wfile.write(b'{}')

            def do_POST(self):
                self._answer(200)

            def do_DELETE(self):
                self._answer(403)
        server = HTTPServer(('127.0.0.1', 0), Fake)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        url = f'http://127.0.0.1:{server.server_address[1]}'
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(remote_gpu, 'GRAPHQL_IN_POD', f'{url}/graphql'):
            ways = remote_gpu.self_end_ways(f'{url}/v1')
            self.assertEqual([name for name, _ in ways], ['graphql podTerminate', 'runpodctl remove', 'runpodctl stop', 'rest delete'])
            tries = remote_gpu.self_end_script(f'{url}/v1', f'{tmp}/watchdog.log')   # the script the watchdog runs
            subprocess.run(['bash', '-c', tries], env={'PATH': '/usr/bin:/bin', 'RUNPOD_API_KEY': 'podkey', 'RUNPOD_POD_ID': 'abc123'},
                           check=True, timeout=60)
            log = open(f'{tmp}/watchdog.log').read()
        graphql = next(b for b in bodies if b[0] == 'POST')
        self.assertEqual(graphql[2], 'Bearer podkey')
        self.assertIn('podId: "abc123"', json.loads(graphql[3])['query'])   # the body is JSON and names this pod
        self.assertIn(('DELETE', '/v1/pods/abc123'), [(b[0], b[1]) for b in bodies])
        self.assertIn('graphql podTerminate: {} HTTP 200', log)
        self.assertIn('rest delete: {} HTTP 403', log)
        self.assertIn('runpodctl remove: no runpodctl', log)
        self.assertNotIn('podkey', log)                                       # answers only, never the key
        start = ' '.join(remote_gpu.watchdog_command({'idle_minutes': 1.5, 'max_pod_hours': 0.25}))
        self.assertIn(remote_gpu.WATCHDOG_LOG, start); self.assertIn('podTerminate', start)


if __name__ == '__main__':
    unittest.main()
