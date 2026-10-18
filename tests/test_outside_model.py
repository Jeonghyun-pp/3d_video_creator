"""Where the studio cannot start a model (an agent's sandbox), it asks: a request a fresh agent answers (studio ask
answer), checked and taken when the command runs again (studio/outside_model.py)."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image, PngImagePlugin

from studio import outside_model
from studio.common import StudioError, read_json
from studio.screen_grammar import codex_json

SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['answers'],
          'properties': {'answers': {'type': 'array', 'items': {'type': 'object', 'required': ['id', 'present'],
                                                                'properties': {'id': {'type': 'string'}, 'present': {'enum': ['yes', 'no']}}}}}}
YES = {'answers': [{'id': 'box', 'present': 'yes'}]}


def asked(fn):
    try:
        fn()
    except StudioError as exc:
        if exc.code == 'OUTSIDE_MODEL_ASKED':
            return [Path(p) for p in exc.affected_ids]
        raise
    raise AssertionError('nothing was asked')


def save(path, colour, stamp=None):
    info = PngImagePlugin.PngInfo()
    if stamp:
        info.add_text('Date', stamp)   # what Blender writes into every render
    Image.new('RGB', (40, 60), colour).save(path, pnginfo=info)


class AskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'project.json').write_text('{}')
        (self.root / 'concepts').mkdir()
        self.image = self.root / 'concepts' / 'pic.png'
        save(self.image, (200, 120, 90), '2026/10/18 10:00:00')
        self.target = self.root / 'concepts' / 'read.json'
        self.env = mock.patch.dict(os.environ, {'STUDIO_ASK': '1'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_where_no_model_starts_the_studio_asks(self):
        clean = {k: v for k, v in os.environ.items() if k not in ('STUDIO_ASK', 'CODEX_SANDBOX', 'CODEX_SANDBOX_NETWORK_DISABLED', 'STUDIO_BROKERED')}
        for extra, want in (({}, False), ({'CODEX_SANDBOX': 'seatbelt'}, True),   # a sandbox with the network still cannot start it
                            ({'CODEX_SANDBOX_NETWORK_DISABLED': '1'}, True), ({'STUDIO_BROKERED': '1'}, True),
                            ({'CODEX_SANDBOX': 'seatbelt', 'STUDIO_ASK': '0'}, False)):
            with mock.patch.dict(os.environ, {**clean, **extra}, clear=True):
                self.assertEqual(outside_model.must_ask(), want, extra)

    def test_a_reading_is_asked_answered_and_taken(self):
        with mock.patch('subprocess.run') as spawn:
            [request] = asked(lambda: codex_json([self.image], 'is the box there?', SCHEMA, self.target))
            spawn.assert_not_called()   # no model started here
        self.assertEqual(request.parent.parent, (self.root / 'asks').resolve())   # beside no target
        data = read_json(request)
        self.assertEqual((data['kind'], data['prompt'], data['schema']), ('read', 'is the box there?', SCHEMA))
        for bad in (({'answers': [{'id': 'box', 'present': 'maybe'}]}, 'gpt-6-astra', 'a fresh subagent'),   # not the schema
                    (YES, 'GPT 6!', 'a fresh subagent'), (YES, 'gpt-6-astra', '')):                     # who answered, said
            with self.assertRaises(StudioError):
                outside_model.answer(request, *bad)
        out = outside_model.answer(request, YES, 'gpt-6-astra', 'a fresh subagent')
        self.assertFalse(request.is_file())
        self.assertEqual(out['warnings'], [])
        self.target.unlink()   # a cache cleared since (screen_grammar._fresh): the answer is kept where requests live
        save(self.image, (200, 120, 90), '2026/10/18 11:30:00')   # the pass rendered again: new stamp, same picture
        with mock.patch('subprocess.run') as spawn:
            got = codex_json([self.image], 'is the box there?', SCHEMA, self.target)
            spawn.assert_not_called()
        self.assertEqual((got['answer'], got['reader']['model']), (YES, 'gpt-6-astra'))
        self.assertTrue(asked(lambda: codex_json([self.image], 'is the lid open?', SCHEMA, self.target)))   # another question
        # forced: the old answer does not count; the answer to the forced request does, once
        [forced] = asked(lambda: codex_json([self.image], 'is the box there?', SCHEMA, self.target, force=True))
        outside_model.answer(forced, {'answers': [{'id': 'box', 'present': 'no'}]}, 'gpt-6-astra', 'a fresh subagent')
        got = codex_json([self.image], 'is the box there?', SCHEMA, self.target, force=True)
        self.assertEqual(got['answer']['answers'][0]['present'], 'no')
        self.assertTrue(asked(lambda: codex_json([self.image], 'is the box there?', SCHEMA, self.target, force=True)))   # forced again

    def test_a_picture_changed_since_it_was_asked_about_is_not_answered(self):
        [request] = asked(lambda: codex_json([self.image], 'is the box there?', SCHEMA, self.target))
        save(self.image, (10, 10, 10))
        self.assertTrue(outside_model.pending(self.root)[0]['stale'])
        with self.assertRaises(StudioError):
            outside_model.answer(request, {'answers': []}, 'gpt-6-astra', 'a fresh subagent')

    def test_an_answer_goes_only_where_its_request_stands_and_never_over_the_studios_records(self):
        [request] = asked(lambda: codex_json([self.image], 'is the box there?', SCHEMA, self.target))
        data = read_json(request)
        for elsewhere in (self.root / 'concepts' / 'other.json', self.root / 'project.json'):
            request.write_text(json.dumps({**data, 'target': str(elsewhere)}))   # a request edited to point elsewhere
            with self.assertRaises(StudioError):
                outside_model.answer(request, YES, 'gpt-6-astra', 'a fresh subagent')
        from studio.common import REPO
        code = REPO / 'studio' / 'common.py'   # the studio's own code, asked about by a mistaken request: never written
        [own] = asked(lambda: outside_model.ask('read', code.with_suffix('.json'), [self.image], 'q', SCHEMA))
        try:
            with self.assertRaises(StudioError):
                outside_model.answer(own, YES, 'gpt-6-astra', 'a fresh subagent')
        finally:
            import shutil
            shutil.rmtree(own.parent)
        [png] = asked(lambda: outside_model.ask('read', self.root / 'concepts' / 'x.png', [self.image], 'q', SCHEMA))
        with self.assertRaises(StudioError):   # a reading is written only as .json
            outside_model.answer(png, YES, 'gpt-6-astra', 'a fresh subagent')
        for record in (self.root / 'project.json', self.root / 'decisions' / 'world.json'):
            (record.parent).mkdir(exist_ok=True)
            [own] = asked(lambda: outside_model.ask('read', record, [self.image], 'q', SCHEMA))
            with self.assertRaises(StudioError):
                outside_model.answer(own, YES, 'gpt-6-astra', 'a fresh subagent')

    def test_a_painting_is_asked_for_and_handed_in(self):
        from studio.picture3d import paint_into
        target = self.root / 'concepts' / 'paint_1.png'
        runner = mock.Mock()
        [request] = asked(lambda: paint_into(target, [self.image], 'paint the clay', runner))
        runner.assert_not_called()
        fake = self.root / 'not.png'
        fake.write_text('no picture')
        with self.assertRaises(StudioError):   # a painting is an image
            outside_model.answer(request, str(fake), 'gpt-6-astra', 'a fresh subagent with the image tool')
        painted = self.root / 'made.png'
        save(painted, (30, 160, 90))
        outside_model.answer(request, str(painted), 'gpt-6-astra', 'a fresh subagent with the image tool')
        made, how, _tail = paint_into(target, [self.image], 'paint the clay', runner)
        runner.assert_not_called()
        self.assertEqual(how, 'answered')
        self.assertEqual(Image.open(target).getpixel((5, 5)), (30, 160, 90))
        Path(made).unlink()   # the kept painting gone: asked again, not a crash
        self.assertTrue(asked(lambda: paint_into(target, [self.image], 'paint the clay', runner)))

    def test_a_target_outside_every_project_still_reads_where_a_model_starts(self):
        with tempfile.TemporaryDirectory() as bare, mock.patch.dict(os.environ, {'STUDIO_ASK': '0'}):
            self.assertIsNone(outside_model.answered('read', Path(bare) / 'r.json', [self.image], 'q', SCHEMA))

    def test_a_loop_asks_all_its_questions_in_one_round(self):
        images = []
        for i in range(3):
            images.append(self.root / 'concepts' / f'f{i}.png')
            save(images[-1], (i * 40, 90, 90))
        requests = asked(lambda: outside_model.gather(images, lambda p: codex_json([p], 'box?', SCHEMA, p.with_suffix('.json'))))
        self.assertEqual(len(requests), 3)
        self.assertEqual(len(outside_model.pending(self.root)), 3)

    def test_readings_by_different_models_are_warned(self):
        (self.root / 'concepts' / 'old.json').write_text(json.dumps({'strips': ['a.png'], 'answer': {}, 'usage_events': []}))   # Codex's
        [request] = asked(lambda: codex_json([self.image], 'is the box there?', SCHEMA, self.target))
        same = outside_model.answer(request, {'answers': []}, 'gpt-6-astra', 'a fresh subagent')
        self.assertEqual(same['warnings'], [])   # the studio's own reader's model: one model
        [request] = asked(lambda: codex_json([self.image], 'is the lid open?', SCHEMA, self.root / 'concepts' / 'lid.json'))
        out = outside_model.answer(request, {'answers': []}, 'claude-opus-5-5', 'a fresh subagent')
        self.assertTrue(out['warnings'][0].startswith('READER_MIXED'), out)


if __name__ == '__main__':
    unittest.main()
