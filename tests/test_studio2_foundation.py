"""v2's foundations (studio2/): its CLI runs as v1's does (one JSON result, a timings row), a v2 project is a v1 project
(project init reused; a story copied from another project keeps its settled layers), and pictures come from the image
tool through one runner that keeps what made each one and stops on a quota or a pay prompt."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from studio import decisions, timings
from studio.common import StudioError, file_hash, read_json, stable_hash
from studio.project import init_project
from tests.decisions_fixture import settle


def run(argv):
    from studio2 import __main__ as cli
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(argv)
    return code, json.loads(out.getvalue())


class FakeCodex:
    """Stands in for `codex exec`: each call answers the next scripted reply - 'image' (a picture is made and named in
    the --json events) or a failure tail - and remembers the command it was given."""

    def __init__(self, tmp, replies):
        self.tmp, self.replies, self.calls = Path(tmp), list(replies), []

    def __call__(self, cmd, cwd, timeout):
        self.calls.append(cmd)
        reply = self.replies.pop(0)
        if reply == 'image':
            made = self.tmp / f'made_{len(self.calls)}.png'
            Image.new('RGB', (90, 160), (40 * len(self.calls) % 255, 90, 120)).save(made)
            return {'returncode': 0, 'stdout': json.dumps({'type': 'item', 'path': str(made)}) + '\n', 'stderr': '', 'timed_out': False}
        return {'returncode': 1, 'stdout': '', 'stderr': reply, 'timed_out': False}


class ImageRunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = Path(init_project('v2img', {'request': 'v2 images', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, self.root)['project_path'])
        self.ref = self.root / 'ref.png'
        Image.new('RGB', (90, 160), (200, 10, 10)).save(self.ref)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_picture_is_kept_with_what_made_it(self):
        from studio2 import images
        fake = FakeCodex(self.root, ['image'])
        target = self.project / 'shots' / 's' / 'frames' / 'k1' / 'first.png'
        got = images.make(target, 'a cutaway of one bay', [self.ref], runner=fake)
        self.assertTrue(target.is_file())
        record = read_json(target.with_suffix('.json'))
        self.assertEqual(record['prompt'], 'a cutaway of one bay')
        self.assertEqual(record['prompt_sha256'], stable_hash('a cutaway of one bay'))
        self.assertEqual(record['inputs'], [{'path': str(self.ref), 'sha256': file_hash(self.ref)}])
        self.assertEqual(record['image_sha256'], file_hash(target))
        self.assertEqual(len(record['attempts']), 1)
        self.assertIn('seconds', record['attempts'][0])
        self.assertFalse(got['cached'])
        cmd = fake.calls[0]   # the prompt goes before the -i inputs: -i takes every argument after it as an image
        self.assertLess(next(i for i, a in enumerate(cmd) if 'a cutaway of one bay' in a), cmd.index('-i'))

    def test_the_same_request_is_not_made_twice(self):
        from studio2 import images
        target = self.project / 'f' / 'first.png'
        images.make(target, 'p', [self.ref], runner=FakeCodex(self.root, ['image']))
        again = images.make(target, 'p', [self.ref], runner=FakeCodex(self.root, []))
        self.assertTrue(again['cached'])
        Image.new('RGB', (90, 160), (0, 200, 10)).save(self.ref)   # an input changed: made again
        fake = FakeCodex(self.root, ['image'])
        self.assertFalse(images.make(target, 'p', [self.ref], runner=fake)['cached'])
        self.assertEqual(len(fake.calls), 1)

    def test_a_failure_is_tried_again(self):
        from studio2 import images
        fake = FakeCodex(self.root, ['stream disconnected before completion', 'image'])
        got = images.make(self.project / 'f' / 'a.png', 'p', [], runner=fake)
        self.assertEqual([a['ok'] for a in got['attempts']], [False, True])

    def test_failures_past_the_retries_stop(self):
        from studio2 import images
        fake = FakeCodex(self.root, ['boom'] * 5)
        with self.assertRaises(StudioError) as caught:
            images.make(self.project / 'f' / 'a.png', 'p', [], runner=fake, retries=2)
        self.assertEqual(caught.exception.code, 'IMAGE_FAILED')
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(len(read_json(self.project / 'f' / 'a.json')['attempts']), 3)   # the failed tries are kept

    def test_a_quota_or_a_pay_prompt_stops_at_once(self):
        from studio2 import images
        for tail, code in (("ERROR: You've hit your usage limit. Try again in 3 hours.", 'IMAGE_QUOTA'),
                           ('To continue, upgrade your plan or purchase more credits.', 'IMAGE_PAYMENT')):
            fake = FakeCodex(self.root, [tail, 'image'])
            with self.assertRaises(StudioError) as caught:
                images.make(self.project / 'f' / f'{code}.png', 'p', [], runner=fake)
            self.assertEqual(caught.exception.code, code)
            self.assertFalse(caught.exception.retryable)
            self.assertEqual(len(fake.calls), 1)   # never tried again: a quota or a payment is the user's

    def test_repeated_refusals_stop(self):
        from studio2 import images
        fake = FakeCodex(self.root, ["I can't help with creating that image."] * 4)
        with self.assertRaises(StudioError) as caught:
            images.make(self.project / 'f' / 'r.png', 'p', [], runner=fake)
        self.assertEqual(caught.exception.code, 'IMAGE_REFUSED')
        self.assertLessEqual(len(fake.calls), 3)

    def test_a_call_leaves_a_model_row_under_its_command(self):
        from studio2 import images
        row = timings.command(type('A', (), {'project': str(self.project)})(), 'v2.frames.make')
        images.make(self.project / 'f' / 't.png', 'p', [], runner=FakeCodex(self.root, ['image']))
        row.end(True)
        kinds = [(r['kind'], r.get('parent')) for r in timings.rows(self.project)]
        self.assertIn(('model', row.row['id']), kinds)


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_is_v1_init(self):
        brief = self.root / 'brief.json'
        brief.write_text(json.dumps({'request': '반도체 공장'}), encoding='utf-8')
        code, out = run(['project', 'init', '--id', 'v2p', '--brief', str(brief), '--root', str(self.root)])
        self.assertEqual(code, 0, out)
        project = read_json(self.root / 'v2p' / 'project.json')
        self.assertEqual(project['brief']['request'], '반도체 공장')
        self.assertEqual(project['pattern_library'], 'screen_grammar_cutaway_v1')   # the grammar's reference

    def test_a_story_is_copied_with_its_settled_layers(self):
        source = Path(init_project('src', {'request': 'src'}, self.root)['project_path'])
        bodies = {'script': {'lines': [{'line_id': 'l01', 'text': '첫 줄입니다.', 'illustrative': 'a test line'}]}}
        settle(source, ['script'], bodies)
        data = read_json(source / 'project.json')
        data['delegation'] = {'user_words': '알아서 해 주세요', 'scope': [], 'recorded_at': '2026-10-26T00:00:00+00:00'}
        (source / 'project.json').write_text(json.dumps(data), encoding='utf-8')
        code, out = run(['project', 'copy-story', '--id', 'dst', '--from', str(source), '--root', str(self.root)])
        self.assertEqual(code, 0, out)
        dst = self.root / 'dst'
        for layer in ('brief', 'facts', 'story', 'script'):
            self.assertEqual(decisions.state(dst, layer)['state'], 'decided', layer)
        self.assertEqual(read_json(dst / 'project.json')['delegation']['user_words'], '알아서 해 주세요')   # decided under it
        self.assertEqual(read_json(dst / 'decisions' / 'copied.json')['from'], str(source))
        self.assertIsNone(decisions.envelope(dst, 'shotlist'))   # what is shown is v2's to plan anew
        code, out = run(['project', 'copy-story', '--id', 'dst', '--from', str(source), '--root', str(self.root)])
        self.assertNotEqual(code, 0)   # never over an existing project

    def test_a_command_leaves_a_timings_row(self):
        project = Path(init_project('tr', {'request': 'tr'}, self.root)['project_path'])
        code, out = run(['project', 'status', '--project', str(project)])
        self.assertEqual(code, 0, out)
        self.assertEqual([r['command'] for r in timings.rows(project)], ['v2.project.status'])
        self.assertEqual(out['layers']['cuts']['state'], 'missing')


if __name__ == '__main__':
    unittest.main()
