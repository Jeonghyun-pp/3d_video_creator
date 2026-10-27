"""What the located parts of a generated clip do over time (studio2/video_parts.py), on made boxes and made frames whose
answer is known: a held thing that stays with its holder, one that drifts away below it, one that vanishes, one that
ends outside the holder it should be in; a part that keeps its look, one that blurs or merges; a count that falls."""
from pathlib import Path
import random
import tempfile
import unittest

KINDS = {'carried': {'keeps': ['apart', 'drift', 'gone']}, 'inside': {'keeps': ['outside', 'drift', 'gone']},
         'rides': {'keeps': ['apart', 'gone']}}


def frames_of(boxes, shown=None):
    """[{t, parts: {id: {box, shown}}}] from per-part box lists over evenly spaced times."""
    n = len(next(iter(boxes.values())))
    shown = shown or {}
    return [{'t': i / (n - 1), 'parts': {p: {'box': b[i], 'shown': (shown.get(p) or [True] * n)[i]} for p, b in boxes.items()}}
            for i in range(n)]


def hold(held, holder, kind, at=None):
    row = {'says': f'The {held} stays with the {holder}.', 'held': held, 'holder': holder, 'kind': kind}
    if at:
        row['at'] = at
    return row


class ContactTest(unittest.TestCase):
    def test_a_held_thing_that_moves_with_its_holder_keeps_its_contact(self):
        from studio2 import video_parts as vp
        hoist = [[0.3, 0.1 - 0.05 * i, 0.7, 0.3 - 0.05 * i] for i in range(5)]
        box = [[0.35, 0.3 - 0.05 * i, 0.65, 0.5 - 0.05 * i] for i in range(5)]   # hangs right under it, rises with it
        got = vp.contact(frames_of({'hoist': hoist, 'box': box}), [hold('box', 'hoist', 'carried')], KINDS, aspect=16 / 9)
        self.assertLess(got['apart'], 0.02)
        self.assertLess(got['drift'], 0.02)
        self.assertEqual(got['gone'], 0)
        self.assertIsNone(got['outside'])          # a carried thing hangs from its holder: not inside it

    def test_a_held_thing_that_drifts_away_below_its_holder_is_apart_and_drifts(self):
        from studio2 import video_parts as vp
        hoist = [[0.3, 0.1, 0.7, 0.3]] * 5
        box = [[0.35, 0.3 + 0.08 * i, 0.65, 0.5 + 0.08 * i] for i in range(5)]
        got = vp.contact(frames_of({'hoist': hoist, 'box': box}), [hold('box', 'hoist', 'carried')], KINDS, aspect=16 / 9)
        self.assertGreater(got['apart'], 0.5)
        self.assertGreater(got['drift'], 0.5)

    def test_a_rider_slides_along_its_holder_without_counting_as_drift(self):
        from studio2 import video_parts as vp
        rail = [[0.0, 0.1, 1.0, 0.2]] * 5
        car = [[0.1 + 0.15 * i, 0.15, 0.25 + 0.15 * i, 0.3] for i in range(5)]
        got = vp.contact(frames_of({'rail': rail, 'car': car}), [hold('car', 'rail', 'rides')], KINDS, aspect=16 / 9)
        self.assertLess(got['apart'], 0.02)
        self.assertIsNone(got['drift'])             # a ride moves along what it rides: its kind keeps no offset

    def test_one_of_the_pair_vanishing_while_the_other_is_shown_is_gone(self):
        from studio2 import video_parts as vp
        hoist = [[0.3, 0.1, 0.7, 0.3]] * 5
        box = [[0.35, 0.3, 0.65, 0.5]] * 5
        got = vp.contact(frames_of({'hoist': hoist, 'box': box}, {'box': [True, True, True, False, False]}),
                         [hold('box', 'hoist', 'carried')], KINDS, aspect=16 / 9)
        self.assertAlmostEqual(got['gone'], 0.4)
        both_out = vp.contact(frames_of({'hoist': hoist, 'box': box}, {'box': [True, True, True, False, False],
                                                                        'hoist': [True, True, True, False, False]}),
                              [hold('box', 'hoist', 'carried')], KINDS, aspect=16 / 9)
        self.assertEqual(both_out['gone'], 0)      # both left the frame together: nothing is lost

    def test_a_thing_that_should_end_inside_its_holder_and_hangs_below_it_is_outside(self):
        """fab c01 (2026-10-27): the box was to rise into the hoist and be gripped inside it; it hung below it. The hold
        starts when the change ends (its `at`), so the box's rise from the shelf before it is not counted."""
        from studio2 import video_parts as vp
        hoist = [[0.32, 0.0, 0.71, 0.33]] * 5
        below = [[0.34, 0.45 - 0.03 * i, 0.79, 0.69 - 0.03 * i] for i in range(5)]
        into = [[0.36, 0.45 - 0.1 * i, 0.68, 0.65 - 0.1 * i] for i in range(5)]
        rule = [hold('box', 'hoist', 'inside', at=[0.7, 1.0])]
        bad = vp.contact(frames_of({'hoist': hoist, 'box': below}), rule, KINDS, aspect=16 / 9)
        good = vp.contact(frames_of({'hoist': hoist, 'box': into}), rule, KINDS, aspect=16 / 9)
        self.assertGreater(bad['outside'], 0.6)
        self.assertLess(good['outside'], 0.3)
        self.assertEqual(len(good['pairs'][0]['frames']), 2)   # only the frames from 0.7 of the clip on

    def test_a_new_kind_of_contact_is_a_row_not_code(self):
        from studio2 import video_parts as vp
        kinds = {**KINDS, 'glued': {'keeps': ['apart', 'outside', 'drift', 'gone']}}
        hoist = [[0.3, 0.1, 0.7, 0.3]] * 5
        box = [[0.35, 0.3 + 0.08 * i, 0.65, 0.5 + 0.08 * i] for i in range(5)]
        got = vp.contact(frames_of({'hoist': hoist, 'box': box}), [hold('box', 'hoist', 'glued')], kinds, aspect=16 / 9)
        self.assertGreater(got['outside'], 0.9)
        self.assertGreater(got['apart'], 0.5)

    def test_holds_that_name_no_pair_are_not_measured(self):
        from studio2 import video_parts as vp
        got = vp.contact(frames_of({'a': [[0, 0, 1, 1]] * 3}), ['The jar stays upright.'], KINDS, aspect=1)
        self.assertEqual(got['pairs'], [])
        self.assertIsNone(got['apart'])


