"""How a moving thing is drawn is the cut plan's to say, and it is checked: a thing that belongs to the scene is drawn in
the picture (first and last as edits; the paid video moves it), a sprite is only for a small thing (SPRITE_MAX), a speck
is a mark. Why (2026-10-26, the dev preview): c1's wafer box (a sprite 0.12 of the frame tall) sat pasted over the
picture and c8's (0.26) floated - a picture laid over another takes neither its light, its perspective nor what stands
in front of it."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from studio import decisions
from studio.common import read_json
from tests import studio2_fixture as fx
from tests.test_studio2_foundation import FakeCodex
from tests.test_studio2_pictures import answer_all, marks_ok, place_reader

BOX = {'what': 'a closed grey-blue wafer carrier box', 'path': 'from the load port shelf up to the rail vehicle'}


def moving(drawn, **element):
    body = fx.body()
    body['cuts'][0]['change'] = {'kind': 'move', 'what': 'the wafer box is lifted up to the rail', 'at': [0.2, 0.8], 'parts': ['floor'],
                                 'element': {**BOX, 'drawn': drawn, **element}}
    return body


class DrawnTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = fx.project(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def errors(self, body):
        return decisions.lint(self.path, 'cuts', body)['errors']

    def test_a_big_sprite_is_refused_and_a_small_one_kept(self):
        from studio2.cuts import SPRITE_MAX
        errors = self.errors(moving('sprite', size=0.12))
        self.assertTrue(any(e.startswith('CUT_ELEMENT_PASTED: c1') for e in errors), errors)
        self.assertTrue(any(e.startswith('CUT_ELEMENT_PASTED: c1') for e in self.errors(moving('sprite'))))   # unsized: unknown, refused
        self.assertFalse(any(e.startswith('CUT_ELEMENT_PASTED') for e in self.errors(moving('sprite', size=SPRITE_MAX))))

    def test_a_thing_in_the_picture_needs_a_last_picture_that_differs(self):
        body = moving('picture')
        errors = self.errors(body)
        self.assertTrue(any(e.startswith('CHANGE_FRAMES_SAME: c1') for e in errors), errors)
        body['cuts'][0]['frames']['last'] = {'shows': 'the same room, the wafer box up at the rail vehicle'}
        self.assertFalse([e for e in self.errors(body) if e.startswith(('CHANGE_FRAMES_SAME', 'CUT_ELEMENT_PASTED'))])

    def test_a_thing_in_the_picture_is_painted_in_the_first_and_moved_by_an_edit(self):
        from studio2 import checks, frames
        body = moving('picture')
        body['cuts'][0]['frames']['last'] = {'shows': 'the same room, the wafer box up at the rail vehicle'}
        decisions.write(self.path, 'cuts', body, agent_note=fx.TEST_NOTE)
        cut = body['cuts'][0]
        self.assertEqual(frames.needed(cut), ['first', 'last'])
        fake = FakeCodex(self.root, ['image'])
        got = frames.make(self.path, 'c1', 'first', runner=fake)
        prompt = read_json(Path(got['made'][0]['path']).with_suffix('.json'))['prompt']
        self.assertNotIn('do not draw a closed grey-blue', prompt)
        self.assertIn('a closed grey-blue wafer carrier box is in the picture, where its path begins', prompt)
        checks.check(self.path, 'c1', 'first', reader=answer_all(), matcher=answer_all())
        frames.pick(self.path, 'c1', 'first', 1, 'a test pick: it shows what the cut needs')
        fake = FakeCodex(self.root, ['image'])
        frames.make(self.path, 'c1', 'last', runner=fake)
        edit = next(a for a in fake.calls[0] if 'Edit the attached image' in a)
        self.assertIn('the wafer box up at the rail vehicle', edit)

    def test_the_preview_crosses_from_the_first_picture_to_the_last_over_its_window(self):
        from studio2 import checks, frames, locate, motion
        body = moving('picture', label='상자')
        body['cuts'][0]['frames']['last'] = {'shows': 'the same room, the wafer box up at the rail vehicle'}
        body['cuts'][0]['cutaway']['camera'] = {'move': 'still'}
        decisions.write(self.path, 'cuts', body, agent_note=fx.TEST_NOTE)
        cut = body['cuts'][0]
        for frame, colour in (('first', (20, 20, 20)), ('last', (230, 230, 230))):
            frames.make(self.path, 'c1', frame, runner=FakeCodex(self.root, ['image']))
            Image.new('RGB', (108, 192), colour).save(frames.folder(self.path, cut) / f'{frame}_1.png')   # a flat picture to tell apart
            checks.check(self.path, 'c1', frame, reader=answer_all(), matcher=answer_all())
            frames.pick(self.path, 'c1', frame, 1, 'a test pick: it shows what the cut needs')
        locate.locate(self.path, 'c1', reader=place_reader, checker=marks_ok)
        plan = motion.plan_of(self.path, cut, locate.places_of(self.path, cut)['places'], 108, 192, 120)
        frame = motion.frame_function(plan, motion._pictures(self.path, cut, 108, 192), 108, 192)
        corner = lambda t: frame(t).getpixel((2, 190))[0]   # noqa: E731 - a corner no mark is drawn on
        self.assertLess(corner(0.5), 40)                 # before its window: the first picture
        self.assertGreater(corner(3.9), 200)             # after it: the last
        self.assertTrue(40 < corner(2.0) < 200)          # crossing in between
        self.assertIsNone(plan['element'].get('sprite'))


if __name__ == '__main__':
    unittest.main()
