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
        self.assertEqual(target['screen']['details'], [{'id': 'booster/engine_00', 'min_px': 32, 'why': 'the engine inside',
                                                        'source': 'shotlist'}])

    def test_a_part_taken_out_of_the_shot_list_takes_its_row_with_it(self):
        """2026-10-20 (canal s05): a part the shot list stopped asking for kept its screen.details row, and the build still
        asked for it. Hand-written rows for other ids stay; a row from before rows said their source goes when the
        earlier show listed its part."""
        target = load_shot(self.project, 's')
        hand = {'id': 'booster/fin', 'min_px': 20, 'why': 'written by hand'}
        target['screen'] = {**(target.get('screen') or {}), 'details': [hand]}
        both = [{'at': 'end', 'parts': ['booster/engine_00', 'water'], 'how': 'exterior', 'why': 'the engine and the water'}]
        decisions._carry_show(target, shot('s', ['l1'], show=both), WORLD, 1920)
        self.assertEqual([d['id'] for d in target['screen']['details']], ['booster/fin', 'booster/engine_00', 'water'])
        one = [{'at': 'end', 'parts': ['booster/engine_00'], 'how': 'exterior', 'why': 'the engine'}]
        decisions._carry_show(target, shot('s', ['l1'], show=one), WORLD, 1920)
        self.assertEqual([d['id'] for d in target['screen']['details']], ['booster/fin', 'booster/engine_00'])
        for row in target['screen']['details']:   # an older shot: its rows carry no source
            row.pop('source', None)
        decisions._carry_show(target, shot('s', ['l1'], show=[]), WORLD, 1920)   # nothing asked any more
        self.assertEqual(target['screen']['details'], [hand])


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

    def test_the_rhythm_floor_is_the_references_short_cut_never_a_flash(self):
        floor = lambda shots, p10: decisions.length_lines({'shots': shots}, self.TEXT, shot_min_s=decisions.shot_floor(p10))  # noqa: E731
        self.assertIn('the rhythm need 1.40 s', floor([shot('a', ['l1'], 1.2)], 1.4)[0])   # the reference's 10th percentile
        self.assertEqual(floor([shot('a', ['l1'], 1.0)], 0.65), [])                        # under a second it is a flash: 1.0 holds
        self.assertTrue(floor([shot('a', ['l1'], 0.8)], 0.65))
        self.assertEqual(decisions.shot_floor(None), decisions.FLASH_S)                    # no evidence: the flash floor


