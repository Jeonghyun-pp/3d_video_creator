import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'studio/blender_ops'))
import camera_moves_core as moves  # noqa: E402
import camera_rig_core as rig  # noqa: E402

GEO = {
    'points': {'concourse': (0.0, 2.0, -20.0), 'platform': (0.0, 30.0, -30.0)},
    'boxes': {
        'road.opening': ((-6.0, -4.0, -1.0), (6.0, 4.0, 0.0)),
        'col.3': ((-4.5, 9.5, -30.0), (-3.5, 10.5, -10.0)),
        'col.4': ((3.5, 9.5, -30.0), (4.5, 10.5, -10.0)),
        'section': ((-20.0, 40.0, -40.0), (20.0, 80.0, 0.0)),
    },
}


def inside(point, box, pad=0.0):
    return all(box[0][i] - pad <= point[i] <= box[1][i] + pad for i in range(3))


class CatmullRomTest(unittest.TestCase):
    def test_passes_through_waypoints_and_is_evenly_dense(self):
        pts = [(0, 0, 0), (10, 0, 0), (10, 10, 5)]
        dense = moves.catmull_rom(pts)
        for p in pts:
            self.assertLess(min(math.dist(p, q) for q in dense), 1e-9)
        steps = [math.dist(a, b) for a, b in zip(dense, dense[1:])]
        self.assertLess(max(steps), 0.4)
        self.assertGreater(min(steps), 0.05)

    def test_short_leg_between_long_ones_never_runs_backwards(self):
        # samsung s01: a thin opening put a 7.5 m mouth->inside leg between 55 m and 44 m legs (uniform CR looped)
        wps = [(0, 17.5, 22), (0, 67.5, 0.5), (0, 75, -0.2), (0, 114, -20.75)]
        dense = moves.catmull_rom(wps)
        self.assertLess(moves.backtrack_m(dense, wps), 1e-6)
        ys = [p[1] for p in dense]
        self.assertEqual(ys, sorted(ys))
        mouth = min(range(len(dense)), key=lambda i: math.dist(dense[i], wps[1]))
        zs = [p[2] for p in dense[mouth - 12:mouth + 40]]
        self.assertLess(max(zs) - zs[0], 0.5)            # no bounce above the mouth after passing it

    def test_backtrack_measures_a_reversal(self):
        wps = [(0, 0, 0), (0, 10, 0)]
        self.assertAlmostEqual(moves.backtrack_m([(0, 0, 0), (0, 6, 0), (0, 5, 0), (0, 10, 0)], wps), 1.0)

    def test_thin_opening_dive_goes_below_the_slab(self):
        geo = {'points': {'platform': (0.0, 70.0, -34.0)}, 'boxes': {'road.opening': ((-16, 55, -0.7), (16, 80, -0.3))}}
        plan = moves.plan({'type': 'dive_through', 'params': {'opening': 'road.opening', 'below': 'platform', 'above_m': 22, 'back_m': 50}}, geo)
        self.assertLessEqual(plan['waypoints'][2][2], -0.7 - 1.5 + 1e-9)
        dense = moves.catmull_rom(plan['waypoints'])
        self.assertLess(moves.backtrack_m(dense, plan['waypoints']), 1e-6)

    def test_needs_two_points(self):
        with self.assertRaises(ValueError):
            moves.catmull_rom([(0, 0, 0)])


