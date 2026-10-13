"""The shot list decides what each moment shows and how, before any picture (studio/blender_ops/show_core.py), and is
held to the story: shots follow the script, a shot's beats are its lines' beats, what the lines name is on screen while
they are said, and the same things on screen for long read as one shot (studio/decisions.py)."""
from pathlib import Path
import tempfile
import unittest

from studio import decisions
from studio.blender_ops import show_core
from studio.common import write_json
from studio.project import init_project, load_shot

LINES = ['l1', 'l2', 'l3']
WORLD = {'beats': [{'beat_id': 'b1', 'line_ids': ['l1', 'l2']}, {'beat_id': 'b2', 'line_ids': ['l3']}],
         'elements': [{'id': 'rocket', 'function': 'subject'}, {'id': 'pad', 'function': 'set'},
                      {'id': 'crowd', 'function': 'agent', 'line_ids': ['l3']}]}


def shot(shot_id, lines, seconds=3.0, elements=('rocket',), **extra):
    return {'shot_id': shot_id, 'purpose': 'what it is for', 'line_ids': list(lines), 'duration_s': seconds, 'elements': list(elements), **extra}


class StoryTest(unittest.TestCase):
    def test_shots_follow_the_script_and_their_beats(self):
        errors, _ = decisions.story_lines({'shots': [shot('a', ['l2']), shot('b', ['l1']), shot('c', ['l3'])]}, LINES, WORLD)
        self.assertTrue(any('SHOTS_OUT_OF_ORDER' in e for e in errors))
        errors, _ = decisions.story_lines({'shots': [shot('a', ['l1', 'l2'], beat_ids=['b2']), shot('c', ['l3'])]}, LINES, WORLD)
        self.assertTrue(any('BEAT_MISMATCH' in e for e in errors))
        errors, _ = decisions.story_lines({'shots': [shot('a', ['l1', 'l2'], beat_ids=['b1']), shot('c', ['l3'], elements=('crowd',))]},
                                          LINES, WORLD)
        self.assertEqual(errors, [])
        errors, _ = decisions.story_lines({'shots': [shot('h', [], beat_ids=['b1']), shot('a', ['l1', 'l2']), shot('c', ['l3'])]}, LINES, WORLD)
        self.assertEqual(errors, [])                                          # a wordless shot names its own beat

    def test_what_the_lines_name_is_shown_and_one_thing_is_not_shown_for_long(self):
        _, warnings = decisions.story_lines({'shots': [shot('a', ['l1', 'l2']), shot('c', ['l3'])]}, LINES, WORLD)
        self.assertTrue(any('STORY_ELEMENT_UNSHOWN' in w and 'crowd' in w for w in warnings))
        long = {'shots': [shot('a', ['l1'], 3), shot('b', ['l2'], 3), shot('c', ['l3'], 3, elements=('rocket', 'crowd'))]}
        self.assertFalse(any('SUBJECT_MONOTONE' in w for w in decisions.story_lines(long, LINES, WORLD)[1]))   # 6 s
        long['shots'][1]['duration_s'] = 4.5
        self.assertTrue(any('SUBJECT_MONOTONE' in w for w in decisions.story_lines(long, LINES, WORLD)[1]))    # 7.5 s


class ShowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(init_project('show', {'request': 'r', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, self.tmp.name)['project_path'])
        write_json(self.project / 'subjects/booster/spec.json', {'builders': [{'part_id': 'engine_00'}, {'part_id': 'body'}]})

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_shown_part_must_be_modelled_or_a_world_element(self):
        body = {'shots': [shot('a', ['l1'], show=[{'at': 'end', 'parts': ['booster/engine_00', 'pad', 'booster/valve'], 'how': 'section',
                                                    'why': 'the engine inside'}])]}
        errors = decisions.show_lines(self.project, body, WORLD)
        self.assertEqual(len(errors), 1)
        self.assertIn("['booster/valve']", errors[0])

    def test_an_earlier_shot_list_is_read_as_it_meant(self):
        old = shot('a', ['l1'], direction={'camera': 'push in slowly', 'reveal': 'section_cut', 'transition': 'match cut on the burn'})
        self.assertEqual(show_core.show_of(old)[0]['how'], 'section')
        self.assertEqual(show_core.handover_of(old)['kind'], 'match_action')
        self.assertEqual(show_core.show_of(shot('b', ['l1'])), [])
        words = lambda t: (show_core.legacy_handover({'transition': t}) or {}).get('kind')  # noqa: E731
        self.assertEqual([words('discontinuous jump'), words('s21이 이어받아 시작'), words('cut away')], [None, 'continue', None])

    def test_the_shot_carries_show_handover_beats_and_details(self):
        target = load_shot(self.project, 's')
        entry = shot('s', ['l1', 'l2'], show=[{'at': 'end', 'parts': ['booster/engine_00'], 'how': 'xray', 'why': 'the engine inside'}],
                     handover={'kind': 'continue', 'why': 'the descent runs on'})
        decisions._carry_show(target, entry, WORLD, 1920)
        self.assertEqual((target['beat_ids'], target['handover']['kind'], target['show'][0]['how']), (['b1'], 'continue', 'xray'))
        self.assertEqual(target['screen']['details'], [{'id': 'booster/engine_00', 'min_px': 32, 'why': 'the engine inside'}])


class NumberTest(unittest.TestCase):
    def test_a_stage_name_is_not_a_number_said_aloud(self):
        self.assertFalse(decisions._digits('로켓 1단 하나를 끝까지 따라가 보죠.'))
        self.assertTrue(decisions._digits('1단 하나가 37번 날았습니다.'))
        self.assertTrue(decisions._digits('높이 12.2미터'))


class LengthTest(unittest.TestCase):
    TEXT = {'l1': '로켓이 섭니다', 'l2': '아주 긴 문장이 이어지고 이어지고 또 이어집니다 끝까지 말해야 합니다'}

    def test_a_shot_is_never_shorter_than_its_floors(self):
        floor = lambda shots: decisions.length_lines({'shots': shots}, self.TEXT)  # noqa: E731
        self.assertEqual(floor([shot('a', ['l1'], 1.0)]), [])
        self.assertTrue(floor([shot('a', ['l1'], 0.8)])[0].startswith('SHOT_TOO_SHORT: a is 0.80 s, the rhythm need 1.00 s'))
        self.assertEqual(floor([shot('h', [], 0.8, role='hook')]), [])                                  # a hook cuts fast
        long = floor([shot('a', ['l2'], 2.0), shot('b', [], 3.0)])
        self.assertIn('its words', long[0]); self.assertIn('join it with b', long[0])
        opened = floor([shot('a', [], 1.2, show=[{'at': 'end', 'parts': [], 'how': 'section', 'why': 'w'},
                                                 {'at': 0.5, 'parts': [], 'how': 'xray', 'why': 'w'}])])
        self.assertIn('its openings need 1.80 s', opened[0])
        held = [{'at': 'start', 'parts': [], 'how': 'section', 'why': 'w'}, {'at': 'end', 'parts': [], 'how': 'section', 'why': 'w'}]
        self.assertEqual(floor([shot('a', [], 1.5, show=held)]), [])          # one opening held through, counted once


class OpeningGeometryTest(unittest.TestCase):
    def test_moments_merge_into_openings_that_close_before_an_outside_moment(self):
        row = lambda how, parts=('a',): {'how': how, 'parts': list(parts), 'why': 'w'}  # noqa: E731
        merged = show_core.openings([None, row('section'), row('section'), row('exterior'), row('xray', ('b',))])
        self.assertEqual([(o['how'], o['first'], o['last'], o['closes']) for o in merged], [('section', 1, 2, 3), ('xray', 4, 4, None)])

    def test_the_cut_faces_the_camera_through_the_parts_middle(self):
        parts, whole = ((-0.5, -0.5, 0.0), (0.5, 0.5, 1.0)), ((-1.0, -1.0, 0.0), (1.0, 1.0, 4.0))
        placed = show_core.cutter('parts', parts, whole, (0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        t0 = -placed['centre'][1] - placed['half'][2]        # along toward (-y): the cutter's near face
        self.assertAlmostEqual(t0, 0.0)                       # the plane through the parts' middle
        self.assertGreaterEqual(-placed['centre'][1] + placed['half'][2], 1.0)   # reaches past the subject's front
        section = show_core.cutter('subject', parts, whole, (0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        self.assertGreater(section['half'][1], placed['half'][1])                # a section is as wide as the subject


if __name__ == '__main__':
    unittest.main()