class OpeningGeometryTest(unittest.TestCase):
    def test_moments_merge_into_openings_that_close_before_an_outside_moment(self):
        row = lambda how, parts=('a',): {'how': how, 'parts': list(parts), 'why': 'w'}  # noqa: E731
        merged = show_core.openings([None, row('section'), row('section'), row('exterior'), row('xray', ('b',))])
        self.assertEqual([(o['how'], o['first'], o['last'], o['closes']) for o in merged], [('section', 1, 2, 3), ('xray', 4, 4, None)])

    def test_the_cut_faces_the_camera_through_the_parts_middle(self):
        # A3-2: the parts are given box by box (one box here); the plane is the deepest part's middle - one part's middle
        parts, whole = ((-0.5, -0.5, 0.0), (0.5, 0.5, 1.0)), ((-1.0, -1.0, 0.0), (1.0, 1.0, 4.0))
        placed = show_core.cutter('parts', [parts], whole, (0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        t0 = -placed['centre'][1] - placed['half'][2]        # along toward (-y): the cutter's near face
        self.assertAlmostEqual(t0, 0.0)                       # one part: the plane through its middle
        self.assertGreaterEqual(-placed['centre'][1] + placed['half'][2], 1.0)   # reaches past the subject's front
        section = show_core.cutter('subject', [parts], whole, (0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        self.assertGreater(section['half'][1], placed['half'][1])                # a section is as wide as the subject

    def test_the_cut_reaches_the_middle_of_the_deepest_part(self):
        """A3-2 (2026-10-14): the plane went through the middle of the parts' joint box; seen at a slant, the farther of
        two stacked parts stayed 94 % behind uncut skin. The plane now goes through the middle of the part whose middle
        lies deepest along the view, so every opened part has its front half cut free (two shelves of a cabinet)."""
        top, bottom = ((-0.5, -0.5, 2.0), (0.5, 0.5, 3.0)), ((-0.5, 0.5, 0.0), (0.5, 1.5, 1.0))
        whole = ((-1.0, -1.0, 0.0), (1.0, 2.0, 4.0))
        s, c = 0.3420201, 0.9396926                           # toward: from the cabinet to a camera 20 deg up on -y
        toward, up = (0.0, -c, s), (0.0, s, c)
        placed = show_core.cutter('parts', [top, bottom], whole, toward, (1.0, 0.0, 0.0), up)
        near = sum(a * b for a, b in zip(placed['centre'], toward)) - placed['half'][2]
        deepest = min(sum((lo + hi) / 2 * t for lo, hi, t in zip(*box, toward)) for box in (top, bottom))
        self.assertAlmostEqual(near, deepest, places=6)
        joint = sum((min(top[0][i], bottom[0][i]) + max(top[1][i], bottom[1][i])) / 2 * toward[i] for i in range(3))
        self.assertLess(near, joint - 0.1)                    # deeper than the joint box's middle (the old plane)


if __name__ == '__main__':
    unittest.main()


class AppliedAtTest(unittest.TestCase):
    """The opening a built scene shows at a frame, from the openings it applied (show_core.applied_at)."""

    def test_an_opening_holds_until_it_starts_to_shut_and_a_held_moment_shows_it(self):
        from studio.blender_ops.show_core import applied_at
        rows = [{'how': 'cutaway', 'parts': ['crate/lid'], 'open': [0, 10], 'close': [30, 35]},
                {'how': 'xray', 'parts': ['crate/toy'], 'open': [40, 50], 'close': None, 'opened': []}]
        self.assertIsNone(applied_at(rows, 5))                              # still opening
        self.assertEqual(applied_at(rows, 20)['how'], 'cutaway')            # held open
        self.assertIsNone(applied_at(rows, 32))                             # shutting: the outside again
        self.assertEqual(applied_at(rows, 59), {'how': 'xray', 'parts': ['crate/toy'], 'opened': []})   # held to the end
        self.assertIsNone(applied_at([], 10))


class HeldPartsTest(unittest.TestCase):
    """What an opening keeps whole: only what the moments it is open through show (show_core.held_parts)."""

    def rows(self, *hows):
        return [None if row is None else {'how': row[0], 'parts': [f'kettle/{p}' for p in row[1]], 'why': ''} for row in hows]

    def test_a_section_after_a_closed_start_cuts_the_skin_the_start_showed(self):
        rows = self.rows(('exterior', ['shell']), ('section', ['element']))
        shown = [['kettle/shell'], ['kettle/element']]
        opening = show_core.openings(rows)[0]
        self.assertEqual(show_core.held_parts(opening, shown), ['kettle/element'])

    def test_a_held_opening_keeps_what_a_later_moment_shows(self):
        rows = self.rows(None, ('section', ['pump']), ('cutaway', ['pipe']))
        shown = [['kettle/shell'], ['kettle/pump'], ['kettle/pipe']]
        first = show_core.openings(rows)[0]
        self.assertEqual(show_core.held_parts(first, shown), ['kettle/pipe', 'kettle/pump'])

    def test_an_opening_that_closes_does_not_keep_what_comes_after(self):
        rows = self.rows(('section', ['pump']), ('exterior', ['shell']))
        shown = [['kettle/pump'], ['kettle/shell']]
        opening = show_core.openings(rows)[0]
        self.assertEqual(opening['closes'], 1)
        self.assertEqual(show_core.held_parts(opening, shown), ['kettle/pump'])
