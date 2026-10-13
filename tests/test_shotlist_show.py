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

    def test_the_shot_carries_show_handover_beats_and_details(self):
        target = load_shot(self.project, 's')
        entry = shot('s', ['l1', 'l2'], show=[{'at': 'end', 'parts': ['booster/engine_00'], 'how': 'xray', 'why': 'the engine inside'}],
                     handover={'kind': 'continue', 'why': 'the descent runs on'})
        decisions._carry_show(target, entry, WORLD, 1920)
        self.assertEqual((target['beat_ids'], target['handover']['kind'], target['show'][0]['how']), (['b1'], 'continue', 'xray'))
        self.assertEqual(target['screen']['details'], [{'id': 'booster/engine_00', 'min_px': 32, 'why': 'the engine inside'}])


if __name__ == '__main__':
    unittest.main()