def textured(w=540, h=960, seed=5):
    from PIL import Image, ImageDraw
    rnd = random.Random(seed)
    im = Image.new('RGB', (w, h), (120, 120, 120))
    d = ImageDraw.Draw(im)
    for _ in range(700):
        x, y, s = rnd.randrange(w), rnd.randrange(h), rnd.randrange(6, 40)
        d.rectangle([x, y, x + s, y + s], fill=(rnd.randrange(256),) * 3)
    for k in range(20):                                       # a stack of thin bright plates in the middle (the "wafers")
        y = 380 + 10 * k
        d.rectangle([180, y, 360, y + 3], fill=(250, 250, 250))
    return im


class ShapeTest(unittest.TestCase):
    def setUp(self):
        from PIL import ImageFilter
        self.base = textured()
        self.box = [180 / 540, 370 / 960, 360 / 540, 590 / 960]
        x0, y0, x1, y1 = 180, 370, 360, 590
        self.merged = []
        for i in range(5):                                    # the plates smear together over the clip
            im = self.base.copy()
            if i:
                im.paste(im.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(2 * i)), (x0, y0))
            self.merged.append(im)

    def located(self, n=5):
        return [{'t': i / (n - 1), 'parts': {'stack': {'box': self.box, 'shown': True}}} for i in range(n)]

    def test_a_part_that_keeps_its_look_is_like_its_first_frame(self):
        from studio2 import video_parts as vp
        got = vp.shape([self.base] * 5, self.located())
        self.assertGreater(got['likeness'], 0.97)
        self.assertGreater(got['detail'], 0.97)

    def test_a_part_that_merges_loses_its_detail_and_its_likeness(self):
        from studio2 import video_parts as vp
        got = vp.shape(self.merged, self.located())
        self.assertLess(got['detail'], 0.6)
        self.assertLess(got['likeness'], 0.9)
        self.assertEqual(got['parts']['stack']['worst_at'], 1.0)

    def test_the_count_is_the_median_of_three_asks_against_the_first_tile(self):
        """A narrow reader question (how many separate items in each tile of a part), asked three ways: the median per
        tile against the first tile's count; a fall from 20 plates to 1 solid thing is a count ratio of 0.05."""
        from studio.common import read_json, write_json
        from studio2 import video_parts as vp
        asked = []
        answers = [[20, 20, 12, 2, 1], [18, 19, 10, 1, 1], [22, 25, 30, 1, 2]]

        def reader(images, prompt, schema, target):
            asked.append(prompt)
            write_json(target, {'answer': {'parts': [{'id': 'stack', 'counts': answers[len(asked) - 1]}]}})
        with tempfile.TemporaryDirectory() as tmp:
            crops = vp.crops(self.merged, self.located())
            strips = vp.strips(crops, Path(tmp))
            got = vp.count_read(strips, {'stack': 'the stack of thin plates'}, Path(tmp), reader)
            self.assertEqual(len(asked), len(vp.COUNT_ASKS))
            self.assertEqual(len(set(asked)), 3)                       # three wordings
            self.assertEqual(got['parts']['stack'], [20, 20, 12, 1, 1])
            self.assertAlmostEqual(got['count'], 0.05)
            self.assertTrue(Path(strips['stack']).is_file())
            self.assertEqual(read_json(Path(tmp) / 'count' / 'read_0.json')['answer']['parts'][0]['counts'], answers[0])


if __name__ == '__main__':
    unittest.main()
