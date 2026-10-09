"""A remote render submitted through the broker's network-less sandbox waits ('awaiting_dispatch'); the broker - outside
the sandbox - starts only the allow-listed network entries (floor_noise, 2026-10-09: the remote worker inherited the
sandbox and RunPod answered EPERM)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from studio import broker, jobs
from studio.common import REPO, read_json, write_json


class BrokerDispatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=REPO / '.studio')
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def job(self, name, executor=None):
        path = self.root / name / 'job.json'
        path.parent.mkdir()
        write_json(path, {'job_id': name, 'status': 'queued', 'updated_at': '2026-10-09T00:00:00+00:00', **({'executor': executor} if executor else {})})
        return path

    def test_remote_job_waits_inside_the_sandbox_and_the_broker_starts_it(self):
        remote = self.job('remote', 'runpod')
        with mock.patch.dict(os.environ, {broker.BROKERED: '1'}), mock.patch.object(jobs, 'spawn_worker') as spawn:
            self.assertIsNone(jobs.start_worker(remote))
            spawn.assert_not_called()
        self.assertEqual(read_json(remote)['status'], 'awaiting_dispatch')
        with mock.patch.object(jobs, 'spawn_worker') as spawn:
            started = broker.dispatch([str(remote), str(remote.parent / 'render.json')])
        spawn.assert_called_once()
        self.assertEqual(spawn.call_args.args[1], '_remote_worker')
        self.assertEqual([s['entry'] for s in started], ['_remote_worker'])
        self.assertEqual(read_json(remote)['status'], 'queued')

    def test_local_jobs_and_unlisted_entries_never_leave_the_sandbox(self):
        local = self.job('local')
        with mock.patch.dict(os.environ, {broker.BROKERED: '1'}), mock.patch.object(jobs, 'spawn_worker', return_value=7) as spawn:
            self.assertEqual(jobs.start_worker(local), 7)       # a local render starts inside, as before
            self.assertEqual(spawn.call_args.args[1], '_worker')
        data = read_json(local); data['status'] = 'awaiting_dispatch'; write_json(local, data)
        with mock.patch.object(jobs, 'spawn_worker') as spawn:
            self.assertEqual(broker.dispatch([str(local)]), [])  # '_worker' is not a network entry: default deny
            spawn.assert_not_called()

    def test_outside_the_repository_is_ignored(self):
        with tempfile.TemporaryDirectory() as other:
            path = Path(other) / 'job.json'
            write_json(path, {'status': 'awaiting_dispatch', 'executor': 'runpod'})
            with mock.patch.object(jobs, 'spawn_worker') as spawn:
                self.assertEqual(broker.dispatch([str(path)]), [])
                spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
