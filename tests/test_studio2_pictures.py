"""A cut's pictures (studio2/prompts, frames, checks, locate) and its clip (studio2/motion) and the preview reel
(studio2/reel): prompts built from the cut and its place chain, tries kept and picked by the agent with the checks as
guard, marks located and judged, the clip drawn at the planned length with the reference's label size."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image

from studio import decisions
from studio.audio import build_audio, run_media
from studio.common import StudioError, file_hash, read_json, write_json
from studio.project import load_shot
from tests import studio2_fixture as fx
from tests.test_studio2_foundation import FakeCodex


def answer_all(ok=True, counts=None, seen='yes'):
    """A reader that answers every check question ok (or not), counting what it is asked to count; the blind viewer
    says something changed and the matcher answers `seen`."""
    def reader(images, prompt, schema, target):
        if 'changes' in schema['properties']:
            return write_json(target, {'answer': {'seen': 'chips', 'changes': 'one chip went dark', 'learns': 'a chip died'}})
        if 'cuts' in schema['properties']:
            return write_json(target, {'answer': {'cuts': [{'shot_id': 'cut', 'seen': seen, 'why': 'a test'}], 'reel': {'seen': seen, 'why': 'a test'}}})
        ids = schema['properties']['answers']['items']['properties']['id']['enum']
        write_json(target, {'answer': {'answers': [{'id': i, 'ok': ok if not callable(ok) else ok(i), 'count': (counts or {}).get(i),
                                                    'why': 'a test reading'} for i in ids], 'note': ''}})
    return reader


def place_reader(images, prompt, schema, target):
    parts = schema['properties']['parts']['items']['properties']['id']['enum']
    flows = schema['properties']['flows']['minItems']
    element = schema['properties']['element'] != {'type': 'null'}
    write_json(target, {'answer': {'parts': [{'id': p, 'box': [0.2, 0.2 + 0.3 * n, 0.5, 0.4 + 0.3 * n], 'point': [0.35, 0.3 + 0.3 * n]}
                                             for n, p in enumerate(parts)],
                                   'flows': [{'index': i, 'ways': [[[0.5, 0.1], [0.5, 0.5], [0.55, 0.9]]]} for i in range(flows)],
                                   'element': [[0.2, 0.2], [0.4, 0.6]] if element else None, 'focus': [0.5, 0.45]}})


def marks_ok(images, prompt, schema, target):
    ids = schema['properties']['marks']['items']['properties']['mark']['enum']
    write_json(target, {'answer': {'marks': [{'mark': i, 'ok': True, 'why': 'on it'} for i in ids]}})


class PicturesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = fx.project(self.root)
        body = fx.body()
        body['cuts'][0]['cutaway']['parts'][1]['count'] = 1
        body['cuts'][1]['place'] = 'bay'
        self.body = body
        decisions.write(self.path, 'cuts', body, agent_note=fx.TEST_NOTE)

    def tearDown(self):
        self.tmp.cleanup()

    def make_picked(self, cut_id, frames=('first',)):
        from studio2 import checks, frames as fr
        for frame in frames:
            fr.make(self.path, cut_id, frame, runner=FakeCodex(self.root, ['image']))
            checks.check(self.path, cut_id, frame, reader=answer_all(counts={'count_right:floor': 1}), matcher=answer_all())
            fr.pick(self.path, cut_id, frame, 1, 'a test pick: it shows what the cut needs')

    # --- prompts -------------------------------------------------------------------------------------------------
    def test_the_first_prompt_is_built_from_the_cut(self):
        from studio2 import frames
        got = frames.make(self.path, 'c1', 'first', runner=FakeCodex(self.root, ['image']))
        prompt = read_json(Path(got['made'][0]['path']).with_suffix('.json'))['prompt']
        self.assertIn('Photoreal, natural light', prompt)                  # the reel's look
        self.assertIn('no text, no letters', prompt)
        self.assertIn('as if a giant knife sliced it', prompt)              # the opening's row in the grammar
        self.assertIn('exactly 1 the perforated floor', prompt)            # a counted part
        self.assertIn('leave the way of filtered air', prompt.lower())     # room for the flow drawn later
        self.assertIn('Seen at eye level', prompt)                         # the view's row

    def test_a_cut_at_the_same_place_is_drawn_from_its_first_picture(self):
        from studio2 import frames
        with self.assertRaises(StudioError) as caught:   # pictures are made in cut order
            frames.make(self.path, 'c2', 'first', runner=FakeCodex(self.root, []))
        self.assertEqual(caught.exception.code, 'PLACE_REF_UNPICKED')
        self.make_picked('c1')
        fake = FakeCodex(self.root, ['image'])
        frames.make(self.path, 'c2', 'first', runner=fake)
        c1 = frames.picked(self.path, self.body['cuts'][0], 'first')
        self.assertIn(str(c1), fake.calls[0])
        prompt = next(a for a in fake.calls[0] if 'Generate an image' in a)
        self.assertIn('place and style reference', prompt)
        self.assertIn("NOT the reference's view: the camera is much closer", prompt)   # c1 is a room, c2 a part: the step is said

    def test_the_last_picture_is_an_edit_of_the_picked_first(self):
        from studio2 import frames
        self.make_picked('c1')
        got = frames.make(self.path, 'c2', runner=FakeCodex(self.root, ['image']))   # first made; last waits for its pick
        self.assertEqual([m['frame'] for m in got['made']], ['first'])
        self.assertTrue(got['waiting'])
        self.make_picked('c2')
        fake = FakeCodex(self.root, ['image'])
        frames.make(self.path, 'c2', runner=fake)
        prompt = next(a for a in fake.calls[0] if 'Edit the attached image' in a)
        self.assertIn('Change only one thing: the centre chip goes dark', prompt)
        self.assertIn(str(frames.picked(self.path, self.body['cuts'][1], 'first')), fake.calls[0])

    def test_a_picture_is_kept_while_what_it_is_drawn_from_holds(self):
        from studio2 import frames
        self.make_picked('c1')
        body = deepcopy(self.body)
        body['cuts'][0]['cutaway']['camera'] = {'move': 'still'}            # the camera, the labels, the timing: drawn over it
        body['cuts'][0]['cutaway']['labels'] = [{'part': 'floor', 'word': '바닥'}]
        body['cuts'][0]['duration_s'] = 4.5
        decisions.write(self.path, 'cuts', body, agent_note=fx.TEST_NOTE)
        self.assertIsNotNone(frames.picked(self.path, body['cuts'][0], 'first'))
        body['cuts'][0]['change'] = {'kind': 'move', 'what': 'a speck falls', 'at': [0.2, 0.8],   # a moving thing: kept out of the picture
                                     'element': {'what': 'a speck', 'path': 'down', 'drawn': 'speck'}}
        decisions.write(self.path, 'cuts', body, agent_note=fx.TEST_NOTE)
        self.assertIsNone(frames.picked(self.path, body['cuts'][0], 'first'))
        self.make_picked('c1')
        body['cuts'][0]['change'].update({'what': 'a speck falls and leaves', 'at': [0.1, 0.9]})   # a move's words and path: drawn over it
        body['cuts'][0]['change']['element']['path'] = 'down and out to the right'
        decisions.write(self.path, 'cuts', body, agent_note=fx.TEST_NOTE)
        self.assertIsNotNone(frames.picked(self.path, body['cuts'][0], 'first'))
        body['cuts'][0]['cutaway']['what'] = 'a vertical section of one bay, seen from a little above'   # what is drawn: a new picture
        decisions.write(self.path, 'cuts', body, agent_note=fx.TEST_NOTE)
        self.assertIsNone(frames.picked(self.path, body['cuts'][0], 'first'))

    def test_a_try_again_carries_its_reason_and_tries_are_bounded(self):
        from studio2 import frames
        frames.make(self.path, 'c1', 'first', runner=FakeCodex(self.root, ['image']))
        again = frames.make(self.path, 'c1', 'first', runner=FakeCodex(self.root, []))   # made already: nothing new without a reason
        self.assertEqual(again['made'], [])
        for n in (2, 3):
            fake = FakeCodex(self.root, ['image'])
            got = frames.make(self.path, 'c1', 'first', reason='the worker is two people', runner=fake)
            self.assertEqual(got['made'][0]['try'], n)
            self.assertIn('The last try was wrong: the worker is two people', next(a for a in fake.calls[0] if 'Generate' in a))
        with self.assertRaises(StudioError) as caught:
            frames.make(self.path, 'c1', 'first', reason='still wrong', runner=FakeCodex(self.root, []))
        self.assertEqual(caught.exception.code, 'TRIES_SPENT')

    # --- checks and picks ----------------------------------------------------------------------------------------
    def test_a_pick_needs_a_check_and_never_takes_a_floor_failure(self):
        from studio2 import checks, frames
        frames.make(self.path, 'c1', 'first', runner=FakeCodex(self.root, ['image']))
        with self.assertRaises(StudioError) as caught:
            frames.pick(self.path, 'c1', 'first', 1, 'a test pick: it shows what the cut needs')
        self.assertEqual(caught.exception.code, 'PICTURE_UNCHECKED')
        table = checks.table()
        table['rows']['text_free']['floor'] = True
        with mock.patch('studio2.checks.table', return_value=table):
            got = checks.check(self.path, 'c1', reader=answer_all(ok=lambda i: i != 'text_free', counts={'count_right:floor': 1}))
            self.assertEqual(got['results'][0]['failures'], ['text_free'])
            with self.assertRaises(StudioError) as caught:
                frames.pick(self.path, 'c1', 'first', 1, 'a test pick: it shows what the cut needs')
            self.assertEqual(caught.exception.code, 'PICTURE_FLOOR')

    def test_counts_are_compared_by_the_check_not_the_reader(self):
        from studio2 import checks
        from studio2 import frames
        frames.make(self.path, 'c1', 'first', runner=FakeCodex(self.root, ['image']))
        for floor, kind in ((False, 'warnings'), (True, 'failures')):   # advice, or a floor where calibration made it one
            table = checks.table()
            table['rows']['count_right']['floor'] = floor
            with mock.patch('studio2.checks.table', return_value=table):
                got = checks.check(self.path, 'c1', reader=answer_all(counts={'count_right:floor': 2}))
            self.assertIn('count_right:floor', got['results'][0][kind])
            self.assertEqual(got['results'][0]['answers']['count_right:floor']['expect'], 1)

    def test_a_check_holds_for_the_picture_and_the_table_it_read(self):
        from studio2 import checks, frames
        frames.make(self.path, 'c1', 'first', runner=FakeCodex(self.root, ['image']))
        checks.check(self.path, 'c1', reader=answer_all())
        cut = self.body['cuts'][0]
        self.assertIsNotNone(checks.result_of(self.path, cut, 'first', 1))
        table = checks.table()
        table['rows']['text_free']['asks'] += ' (asked anew)'
        with mock.patch('studio2.checks.table', return_value=table):
            self.assertIsNone(checks.result_of(self.path, cut, 'first', 1))

    def test_calibration_makes_a_floor_only_where_every_answer_held(self):
        from studio2 import checks
        image = self.root / 'cal.png'
        Image.new('RGB', (90, 160)).save(image)
        cut = self.body['cuts'][0]
        items = {'items': [{'name': 'kept', 'image': str(image), 'cut': cut, 'frame': 'first', 'expect': {'text_free': True, 'anatomy_ok': True}},
                           {'name': 'bad', 'image': str(image), 'cut': cut, 'frame': 'first', 'expect': {'text_free': False, 'anatomy_ok': False}}]}
        set_file = self.root / 'set.json'
        set_file.write_text(json.dumps(items))

        def reader(images, prompt, schema, target):   # text read right every time; anatomy wrong on the bad one
            ids = schema['properties']['answers']['items']['properties']['id']['enum']
            bad = 'bad_' in Path(target).name
            write_json(target, {'answer': {'answers': [{'id': i, 'ok': (not bad) if i == 'text_free' else True, 'count': None, 'why': ''}
                                                       for i in ids], 'note': ''}})
        got = checks.calibrate(set_file, self.root / 'cal', runs=2, reader=reader)
        self.assertTrue(got['rows']['text_free']['floor'])
        self.assertFalse(got['rows']['anatomy_ok']['floor'])

    # --- locate and the clip -------------------------------------------------------------------------------------
    def located(self, cut_id):
        from studio2 import locate
        return locate.locate(self.path, cut_id, reader=place_reader, checker=marks_ok)

    def test_marks_are_located_drawn_and_judged(self):
        from studio2 import locate
        self.make_picked('c1')
        got = self.located('c1')
        self.assertTrue(got['ok'])
        self.assertTrue(Path(got['overlay']).is_file())
        self.assertIsNotNone(locate.places_of(self.path, self.body['cuts'][0]))

        def one_off(images, prompt, schema, target):
            ids = schema['properties']['marks']['items']['properties']['mark']['enum']
            write_json(target, {'answer': {'marks': [{'mark': i, 'ok': i != 'flow:0', 'why': 'it runs up a wall'} for i in ids]}})
        got = locate.locate(self.path, 'c1', reason='the air line ran up a wall', reader=place_reader, checker=one_off)
        self.assertFalse(got['ok'])
        from studio2 import motion
        with self.assertRaises(StudioError) as caught:
            motion.render(self.path, 'c1')
        self.assertEqual(caught.exception.code, 'PLACES_OFF')

    def test_a_part_the_reader_did_not_locate_is_off(self):
        """Each named part is located once: a reading that names one part twice and another never is off, whatever the
        overlay judge says (2026-10-27, dev c11: 'chips' twice, 'wiring' - the key change's part - never)."""
        from studio2 import locate
        self.make_picked('c1')

        def twice(images, prompt, schema, target):
            place_reader(images, prompt, schema, target)
            from studio.common import read_json
            data = read_json(target)
            data['answer']['parts'] = [dict(data['answer']['parts'][0]) for _ in data['answer']['parts']]
            write_json(target, data)
        got = locate.locate(self.path, 'c1', reader=twice, checker=marks_ok)
        self.assertFalse(got['ok'])
        self.assertTrue(any(m['mark'] == 'part:floor' and not m['ok'] for m in got['check']), got['check'])

    def test_a_try_locates_again_only_the_pictures_judged_off(self):
        """2026-10-27, fab c04: the route on the first picture was off, both edits right; each try read the edits again
        with a reason about the route their reading has no field for, and those reads hung at the limit. A picture whose
        marks were all judged right keeps its reading and its verdicts; only the pictures with an off mark are read."""
        from studio2 import locate
        self.make_picked('c1')
        self.make_picked('c2', ('first', 'last'))
        read, judged = [], []

        def reader(images, prompt, schema, target):
            read.append(Path(images[0]).stem)
            place_reader(images, prompt, schema, target)

        def first_off(images, prompt, schema, target):
            judged.append(Path(images[0]).stem)
            ids = schema['properties']['marks']['items']['properties']['mark']['enum']
            off = Path(images[0]).stem == 'overlay' and len(judged) == 1
            write_json(target, {'answer': {'marks': [{'mark': i, 'ok': not (off and n == 0), 'why': 'w'} for n, i in enumerate(ids)]}})
        got = locate.locate(self.path, 'c2', reader=reader, checker=first_off)
        self.assertFalse(got['ok'])
        self.assertEqual(sorted(read), ['first_1', 'last_1'])
        kept = got['edits']['last']
        read.clear()
        again = locate.locate(self.path, 'c2', reason='the first mark sat off its part', reader=reader, checker=first_off)
        self.assertEqual(read, ['first_1'])                       # the edit judged right is not read again
        self.assertEqual(again['edits']['last'], kept)
        self.assertTrue(again['ok'])
        self.assertTrue(any(m['mark'].startswith('last ') for m in again['check']))   # its verdicts stay on the record
        self.assertEqual(again['tries'], 2)

    def test_tries_are_counted_for_what_is_located(self):
        from studio2 import locate
        self.make_picked('c1')
        for n in range(3):
            locate.locate(self.path, 'c1', reason=f'off {n}' if n else None, reader=place_reader, checker=marks_ok)
        with self.assertRaises(StudioError) as caught:
            locate.locate(self.path, 'c1', reason='still off', reader=place_reader, checker=marks_ok)
        self.assertEqual(caught.exception.code, 'TRIES_SPENT')
        body = deepcopy(self.body)   # what is located changes (a flow's way): its tries start again, its picture kept
        body['cuts'][0]['cutaway']['flow'][0]['ways'] = 2
        decisions.write(self.path, 'cuts', body, agent_note=fx.TEST_NOTE)
        self.assertIsNone(locate.places_of(self.path, body['cuts'][0]))   # the old places are not this plan's
        got = locate.locate(self.path, 'c1', reader=place_reader, checker=marks_ok)
        self.assertEqual(got['tries'], 1)

    def test_the_clip_is_drawn_at_its_planned_length(self):
        from studio2 import motion
        from studio.edit import latest_composite
        self.make_picked('c1')
        self.located('c1')
        got = motion.render(self.path, 'c1')
        self.assertFalse(got['cached'])
        self.assertTrue(motion.render(self.path, 'c1')['cached'])
        shot = load_shot(self.path, 'c1')
        self.assertEqual(got['frame_count'], shot['duration_frames'])
        self.assertEqual(shot['route']['composite']['key'], got['key'])
        self.assertIsNotNone(latest_composite(self.path, shot, shot['duration_frames']))
        self.assertAlmostEqual(got['label']['ink_px'] / 1920, 0.037, delta=0.002)   # the reel's letter height, drawn as said

    def test_a_label_size_outside_the_references_middle_half_is_said(self):
        body = fx.body(label_height=0.015)
        warnings = decisions.lint(self.path, 'cuts', body)['warnings']
        self.assertTrue(any(w.startswith('LABEL_SIZE_OFF') for w in warnings), warnings)
        self.assertFalse(any(w.startswith('LABEL_SIZE_OFF') for w in decisions.lint(self.path, 'cuts', fx.body())['warnings']))

    def test_labels_keep_clear_of_the_subtitle_and_each_other(self):
        from studio2 import motion
        font = motion.font_for(0.04 * 1920)
        band = motion.caption_band(self.path, 1080, 1920)
        rows = [('필터', (300.0, band[0] + 20)), ('이중 바닥', (320.0, band[0] + 40)), ('먼지', (700.0, 600.0)), ('천장 레일', (520.0, 150.0))]
        spots = motion.place_labels(rows, 1080, 1920, band, font, motion.safe_rect(self.path, 1080, 1920))
        from PIL import ImageDraw
        probe = ImageDraw.Draw(Image.new('L', (8, 8)))
        boxes = [probe.textbbox(s, w, font=font, anchor='mm') for (w, _a), s in zip(rows, spots)]
        from studio.blender import ui_rect
        x0, y0, x1, y1 = ui_rect({}, (1080, 1920))
        for box in boxes:
            self.assertFalse(box[3] > band[0] and box[1] < band[1], box)
            self.assertTrue(x0 * 1080 <= box[0] and box[2] <= x1 * 1080 and y0 * 1920 <= box[1] and box[3] <= y1 * 1920, box)   # clear of the feed's UI
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                self.assertTrue(a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])

    def test_a_state_change_crosses_to_the_last_picture(self):
        from studio2 import motion
        self.make_picked('c1')
        self.make_picked('c2', ('first', 'last'))
        self.located('c2')
        cut = self.body['cuts'][1]
        from studio2.locate import places_of
        if True:
            plan = motion.plan_of(self.path, cut, places_of(self.path, cut)['places'], 108, 192, 120)
            frame = motion.frame_function(plan, motion._pictures(self.path, cut, 108, 192), 108, 192)
        last = motion.cover(Image.open(motion._pictures(self.path, cut, 108, 192)['last']).convert('RGB'), 108, 192)
        first = motion.cover(Image.open(motion._pictures(self.path, cut, 108, 192)['first']).convert('RGB'), 108, 192)
        corner = lambda im: im.getpixel((2, 190))   # noqa: E731 - a corner no mark is drawn on
        self.assertEqual(corner(frame(0.0)), corner(first))
        self.assertLess(sum(abs(a - b) for a, b in zip(corner(motion.zoom(last, 1.06, 0.5, 0.45)), corner(frame(3.99)))), 30)

    def test_a_moving_thing_keeps_its_own_window(self):
        from studio2 import motion
        cut = deepcopy(self.body['cuts'][1])
        cut['change']['element'] = {'what': 'a dust speck', 'path': 'down onto the chip', 'drawn': 'speck', 'at': [0.1, 0.3], 'size': 0.02}
        places = {'parts': [{'id': 'filters', 'point': [0.3, 0.3]}, {'id': 'floor', 'point': [0.3, 0.6]}],
                  'flows': [], 'element': [[0.5, 0.1], [0.5, 0.5]], 'focus': [0.5, 0.5]}
        plan = motion.plan_of(self.path, cut, places, 108, 192, 120)
        self.assertEqual(plan['element']['at'], [0.1, 0.3])                  # its own window, not the chip's [0.4, 0.6]
        self.assertAlmostEqual(plan['element']['size'], 0.02)
        body = deepcopy(self.body)
        body['cuts'][1]['change']['element'] = {'what': 'a dust speck', 'path': 'down', 'drawn': 'speck', 'at': [0.5, 0.2]}
        errors = decisions.lint(self.path, 'cuts', body)['errors']
        self.assertTrue(any(e.startswith('CHANGE_UNDECIDED: c2') for e in errors), errors)

    def test_a_flow_through_a_whole_room_is_drawn_as_its_ways(self):
        from studio2 import locate, motion
        cut = deepcopy(self.body['cuts'][0])
        cut['cutaway']['flow'][0]['ways'] = 3
        schema = locate._schema(['filters', 'floor'], [0], None, [3])
        self.assertEqual(schema['properties']['flows']['items']['properties']['ways']['minItems'], 3)
        places = {'parts': [{'id': 'filters', 'point': [0.3, 0.2]}, {'id': 'floor', 'point': [0.3, 0.6]}],
                  'flows': [{'index': 0, 'ways': [[[0.2, 0.1], [0.2, 0.6]], [[0.5, 0.1], [0.5, 0.6]], [[0.8, 0.1], [0.8, 0.6]]]}],
                  'element': None, 'focus': [0.5, 0.5]}
        plan = motion.plan_of(self.path, cut, places, 108, 192, 120)
        self.assertEqual(len(plan['flows']), 3)

    def test_a_stream_of_specks_and_a_label_on_a_flow(self):
        from studio2 import motion
        body = deepcopy(self.body)
        cut = body['cuts'][0]
        cut['change'] = {'kind': 'move', 'what': 'specks stream off', 'at': [0.2, 0.9],
                         'element': {'what': 'specks of dust', 'path': 'down', 'drawn': 'speck', 'count': 12, 'label': '먼지'}}
        cut['cutaway']['labels'] = [{'flow': 0, 'word': '공기'}]
        self.assertEqual(decisions.lint(self.path, 'cuts', body)['errors'], [])
        places = {'parts': [{'id': 'filters', 'point': [0.3, 0.2]}, {'id': 'floor', 'point': [0.3, 0.6]}],
                  'flows': [{'index': 0, 'ways': [[[0.5, 0.1], [0.5, 0.6]]]}], 'element': [[0.4, 0.3], [0.4, 0.6]], 'focus': [0.5, 0.5]}
        plan = motion.plan_of(self.path, cut, places, 108, 192, 120, body)
        self.assertAlmostEqual(plan['labels'][0][1][1], 67.2, places=6)   # the flow's middle
        self.assertAlmostEqual(plan['labels'][0][1][0], 54.0, places=6)
        seen = [len(motion.specks_at(plan['element'], t, 4.0, 192)) for t in (0.5, 1.5, 2.5, 3.5)]
        self.assertGreater(max(seen), 1)                        # several in the air at once
        cut['cutaway']['labels'] = [{'flow': 3, 'word': '공기'}, {'part': 'floor', 'flow': 0, 'word': '둘'}]
        errors = decisions.lint(self.path, 'cuts', body)['errors']
        self.assertTrue(any(e.startswith('CUT_LABEL_UNSHOWN: c1') for e in errors), errors)

    def test_a_moving_label_keeps_off_the_fixed_ones(self):
        from studio2 import motion
        font = motion.font_for(0.03 * 1920)
        fixed = [(400.0, 1000.0, (300.0, 960.0, 500.0, 1040.0))]
        spot = motion.moving_spot((420.0, 1010.0), '먼지', font, 1080, 1920, (100, 270, 980, 1250), fixed)
        from PIL import ImageDraw
        box = ImageDraw.Draw(Image.new('L', (8, 8))).textbbox(spot, '먼지', font=font, anchor='mm')
        self.assertTrue(box[2] < 300 or box[0] > 500 or box[3] < 960 or box[1] > 1040, (spot, box))

    def test_the_preview_reel_is_v1s_edit_of_the_clips(self):
        from studio2 import reel
        with self.assertRaises(StudioError) as caught:
            reel.preview(self.path)
        self.assertEqual(caught.exception.code, 'RENDER_REQUIRED')
        from studio2 import motion
        self.make_picked('c1')
        self.make_picked('c2', ('first', 'last'))
        wav = self.root / 'v.wav'
        run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=300:sample_rate=48000:duration=2', str(wav)])
        for cut in ('c1', 'c2'):
            self.located(cut)
            motion.render(self.path, cut)
            build_audio(self.path, cut, input_wav=wav)
        got = reel.preview(self.path)
        self.assertTrue(Path(got['reel']).is_file())
        self.assertTrue((self.path / 'review' / 'clay' / 'see' / 'c2.mp4').is_file())


if __name__ == '__main__':
    unittest.main()
