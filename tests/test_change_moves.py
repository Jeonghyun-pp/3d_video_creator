"""What a line explains changes on screen (studio/change_moves.py, 2026-10-22): a shot says its changes or why it is
still, each change is checked as written, shown while it happens and opened when it is inside; kinds are data."""
import ast
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from studio import change_moves as cm

ROOT = Path(__file__).resolve().parents[1]
FILL = {'id': 'fill_a', 'parts': ['boat/tank'], 'kind': 'level', 'from': 0.0, 'to': 0.9, 'at': [0.2, 0.8], 'why': 'the line says it fills'}


class KindsTest(unittest.TestCase):
    def test_every_kind_is_made_and_measured_by_a_known_way(self):
        for kid, kind in cm.kinds().items():
            self.assertIn(kind['makes'], cm.MAKES, kid)
            self.assertIn(kind['measure'], cm.MEASURES, kid)
            self.assertLessEqual(set(kind.get('needs') or []), set(cm.NEEDS), kid)
            low, high = kind['value']['range']
            self.assertLess(low, high, kid)
            self.assertGreater(kind['screen_min'], 0, kid)

    def test_no_code_branches_on_a_kind_id(self):
        """The code that makes and judges changes names no kind: a new kind is a library row (N+1). Kind ids are common
        words ('open', 'flow'), so the modules that handle changes are the ones read."""
        ids = set(cm.kinds())
        found = []
        handlers = [ROOT / 'studio' / 'change_moves.py', ROOT / 'studio' / 'blender_ops' / 'change_scene.py']
        for file in [f for f in handlers if f.is_file()]:
            for node in ast.walk(ast.parse(file.read_text())):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in ids:
                    found.append(f'{file.relative_to(ROOT)}:{node.lineno} {node.value}')
        self.assertEqual(found, [])

    def test_a_change_is_checked_as_written(self):
        self.assertEqual(cm.check_change(FILL), [])
        self.assertTrue(cm.check_change({**FILL, 'kind': 'melt'}))
        self.assertTrue(cm.check_change({**FILL, 'to': 3}))                        # outside the level's 0-1
        self.assertTrue(cm.check_change({**FILL, 'to': 0.0}))                      # nothing changes
        self.assertTrue(cm.check_change({**FILL, 'at': [0.8, 0.2]}))
        turn = {**FILL, 'kind': 'turn', 'from': 0, 'to': 20}
        self.assertTrue(cm.check_change(turn))                                     # a turn needs its axis
        self.assertEqual(cm.check_change({**turn, 'axis': 'x'}), [])
        self.assertTrue(cm.check_change({**FILL, 'with_words': '물을 채우면'}, narration='벤트를 엽니다'))
        self.assertEqual(cm.check_change({**FILL, 'with_words': '물을 채우면'}, narration='벤트를 열고 물을 채우면'), [])


