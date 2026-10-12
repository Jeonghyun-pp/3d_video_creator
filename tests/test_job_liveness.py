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


class OtherJobsTest(unittest.TestCase):
    """2026-10-12: a reference fetch job sits beside render jobs; project status read render fields from it (KeyError
    'frames') after every command, and a running fetch has no heartbeat to be judged by."""
    def setUp(self):
        patcher = mock.patch.object(jobs.os, 'waitpid', side_effect=ChildProcessError)
        patcher.start(); self.addCleanup(patcher.stop)

    def test_a_running_fetch_is_alive_while_its_process_is(self):
        with mock.patch.object(jobs.os, 'kill', side_effect=PermissionError):
            fetch = {'pid': 4242, 'status': 'running', 'operation': 'reference_fetch'}
            self.assertTrue(jobs.worker_alive(fetch, None, now_unix=10_000.0))
        with mock.patch.object(jobs.os, 'kill', side_effect=ProcessLookupError):
            self.assertFalse(jobs.worker_alive(fetch, None))

    def test_status_of_a_fetch_job(self):
        import json, tempfile
        from pathlib import Path
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'runs' / 'run_x' / 'jobs' / 'job_fetch'
            folder.mkdir(parents=True)
            job = {'job_id': 'job_fetch', 'run_id': 'run_x', 'project_id': 'p', 'operation': 'reference_fetch', 'status': 'complete',
                   'output_dir': str(Path(tmp) / 'references'), 'progress_path': str(folder / 'progress.json'), 'pid': None,
                   'updated_at': datetime.now(timezone.utc).isoformat()}
            (folder / 'job.json').write_text(json.dumps(job))
            with mock.patch.object(jobs, 'find_job', return_value=folder / 'job.json'):
                status = jobs.job_status(tmp, 'job_fetch')
            self.assertEqual((status['operation'], status['progress'], status['attempt']), ('reference_fetch', {}, 1))


if __name__ == '__main__':
    unittest.main()
