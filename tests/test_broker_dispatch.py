"""A remote render submitted through the broker's network-less sandbox waits ('awaiting_dispatch'); the broker - outside
the sandbox - starts only the allow-listed network entries, and only for a job whose every path lies inside the project
it sits in (floor_noise, 2026-10-09: the remote worker inherited the sandbox and RunPod answered EPERM; code review: a
job naming ~/.ssh as its output would have carried it off)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from studio.common import CODE
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
        write_json(path, {'job_id': name, 'status': 'queued', 'updated_at': '2026-10-09T00:00:00+00:00', 'worker_token': 'ab' * 16,
                          'project_id': 'dispatch_test', 'shot_id': 's', 'scene_version': 'v0001', 'fingerprint': name, 'frames': [0, 1],
                          'render_settings': {'device': 'GPU'},   # as render submit writes them
                          'project_dir': str(self.project), 'output_dir': str(out), 'cancel_path': str(path.parent / 'cancel'),
                          'progress_path': str(path.parent / 'progress.json'),
                          'scene_path': str(self.project / 'shots/s/versions/v0001/scene.blend'),
                          **({'executor': executor} if executor else {}), **paths})
        return path

    def test_a_render_job_is_dispatched_only_as_submit_made_it(self):
        # what the worker writes to, runs and ships is checked against the engine, not taken from job.json (2026-10-18)
        cases = {'other_shot': {'output_dir': str(self.project / 'shots' / 'x' / 'renders' / 'r')},
                 'project_itself': {'output_dir': str(self.project)},
                 'shell_device': {'render_settings': {'device': 'GPU; touch /tmp/x'}},
                 'shell_token': {'worker_token': 'x; curl evil'},
                 'other_script': {'renderer_script': str(self.project / 'shots/s/renders/x/evil.py')},
                 'climbing_version': {'scene_version': '../../../elsewhere/v001'},
                 'foreign_project': {'project_id': 'someone_else'},
                 'shell_shot': {'shot_id': 'a;cmd'},
                 'text_frames': {'frames': ['0; rm']}}
        for name, change in cases.items():
            self.assertTrue(broker.job_problems(self.job(name, **change), json.loads((self.project / 'runs/r1/jobs' / name / 'job.json').read_text())), name)
        shipped = self.job('shipped')
        code = shipped.parent / 'code'; code.mkdir()
        (code / 'render_frames.py').write_text('import os  # not the engine\'s')
        self.assertTrue(any('renderer code' in p for p in broker.job_problems(shipped, json.loads(shipped.read_text()))))
        (code / 'render_frames.py').write_bytes((CODE / 'studio/blender_ops/render_frames.py').read_bytes())
        self.assertEqual(broker.job_problems(shipped, json.loads(shipped.read_text())), [])
        (self.project / 'shots/s/renders').mkdir(parents=True, exist_ok=True)
        linked = self.job('linked')
        (self.project / 'shots/s/renders/linked').symlink_to(Path(self.tmp.name))   # a render folder that is a link
        self.assertTrue(any('link' in p for p in broker.job_problems(linked, json.loads(linked.read_text()))))

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

    def test_a_job_id_other_than_its_folder_never_leaves(self):
        """2026-10-12 audit: job_id was the one job field not checked, and the network workers build file names from it."""
        for job_id in ('../../../../escape', 'other'):
            bad = self.job('j' + str(len(job_id)))
            data = read_json(bad); data.update({'status': 'awaiting_dispatch', 'job_id': job_id}); write_json(bad, data)
            with mock.patch.object(jobs, 'spawn_worker') as spawn:
                self.assertEqual(broker.dispatch([str(bad)]), [])
                spawn.assert_not_called()
            self.assertIn('job_id', read_json(bad)['error']['message'])

    def test_an_unreadable_job_is_skipped_and_the_rest_start(self):
        broken = self.job('broken')
        broken.write_text('{ half written')
        remote = self.job('remote')
        data = read_json(remote); data['status'] = 'awaiting_dispatch'; write_json(remote, data)
        from studio import freeze
        clean = {'baseline': 'b', 'changed': {}, 'warnings': []}
        with mock.patch.object(freeze, 'check', return_value=clean), mock.patch.object(jobs, 'spawn_worker') as spawn:
            started = broker.dispatch([str(broken), str(remote)])
        spawn.assert_called_once()
        self.assertEqual([s.get('entry') for s in started], [None, '_remote_worker'])
        self.assertIn('unreadable', started[0]['skipped'])

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

    def test_internal_entries_are_not_commands_and_the_sandbox_writes_no_python(self):
        from studio.common import StudioError
        for entry in ('_worker', '_remote_worker'):
            with self.assertRaises(StudioError):
                broker.command([entry, 'job.json', 't'])
        profile = broker.profile()
        # no Python lib folder is writable: a .pth there runs in every later interpreter outside the sandbox (2026-10-18)
        for lib in (Path(sys.prefix) / 'lib', Path(sys.executable).resolve().parents[1] / 'lib', Path(sys.base_prefix) / 'lib'):
            self.assertNotIn(f'(subpath "{lib.resolve()}")', profile)


if __name__ == '__main__':
    unittest.main()