class PlanTest(unittest.TestCase):
    def body(self, direction, show):
        return {'shots': [{'shot_id': 's1', 'line_ids': ['l1'], 'duration_s': 3, 'show': show, 'direction': direction}]}

    def lines(self, body, inside=False):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch('studio.decisions.known_show_parts', return_value={'boat', 'boat/tank'}), \
                mock.patch('studio.explains.resolve', return_value={'id': 'boat/tank', 'kind': 'part'}), \
                mock.patch('studio.explains.need', return_value={'inside': inside}):
            return cm.plan_lines(Path(tmp), body, {'l1': '탱크에 물을 채우면'})

    def test_a_shot_says_its_changes_or_why_it_is_still(self):
        found = self.lines(self.body({}, []))
        self.assertTrue(found[0].startswith('CHANGE_UNDECIDED: s1'))
        self.assertEqual(self.lines(self.body({'still': 'the boat holds'}, [])), [])

    def test_a_change_is_shown_while_it_happens_and_opened_when_inside(self):
        shown = [{'at': 'start', 'parts': ['boat/tank'], 'how': 'exterior', 'why': 'x'}]
        self.assertEqual(self.lines(self.body({'change': [FILL]}, shown)), [])
        unseen = self.lines(self.body({'change': [FILL]}, [{'at': 'start', 'parts': ['boat'], 'how': 'exterior', 'why': 'x'}]))
        self.assertEqual(unseen, [])   # the whole boat on screen holds its tank
        world = {'elements': [{'id': 'tanks', 'ids': ['boat/tank']}]}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch('studio.decisions.known_show_parts', return_value={'boat', 'boat/tank', 'tanks'}), \
                mock.patch('studio.explains.resolve', return_value=None):
            linked = cm.plan_lines(Path(tmp), self.body({'change': [{**FILL, 'parts': ['tanks']}]},
                                                       [{'at': 'start', 'parts': ['boat/tank'], 'how': 'cutaway', 'why': 'x'}]),
                                   {'l1': '탱크에 물을 채우면'}, world)
        self.assertEqual(linked, [])   # an element and the parts its link builds are one thing
        missing = self.lines(self.body({'change': [FILL]}, []))
        self.assertTrue(missing[0].startswith('CHANGE_UNSEEN_PLAN: s1'))
        closed = self.lines(self.body({'change': [FILL]}, shown), inside=True)
        self.assertTrue(closed[0].startswith('CHANGE_INSIDE_CLOSED: s1'))
        opened = [{**shown[0], 'how': 'cutaway'}]
        unmarked = self.lines(self.body({'change': [FILL]}, opened), inside=True)
        self.assertEqual([l.split(':')[0] for l in unmarked], ['CHANGE_UNMARKED'])   # opened, and nobody is told which it is
        named = {**FILL, 'marks': [{'kind': 'label', 'text': '탱크', 'why': 'the tank the line names'}]}
        self.assertEqual(self.lines(self.body({'change': [named]}, opened), inside=True), [])
        self.assertEqual(self.lines(self.body({'change': [{**FILL, 'unmarked': 'it fills the frame'}]}, opened), inside=True), [])
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch('studio.decisions.known_show_parts', return_value={'boat', 'boat/tank'}), \
                mock.patch('studio.explains.resolve', return_value={'id': 'boat/tank', 'kind': 'part', 'subject': 'boat'}), \
                mock.patch('studio.explains.need', return_value={'inside': None, 'problem': 'EXPOSURE_MISSING'}):
            unmeasured = cm.plan_lines(Path(tmp), self.body({'change': [FILL]}, shown), {'l1': '탱크에 물을 채우면'})
        self.assertTrue(unmeasured[0].startswith('CHANGE_INSIDE_UNMEASURED: s1'))   # unmeasured is said, never passed


class ScreenTest(unittest.TestCase):
    """A built change read on the probe's frames at its start and end (2026-10-22)."""

    def shot(self, change=FILL, text='탱크에 물을 채우면 가라앉습니다'):
        return {'shot_id': 's1', 'duration_frames': 61, 'narration': {'text': text}, 'direction': {'change': [change]}}

    def report(self, start_box, end_box, px=(10, 900)):
        def row(frame, box, n):
            return {'frame': frame, 'shapes': {'key:boat/tank/fill': {'px': n, 'bbox': box, 'centroid': [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]}}}
        return {'frames': [row(12, start_box, px[0]), row(48, end_box, px[1])]}

    def test_its_frames_and_ids_and_the_words_it_waits_for(self):
        row = cm.timed(self.shot())[0]
        self.assertEqual((row['start_frame'], row['end_frame'], row['measure_ids']), (12, 48, ['boat/tank/fill']))
        late = cm.timed(self.shot({**FILL, 'with_words': '가라앉습니다'}))[0]
        self.assertGreater(late['start_frame'], 12)
        self.assertEqual(late['end_frame'] - late['start_frame'], 36)      # the window is kept
        self.assertIn('timed', late)                                        # an estimate, said

    def test_a_change_the_screen_shows_passes_and_the_others_are_said(self):
        made = {'changes': [{'id': 'fill_a'}]}
        grown = cm.screen_lines(self.shot(), self.report([0.3, 0.6, 0.7, 0.62], [0.3, 0.3, 0.7, 0.62]), made)
        self.assertEqual(grown, [])
        self.assertEqual(cm.screen_lines(self.shot(), self.report([0.3, 0.6, 0.7, 0.62], [0.3, 0.3, 0.7, 0.62]), {})[0]['code'], 'CHANGE_UNMADE')
        tiny = cm.screen_lines(self.shot(), self.report([0.5, 0.5, 0.505, 0.505], [0.5, 0.5, 0.506, 0.506]), made)
        self.assertEqual(tiny[0]['code'], 'CHANGE_HIDDEN')                  # too thin to read anywhere in its window
        crossing = self.report([0.5, 0.5, 0.505, 0.505], [0.5, 0.5, 0.506, 0.506])
        crossing['frames'].insert(1, {'frame': 30, 'shapes': {'key:boat/tank/fill': {'px': 500, 'bbox': [0.3, 0.3, 0.6, 0.5], 'centroid': [0.45, 0.4]}}})
        self.assertEqual(cm.screen_lines(self.shot(), crossing, made), [])  # seen as it passes through the frame: shown
        same = cm.screen_lines(self.shot(), self.report([0.3, 0.3, 0.7, 0.62], [0.3, 0.3, 0.7, 0.62], px=(880, 900)), made)
        self.assertEqual(same[0]['code'], 'CHANGE_UNSHOWN')                 # it shows, and barely changes
        small = cm.screen_lines(self.shot(), self.report([0.3, 0.3, 0.7, 0.62], [0.3, 0.3, 0.7, 0.62], px=(600, 900)), made)
        self.assertEqual(small[0]['code'], 'CHANGE_SMALL')
        said = cm.screen_lines(self.shot({**FILL, 'exaggerated': 'drawn larger to read'}),
                               self.report([0.3, 0.3, 0.7, 0.62], [0.3, 0.3, 0.7, 0.62], px=(600, 900)), made)
        self.assertEqual(said, [])


