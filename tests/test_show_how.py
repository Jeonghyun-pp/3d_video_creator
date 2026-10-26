"""The decision chain a shot list carries (studio/show_how.py): the reel's point and the one scene that proves it (V1), each
cut's show_how - what the viewer learns, the rows on screen that prove it, the showing way, the beat's picture kept or left
with why (V2) - and checks that the fields agree with each other. Why (2026-10-26, the user): the agent must decide per
scene what the viewer must learn, what on screen proves it, where and at what scale, and which motion delivers it, each
step following from the one before."""
import unittest

from studio import decisions, reel_flow, show_how


def cut(sid, **extra):
    return {'shot_id': sid, 'purpose': 'a test cut, what it is for', 'line_ids': [], 'duration_s': 3.0, **extra}


CLIMAX = cut('s11', show=[{'at': 0.35, 'parts': ['dust_speck'], 'how': 'exterior', 'key': True, 'why': 'the speck lands'}],
             direction={'change': [{'id': 'speck_lands', 'parts': ['dust_speck'], 'kind': 'move', 'from': 0, 'to': 0.02,
                                    'at': [0.1, 0.35], 'why': 'it lands'}]})
POINT = {'says': 'one speck of dust kills a whole chip', 'proof': {'what': 'a speck lands on a chip and the chip goes dark',
                                                                    'rows': ['s11/speck_lands']},
         'opening': {'does': 'asks why the hand may not touch the box', 'why': 'the question the proof answers'}}


class PointTest(unittest.TestCase):
    def codes(self, body, silenced=()):
        errors, warnings = show_how.point_lines(body, silenced)
        return [e.split(':')[0] for e in errors], [w.split(':')[0] for w in warnings]

    def test_a_shot_list_says_its_point_and_the_scene_that_proves_it(self):
        self.assertIn('POINT_UNDECIDED', self.codes({'shots': [CLIMAX]})[0])
        self.assertEqual(self.codes({'point': POINT, 'shots': [CLIMAX]}), ([], []))

    def test_the_proof_is_a_row_the_shot_list_has(self):
        # s11 of the fab: the climax says a chip dies, and no row of it makes the chip go dark
        dark = {**POINT, 'proof': {**POINT['proof'], 'rows': ['s11/chip_goes_dark']}}
        self.assertIn('POINT_UNSHOWN', self.codes({'point': dark, 'shots': [CLIMAX]})[1])
        elsewhere = {**POINT, 'proof': {**POINT['proof'], 'rows': ['s12/speck_lands']}}
        self.assertIn('POINT_UNSHOWN', self.codes({'point': elsewhere, 'shots': [CLIMAX]})[1])
        # a show row is named by its moment
        held = {**POINT, 'proof': {**POINT['proof'], 'rows': ['s11/show@0.35']}}
        self.assertEqual(self.codes({'point': held, 'shots': [CLIMAX]}), ([], []))
        self.assertIn('POINT_UNSHOWN', self.codes({'point': {**held, 'proof': {**held['proof'], 'rows': ['s11/show@end']}},
                                                   'shots': [CLIMAX]})[1])

    def test_an_unshown_point_is_answered_like_a_flow_finding(self):
        dark = {**POINT, 'proof': {**POINT['proof'], 'rows': ['s11/chip_goes_dark']}}
        self.assertIn('POINT_UNSHOWN', reel_flow.DEPARTS)
        found = show_how.point_lines({'point': dark, 'shots': [CLIMAX]})[1]
        self.assertTrue(reel_flow.unresolved(found))
        self.assertEqual(self.codes({'point': dark, 'shots': [CLIMAX]}, silenced={'POINT_UNSHOWN'}), ([], []))   # the reel departs

    def test_the_schema_holds_the_point(self):
        body = {'point': POINT, 'shots': [CLIMAX]}
        decisions.validate_body('shotlist', body)
        with self.assertRaises(Exception):
            decisions.validate_body('shotlist', {'point': {**POINT, 'proof': {'what': 'w', 'rows': ['no-slash']}}, 'shots': [CLIMAX]})


if __name__ == '__main__':
    unittest.main()
