"""Storyboard edits and contract (studio/storyboard.py), pure parts: the closed edit vocabulary maps the user's words to
the move's own knobs and the scene's data; the contract tolerates polish and catches a different shot."""
import unittest

from studio.common import StudioError
from studio.storyboard import apply_ops, compare

SHOT = {'camera': {'move': {'type': 'push_in', 'params': {'target': 'pump', 'to_m': 10.0, 'height_m': 2.0, 'azimuth_deg': 0.0}}},
        'scene': {'primitives': [{'id': 'pump', 'shape': 'box', 'size': [2, 2, 2], 'at': [0, 0, 1], 'material': 'm'}]}, 'titles': []}
FRAME = {'frame': 10, 'eye': [0, -10, 2], 'forward': [0, 1, 0], 'lens_mm': 35, 'focus_distance_m': 10.0, 'focus': {'pump': [0.4, 0.4, 0.6, 0.6]}}


class StoryboardOpsTest(unittest.TestCase):
    def test_words_become_knob_and_data_edits(self):
        change, said = apply_ops(SHOT, [{'op': 'camera.closer', 'factor': 0.5}, {'op': 'camera.height', 'delta_m': 1.5},
                                        {'op': 'camera.angle', 'delta_deg': 90}, {'op': 'object.move', 'id': 'pump', 'delta_m': [1, 0, 0]}])
        params = change['camera']['move']['params']
        self.assertEqual((params['to_m'], params['height_m'], params['azimuth_deg']), (5.0, 3.5, 90.0))
        self.assertEqual(change['scene']['primitives'][0]['at'], [1, 0, 1])
        self.assertEqual(len(said), 4)

    def test_closed_vocabulary(self):
        with self.assertRaises(StudioError):
            apply_ops(SHOT, [{'op': 'camera.fly_around'}])
        with self.assertRaises(StudioError) as caught:
            apply_ops({**SHOT, 'camera': {'move': {'type': 'section_push', 'params': {'back_m': 70}}}}, [{'op': 'camera.angle', 'delta_deg': 10}])
        self.assertIn("it has ['distance', 'height']", caught.exception.message)


class StoryboardContractTest(unittest.TestCase):
    def test_polish_passes_and_a_different_shot_fails(self):
        contract = {'frames': [FRAME]}
        polished = {'frames': [{**FRAME, 'eye': [0.3, -10.5, 2.2], 'lens_mm': 38, 'focus': {'pump': [0.42, 0.41, 0.61, 0.62]}}]}
        self.assertEqual(compare(contract, polished), [])
        other = {'frames': [{**FRAME, 'eye': [8, -2, 6], 'forward': [-1, 0, 0], 'focus': {'pump': [0.0, 0.1, 0.2, 0.3]}}]}
        problems = ' | '.join(compare(contract, other))
        self.assertIn('looks', problems); self.assertIn('from the approved position', problems); self.assertIn('moved in the frame', problems)


if __name__ == '__main__':
    unittest.main()