class MarksTest(unittest.TestCase):
    def test_marks_become_labels_and_graphics_timed_to_the_change(self):
        shot = {'shot_id': 's1', 'duration_frames': 61, 'narration': {'text': ''}, 'labels': [{'label_id': 'mine', 'text': 'x'}],
                'graphics': [{'graphic_id': 'mark_old_0', 'kind': 'arrow'}],
                'direction': {'change': [{**FILL, 'marks': [{'kind': 'label', 'text': '보정 탱크', 'why': 'named in the line'},
                                                            {'kind': 'flow', 'along': ['boat/tank', [0, 0, -5]], 'why': 'where the water goes'},
                                                            {'kind': 'outline', 'why': 'its edge'}]}]}}
        out = cm.with_marks(shot)
        self.assertEqual([l['label_id'] for l in out['labels']], ['mine', 'mark_fill_a_0'])
        self.assertEqual(out['labels'][1]['anchor'], 'boat/tank')
        self.assertEqual([(g['graphic_id'], g['kind']) for g in out['graphics']], [('mark_fill_a_1', 'flow'), ('mark_fill_a_2', 'outline')])
        self.assertEqual(out['graphics'][1]['target'], 'boat/tank')
        self.assertEqual((out['labels'][1]['start_frame'], out['labels'][1]['end_frame']), (12, 49))


class SizeTest(unittest.TestCase):
    def test_a_squeeze_that_keeps_its_length_reads_on_the_side_that_changes(self):
        """2026-10-23 r3 s09: a long hull squeezed 15 % across read 0.009 by its box's diagonal."""
        before = {'box': [0.1, 0.45, 0.9, 0.55], 'px': 1, 'centre': [0.5, 0.5]}
        after = {'box': [0.1, 0.4575, 0.9, 0.5425], 'px': 1, 'centre': [0.5, 0.5]}
        self.assertAlmostEqual(cm.screen_change('size', before, after), 0.15, places=6)


