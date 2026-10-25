"""Can an eye follow the camera (studio/camera_follow.py): one ruler for built takes and references, and the hard floor
CAMERA_JERK read from the goal row `followable` (library/shot_goals.json). 2026-10-25, F0: speed alone blocked a fast
but followable ride-along; how sharply the pace changes separated harsh takes from fly-throughs."""
import math
import unittest

from studio import camera_follow
from studio.camera_comfort import goal

FPS = 30


def series(speeds):
    """A push along x: each pair slides the picture by its speed (frame widths per frame)."""
    return [{'tx': s, 'ty': 0.0, 'ds': 0.0} for s in speeds]


def eased(cruise, frames):
    """Smooth speed-up, cruise, slow-down to rest (a fly-in that arrives)."""
    return [cruise * math.sin(math.pi * i / (frames - 1)) ** 2 for i in range(frames)]


class CameraFollowTest(unittest.TestCase):
    def limit(self):
        return goal('followable')['limits']['jerk']

    def test_a_steady_fly_in_passes_however_fast_it_cruises(self):
        # 0.03 frame widths per frame at the peak is ~0.9 frame widths per second: as fast as the fly-throughs ran
        got = camera_follow.follow(series(eased(0.03, 90) + [0.0] * 30), FPS)
        self.assertLess(got['jerk'], self.limit())
        self.assertAlmostEqual(got['arrive_s'], 3.0, delta=0.3)   # it rests once the ease ends, before the hold

    def test_a_creep_then_a_rush_is_past_the_floor(self):
        got = camera_follow.follow(series([0.001] * 30 + [0.06] * 15 + [0.001] * 30), FPS)
        self.assertGreaterEqual(got['jerk'], self.limit())
        self.assertAlmostEqual(got['jerk_at_s'], 0.75, delta=0.3)

    def test_a_rush_that_stops_dead_is_past_the_floor(self):
        got = camera_follow.follow(series(eased(0.06, 30)[:15] + [0.06] * 15 + [0.0] * 30), FPS)
        self.assertGreaterEqual(got['jerk'], self.limit())

    def test_a_direction_swing_at_one_pace_is_not_this_floor(self):
        # F0: heading swing did not separate harsh takes; a swing at an even pace is the watcher's (harsh, lost)
        pairs = [{'tx': 0.01 if (i // 15) % 2 else -0.01, 'ty': 0.0, 'ds': 0.0} for i in range(90)]
        self.assertLess(camera_follow.follow(pairs, FPS)['jerk'], self.limit())

    def test_scale_counts_as_screen_motion_and_takes_and_references_read_alike(self):
        built = [{'ok': True, 'camera': {'tx': 0.0, 'ty': 0.0, 'ds': s, 'rot': 0.0}, 'tx': 0.5} for s in eased(0.02, 60)]
        seen = [{'ok': True, 'tx': 0.0, 'ty': 0.0, 'ds': s} for s in eased(0.02, 60)]
        a = camera_follow.follow(camera_follow.camera_pairs(built), FPS)   # the camera's own share, not the picture's tx
        b = camera_follow.follow(camera_follow.picture_pairs(seen), FPS)
        self.assertEqual(a['jerk'], b['jerk'])
        self.assertGreater(a['jerk'], 0.0)

    def test_moving_stretches_and_time_moving_over_a_window(self):
        got = camera_follow.follow(series([0.0] * 30 + [0.01] * 30 + [0.0] * 30), FPS)
        self.assertEqual(len(got['moving_s']), 1)
        start, end = got['moving_s'][0]
        self.assertLess(start, 1.0); self.assertGreater(end, 2.0)
        self.assertEqual(camera_follow.moving_over(got, 2.6, 3.0), 0.0)
        self.assertGreater(camera_follow.moving_over(got, 1.0, 2.0), 0.9)

    def test_too_short_a_series_reads_no_pace_change(self):
        self.assertIsNone(camera_follow.follow(series([0.01] * 8), FPS))


if __name__ == '__main__':
    unittest.main()
