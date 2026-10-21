"""Who may judge and change a job (studio/jobs.py, remote jobs audit 2026-10-21): readers never write, a pid is judged
only on the machine that wrote it, and every change goes through update_job. Why: a rented server's status refresh
marked queued jobs interrupted by signalling this Mac's pid (submarine, 2026-10-20), a status poll here marked a live
remote job interrupted during one slow ssh read, and writers overwrote each other's fields."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from studio import jobs


def stamp(seconds_ago=0):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


class JobFile:
    def __init__(self, test, **fields):
        tmp = tempfile.TemporaryDirectory(); test.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name) / 'runs' / 'run_x' / 'jobs' / 'job_a'
        self.folder.mkdir(parents=True)
        self.path = self.folder / 'job.json'
        job = {'job_id': 'job_a', 'run_id': 'run_x', 'project_id': 'p', 'status': 'queued', 'pid': None, 'frames': [0, 1],
               'output_dir': str(Path(tmp.name) / 'out'), 'progress_path': str(self.folder / 'progress.json'),
               'updated_at': stamp(), 'error': None, **fields}
        self.path.write_text(json.dumps(job))
        patcher = mock.patch.object(jobs, 'find_job', return_value=self.path)
        patcher.start(); test.addCleanup(patcher.stop)

    def bytes(self):
        return self.path.read_bytes()


class ReadersNeverWriteTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(jobs.os, 'waitpid', side_effect=ChildProcessError)
        patcher.start(); self.addCleanup(patcher.stop)

    def assert_read_only(self, f, expect_status):
        before = f.bytes()
        status = jobs.job_status('p', 'job_a')
        self.assertEqual(f.bytes(), before)             # the file is the owner's to change
        self.assertEqual(status['status'], expect_status)
        return status

    def test_a_dead_worker_reads_as_interrupted_with_its_evidence(self):
        f = JobFile(self, status='running', pid=4242, heartbeat_unix=1.0)
        with mock.patch.object(jobs.os, 'kill', side_effect=ProcessLookupError):
            status = self.assert_read_only(f, 'interrupted')
        self.assertIn('not running on this machine', status['evidence'])

    def test_a_queued_job_nobody_claimed(self):
        f = JobFile(self, updated_at=stamp(jobs.NEVER_STARTED_S + 5))
        self.assert_read_only(f, 'interrupted')

    def test_a_job_waiting_for_the_broker_stays_dispatchable(self):
        f = JobFile(self, status='awaiting_dispatch', updated_at=stamp(3600))
        status = self.assert_read_only(f, 'awaiting_dispatch')   # was 'interrupted' after 600 s: never dispatched after that
        self.assertIn('not dispatched since', status['evidence'])

    def test_a_pid_of_another_machine_is_not_judged_here(self):
        f = JobFile(self, status='running', pid=4242, owner_machine='elsewhere', heartbeat_unix=1.0)
        with mock.patch.object(jobs.os, 'kill', side_effect=ProcessLookupError) as kill:
            self.assert_read_only(f, 'running')
        kill.assert_not_called()

    def test_this_machines_own_pid_is_judged(self):
        f = JobFile(self, status='running', pid=4242, owner_machine=jobs.machine_id(), heartbeat_unix=1.0)
        with mock.patch.object(jobs.os, 'kill', side_effect=ProcessLookupError):
            self.assert_read_only(f, 'interrupted')


class UpdateJobTest(unittest.TestCase):
    def test_a_finished_job_stays_finished_unless_reopened(self):
        f = JobFile(self, status='complete')
        self.assertEqual(jobs.update_job(f.path, {'status': 'running', 'note': 1})['status'], 'complete')
        self.assertEqual(json.loads(f.bytes())['note'], 1)       # other fields still merge
        self.assertEqual(jobs.update_job(f.path, {'status': 'queued'}, reopen=True)['status'], 'queued')

    def test_two_writers_lose_nothing(self):
        f = JobFile(self)

        def write(key):
            for i in range(60):
                jobs.update_job(f.path, lambda job: {**job, key: i})
        threads = [threading.Thread(target=write, args=(k,)) for k in ('beat', 'progress')]
        [t.start() for t in threads]; [t.join() for t in threads]
        job = json.loads(f.bytes())
        self.assertEqual((job['beat'], job['progress']), (59, 59))

    def test_create_never_overwrites(self):
        f = JobFile(self)
        with self.assertRaises(jobs.StudioError):
            jobs.create_job(f.path, {'job_id': 'job_a'})

    def test_one_machine_id_per_data_root(self):
        self.assertEqual(jobs.machine_id(), jobs.machine_id())


if __name__ == '__main__':
    unittest.main()


class OneWayToChangeAJobTest(unittest.TestCase):
    """Machine check: a job file (runs/<run>/jobs/<job>/job.json, held in variables named job_path by convention) is
    written only by jobs.create_job / jobs.update_job. The one exception is render_worker.py: it is part of every render
    fingerprint (jobs.RENDER_CODE), so changing it re-renders everything; with readers no longer writing, nothing else
    writes a local render job while its worker runs (cancel waits for the worker first)."""
    ALLOWED = {('studio/jobs.py', 'create_job'), ('studio/jobs.py', 'update_job')}
    EXEMPT_FILES = {'studio/render_worker.py'}

    def test_job_files_change_only_through_update_job(self):
        import ast
        root = Path(jobs.__file__).resolve().parents[1]
        offenders = []
        for file in sorted((root / 'studio').rglob('*.py')):
            rel = str(file.relative_to(root))
            if rel in self.EXEMPT_FILES:
                continue
            tree = ast.parse(file.read_text())
            for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                # a job_path made from a staging folder is a worker's request file, not a job of a run
                staged = any(isinstance(n, ast.Assign) and any(ast.unparse(t) == 'job_path' for t in n.targets)
                             and 'staging' in ast.unparse(n.value) for n in ast.walk(fn))
                if staged:
                    continue
                for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
                    name = getattr(call.func, 'id', None) or getattr(call.func, 'attr', None)
                    if name != 'write_json' or len(call.args) < 2:
                        continue
                    target, data = ast.unparse(call.args[0]), ast.unparse(call.args[1])
                    if ('job_path' in target or (target == 'path' and data == 'job')) and (rel, fn.name) not in self.ALLOWED:
                        offenders.append(f'{rel}:{call.lineno} {fn.name}: write_json({target}, {data})')
        self.assertEqual(sorted(set(offenders)), [])


class HandoverAndServerVerdictTest(unittest.TestCase):
    def test_the_server_copy_has_no_owner(self):
        from studio.remote_render import handover
        job = {'status': 'queued', 'pid': 4242, 'owner_machine': jobs.machine_id(), 'heartbeat_unix': 1.0, 'evidence': 'x',
               'output_dir': '/Users/me/p/shots/s/renders/f', 'updated_at': stamp(600)}
        out = handover(job, {'/Users/me/p': '/workspace/studio/projects/p'})
        self.assertEqual((out['status'], 'pid' in out, 'owner_machine' in out, 'heartbeat_unix' in out),
                         ('handed_over', False, False, False))
        self.assertEqual(out['output_dir'], '/workspace/studio/projects/p/shots/s/renders/f')
        self.assertIn('handed_over', jobs.ACTIVE_STATES)
        self.assertEqual(job['pid'], 4242)   # this machine's job is untouched

    def test_the_server_judges_its_own_worker(self):
        from studio.remote_render import server_verdict
        alive = {'worker': 'alive', 'pid': '77', 'now': 1000.0}
        self.assertIsNone(server_verdict({'status': 'handed_over'}, alive))
        self.assertIsNone(server_verdict({'status': 'running', 'heartbeat_unix': 990.0}, alive))
        self.assertIn('no heartbeat for', server_verdict({'status': 'running', 'heartbeat_unix': 1000.0 - jobs.HEARTBEAT_STALE_S - 5}, alive))
        self.assertIn('not running on the server', server_verdict({'status': 'handed_over'}, {'worker': 'gone', 'pid': None, 'now': 1000.0}))
        self.assertIsNone(server_verdict({'status': 'complete'}, {'worker': 'gone', 'pid': '77', 'now': 1000.0}))   # it finished


class HeldServersTest(unittest.TestCase):
    def test_a_queue_longer_than_a_servers_life_is_refused(self):
        from studio import remote_gpu, remote_render
        many = [{'job_id': f'j{i}', 'frames': list(range(300)), 'profile': 'final'} for i in range(6)]   # 6 x 300 x 5 s = 2.5 h
        with mock.patch.object(remote_render, 'queued_remote_jobs', return_value=[]), mock.patch.object(remote_gpu, 'known_price', return_value=1.0), \
                mock.patch.object(remote_gpu, 'month_spend', return_value=0.0), \
                mock.patch.object(remote_gpu, 'settings', return_value={**remote_gpu.DEFAULTS, 'monthly_cap_usd': 100.0}):
            quote = remote_render.queue_estimate(adding=many)
        self.assertTrue(quote['fits'])                 # inside the money
        self.assertFalse(quote['fits_pod_life'])       # but past the 2 h the server's watchdog allows

    def test_frames_half_way_home_join_the_next_attempt(self):
        from PIL import Image
        from studio import remote_render
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        project = Path(tmp.name) / 'p'
        (project / 'shots/s/renders/fp').mkdir(parents=True)
        job_path = project / 'runs/r/jobs/j/job.json'
        job_path.parent.mkdir(parents=True)
        incoming = job_path.parent / '.incoming/fp/frames'
        incoming.mkdir(parents=True)
        Image.new('RGB', (8, 8)).save(incoming / 'frame_000000.png')
        (incoming / 'frame_000001.png').write_bytes(b'half a png')
        job = {'project_dir': str(project), 'shot_id': 's', 'output_dir': str(project / 'shots/s/renders/fp'), 'frames': [0, 1, 2],
               'render_settings': {'width': 8, 'height': 8}}
        self.assertEqual(remote_render._reuse_incoming(job_path, job), 1)   # the whole frame only
        self.assertTrue((project / 'shots/s/renders/fp/frames/frame_000000.png').is_file())


if __name__ == '__main__':
    unittest.main()
