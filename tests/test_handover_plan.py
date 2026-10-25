"""Cuts joined at the plan, not after the build (2026-10-25, plan M5): a cut the one before hands over 'continue' starts,
by the engine's default, looking the way the cut before ends (its azimuth, said as carried); the director may say
another with the move's settings. A built join that snaps back (JOIN_REVERSED) or breaks a planned continue
(HANDOVER_BROKEN) is a flow finding: fixed, or departed with why (read with the picture), as VIEW_MONOTONE is."""
import unittest

from studio import reel_flow
from studio.decisions import move_lines
from studio.recipe_moves import end_azimuth, recipe_move


def entry(sid, settings, handover=None, recipe='pass_by'):
    move = {'recipe': recipe, 'reveals': 'the hull', 'key_at': 0.5, 'why': 'the hull goes by', 'settings': settings}
    row = {'shot_id': sid, 'frame': {'focus': ['boat']}, 'direction': {'move': move},
           'show': [{'at': 'start', 'parts': ['boat'], 'how': 'exterior', 'why': 'the boat'}]}
    if handover:
        row['handover'] = {'kind': handover, 'why': 'the same motion runs on'}
    return row


class CarryTest(unittest.TestCase):
    def test_a_continued_cut_may_leave_its_side_to_the_cut_before(self):
        a = entry('a', {'height_share': 0.5, 'azimuth_deg': 40}, handover='continue')
        b = entry('b', {'height_share': 0.5})
        self.assertEqual(move_lines({'shots': [a, b]}), [])
        cut = entry('a', {'height_share': 0.5, 'azimuth_deg': 40}, handover='contrast')
        self.assertTrue(any('MOVE_FRAMING_UNSAID' in l for l in move_lines({'shots': [cut, b]})))

    def test_the_carried_side_is_the_end_of_the_cut_before_and_said(self):
        self.assertEqual(end_azimuth({'settings': {'azimuth_deg': 40}}), 40)
        moments = {'moments': [{'at': 0, 'size': 0.3, 'view': {'elevation_deg': 5, 'azimuth_deg': 10}, 'why': 'w1'},
                               {'at': 1, 'size': 0.5, 'view': {'elevation_deg': 5, 'azimuth_deg': 70}, 'why': 'w2'}]}
        self.assertEqual(end_azimuth(moments), 70)
        made = recipe_move(entry('b', {'height_share': 0.5}), carry=40)
        self.assertEqual(made['chosen']['azimuth_deg'], {'value': 40, 'by': 'carried'})
        said = recipe_move(entry('b', {'height_share': 0.5, 'azimuth_deg': 120}), carry=40)
        self.assertEqual(said['chosen']['azimuth_deg']['by'], 'director')   # the director's own side wins


class JoinFlowTest(unittest.TestCase):
    def test_a_join_that_snaps_back_or_breaks_a_continue_is_a_flow_finding(self):
        rows = [{'shot_id': 'a', 'len_s': 2.0, 'handover': 'continue'}, {'shot_id': 'b', 'len_s': 2.0, 'join_in': 'reverse'}]
        codes = [l.split(':')[0] for l in reel_flow.lines(rows)]
        self.assertIn('JOIN_REVERSED', codes)
        self.assertIn('HANDOVER_BROKEN', codes)
        self.assertTrue({'JOIN_REVERSED', 'HANDOVER_BROKEN'} <= set(reel_flow.DEPARTS))
        departed = [rows[0], {**rows[1], 'departs': ['join_reversed', 'handover_broken']}]
        self.assertFalse([l for l in reel_flow.lines(departed) if l.startswith(('JOIN_REVERSED', 'HANDOVER_BROKEN'))])
        turned = [{'shot_id': 'a', 'len_s': 2.0, 'handover': 'contrast'}, {'shot_id': 'b', 'len_s': 2.0, 'join_in': 'turn'}]
        self.assertFalse([l for l in reel_flow.lines(turned) if l.startswith(('JOIN_REVERSED', 'HANDOVER_BROKEN'))])


if __name__ == '__main__':
    unittest.main()
