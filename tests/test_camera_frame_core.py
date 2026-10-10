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

    def test_nothing_visible_is_unsolved(self):
        with self.assertRaises(ValueError) as caught:
            core.solve(lambda p: [], (0, -5, 1), (0, 0), {'height_share': 0.5, 'centre': [0.5, 0.5]}, 50, ASPECT)
        self.assertIn('FRAME_UNSOLVED', str(caught.exception))

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
