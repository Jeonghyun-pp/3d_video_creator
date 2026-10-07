"""Storyboard edits and contract (studio/storyboard.py), pure parts: the user's words reach any value of the shot (word-ops
over the move's knobs and the scene's data, set by path for the rest); the contract tolerates polish and catches a
different shot."""
import json
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

    def test_replacing_a_whole_key_reaches_the_result_and_later_ops(self):
        """2026-10-07: set /camera was reported as done while the old camera came back (it was cached before the edit)."""
        orbit = {'move': {'type': 'turntable', 'params': {'target': 'pump', 'sweep_deg': 40}, 'lens_mm': 50}}
        change, said = apply_ops(SHOT, [{'op': 'set', 'path': '/camera', 'value': orbit}, {'op': 'camera.lens', 'mm': 85}])
        self.assertEqual(change['camera']['move']['type'], 'turntable')
        self.assertEqual(change['camera']['move']['lens_mm'], 85)   # the later op edited the new camera
        scene = {'primitives': [{'id': 'tank', 'shape': 'box', 'size': [1, 1, 1], 'at': [0, 0, 0], 'material': 'm'}]}
        change, _ = apply_ops(SHOT, [{'op': 'set', 'path': '/scene', 'value': scene}, {'op': 'object.move', 'id': 'tank', 'delta_m': [0, 2, 0]}])
        self.assertEqual(change['scene']['primitives'][0]['at'], [0, 2, 0])
        self.assertEqual(SHOT['camera']['move']['type'], 'push_in')   # the caller's shot is never edited in place

    def test_every_knob_is_a_param_its_move_reads(self):
        """A knob naming a param the planner never reads would make "closer" or "higher" a silent no-op."""
        from studio.blender_ops import camera_moves_core as core
        from studio.storyboard import MOVE_KNOBS
        for move, knobs in MOVE_KNOBS.items():
            for knob in knobs.values():
                self.assertIn(knob, core.PARAMS[move], f'{move}.{knob}')

    def test_set_reaches_any_value_and_refuses_undeclared_ones(self):
        shot = json.loads(json.dumps(SHOT))
        shot['camera'] = {**shot['camera'], 'move': {'type': 'slide', 'params': {'target': 'r'}, 'lens_mm': 50}}
        change, said = apply_ops(shot, [{'op': 'set', 'path': '/camera/move/params/span', 'factor': 2.5}])
        self.assertEqual(change['camera']['move']['params']['span'], 2.0)            # default 0.8 x 2.5
        self.assertEqual(said, ['/camera/move/params/span: 0.8 → 2.0'])
        change, _ = apply_ops(shot, [{'op': 'set', 'path': '/camera/move/lens_mm', 'value': 35}])
        self.assertEqual(change['camera']['move']['lens_mm'], 35)
        for bad, words in [({'path': '/camera/move/params/spann', 'value': 2}, "nothing reads 'spann'"),
                           ({'path': '/camera/move/bogus', 'value': 1}, 'not a value this shot declares'),
                           ({'path': '/narration/text', 'value': 'x'}, 'edit path must start'),
                           ({'path': '/camera/move/params/detail', 'factor': 2}, "nothing reads 'detail'")]:
            with self.assertRaises(StudioError) as caught:
                apply_ops(shot, [{'op': 'set', **bad}])
            self.assertIn(words, caught.exception.message, bad)

    def test_takes_of_one_idea_are_flagged(self):
        from studio.storyboard import _one_idea
        move = lambda kind, **params: {'change': {'camera': {'move': {'type': kind, 'params': params}}}}  # noqa: E731
        self.assertTrue(_one_idea([move('turntable', target='r', elevation_deg=a) for a in (20, 40, 60)]))
        self.assertFalse(_one_idea([move('turntable', target='r'), move('macro_push', target='r', detail='r/p0'), move('slide', target='r')]))

    def test_unknown_op_refused_and_missing_knob_points_to_set(self):
        with self.assertRaises(StudioError):
            apply_ops(SHOT, [{'op': 'camera.fly_around'}])
        with self.assertRaises(StudioError) as caught:
            apply_ops({**SHOT, 'camera': {'move': {'type': 'section_push', 'params': {'back_m': 70}}}}, [{'op': 'camera.angle', 'delta_deg': 10}])
        self.assertIn("has no angle knob (['distance', 'height']); use set on /camera/move/params/", caught.exception.message)


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
