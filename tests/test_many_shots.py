"""Takes and judges of several shots in one command (storyboard takes|judge --shots ... | --reopened): each shot's work
is its own, run at the same time (studio/pool.py), said shot by shot in the order asked; one shot failing never stops
the others and the command says it failed."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from studio import __main__ as cli
from studio import pool, storyboard, timings
from studio.common import StudioError, write_json
from studio.project import init_project
from tests.decisions_fixture import settle


def run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(argv)
    return code, json.loads(out.getvalue())


class ManyShotsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(init_project('ms', {'request': 'x', 'shots': [{'shot_id': s, 'frame_count': 30} for s in ('a', 'b', 'c')]},
                                   self.tmp.name)['project_path'])
        settle(self.p)
        self.env = patch.dict(os.environ, {pool.ENV: '3'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_takes_of_several_shots_run_at_once_and_answer_in_order(self):
        running, most, guard = [0], [0], threading.Lock()

        def takes(project, shot_id, recipes=None, user_words=None):
            with guard:
                running[0] += 1; most[0] = max(most[0], running[0])
            time.sleep(0.1 if shot_id == 'a' else 0.02)
            timings.record('blender', 'blender build_scene.py', 0.5)   # a Blender process inside it
            with guard:
                running[0] -= 1
            return {'shot_id': shot_id, 'rev': 'v01', 'warnings': [f'w {shot_id}'], 'artifacts': [f'{shot_id}.md'],
                    'next': f'storyboard judge --shot {shot_id} --sheet v01'}
        with patch('studio.storyboard.takes', side_effect=takes):
            code, body = run(['storyboard', 'takes', '--project', str(self.p), '--shots', 'c', 'a'])
        self.assertEqual(code, 0)
        self.assertEqual(body['shots'], ['c', 'a'])
        self.assertEqual([r['shot_id'] for r in body['results']], ['c', 'a'])
        self.assertEqual(most[0], 2)
        self.assertEqual(body['warnings'][:2], ['c: w c', 'a: w a'])
        self.assertEqual(body['artifacts'], ['c.md', 'a.md'])
        nested = sorted(r['shots'] for r in timings.rows(self.p) if r['kind'] == 'blender')
        self.assertEqual(nested, [['a'], ['c']])   # each Blender row is its own shot's

    def test_one_shot_failing_leaves_the_others_and_fails_the_command(self):
        def takes(project, shot_id, recipes=None, user_words=None):
            if shot_id == 'b':
                raise StudioError('TAKES_UNBUILT', 'b: nothing built', retryable=True)
            return {'shot_id': shot_id, 'rev': 'v01'}
        with patch('studio.storyboard.takes', side_effect=takes):
            code, body = run(['storyboard', 'takes', '--project', str(self.p), '--shots', 'a,b', 'c'])
        self.assertEqual(code, 2)
        self.assertEqual([r['shot_id'] for r in body['results']], ['a', 'c'])
        self.assertEqual(body['failed'], [{'shot_id': 'b', 'error': {'code': 'TAKES_UNBUILT', 'message': 'b: nothing built', 'retryable': True,
                                                                    'affected_ids': [], 'recovery': ''}}])

    def test_reopened_names_the_shots_the_reels_watcher_sent_back(self):
        for sid in ('a', 'c'):
            write_json(storyboard._envelope_path(self.p, sid), {'schema_version': 1, 'shot_id': sid, 'history': [], 'status': 'proposed',
                                                                 'reopened': {'why': 'dull', 'try': 'closer', 'at': 'x'}})
        seen = []
        with patch('studio.storyboard.judge', side_effect=lambda project, shot_id, sheet_rev=None, reader=None, force=False:
                   seen.append(shot_id) or {'shot_id': shot_id}):
            code, body = run(['storyboard', 'judge', '--project', str(self.p), '--reopened'])
        self.assertEqual((code, sorted(seen), body['shots']), (0, ['a', 'c'], ['a', 'c']))

    def test_one_shot_answers_as_before(self):
        with patch('studio.storyboard.takes', return_value={'shot_id': 'a', 'rev': 'v01'}) as made:
            code, body = run(['storyboard', 'takes', '--project', str(self.p), '--shot', 'a'])
        self.assertEqual((code, body['rev']), (0, 'v01'))
        made.assert_called_once()

    def test_a_shot_not_in_the_reel_or_none_asked_is_refused(self):
        code, body = run(['storyboard', 'takes', '--project', str(self.p), '--shots', 'a', 'zz'])
        self.assertEqual((code, body['error']['code']), (2, 'INPUT_INVALID'))
        code, body = run(['storyboard', 'takes', '--project', str(self.p), '--reopened'])   # none sent back
        self.assertEqual((code, body['error']['code']), (2, 'INPUT_INVALID'))


if __name__ == '__main__':
    unittest.main()