class SmallTest(unittest.TestCase):
    """2026-10-23, r3: a bow tank's water (0.078 of the frame tall) and a burst of scattered air (0.0039 of it) - unseen."""

    def report(self, box, px):
        def row(f):
            return {'frame': f, 'shapes': {'all': {'px': 36864, 'share': 1.0}, 'key:boat/tank/fill': {'px': px, 'bbox': box, 'centroid': [0.5, 0.5]}}}
        return {'frames': [row(12), row(30), row(48)]}

    def shot(self, change):
        return {'shot_id': 's1', 'duration_frames': 61, 'narration': {'text': ''}, 'direction': {'change': [change]}}

    def test_a_change_too_small_on_screen_is_said_and_a_mark_halves_the_area_it_needs(self):
        made = {'changes': [{'id': 'fill_a'}]}
        speck = cm.small_lines(self.shot(FILL), self.report([0.45, 0.45, 0.55, 0.528], 360), made)   # 0.078 tall, 0.0098
        self.assertEqual([l['code'] for l in speck], ['CHANGE_TOO_SMALL'])
        scattered = cm.small_lines(self.shot(FILL), self.report([0.2, 0.2, 0.8, 0.67], 144), made)  # tall, 0.0039 of the frame
        self.assertEqual([l['code'] for l in scattered], ['CHANGE_TOO_SMALL'])
        seen = cm.small_lines(self.shot(FILL), self.report([0.3, 0.3, 0.7, 0.6], 700), made)       # 0.3 tall, 0.019
        self.assertEqual(seen, [])
        marked = {**FILL, 'marks': [{'kind': 'label', 'text': '탱크', 'why': 'named'}]}
        self.assertEqual(cm.small_lines(self.shot(marked), self.report([0.3, 0.3, 0.7, 0.6], 300), made), [])   # 0.0081 >= 0.006


class NothingTest(unittest.TestCase):
    def test_a_still_shot_whose_picture_never_changes_is_said(self):
        """2026-10-23 r3: s06 held 0.000 / 0.000 and was dull 4/4; the pull-backs (0.61-0.63) were not."""
        def report(a, b):
            return {'frames': [{'frame': 0, 'shapes': {'subject': {'bbox': a, 'centroid': [(a[0] + a[2]) / 2, (a[1] + a[3]) / 2]}}},
                               {'frame': 60, 'shapes': {'subject': {'bbox': b, 'centroid': [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]}}}]}
        still = {'shot_id': 's6', 'direction': {'still': 'the rule is said'}}
        held = report([0.1, 0.3, 0.9, 0.6], [0.1, 0.3, 0.9, 0.6])
        self.assertEqual([l['code'] for l in cm.nothing_lines(still, held)], ['SHOT_NOTHING_SHOWS'])
        self.assertEqual(cm.nothing_lines(still, report([0.1, 0.2, 0.9, 0.7], [0.3, 0.4, 0.7, 0.6])), [])   # pulled back
        self.assertEqual(cm.nothing_lines({'shot_id': 's1', 'direction': {'change': [FILL]}}, held), [])   # its change carries it


class MaskedTest(unittest.TestCase):
    """2026-10-23, r3: a sinking filmed by a camera pushing in read as the camera moving (CHANGE_MASKED)."""
    SINK = {'id': 'sink', 'parts': ['boat'], 'kind': 'move', 'from': 0, 'to': 10, 'at': [0.2, 0.8], 'toward': [0, 0, -1], 'why': 'it sinks'}

    def shot(self):
        return {'shot_id': 's1', 'duration_frames': 61, 'narration': {'text': ''}, 'direction': {'change': [self.SINK]}}

    def report(self, support):
        def row(frame, y):
            return {'frame': frame, 'support_share': support,
                    'shapes': {'key:boat': {'px': 900, 'bbox': [0.3, y, 0.7, y + 0.2], 'centroid': [0.5, y + 0.1]}}}
        return {'frames': [row(12, 0.40), row(30, 0.45), row(48, 0.50)]}   # it travels 0.1 down the screen

    def pairs(self, ty):
        return [{'ok': True, 'frame': f + 1, 'camera': {'tx': 0.0, 'ty': ty, 'ds': 0.0, 'rot': 0.0}} for f in range(60)]

    def test_a_camera_that_moves_as_much_as_the_change_masks_it(self):
        made = {'changes': [{'id': 'sink'}]}
        lines = cm.masked_lines(self.shot(), self.report(0.0), made, self.pairs(0.004), 30)   # 36 frames x 0.004 = 0.144
        self.assertEqual([l['code'] for l in lines], ['CHANGE_MASKED'])
        self.assertEqual(cm.masked_lines(self.shot(), self.report(0.0), made, self.pairs(0.0), 30), [])    # the camera holds
        self.assertEqual(cm.masked_lines(self.shot(), self.report(0.3), made, self.pairs(0.004), 30), [])  # something still in frame
        fill = {**self.SINK, 'kind': 'level', 'parts': ['boat/tank'], 'from': 0, 'to': 1}
        self.assertEqual(cm.masked_lines({**self.shot(), 'direction': {'change': [fill]}}, {}, made, self.pairs(0.004), 30), [])   # needs none

    def test_only_kinds_that_say_so_need_a_reference(self):
        book = cm.kinds()
        self.assertTrue(book['move'].get('needs_reference') and book['turn'].get('needs_reference'))
        self.assertFalse(book['level'].get('needs_reference'))


