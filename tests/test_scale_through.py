"""A dive through scales (handover scale_through, studio/blender_ops/scale_through_core.py): cut A pushes into a thing,
cut B - the scene at the next smaller scale - starts where A left it, and the engine measures the join on the carried
thing (place on screen, size, zoom rate). At a joined end the camera does not arrive: the end that hands over keeps its
cruise, the end that receives starts at the rate it was handed.
Why (2026-10-26): one metric scene holds one scale; the engine could fly only inside one."""
import math
import unittest

from studio.blender_ops import scale_through_core as st

FPS = 30


def push(frames, share0, rate, centre=(0.5, 0.5), start=0):
    """Samples of a thing growing at `rate` (ln size per second) from share0, centred."""
    return [[start + f, centre[0], centre[1], share0 * math.exp(rate * f / FPS)] for f in range(frames)]


class JoinMeasureTest(unittest.TestCase):
    def test_a_carried_thing_at_the_same_place_size_and_rate_is_one_push(self):
        tail = push(60, 0.1, 0.8)
        last = tail[-1][3] * math.exp(0.8 / FPS)
        head = push(60, last, 0.8)
        got = st.join(tail, head, FPS, 9 / 16)
        self.assertEqual(got['problems'], [])
        self.assertAlmostEqual(got['speed_ratio'], 1.0, places=3)
        self.assertLess(got['size_off'], 1e-3)

    def test_the_place_on_screen_is_read_in_frame_heights(self):
        tail = push(30, 0.2, 0.8)
        head = push(30, 0.2 * math.exp(0.8 * 30 / FPS), 0.8, centre=(0.5 + 0.06, 0.5))   # 6 % of the width = 3.4 % of the height
        self.assertEqual(st.join(tail, head, FPS, 9 / 16)['problems'], [])
        head = push(30, 0.2 * math.exp(0.8 * 30 / FPS), 0.8, centre=(0.5, 0.56))           # 6 % of the height
        got = st.join(tail, head, FPS, 9 / 16)
        self.assertEqual(len(got['problems']), 1)
        self.assertIn('frame height', got['problems'][0])

    def test_a_push_that_changes_speed_or_stops_at_the_cut_is_off(self):
        tail = push(30, 0.2, 0.8)
        size = 0.2 * math.exp(0.8 * 30 / FPS)
        self.assertIn('runs at', ' '.join(st.join(tail, push(30, size, 1.2), FPS, 9 / 16)['problems']))
        self.assertEqual(st.join(tail, push(30, size, 0.9), FPS, 9 / 16)['problems'], [])   # within x1.25
        still = push(30, 0.2, 0.0)
        self.assertIn('no longer pushes in', ' '.join(st.join(still, push(30, 0.2, 0.8), FPS, 9 / 16)['problems']))
        self.assertIn('does not push in', ' '.join(st.join(tail, push(30, size, -0.5), FPS, 9 / 16)['problems']))

    def test_a_size_jump_at_the_cut_is_off_and_an_unmeasured_side_is_said(self):
        tail = push(30, 0.2, 0.8)
        got = st.join(tail, push(30, 0.2 * math.exp(0.8) * 1.4, 0.8), FPS, 9 / 16)
        self.assertIn('stands', ' '.join(got['problems']))
        self.assertEqual(st.join(tail, [], FPS, 9 / 16)['problems'], ['not measured on both sides'])

    def test_the_rate_reads_only_the_join_window(self):
        # a camera that eases in and then pushes evenly: the end's rate is the even push, not the shot's mean
        rows = [[f, 0.5, 0.5, 0.1 * math.exp(0.8 * max(0, f - 30) / FPS)] for f in range(90)]
        self.assertAlmostEqual(st.state(rows, FPS, 'end')['rate'], 0.8, places=3)
        self.assertAlmostEqual(st.state(rows, FPS, 'start')['rate'], 0.0, places=3)


