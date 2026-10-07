"""A worker's liveness (studio/jobs.py): EPERM from signal 0 means the process exists - a detached worker seen from inside
the studio sandbox (archcut3, 2026-10-08: a rendering job was marked interrupted while it rendered); a reused PID is
still caught by the heartbeat."""
import unittest
from unittest import mock

from studio import jobs


class LivenessTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(jobs.os, 'waitpid', side_effect=ChildProcessError)
        patcher.start(); self.addCleanup(patcher.stop)

    def test_eperm_is_alive(self):
        with mock.patch.object(jobs.os, 'kill', side_effect=PermissionError):
            self.assertTrue(jobs._process_running(4242))

    def test_gone_is_dead(self):
        with mock.patch.object(jobs.os, 'kill', side_effect=ProcessLookupError):
            self.assertFalse(jobs._process_running(4242))

    def test_a_running_job_still_needs_a_fresh_heartbeat(self):
        with mock.patch.object(jobs.os, 'kill', side_effect=PermissionError):
            fresh = {'pid': 4242, 'status': 'running', 'heartbeat_unix': 1000.0}
            self.assertTrue(jobs.worker_alive(fresh, None, now_unix=1001.0))
            self.assertFalse(jobs.worker_alive(fresh, None, now_unix=1000.0 + jobs.HEARTBEAT_STALE_S + 1))


if __name__ == '__main__':
    unittest.main()
