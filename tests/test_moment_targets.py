"""Each pictured moment frames its own subject (move.frames[i].target, from its show row's focus) and the camera passes
between subjects the way the scene's director chose (frames[i].transition {how, why}); the engine keeps the floors.
Why (2026-10-22, submarine s01): a pull-back from a stone close to the lens to the whole 83.5 m boat sized every moment on
the boat - the stone the picture drew huge came out a speck."""
import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'studio' / 'blender_ops'))
import camera_moves_core as core  # noqa: E402


class FrameTargetTest(unittest.TestCase):
    def test_a_moment_frames_its_own_subject_else_the_moves(self):
        self.assertEqual(core.frame_target({'target': 'stone'}, {'target': 'hull'}), ('stone', ['stone']))
        self.assertEqual(core.frame_target({}, {'target': 'hull'}), ('hull', ['hull']))
        self.assertEqual(core.frame_target({}, {'target': 'hull', 'parts': ['hull/a', 'hull/b']}), ('hull', ['hull/a', 'hull/b']))

    def test_a_passage_is_chosen_or_the_plain_default_said(self):
        self.assertFalse(core.transition_of({})['chosen'])
        chosen = core.transition_of({'transition': {'how': 'push', 'why': 'head for the tank', 'handover': 0.3}})
        self.assertEqual((chosen['how'], chosen['handover'], chosen['chosen']), ('push', 0.3, True))
        with self.assertRaises(ValueError):
            core.transition_of({'transition': {'how': 'teleport', 'why': 'x'}})

    def test_handover_sets_where_the_camera_is_half_way(self):
        self.assertAlmostEqual(core.handover_progress(0.2, 0.2), 0.5, places=3)   # a push: half-way early
        self.assertAlmostEqual(core.handover_progress(0.8, 0.8), 0.5, places=3)   # a hold: half-way late
        self.assertEqual(core.handover_progress(0.3, 0.5), 0.3)                    # the move's own timing
        self.assertEqual((core.handover_progress(0.0, 0.2), core.handover_progress(1.0, 0.2)), (0.0, 1.0))


class EveryMomentReaderTest(unittest.TestCase):
    def test_the_moment_solvers_take_the_moment_context(self):
        # Blender modules cannot be imported here: their source is read
        root = Path(__file__).resolve().parents[1] / 'studio' / 'blender_ops'
        moves = (root / 'camera_moves.py').read_text()
        check = (root / 'frames_check.py').read_text()
        self.assertIn('ctx = moment_context(ctx, fr)', moves)          # solve_frame
        self.assertEqual(check.count('camera_moves.moment_context(ctx, fr)'), 2)   # _in_front, _moment
        self.assertIn('frame_target(first,', (root / 'show_scene.py').read_text())   # the opening's cut direction

    def test_the_host_readers_take_the_moments_subject(self):
        from studio import frame_targets, storyboard_brief, world
        self.assertIn('frame_target(want, params)', inspect.getsource(frame_targets.moments))
        self.assertIn('frame_target(fr, params)', inspect.getsource(frame_targets.reading_checks))
        self.assertIn('frame_target', inspect.getsource(storyboard_brief._move_ids))
        self.assertIn("fr.get('target')", inspect.getsource(world.named_in))


if __name__ == '__main__':
    unittest.main()
