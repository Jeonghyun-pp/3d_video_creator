"""A remote render submitted through the broker's network-less sandbox waits ('awaiting_dispatch'); the broker - outside
the sandbox - starts only the allow-listed network entries, and only for a job whose every path lies inside the project
it sits in (floor_noise, 2026-10-09: the remote worker inherited the sandbox and RunPod answered EPERM; code review: a
job naming ~/.ssh as its output would have carried it off)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from studio import broker, jobs
from studio.common import REPO, read_json, write_json


class BrokerDispatchTest(unittest.TestCase):
    def setUp(self):
        (REPO / 'projects').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=REPO / 'projects')
        self.project = Path(self.tmp.name).resolve()
        write_json(self.project / 'project.json', {'project_id': 'dispatch_test'})

    def tearDown(self):
        self.tmp.cleanup()

    def job(self, name, executor='runpod', **paths):
        path = self.project / 'runs' / 'r1' / 'jobs' / name / 'job.json'
        path.parent.mkdir(parents=True)
        out = self.project / 'shots' / 's' / 'renders' / name
        write_json(path, {'job_id': name, 'status': 'queued', 'updated_at': '2026-10-09T00:00:00+00:00', 'worker_token': 't',
                          'project_dir': str(self.project), 'output_dir': str(out), 'cancel_path': str(path.parent / 'cancel'),
                          'progress_path': str(path.parent / 'progress.json'), 'scene_path': str(self.project / 'scene.blend'),
                          **({'executor': executor} if executor else {}), **paths})
        return path

    def test_remote_job_waits_inside_the_sandbox_and_the_broker_starts_it(self):
        remote = self.job('remote')
        with mock.patch.dict(os.environ, {broker.BROKERED: '1'}), mock.patch.object(jobs, 'spawn_worker') as spawn:
            self.assertIsNone(jobs.start_worker(remote))
            spawn.assert_not_called()
        self.assertEqual(read_json(remote)['status'], 'awaiting_dispatch')
        from studio import freeze
        clean = {'baseline': 'b', 'changed': {}, 'warnings': []}
        with mock.patch.object(freeze, 'check', return_value=clean), mock.patch.object(jobs, 'spawn_worker') as spawn:
            started = broker.dispatch([str(remote), str(remote.parent / 'render.json')])
        spawn.assert_called_once()
        self.assertEqual(spawn.call_args.args[1], '_remote_worker')
        self.assertEqual([s['entry'] for s in started], ['_remote_worker'])
        self.assertEqual(read_json(remote)['status'], 'queued')

    def test_a_job_naming_paths_outside_its_project_never_leaves(self):
        bad = self.job('bad', output_dir=str(Path.home() / '.ssh'))
        data = read_json(bad); data['status'] = 'awaiting_dispatch'; write_json(bad, data)
        with mock.patch.object(jobs, 'spawn_worker') as spawn:
            self.assertEqual(broker.dispatch([str(bad)]), [])
            spawn.assert_not_called()
        refused = read_json(bad)
        self.assertEqual((refused['status'], refused['error']['code']), ('failed', 'DISPATCH_REFUSED'))
        self.assertIn('output_dir', refused['error']['message'])

    def test_changed_frozen_code_never_leaves(self):
        remote = self.job('remote')
        data = read_json(remote); data['status'] = 'awaiting_dispatch'; write_json(remote, data)
        from studio import freeze
        changed = {'baseline': 'b', 'changed': {'network_worker': ['studio/remote_gpu.py']}, 'warnings': []}
        with mock.patch.object(freeze, 'check', return_value=changed), mock.patch.object(jobs, 'spawn_worker') as spawn:
            self.assertEqual(broker.dispatch([str(remote)]), [])
            spawn.assert_not_called()
        refused = read_json(remote)
        self.assertEqual(refused['status'], 'failed')
        self.assertIn('studio/remote_gpu.py', refused['error']['message'])

    def test_local_jobs_and_unlisted_entries_never_leave_the_sandbox(self):
        local = self.job('local', executor=None)
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

    def test_internal_entries_are_not_commands_and_the_sandbox_writes_only_the_venv(self):
        from studio.common import StudioError
        for entry in ('_worker', '_remote_worker'):
            with self.assertRaises(StudioError):
                broker.command([entry, 'job.json', 't'])
        profile = broker.profile()
        self.assertIn(str((Path(sys.prefix) / 'lib').resolve()), profile)
        self.assertNotIn(str(Path(sys.executable).resolve().parents[1] / 'lib') + '"', profile.replace(str((Path(sys.prefix) / 'lib').resolve()), ''))


if __name__ == '__main__':
    unittest.main()
