"""Render worker liveness (studio/jobs.worker_alive): the PID the worker wrote exists, and a running worker's heartbeat is
fresh - no external tools, so the rule is the same inside the studio sandbox (where ps cannot run) and outside it."""
import os
import subprocess
import sys
import unittest

from studio.jobs import HEARTBEAT_STALE_S, worker_alive


class WorkerAliveTest(unittest.TestCase):
    def test_rules(self):
        me = os.getpid()
        now = 1_000_000.0
        self.assertFalse(worker_alive({'status': 'queued'}, None))                                              # never claimed
        self.assertTrue(worker_alive({'status': 'queued', 'pid': me}, None))                                    # waiting for the GPU
        self.assertTrue(worker_alive({'status': 'running', 'pid': me, 'heartbeat_unix': now - 1}, None, now))
        self.assertFalse(worker_alive({'status': 'running', 'pid': me, 'heartbeat_unix': now - HEARTBEAT_STALE_S - 1}, None, now))  # hung
        self.assertFalse(worker_alive({'status': 'running', 'pid': me}, None, now))                             # running, never beat
        dead = subprocess.Popen([sys.executable, '-c', 'pass']); dead.wait()
        self.assertFalse(worker_alive({'status': 'queued', 'pid': dead.pid}, None))                             # gone
        zombie = subprocess.Popen([sys.executable, '-c', 'pass'])                                               # exited, not reaped
        import time; time.sleep(0.5)
        self.assertFalse(worker_alive({'status': 'cancelled', 'pid': zombie.pid}, None))                        # a zombie is not alive
        running = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'])
        try:
            self.assertTrue(worker_alive({'status': 'queued', 'pid': running.pid}, None))
        finally:
            running.kill(); running.wait()


if __name__ == '__main__':
    unittest.main()
