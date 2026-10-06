"""fillet_core: rounded corners are tangent arcs of the given radius, and radii that cannot fit are refused."""
import math
import unittest

from studio.blender_ops.fillet_core import round_corners, rounded_rect


def area(pts):
    return 0.5 * abs(sum(pts[i - 1][0] * pts[i][1] - pts[i][0] * pts[i - 1][1] for i in range(len(pts))))


def arc_area_error(segments):   # an inscribed polygon arc misses this fraction of a full circle's area
    return 1 - segments * math.sin(2 * math.pi / segments) / (2 * math.pi)


class FilletCoreTest(unittest.TestCase):
    def test_rounded_square_area_and_extent(self):
        s, r, seg = 2.0, 0.3, 64
        pts = rounded_rect(s / 2, s / 2, r, seg)
        exact = s * s - (4 - math.pi) * r * r
        self.assertLess(abs(area(pts) - exact), math.pi * r * r * 4 * arc_area_error(4 * seg) + 1e-12)
        self.assertAlmostEqual(max(p[0] for p in pts), s / 2, places=12)
        self.assertAlmostEqual(max(p[1] for p in pts), s / 2, places=12)

    def test_arc_points_lie_on_a_circle_tangent_to_both_edges(self):
        pts = round_corners([(0, 0), (4, 0), (4, 3)], [0, 1.0, 0], segments=10, closed=False)
        arc = pts[1:-1]
        self.assertEqual(len(arc), 11)
        self.assertTrue(all(abs(math.dist(p, (3, 1)) - 1.0) < 1e-12 for p in arc))   # centre 1 from both edges
        self.assertEqual((round(arc[0][0], 12), round(arc[0][1], 12)), (3.0, 0.0))
        self.assertEqual((round(arc[-1][0], 12), round(arc[-1][1], 12)), (4.0, 1.0))

    def test_concave_corner_and_open_ends(self):
        l_shape = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]   # corner 3 is concave
        out = round_corners(l_shape, [0, 0, 0, 0.3, 0, 0], segments=8)
        self.assertGreater(area(out), area(l_shape))   # a concave fillet adds material
        self.assertTrue(all(abs(math.dist(p, (1.3, 1.3)) - 0.3) < 1e-12 for p in out[3:12]))
        open_profile = round_corners([(0, 0), (1, 0), (1, 1)], 0.2, closed=False)
        self.assertEqual(open_profile[0], (0.0, 0.0))
        self.assertEqual(open_profile[-1], (1.0, 1.0))

    def test_stadium_and_straight_corners(self):
        stadium = rounded_rect(1.0, 0.5, 0.5, 16)
        self.assertEqual(len(stadium), len({(round(u, 12), round(v, 12)) for u, v in stadium}))   # no doubled points
        self.assertEqual(round_corners([(0, 0), (1, 0), (2, 0), (2, 1), (0, 1)], [0, 0.4, 0, 0, 0]),
                         [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)])

    def test_refuses_radii_that_do_not_fit(self):
        with self.assertRaisesRegex(ValueError, 'too large for edge'):
            round_corners([(0, 0), (1, 0), (1, 1), (0, 1)], 0.6)
        with self.assertRaisesRegex(ValueError, 'one per corner'):
            round_corners([(0, 0), (1, 0), (1, 1)], [0.1, 0.1])
        with self.assertRaises(ValueError):
            rounded_rect(1, 0.5, 0.6)


if __name__ == '__main__':
    unittest.main()
