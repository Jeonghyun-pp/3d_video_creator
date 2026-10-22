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
        missing = self.lines(self.body({'change': [FILL]}, []))
        self.assertTrue(missing[0].startswith('CHANGE_UNSEEN_PLAN: s1'))
        closed = self.lines(self.body({'change': [FILL]}, shown), inside=True)
        self.assertTrue(closed[0].startswith('CHANGE_INSIDE_CLOSED: s1'))
        opened = [{**shown[0], 'how': 'cutaway'}]
        self.assertEqual(self.lines(self.body({'change': [FILL]}, opened), inside=True), [])


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
        self.assertEqual(tiny[0]['code'], 'CHANGE_HIDDEN')                  # too thin to read at either end
        same = cm.screen_lines(self.shot(), self.report([0.3, 0.3, 0.7, 0.62], [0.3, 0.3, 0.7, 0.62], px=(880, 900)), made)
        self.assertEqual(same[0]['code'], 'CHANGE_UNSHOWN')                 # it shows, and barely changes
        small = cm.screen_lines(self.shot(), self.report([0.3, 0.3, 0.7, 0.62], [0.3, 0.3, 0.7, 0.62], px=(600, 900)), made)
        self.assertEqual(small[0]['code'], 'CHANGE_SMALL')
        said = cm.screen_lines(self.shot({**FILL, 'exaggerated': 'drawn larger to read'}),
                               self.report([0.3, 0.3, 0.7, 0.62], [0.3, 0.3, 0.7, 0.62], px=(600, 900)), made)
        self.assertEqual(said, [])


if __name__ == '__main__':
    unittest.main()
