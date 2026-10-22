"""The framing solver places the camera so the focus shows at the asked size and place (perspective, unclipped)."""
import math
import unittest

from studio.blender_ops import camera_frame_core as core

ASPECT = 9 / 16


def box_points(lo, hi, n=6):
    """Points on the surface of a box (a stand-in for sampled mesh points)."""
    out = []
    for i in range(n + 1):
        for j in range(n + 1):
            u, v = i / n, j / n
            x, y, z = (lo[0] + (hi[0] - lo[0]) * u), (lo[1] + (hi[1] - lo[1]) * v), (lo[2] + (hi[2] - lo[2]) * u)
            out += [(x, y, lo[2]), (x, y, hi[2]), (x, lo[1], z), (x, hi[1], z),
                    (lo[0], y, lo[2] + (hi[2] - lo[2]) * u), (hi[0], y, lo[2] + (hi[2] - lo[2]) * u)]
    return out


def front_and_top(lo, hi, view):
    """What a camera on the -Y side, above, sees of a box: its front face and its top (no back, no bottom)."""
    pts = [p for p in box_points(lo, hi) if abs(p[1] - lo[1]) < 1e-9 or abs(p[2] - hi[2]) < 1e-9]
    return lambda position: pts


class SolveTest(unittest.TestCase):
    def check(self, result, height, centre):
        self.assertAlmostEqual(result['achieved']['height_share'], height, delta=height * core.SIZE_TOL)
        if not result['achieved']['band']:   # a band runs across: its sideways place is not solved
            self.assertAlmostEqual(result['achieved']['centre'][0], centre[0], delta=core.PLACE_TOL)
        self.assertAlmostEqual(result['achieved']['centre'][1], centre[1], delta=core.PLACE_TOL)

    def test_a_layer_stack_seen_from_above_fills_the_asked_height_at_the_asked_place(self):
        lo, hi = (-2.5, -2.5, 3.0), (2.5, 2.5, 3.35)       # a 5 m deep floor build-up: from 20 deg its top shows too
        result = core.solve(front_and_top(lo, hi, (20, -16)), (0, -12, 6), (20, -16), {'height_share': 0.65, 'centre': [0.5, 0.4]}, 50, ASPECT)
        self.check(result, 0.65, [0.5, 0.4])
        self.assertTrue(result['achieved']['band'])         # wider than the frame: allowed to run off the sides

    def test_a_pose_under_the_support_is_refused_not_lifted(self):
        """A crate on a table top at z 1 m, the focus looked up at: every pose that frames it is under the table."""
        crate = box_points((-0.2, -0.2, 1.0), (0.2, 0.2, 1.4))
        floor = lambda position: (1.0, 0.1)   # noqa: E731 - the table top, 0.1 m margin
        target = {'height_share': 0.4, 'centre': [0.5, 0.5]}
        with self.assertRaises(core.FrameSolveError) as caught:
            core.solve(lambda p: crate, (0, -4, 0.5), (-20, 0), target, 35, ASPECT, floor=floor)
        self.assertEqual(caught.exception.code, 'FRAME_BELOW_SUPPORT')
        self.assertEqual((caught.exception.data['top'], caught.exception.data['margin']), (1.0, 0.1))
        self.assertLess(caught.exception.data['z'], 1.1)     # the pose as reached: never lifted onto the table
        result = core.solve(lambda p: crate, (0, -4, 2.5), (20, 0), target, 35, ASPECT, floor=floor)   # from above it solves
        self.assertGreater(result['position'][2], 1.1)

    def test_everything_behind_the_camera_is_a_frame_error(self):
        """Every point behind the camera at every step (a focus that follows the camera): the solve says so, it does not
        break on a measure it never made."""
        forward = core.basis(0, 0)[0]
        behind = lambda position: [core._sub(position, core._mul(forward, 1.0))]   # noqa: E731
        with self.assertRaises(core.FrameSolveError) as caught:
            core.solve(behind, (0, -5, 0), (0, 0), {'height_share': 0.5, 'centre': [0.5, 0.5]}, 35, ASPECT)
        self.assertEqual(caught.exception.code, 'FRAME_UNSOLVED')
        self.assertIn('every step stood inside or past the focus', str(caught.exception))

    def test_starting_inside_or_far_off_screen_still_converges(self):
        lo, hi = (-0.3, -0.3, 0.0), (0.3, 0.3, 0.6)
        see = lambda position: box_points(lo, hi)            # noqa: E731
        for start in ((0, -0.1, 0.3), (4, -3, 2), (0, -40, 0.3)):   # inside it; aimed past it; tiny in the distance
            self.check(core.solve(see, start, (10, 0), {'height_share': 0.5, 'centre': [0.5, 0.45]}, 50, ASPECT), 0.5, [0.5, 0.45])

    def test_an_object_fits_whole_when_the_height_would_push_it_off_the_sides(self):
        lo, hi = (-1.0, -0.2, 0.0), (1.0, 0.2, 0.9)          # wide, but not a band: it must fit the narrow frame
        result = core.solve(lambda p: box_points(lo, hi), (0, -10, 0.5), (0, 0), {'height_share': 0.8, 'centre': [0.5, 0.5]}, 50, ASPECT)
        self.assertEqual(result['limited_by'], 'width')
        self.assertLessEqual(result['achieved']['width_share'], 1 - 2 * core.MARGIN + 0.01)
        self.assertLess(result['achieved']['height_share'], 0.8)

    def test_a_long_thing_the_shot_lets_run_off_the_sides_stands_as_tall_as_asked(self):
        """2026-10-22 (submarine): framed whole, an 83.5 m hull in a 9:16 frame stays a strip; the shot's cut choice
        fits its height, also seen obliquely with its near end past the lens."""
        lo, hi = (-10.0, -4.0, -4.0), (10.0, 4.0, 4.0)        # 2.5:1 - not a band: framed whole by default
        see = lambda p: box_points(lo, hi, n=12)               # noqa: E731
        whole = core.solve(see, (0, -60, 0), (0, 0), {'height_share': 0.7, 'centre': [0.5, 0.5]}, 24, ASPECT)
        self.assertEqual(whole['limited_by'], 'width')
        for view, start in (((0, 0), (0, -60, 0)), ((-20, 35), (30, -45, -20))):
            cut = core.solve(see, start, view, {'height_share': 0.7, 'centre': [0.5, 0.5], 'cut': True}, 24, ASPECT)
            self.assertAlmostEqual(cut['achieved']['height_share'], 0.7, delta=0.7 * core.SIZE_TOL)
            self.assertEqual(cut['mode'], 'cut')   # its middle held at the place asked, only the distance solved
            self.assertTrue(cut['achieved']['band'])
            self.assertGreater(cut['achieved']['height_share'], 2 * whole['achieved']['height_share'])

    def test_a_long_cut_focus_seen_obliquely_settles(self):
        """2026-10-22 r3: an 84 m hull let run off the frame, seen from steep and oblique views, never settled while it was
        steered sideways like a whole focus."""
        see = lambda p: box_points((-42.0, -5.0, -5.0), (42.0, 5.0, 5.0), n=16)   # noqa: E731
        for view, height in (((4, 25), 0.9), ((5, 20), 0.63), ((-25, 30), 0.68), ((4, -25), 0.9)):
            e, a = math.radians(view[0]), math.radians(view[1])
            start = (math.sin(a) * math.cos(e) * 200, -math.cos(a) * math.cos(e) * 200, math.sin(e) * 200)
            got = core.solve(see, start, view, {'height_share': height, 'centre': [0.5, 0.395], 'cut': True}, 20, ASPECT)
            self.assertAlmostEqual(got['achieved']['height_share'], height, delta=height * core.SIZE_TOL)

    def test_nothing_visible_is_unsolved(self):
        with self.assertRaises(ValueError) as caught:
            core.solve(lambda p: [], (0, -5, 1), (0, 0), {'height_share': 0.5, 'centre': [0.5, 0.5]}, 50, ASPECT)
        self.assertIn('FRAME_UNSOLVED', str(caught.exception))

    def test_rolled_offcentre_focus_matches_the_baked_camera_projection(self):
        from studio.blender_ops import camera_rig_core as rig
        points = box_points((-1.4, -.7, -.5), (1.4, .7, .5))
        target = {'height_share': .16, 'centre': [.665, .78]}
        view = (12, -20)
        for roll in (-18, 22):
            with self.subTest(roll=roll):
                result = core.solve(lambda p: points, (0, -15, 3), view, target, 35, ASPECT, roll_deg=roll)
                q = rig.look_rotation(result['position'], result['aim'], math.radians(roll))
                tans = rig.view_tangents(35, 36, 'AUTO', 1080, 1920)
                screen = [rig.project(q, result['position'], p, *tans) for p in points]
                measured = core.measure(screen, ASPECT)
                self.assertAlmostEqual(measured['height'], .16, delta=.16 * core.SIZE_TOL)
                for actual, wanted in zip(measured['centre'], target['centre']):
                    self.assertAlmostEqual(actual, wanted, delta=core.PLACE_TOL)
                # Applying the roll only after an unrolled solve reproduces the reported s10 regression.
                unrolled = core.solve(lambda p: points, (0, -15, 3), view, target, 35, ASPECT)
                wrong_q = rig.look_rotation(unrolled['position'], unrolled['aim'], math.radians(roll))
                wrong = core.measure([rig.project(wrong_q, unrolled['position'], p, *tans) for p in points], ASPECT)
                self.assertGreater(abs(wrong['centre'][0] - target['centre'][0]), .1)

    def test_detail_thickness_in_pixels_follows_the_columns_not_the_tilted_box(self):
        tilted = [(x / 100, 0.3 + x / 400 + dy, 1.0) for x in range(101) for dy in (0.0, 0.02)]   # a 0.02-thick line rising 0.25
        self.assertAlmostEqual(core.detail_px(tilted, 1920), 0.02 * 1920, delta=0.006 * 1920)

    def test_projection_matches_a_pinhole(self):
        f, r, u = core.basis(0, 0)
        self.assertEqual([round(v, 9) for v in f], [0, 1, 0])
        tans = core.tangents(50, ASPECT)
        (x, y, z), = core.project([(0.0, 10.0, tans[1] * 10)], (0, 0, 0), (f, r, u), tans)[0]
        self.assertAlmostEqual((x, y, z), (0.5, 0.0, 10.0))  # a point at the top edge of the view