class MoveTest(unittest.TestCase):
    def test_dive_through_enters_the_opening_and_aims_below(self):
        plan = moves.plan({'type': 'dive_through', 'params': {'opening': 'road.opening', 'below': 'concourse'}}, GEO)
        dense = moves.catmull_rom(plan['waypoints'])
        box = GEO['boxes']['road.opening']
        self.assertTrue(any(inside(p, box) for p in dense), 'path never passes through the opening')
        crossing = [p for p in dense if box[0][2] <= p[2] <= box[1][2]]
        self.assertTrue(all(inside(p, box) for p in crossing), 'path crosses the slab level outside the opening')
        self.assertGreater(plan['waypoints'][0][2], box[1][2])
        self.assertLess(plan['waypoints'][-1][2], box[0][2])
        self.assertEqual(plan['aim_ref'], 'concourse')
        self.assertEqual(plan['pitch_limit_deg'], 85.0)

    def test_pass_between_threads_the_gap(self):
        plan = moves.plan({'type': 'pass_between', 'params': {'a': 'col.3', 'b': 'col.4', 'target': 'platform', 'height_m': -20.0}}, GEO)
        dense = moves.catmull_rom(plan['waypoints'])
        gap = plan['notes']['gap_m']
        self.assertAlmostEqual(gap, 7.0, places=3)
        nearest = min(math.dist(p, (0.0, 10.0, -20.0)) for p in dense)
        self.assertLessEqual(nearest, 0.3 * gap)
        # it travels toward the target, not away from it
        self.assertLess(plan['waypoints'][0][1], plan['waypoints'][-1][1])
        for column in ('col.3', 'col.4'):
            self.assertGreater(moves.min_distance_to_box(dense, GEO['boxes'][column]), 2.0)

    def test_descend_levels_goes_down_inside_the_section(self):
        plan = moves.plan({'type': 'descend_levels', 'params': {'section': 'section'}}, GEO)
        w = plan['waypoints']
        self.assertGreater(w[0][2], w[-1][2])
        self.assertTrue(inside(w[1], GEO['boxes']['section']))
        self.assertTrue(inside(w[-1], GEO['boxes']['section']))

    def test_push_in_and_crane(self):
        push = moves.plan({'type': 'push_in', 'params': {'target': 'concourse', 'from_m': 12, 'to_m': 3, 'height_m': -19}}, GEO)
        self.assertAlmostEqual(math.dist(push['waypoints'][0][:2], GEO['points']['concourse'][:2]), 12)
        self.assertAlmostEqual(math.dist(push['waypoints'][-1][:2], GEO['points']['concourse'][:2]), 3)
        crane = moves.plan({'type': 'crane', 'params': {'target': 'concourse', 'from_h': 0.5, 'to_h': 9}}, GEO)
        self.assertEqual([round(p[2], 3) for p in crane['waypoints']], [0.5, 4.75, 9.0])

    def test_orbit_reveal_compiles_to_orbit(self):
        plan = moves.plan({'type': 'orbit_reveal', 'params': {'target': 'col.3', 'sweep_deg': 120}}, GEO)
        self.assertEqual(plan['kind'], 'orbit')
        self.assertEqual(plan['sweep_deg'], 120)
        self.assertEqual(plan['aim_ref'], 'col.3')

    def test_unknown_reference_and_move_fail_loudly(self):
        with self.assertRaisesRegex(ValueError, 'CAMERA_MOVE'):
            moves.plan({'type': 'dive_through', 'params': {'opening': 'nope', 'below': 'concourse'}}, GEO)
        with self.assertRaisesRegex(ValueError, 'CAMERA_MOVE'):
            moves.plan({'type': 'barrel_roll', 'params': {}}, GEO)

    def test_waypoints_reproduce_a_named_move(self):
        """N+1: a move the library does not name is just waypoints - same path, no new code."""
        named = moves.plan({'type': 'dive_through', 'params': {'opening': 'road.opening', 'below': 'concourse'}}, GEO)
        free = moves.plan({'type': 'waypoints', 'params': {'points': [list(p) for p in named['waypoints']], 'aim': 'concourse'}}, GEO)
        self.assertEqual(moves.catmull_rom(named['waypoints']), moves.catmull_rom(free['waypoints']))
        self.assertEqual(named['aim'], free['aim'])
        ahead = moves.plan({'type': 'waypoints', 'params': {'points': ['concourse', 'platform']}}, GEO)
        self.assertIsNone(ahead['aim']); self.assertIsNone(ahead['aim_ref'])


class WhipTest(unittest.TestCase):
    def test_whip_starts_off_target_and_lands_on_it(self):
        eye, targets = (0.0, 0.0, 0.0), [(0.0, 10.0, 0.0)] * 20
        aim = moves.whip_aim(eye, targets, 60.0, 8)
        first = math.degrees(math.atan2(aim[0][1], aim[0][0]) - math.atan2(10, 0))
        self.assertAlmostEqual(first, 60.0, places=6)
        for a, t in zip(aim[8:], targets[8:]):
            self.assertLess(math.dist(a, t), 1e-9)
        angles = [abs(math.atan2(a[1], a[0]) - math.pi / 2) for a in aim[:9]]
        self.assertTrue(all(x >= y for x, y in zip(angles, angles[1:])))


class CompiledPathTest(unittest.TestCase):
    def test_timed_flythrough_over_a_compiled_path_covers_it(self):
        plan = moves.plan({'type': 'pass_between', 'params': {'a': 'col.3', 'b': 'col.4', 'target': 'platform', 'height_m': -20.0}}, GEO)
        dense = moves.catmull_rom(plan['waypoints'])
        length = moves.path_length(dense)
        spec = {'type': 'flythrough', 'path': 'p', 'timing': {'profile': 'burst_settle', 'distance_m': length}, 'look_ahead_m': 0.0}
        target = [plan['aim']] * 90
        out = rig.bake(spec, 30, 90, target=target, path=dense, view=(36.0, 'AUTO', 1080, 1920))
        self.assertLess(math.dist(out['frames'][0]['location'], dense[0]), 1e-6)
        self.assertLess(math.dist(out['frames'][-1]['location'], dense[-1]), 1e-3)


if __name__ == '__main__':
    unittest.main()


class SectionPushTest(unittest.TestCase):
    GEO = {'points': {}, 'boxes': {'st.box': ((-17.0, 60.0, -36.5), (17.0, 220.0, 0.0))}}

    def test_front_frames_the_section_width_and_centre(self):
        plan = moves.plan({'type': 'section_push', 'lens_mm': 24, 'params': {'section': 'st.box', 'fill': 0.45, 'centre_v': 0.68}}, self.GEO)
        start, front, inside = plan['waypoints']
        tan_y = 36 / 48; tan_x = tan_y * 9 / 16
        d = 60.0 - front[1]
        self.assertAlmostEqual(34 / (2 * d * tan_x), 0.45, 6)               # section spans 45 % of the frame width
        v = 0.40 + (front[2] - (-18.25)) / (2 * d * tan_y)                    # level camera, horizon at 0.40
        self.assertAlmostEqual(v, 0.68, 6)
        self.assertGreater(inside[1], 60.0)                                   # pushes through the face
        self.assertLess(start[1], front[1]); self.assertGreater(start[2], front[2])
        dense = moves.catmull_rom(plan['waypoints'])
        self.assertLess(moves.backtrack_m(dense, plan['waypoints']), 1e-6)
        self.assertEqual(plan['marks']['front'], 1)
