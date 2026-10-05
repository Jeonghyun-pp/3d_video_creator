"""Camera cues (when the camera passes a named point), the slow-head timing and fit's arrive constraint."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'studio/blender_ops'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import camera_moves_core as moves  # noqa: E402
import camera_rig_core as rig  # noqa: E402
from studio import camera_fit  # noqa: E402
from tests.test_camera_fit import STYLE, uniform  # noqa: E402

GEO = {'points': {'concourse': (0.0, 2.0, -20.0)}, 'boxes': {'road.opening': ((-6.0, -4.0, -1.0), (6.0, 4.0, 0.0))}}


class HeadTimingTest(unittest.TestCase):
    def test_absent_head_is_the_classic_curve(self):
        a = rig.timing_curve({'profile': 'burst_settle', 'burst_frac': 0.3, 'burst_share': 0.6, 'hold_frac': 0.2})
        b = rig.timing_curve({'profile': 'burst_settle', 'burst_frac': 0.3, 'burst_share': 0.6, 'hold_frac': 0.2, 'head_frac': 0})
        self.assertEqual([a(i / 50) for i in range(51)], [b(i / 50) for i in range(51)])

    def test_slow_head_creeps_then_bursts(self):
        u = rig.timing_curve({'profile': 'burst_settle', 'head_frac': 0.35, 'burst_frac': 0.25, 'burst_share': 0.65, 'hold_frac': 0.2})
        self.assertAlmostEqual(u(0.35), rig.HEAD_SHARE, places=6)
        self.assertGreater(u(0.6) - u(0.35), 0.5)  # the burst happens after the head
        values = [u(i / 200) for i in range(201)]
        self.assertTrue(all(b >= a - 1e-12 for a, b in zip(values, values[1:])))
        with self.assertRaises(ValueError):
            rig.timing_curve({'profile': 'burst_settle', 'head_frac': 0.6, 'burst_frac': 0.3, 'hold_frac': 0.2})


class CueTest(unittest.TestCase):
    def test_cues_follow_the_timing(self):
        plan = moves.plan({'type': 'dive_through', 'params': {'opening': 'road.opening', 'below': 'concourse'}}, GEO)
        dense = moves.catmull_rom(plan['waypoints'])
        fast = moves.cue_frames(dense, plan['waypoints'], plan['marks'], rig.timing_curve({'profile': 'burst_settle'}), 90)
        slow = moves.cue_frames(dense, plan['waypoints'], plan['marks'],
                                rig.timing_curve({'profile': 'burst_settle', 'head_frac': 0.4, 'burst_frac': 0.2}), 90)
        self.assertEqual(fast['wp0'], 0)
        self.assertLess(fast['mouth'], fast['inside'])
        self.assertGreater(slow['mouth'], fast['mouth'] + 15)
        linear = moves.cue_frames(dense, plan['waypoints'], plan['marks'], rig.timing_curve({'profile': 'linear'}), 91)
        u = moves.mark_progress(dense, plan['waypoints'], plan['marks'])
        self.assertLessEqual(abs(linear['mouth'] - u['mouth'] * 90), 1)  # inverse of the timing, +/- 1 frame


class ArriveTest(unittest.TestCase):
    def test_fit_holds_the_camera_until_the_reveal(self):
        profile, frames, fps = uniform(600.0), 90, 30
        start = {'profile': 'burst_settle', 'burst_frac': 0.3, 'burst_share': 0.6, 'hold_frac': 0.2}
        free, _, _ = camera_fit.fit(profile, STYLE, frames, start, fps)
        held, _, _ = camera_fit.fit(profile, STYLE, frames, start, fps, arrive=[{'u': 0.4, 'not_before_s': 1.2}])
        self.assertGreater(camera_fit.arrive_penalty(free, [{'u': 0.4, 'not_before_s': 1.2}], frames, fps), 0)
        self.assertAlmostEqual(camera_fit.arrive_penalty(held, [{'u': 0.4, 'not_before_s': 1.2}], frames, fps), 0.0, places=6)
        self.assertGreater(held.get('head_frac', 0), 0.1)


if __name__ == '__main__':
    unittest.main()