if __name__ == '__main__':
    unittest.main()


class TiltTest(unittest.TestCase):
    """2026-10-11, s06: the picture stands the booster upright, the scene flies it 15 deg over - the camera rolls to it."""
    def test_inverted_long_axis_keeps_the_same_lean_and_small_roll(self):
        eye, aim = (0.0, -10.0, 0.0), (0.0, 0.0, 0.0)
        for lean_deg in (0.0, 15.0, -18.0, 89.0):
            axis = (math.sin(math.radians(lean_deg)), 0.0, math.cos(math.radians(lean_deg)))
            for sign in (1, -1):
                with self.subTest(lean_deg=lean_deg, sign=sign):
                    direction = tuple(sign * v for v in axis)
                    self.assertAlmostEqual(core.screen_tilt_deg(eye, aim, direction), lean_deg)
                    roll = core.roll_for_tilt(eye, aim, direction, 5.0)
                    self.assertAlmostEqual(core.screen_tilt_deg(eye, aim, direction, roll), 5.0)
        self.assertAlmostEqual(core.roll_for_tilt(eye, aim, (0.0, 0.0, -1.0), 0.0), 0.0)
        axis = (math.sin(math.radians(89)), 0.0, math.cos(math.radians(89)))
        self.assertAlmostEqual(core.roll_for_tilt(eye, aim, axis, -86.0), 5.0)

    def test_lean_on_screen_and_the_roll_that_matches_a_picture(self):
        eye, aim = (0.0, -10.0, 0.0), (0.0, 0.0, 0.0)
        lean = (math.sin(math.radians(15)), 0.0, math.cos(math.radians(15)))
        self.assertAlmostEqual(core.screen_tilt_deg(eye, aim, (0.0, 0.0, 1.0)), 0.0)
        self.assertAlmostEqual(core.screen_tilt_deg(eye, aim, lean), 15.0)
        roll = core.roll_for_tilt(eye, aim, lean, 0.0)
        self.assertAlmostEqual(roll, -15.0)
        self.assertAlmostEqual(core.screen_tilt_deg(eye, aim, lean, roll), 0.0)
        self.assertIsNone(core.screen_tilt_deg(eye, aim, (0.0, 1.0, 0.0)))   # pointing along the view: no lean to read

    def test_roll_sign_is_the_rigs(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(core.__file__).parent))
        import camera_rig_core as rig
        eye, aim = (3.0, -10.0, 2.0), (0.0, 0.0, 1.0)
        for roll in (12.0, -20.0):
            q = rig.look_rotation(eye, aim, math.radians(roll))
            right, up = rig.q_rotate(q, (1, 0, 0)), rig.q_rotate(q, (0, 1, 0))
            seen = math.degrees(math.atan2(right[2], up[2]))   # the world's vertical as the rolled rig camera sees it
            self.assertAlmostEqual(seen, core.screen_tilt_deg(eye, aim, (0.0, 0.0, 1.0), roll), places=6)
