"""A part the plan says is many separate things is checked to read as separate items on the picture, before it is
picked (studio2/checks.py count_separate): found from the plan, located on the try, cut out, counted three ways by a
narrow reader, the middle answer over the planned number; a floor or advice by calibration (2026-10-27, fab c03)."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image

from studio import decisions
from studio.common import StudioError, write_json
from tests import studio2_fixture as fx
from tests.test_studio2_foundation import FakeCodex
from tests.test_studio2_pictures import answer_all

WAFERS = {'id': 'wafers', 'words': 'the stack of thin round silver wafers in the box\'s slots'}


def row():
    from studio2 import checks
    return deepcopy(checks.table()['rows']['count_separate'])


def placer(images, prompt, schema, target):
    ids = schema['properties']['parts']['items']['properties']['id']['enum']
    write_json(target, {'answer': {'parts': [{'id': i, 'box': [0.2, 0.3, 0.8, 0.7], 'point': [0.5, 0.5], 'shown': True} for i in ids],
                                   'flows': [], 'element': None, 'focus': [0.5, 0.5]}})


def counter(*answers):
    """A count reader that answers the three asks in turn (each its count per part), and records each prompt."""
    asked = []

    def read(images, prompt, schema, target):
        asked.append(prompt)
        ids = schema['properties']['parts']['items']['properties']['id']['enum']
        n = answers[(len(asked) - 1) % len(answers)]
        write_json(target, {'answer': {'parts': [{'id': i, 'counts': [n]} for i in ids]}})
    read.asked = asked
    return read


class ManyPartsTest(unittest.TestCase):
    def cut(self, part, **over):
        out = deepcopy(fx.body()['cuts'][0])
        out['cutaway']['parts'] = [part]
        out.update(over)
        return out

    def test_the_planned_number_is_found_where_the_plan_says_it(self):
        from studio2 import checks
        found = lambda c: [(m['part'], m['n']) for m in checks.many_parts(c, row())]
        self.assertEqual(found(self.cut({**WAFERS, 'count': 25})), [('wafers', 25)])
        self.assertEqual(found(self.cut(WAFERS, purpose='what the box carries: 25 wafers standing in slots')), [('wafers', 25)])
        self.assertEqual(found(self.cut({'id': 'wafers', 'words': 'the 25 thin silver wafers'})), [('wafers', 25)])
        self.assertEqual(found(self.cut(WAFERS)), [('wafers', None)])                        # "stack of": many, number unknown
        self.assertEqual(found(self.cut({'id': 'tools', 'words': 'the rows of white tools'})), [('tools', None)])
        self.assertEqual(found(self.cut({'id': 'chips', 'words': 'the grid of identical chips'})), [])   # marks on one surface

    def test_a_single_thing_a_size_or_a_labels_number_is_not_many(self):
        from studio2 import checks
        self.assertEqual(checks.many_parts(self.cut({'id': 'hand', 'words': 'the gloved hand', 'count': 1}), row()), [])
        self.assertEqual(checks.many_parts(self.cut({'id': 'wafer', 'words': 'the face of the wafer'},
                                                    purpose='one 300 mm wafers face close'), row()), [])     # 300 mm is a size
        labelled = self.cut({'id': 'aisle', 'words': 'the long main aisle'})
        labelled['cutaway']['labels'] = [{'part': 'aisle', 'word': '최대 1,400단계', 'at': 0.1}]
        self.assertEqual(checks.many_parts(labelled, row()), [])   # the label counts steps, not aisles


class SeparateCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = fx.project(self.root)
        body = fx.body()
        body['cuts'][0]['cutaway']['parts'].append({'id': 'wafers', 'words': 'the 25 thin silver wafers in the box\'s slots'})
        decisions.write(self.path, 'cuts', body, agent_note=fx.TEST_NOTE)
        from studio2 import frames
        frames.make(self.path, 'c1', 'first', runner=FakeCodex(self.root, ['image']))

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, table, count):
        from studio2 import checks
        with mock.patch('studio2.checks.table', return_value=table):
            return checks.check(self.path, 'c1', reader=answer_all(), placer=placer, counter=count)['results'][0]

    def test_the_middle_of_three_counts_over_the_planned_number_is_judged_by_the_limit(self):
        from studio2 import checks
        table = checks.table()
        table['rows']['count_separate'].update(limit=0.4, floor=True)
        count = counter(1, 22, 3)                         # the middle answer is 3: the stack reads as one block
        got = self.check(table, count)
        answer = got['answers']['count_separate:wafers']
        self.assertEqual((answer['count'], answer['expect'], answer['value']), (3, 25, 0.12))
        self.assertIn('count_separate:wafers', got['failures'])
        self.assertEqual(len(count.asked), 3)
        self.assertTrue(all(ask in prompt for ask, prompt in zip(table['rows']['count_separate']['asks'], count.asked)))
        with self.assertRaises(StudioError) as caught:
            from studio2 import frames
            with mock.patch('studio2.checks.table', return_value=table):
                frames.pick(self.path, 'c1', 'first', 1, 'a test pick: it shows what the cut needs')
        self.assertEqual(caught.exception.code, 'PICTURE_FLOOR')

    def test_separate_items_pass_and_an_uncalibrated_check_is_advice_never_a_pass(self):
        from studio2 import checks
        table = checks.table()
        table['rows']['count_separate'].update(limit=0.4, floor=True)
        table['rows']['text_free']['asks'] += ' '                       # a new table: checked anew
        got = self.check(table, counter(24, 25, 20))
        self.assertTrue(got['answers']['count_separate:wafers']['ok'])
        self.assertNotIn('count_separate:wafers', got['failures'] + got['warnings'])
        table['rows']['count_separate'].update(limit=None, floor=False)
        table['rows']['text_free']['asks'] += ' '
        got = self.check(table, counter(24, 25, 20))
        self.assertFalse(got['answers']['count_separate:wafers']['ok'])
        self.assertIn('count_separate:wafers', got['warnings'])
        self.assertIn('not calibrated', got['answers']['count_separate:wafers']['why'])

    def test_calibration_sets_a_floor_only_where_every_good_and_bad_are_told_apart(self):
        from studio2 import checks
        image = self.root / 'cal.png'
        Image.new('RGB', (90, 160)).save(image)
        cut = decisions.envelope(self.path, 'cuts')['body']['cuts'][0]
        items = [{'name': n, 'image': str(image), 'cut': cut, 'frame': 'first', 'expect': {'count_separate': good}}
                 for n, good in (('good_a', True), ('good_b', True), ('bad_a', False))]
        set_file = self.root / 'set.json'
        set_file.write_text(json.dumps({'items': items}))

        def by_item(counts):
            def read(images, prompt, schema, target):
                name = Path(target).parts[-4]          # <out>/<item>/run<n>/count/read_k.json
                ids = schema['properties']['parts']['items']['properties']['id']['enum']
                write_json(target, {'answer': {'parts': [{'id': i, 'counts': [counts[name]]} for i in ids]}})
            return read
        table = checks.table()
        with mock.patch('studio2.checks.table', return_value=table):
            got = checks.calibrate(set_file, self.root / 'cal', runs=2, placer=placer, counter=by_item({'good_a': 25, 'good_b': 15, 'bad_a': 1}),
                                   only=['count_separate'])
        result = got['rows']['count_separate']
        self.assertTrue(result['floor'])
        self.assertEqual(result['limit'], round((0.6 + 0.04) / 2, 4))
        self.assertEqual(set(got['rows']), {'count_separate'})              # only the row asked
        with mock.patch('studio2.checks.table', return_value=table):
            got = checks.calibrate(set_file, self.root / 'cal2', runs=2, placer=placer, counter=by_item({'good_a': 25, 'good_b': 1, 'bad_a': 1}),
                                   only=['count_separate'])
        self.assertFalse(got['rows']['count_separate']['floor'])            # a good read as low as the bad: advice


if __name__ == '__main__':
    unittest.main()