class KeyLinesTest(unittest.TestCase):
    def test_a_key_moment_no_change_speaks_of_is_said(self):
        key = [{'at': 0.5, 'parts': ['boat/tank'], 'how': 'cutaway', 'why': 'x', 'key': True}]
        said = cm.key_lines({'shots': [{'shot_id': 's1', 'show': key, 'direction': {'still': 'x'}}]})
        self.assertTrue(said[0].startswith('KEY_CHANGE_UNSAID: s1'))
        self.assertEqual(cm.key_lines({'shots': [{'shot_id': 's1', 'show': key, 'direction': {'change': [FILL]}}]}), [])


class PlaceTest(unittest.TestCase):
    def test_subjects_but_one_say_where_they_stand(self):
        from studio.common import write_json
        from studio.decisions import _place_subjects, place_lines
        with tempfile.TemporaryDirectory() as tmp:
            for sid in ('boat', 'stone'):
                write_json(Path(tmp) / 'subjects' / sid / 'spec.json', {'subject_id': sid})
            row = {'shot_id': 's1', 'elements': ['boat', 'stone', 'water']}
            self.assertTrue(place_lines(Path(tmp), {'shots': [row]})[0].startswith('SUBJECT_UNPLACED: s1'))
            placed = {**row, 'place': [{'id': 'stone', 'at': [0, -12, 4], 'why': 'near the lens'}]}
            self.assertEqual(place_lines(Path(tmp), {'shots': [placed]}), [])
            self.assertTrue(place_lines(Path(tmp), {'shots': [{**placed, 'place': [{'id': 'water', 'at': [0, 0, 0], 'why': 'x'}]}]}))
        shot = {'subjects': [{'subject_id': 'boat'}, {'subject_id': 'stone'}]}
        _place_subjects(shot, placed['place'])
        self.assertEqual({r['id']: r['at'] for r in shot['scene']['instances']}, {'stone': [0, -12, 4], 'boat': [0, 0, 0]})

    def test_a_subject_may_be_placed_by_where_it_shows(self):
        """2026-10-23, r3: a stone near the lens with the boat behind - no world place could be written before the camera."""
        from studio.decisions import _place_subjects
        near = {'at': 0.4, 'screen': [0.3, 0.25], 'size': 0.3}
        shot = {'subjects': [{'subject_id': 'boat'}, {'subject_id': 'stone'}]}
        _place_subjects(shot, [{'id': 'stone', 'near': near, 'why': 'the stone falls past the lens'}])
        row = next(r for r in shot['scene']['instances'] if r['id'] == 'stone')
        self.assertEqual(row['near'], near)
        self.assertEqual(len(row['at']), 3)          # built somewhere first; the camera then finds its place
        deep = {'subjects': [{'subject_id': 'boat'}, {'subject_id': 'stone'}]}
        _place_subjects(deep, [{'id': 'boat', 'at': [0, 0, -100], 'why': 'down'}, {'id': 'stone', 'near': near, 'why': 'near'}])
        self.assertEqual(next(r for r in deep['scene']['instances'] if r['id'] == 'stone')['at'], [0, 0, -100])   # beside the boat
        import json
        import jsonschema
        from studio.common import CODE
        item = json.loads((CODE / 'schemas/studio-v1/decision.schema.json').read_text())['$defs']['shotlist']['properties']['shots']['items']['properties']['place']['items']
        jsonschema.validate({'id': 'stone', 'near': near, 'why': 'xxxx'}, item)
        with self.assertRaises(jsonschema.ValidationError):          # a place says one or the other, never both
            jsonschema.validate({'id': 'stone', 'at': [0, 0, 0], 'near': near, 'why': 'xxxx'}, item)


if __name__ == '__main__':
    unittest.main()
