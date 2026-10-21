import unittest

from studio.common import StudioError
from studio.waiting import wait_until


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class WaitUntilTest(unittest.TestCase):
    def test_returns_when_done_and_records_each_change(self):
        clock, rows, seen = Clock(), [], iter([(None, {'ip': None}), (None, {'ip': None}), (None, {'ip': '1.2.3.4'}), ('pod', {'ip': '1.2.3.4', 'ssh': 0})])
        out = wait_until(lambda: next(seen), limit_s=100, record=rows.append, clock=clock, sleep=clock.sleep)
        self.assertEqual(out, 'pod')
        self.assertEqual([r.get('ip') for r in rows], [None, '1.2.3.4', '1.2.3.4'])   # unchanged observations are not repeated
        self.assertEqual(rows[1]['at_s'], 10.0)

    def test_gives_up_on_no_progress_and_says_what_it_last_saw(self):
        clock, undone = Clock(), []
        with self.assertRaises(StudioError) as caught:
            wait_until(lambda: (None, {'desiredStatus': 'RUNNING', 'ip': None}), limit_s=900, stall_s=120, what='boot of p1',
                       code='REMOTE_GPU_TIMEOUT', on_give_up=undone.append, clock=clock, sleep=clock.sleep)
        self.assertEqual(caught.exception.code, 'REMOTE_GPU_TIMEOUT')
        self.assertIn('nothing changed for 120 s', caught.exception.message)
        self.assertIn('"ip": null', caught.exception.message)
        self.assertLess(clock.t, 200)               # the stall, not the total, ended it
        self.assertEqual(len(undone), 1)            # what the wait started is undone before it raises

    def test_progress_keeps_a_long_wait_alive_until_the_total(self):
        clock, n = Clock(), [0]

        def moving():
            n[0] += 1
            return None, {'frames': n[0]}
        with self.assertRaises(StudioError) as caught:
            wait_until(moving, limit_s=60, stall_s=10, clock=clock, sleep=clock.sleep)
        self.assertIn('no result in 60 s', caught.exception.message)



class WaitsInTheEngineTest(unittest.TestCase):
    """The engine's waits use wait_until (remote jobs audit 2026-10-21)."""

    def test_a_boot_records_what_it_saw_and_gives_up_when_nothing_moves(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from studio import remote_gpu
        clock = Clock()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(remote_gpu, 'LEDGER', Path(tmp) / 'ledger.jsonl'), \
                mock.patch.object(remote_gpu, '_call', return_value={'id': 'p1', 'desiredStatus': 'RUNNING', 'publicIp': '', 'portMappings': None}), \
                mock.patch('studio.waiting.time.monotonic', clock):
            with self.assertRaises(StudioError) as caught:
                remote_gpu.wait_ready('p1', timeout=900, sleep=clock.sleep)
            rows = [__import__('json').loads(l) for l in (Path(tmp) / 'ledger.jsonl').read_text().splitlines()]
        self.assertEqual(caught.exception.code, 'REMOTE_GPU_TIMEOUT')
        self.assertIn(f'nothing changed for {remote_gpu.BOOT_STALL_S} s', caught.exception.message)
        self.assertIn('"publicIp": false', caught.exception.message)
        self.assertEqual([r['event'] for r in rows], ['boot'])        # observed and kept (once: it never changed)
        self.assertLess(clock.t, 900)

    def test_a_clip_wait_that_gives_up_cancels_the_job_it_asked_for(self):
        from unittest import mock
        from studio import storyboard
        cancelled = []
        with mock.patch('studio.jobs.job_status', return_value={'status': 'queued', 'progress': {}}), \
                mock.patch('studio.jobs.cancel_job', side_effect=lambda p, j: cancelled.append(j)), \
                mock.patch('studio.jobs.queued_remote_ahead', return_value=3), \
                mock.patch.object(storyboard, 'ANIMATIC_REMOTE_WAIT_S', 0), mock.patch('time.sleep'), \
                mock.patch.object(storyboard, 'animatic_clip', return_value=__import__('pathlib').Path('/nonexistent/clip.mp4')):
            with self.assertRaises(StudioError) as caught:
                storyboard._animatic('/nonexistent', 's01', 'v0001', remote=True, job={'job_id': 'job_x'})
        self.assertEqual((caught.exception.code, cancelled), ('ANIMATIC_FAILED', ['job_x']))
        with mock.patch('studio.jobs.job_status', return_value={'status': 'queued', 'progress': {}}), \
                mock.patch('studio.jobs.cancel_job', side_effect=lambda p, j: cancelled.append(j)), \
                mock.patch('studio.jobs.queued_remote_ahead', return_value=3), \
                mock.patch.object(storyboard, 'ANIMATIC_REMOTE_WAIT_S', 0), mock.patch('time.sleep'), \
                mock.patch.object(storyboard, 'animatic_clip', return_value=__import__('pathlib').Path('/nonexistent/clip.mp4')):
            with self.assertRaises(StudioError):
                storyboard._animatic('/nonexistent', 's01', 'v0001', remote=True, job={'job_id': 'job_y', 'cache_hit': True})
        self.assertEqual(cancelled, ['job_x'])   # someone else's job (a cache hit) is not this wait's to cancel

    def test_the_broker_starts_waiting_jobs_while_its_command_runs(self):
        import sys
        import time as real
        from unittest import mock
        from studio import broker
        calls = []
        command = [sys.executable, '-c', 'import time; time.sleep(4); print("{\\"ok\\": true}")']
        with mock.patch.object(broker, 'command', return_value=command), mock.patch.object(broker, 'DISPATCH_EVERY_S', 0.5), \
                mock.patch.object(broker, 'dispatch_waiting', side_effect=lambda: calls.append(real.monotonic()) or []), \
                mock.patch.object(broker, 'dispatch', return_value=[]):
            started = real.monotonic()
            result = broker.run(['reel', 'clay', '--project', 'x', '--remote'], timeout=30)
        self.assertTrue(result['ok'])
        self.assertGreaterEqual(sum(1 for t in calls if t - started < 3.5), 3)   # during the command, not only after


if __name__ == '__main__':
    unittest.main()
