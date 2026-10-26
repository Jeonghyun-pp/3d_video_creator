"""Where a command's time goes (studio/timings.py): every studio command on a project leaves one row in its
runs/timings.jsonl, the Blender processes and model calls it made leave rows nested under it, and `studio timings`
sums them. Writing a row never fails the command it measures."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from studio import __main__ as cli
from studio import timings
from studio.common import StudioError
from studio.project import init_project
from tests.decisions_fixture import settle


def run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(argv)
    return code, json.loads(out.getvalue())


class TimingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(init_project('tm', {'request': 'timings', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, self.tmp.name)['project_path'])
        settle(self.project)
        self.argv = ['route', 'check', '--project', str(self.project), '--shot', 's', '--operation', 'build']

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self):
        return timings.rows(self.project)

    def test_a_command_leaves_one_row(self):
        code, _ = run(self.argv)
        self.assertEqual(code, 0)
        [row] = self.rows()
        self.assertEqual((row['kind'], row['command'], row['shots'], row['ok']), ('command', 'route.check', ['s'], True))
        self.assertEqual(row['args']['operation'], 'build')
        self.assertNotIn('handler', row['args'])
        self.assertGreaterEqual(row['seconds'], 0)
        self.assertTrue(row['at'] and row['id'])

    def test_a_failed_command_is_a_row_too(self):
        with patch('studio.routing.check', side_effect=StudioError('ANY', 'x')):
            code, _ = run(self.argv)
        self.assertEqual(code, 2)
        self.assertEqual([r['ok'] for r in self.rows()], [False])

    def test_blender_and_model_calls_nest_under_their_command(self):
        def check(*a, **k):
            from studio.common import run_command
            with patch('studio.common._is_blender', return_value=True), patch('studio.common.os_sandbox', side_effect=lambda c: c):
                run_command(['true', 'scene.blend', '--python', 'build_scene.py'])
            from studio import screen_grammar

            def codex(cmd, **kw):   # the model answers: its -o file is written
                Path(cmd[cmd.index('-o') + 1]).write_text('{"a": 1}')
                return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
            with patch('studio.outside_model.must_ask', return_value=False), patch('studio.screen_grammar.subprocess.run', side_effect=codex):
                screen_grammar.codex_json([], 'q', {'type': 'object'}, self.project / 'review' / 'judge_x.json')
            return {}
        with patch('studio.routing.check', side_effect=check):
            run(self.argv)
        rows = self.rows()
        command = next(r for r in rows if r['kind'] == 'command')
        blender = next(r for r in rows if r['kind'] == 'blender')
        model = next(r for r in rows if r['kind'] == 'model')
        self.assertEqual((blender['parent'], model['parent']), (command['id'], command['id']))
        self.assertIn('build_scene.py', blender['command'])
        self.assertIn('judge_x.json', model['command'])
        self.assertEqual(blender['shots'], ['s'])

    def test_a_render_worker_row_nests_under_the_command_that_started_it(self):
        from studio.jobs import record_render_time
        run_dir = self.project / 'runs' / 'r1'
        (run_dir / 'jobs' / 'j1').mkdir(parents=True)
        (run_dir / 'run.json').write_text('{}')
        job = {'job_id': 'j1', 'attempt': 1, 'project_dir': str(self.project), 'shot_id': 's', 'profile': 'layout', 'version': 'v0001'}
        with patch.dict(os.environ, {timings.ENV: json.dumps({'project': str(self.project), 'id': 'abc'})}):
            record_render_time(run_dir / 'jobs' / 'j1' / 'job.json', job, 4.5)
            record_render_time(run_dir / 'jobs' / 'j1' / 'job.json', job, 4.5)   # the same attempt again: no second row
        [row] = self.rows()
        self.assertEqual((row['kind'], row['parent'], row['shots'], row['seconds']), ('blender', 'abc', ['s'], 4.5))
        self.assertIn('render layout', row['command'])

    def test_writing_a_row_never_fails_the_command(self):
        (self.project / 'runs').mkdir(exist_ok=True)
        (self.project / timings.FILE).mkdir()   # a folder where the file goes: the row cannot be written
        code, body = run(self.argv)
        self.assertEqual((code, body['ok']), (0, True))

    def test_nothing_outside_a_command_is_written_or_raised(self):
        timings.record('blender', 'x', 1.0)   # no command, no project: nowhere to write
        self.assertEqual(self.rows(), [])

    def test_summary_by_command_by_kind_and_slowest(self):
        file = self.project / timings.FILE
        file.parent.mkdir(exist_ok=True)
        rows = [{'id': 'a', 'kind': 'command', 'command': 'storyboard.takes', 'shots': ['s'], 'seconds': 100.0, 'ok': True, 'at': '1'},
                {'id': 'b', 'parent': 'a', 'kind': 'blender', 'command': 'blender build', 'shots': ['s'], 'seconds': 60.0, 'ok': True, 'at': '1'},
                {'id': 'c', 'parent': 'a', 'kind': 'model', 'command': 'model judge', 'shots': ['s'], 'seconds': 30.0, 'ok': True, 'at': '1'},
                {'id': 'd', 'kind': 'command', 'command': 'reel.clay', 'shots': [], 'seconds': 20.0, 'ok': True, 'at': '2'},
                {'id': 'e', 'kind': 'command', 'command': 'storyboard.takes', 'shots': ['t'], 'seconds': 50.0, 'ok': False, 'at': '3'}]
        file.write_text(''.join(json.dumps(r) + '\n' for r in rows) + 'not json\n')
        got = timings.summary(self.project, slowest=2)
        self.assertEqual(got['total_seconds'], 170.0)
        takes = got['by_command']['storyboard.takes']
        self.assertEqual((takes['count'], takes['seconds'], takes['failed']), (2, 150.0, 1))
        self.assertEqual(got['by_kind'], {'blender': 60.0, 'model': 30.0, 'other': 80.0})
        self.assertEqual([r['id'] for r in got['slowest']], ['a', 'b'])
        code, body = run(['timings', '--project', str(self.project)])
        self.assertEqual((code, body['by_kind']['blender']), (0, 60.0))


if __name__ == '__main__':
    unittest.main()