class ThroughTimingTest(unittest.TestCase):
    def test_the_end_that_hands_over_keeps_its_cruise_to_the_last_frame(self):
        g, cruise = st.through_curve(0.0, 0.7, 0.2, keeps_moving=True, seconds=3.0)
        self.assertAlmostEqual(g(0.0), 0.0)
        self.assertAlmostEqual(g(1.0), 1.0, places=6)
        end_speed = (g(1.0) - g(0.99)) / 0.01
        self.assertAlmostEqual(end_speed, cruise, places=3)
        self.assertLess((g(0.01) - g(0.0)) / 0.01, 0.1 * cruise)     # from rest at its own start

    def test_the_end_that_receives_starts_at_the_rate_it_was_handed(self):
        g, cruise = st.through_curve(1.6, 0.7, 0.2, keeps_moving=False, seconds=3.0)
        self.assertAlmostEqual((g(0.001) - g(0.0)) / 0.001, 1.6, places=2)
        self.assertAlmostEqual(g(0.7), 1.0, places=6)
        self.assertAlmostEqual(g(0.85), 1.0)                         # arrived and held from settle_at
        for i in range(100):                                         # never back
            self.assertLessEqual(g(i / 100), g((i + 1) / 100) + 1e-12)
        g, _ = st.through_curve(1.0, 0.7, 0.2, keeps_moving=True, seconds=3.0)   # handed on as well: no arrival
        self.assertGreater((g(1.0) - g(0.99)) / 0.01, 0.5)

    def test_the_received_pace_holds_while_the_join_is_read(self):
        # 2026-10-26 smoke: ramping at once from the received pace read as a push 1.33x faster just after the cut
        g, cruise = st.through_curve(0.84, 0.7, 0.21, keeps_moving=False, seconds=3.0)
        held = st.JOIN_S / 3.0
        self.assertAlmostEqual((g(held) - g(0.0)) / held, 0.84, places=6)
        self.assertGreater(cruise, 0.84)

    def test_a_ramp_shorter_than_an_eye_window_is_widened(self):
        g, _ = st.through_curve(0.0, 1.0, 0.0, keeps_moving=True, seconds=2.0)   # a linear director timing
        self.assertLess((g(0.01) - g(0.0)) / 0.01, 0.5)                          # still eased over a quarter second

    def test_a_handed_rate_the_cut_cannot_absorb_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            st.through_curve(12.0, 0.7, 0.3, keeps_moving=False, seconds=3.0)
        self.assertIn('SCALE_THROUGH_NO_ROOM', str(caught.exception))

    def test_rates_are_screen_rates(self):
        span, seconds = math.log(8.0), 3.0               # from 8x the landing distance, over 3 s
        a = st.start_speed(0.8, seconds, span)
        self.assertAlmostEqual(a * span / seconds, 0.8)   # what it starts at, back in ln size per second
        self.assertAlmostEqual(st.end_rate(1.25, span, seconds), 1.25 * span / seconds)
        with self.assertRaises(ValueError):
            st.start_speed(0.8, seconds, 0.0)

    def test_the_directors_ease_is_read_from_the_timing(self):
        from studio.recipe_moves import settle_timing
        from studio.blender_ops.camera_rig_core import timing_curve
        settle, ramp = st.curve_shape(timing_curve(settle_timing(0.7, 0.4)))
        self.assertAlmostEqual(settle, 0.7, delta=0.01)
        self.assertAlmostEqual(ramp, 0.7 * 0.6 / 2, delta=0.03)


class ReachTest(unittest.TestCase):
    def test_the_receiving_camera_starts_where_the_carried_thing_stands_as_tall_as_it_was_handed(self):
        share_at = lambda d: 1.0 / d   # noqa: E731
        self.assertAlmostEqual(st.reach_for_share(share_at, 0.25, 1.0, 2.0), 4.0, places=4)
        with self.assertRaises(ValueError) as caught:
            st.reach_for_share(share_at, 2.0, 1.0, 10.0)
        self.assertIn('SCALE_THROUGH_NO_ROOM', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
