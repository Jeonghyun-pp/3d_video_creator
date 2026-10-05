"""Environment fill geometry (studio/blender_ops/env_fill_core.py): spacing, sides, gaps, determinism, independence."""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'studio' / 'blender_ops'))
import env_fill_core as core  # noqa: E402

STRAIGHT = [(0, 0, 0), (0, 100, 0)]
BENT = [(0, 0, 0), (0, 50, 0), (50, 50, 0)]


class AlongTest(unittest.TestCase):
    def test_pitch_offset_and_heading(self):
        rows = core.along(STRAIGHT, 10, offset_m=5)
        self.assertEqual(len(rows), 11)
        self.assertEqual(tuple(round(c, 9) for c in rows[0][0]), (-5.0, 0.0, 0.0))   # left of travel (+y) is -x
        self.assertAlmostEqual(rows[0][1], math.pi / 2)
        right = core.along(STRAIGHT, 10, offset_m=-5)
        self.assertEqual(tuple(round(c, 9) for c in right[3][0]), (5.0, 30.0, 0.0))

    def test_follows_a_bend(self):
        rows = core.along(BENT, 25)
        self.assertEqual([tuple(round(c, 6) for c in p) for p, _ in rows], [(0, 0, 0), (0, 25, 0), (0, 50, 0), (25, 50, 0), (50, 50, 0)])
        self.assertAlmostEqual(rows[-1][1], 0.0)

    def test_jitter_is_seeded(self):
        a = core.along(STRAIGHT, 10, jitter_m=2, seed=4)
        self.assertEqual(a, core.along(STRAIGHT, 10, jitter_m=2, seed=4))
        self.assertNotEqual(a, core.along(STRAIGHT, 10, jitter_m=2, seed=5))


class LotsTest(unittest.TestCase):
    def test_lots_fit_avoid_and_sides(self):
        kw = dict(lot_width_range_m=(15, 30), gap_range_m=(3, 8), depth_range_m=(20, 40), setback_m=10, seed=1)
        left = core.lots(STRAIGHT, side='left', **kw)
        self.assertTrue(left and all(l['centre'][0] < -10 for l in left))
        ends = [(l['s'], l['s'] + l['width']) for l in left]
        self.assertTrue(all(b1 <= a2 for (_, b1), (a2, _) in zip(ends, ends[1:])))       # no overlap
        self.assertTrue(all(b <= 100 for _, b in ends))
        held = core.lots(STRAIGHT, side='left', avoid=[(40, 60)], **kw)
        self.assertFalse(any(l['s'] < 60 and l['s'] + l['width'] > 40 for l in held))
        right = core.lots(STRAIGHT, side='right', **kw)
        self.assertTrue(all(l['centre'][0] > 10 for l in right))


class TrafficTest(unittest.TestCase):
    def test_gap_and_independent_lanes(self):
        lanes = [{'path': STRAIGHT, 'direction': 1}, {'path': [(4, 100, 0), (4, 0, 0)], 'direction': 1}]
        cars = core.traffic(lanes, per_100m=6, speed_mps_range=(10, 14), duration_s=3, min_gap_m=9, seed=2)
        for lane in (0, 1):
            starts = sorted(c['s0'] for c in cars if c['lane'] == lane)
            self.assertTrue(all(b - a >= 9 for a, b in zip(starts, starts[1:])), starts)
        more = core.traffic(lanes + [{'path': STRAIGHT, 'direction': -1}], per_100m=6, speed_mps_range=(10, 14), duration_s=3, min_gap_m=9, seed=2)
        self.assertEqual([c for c in more if c['lane'] < 2], cars)               # adding a lane moves no other car
        self.assertTrue(all(c['s1'] < c['s0'] for c in more if c['lane'] == 2))


if __name__ == '__main__':
    unittest.main()


class StreetDetailTest(unittest.TestCase):
    PATH = [(0.0, 0.0, 0.0), (0.0, 400.0, 0.0)]
    MAIN = {'lane_w': 3.4, 'lanes': 4, 'median_w': 0.6, 'sidewalk_w': 6.0}
    CROSS = {'lane_w': 3.3, 'lanes': 2, 'median_w': 0.0, 'sidewalk_w': 4.0}

    def test_along_leaves_avoid_ranges_empty_and_keeps_the_rhythm(self):
        rows = core.along(self.PATH, 8.0, offset_m=10, avoid=[(100, 140)])
        ys = [round(p[1], 6) for p, _ in rows]
        self.assertFalse(any(100 <= y <= 140 for y in ys))
        self.assertEqual(ys[:3], [0.0, 8.0, 16.0])
        self.assertIn(144.0, ys)                       # same grid after the gap, not restarted

    def test_keep_out_drops_points_near_obstacles(self):
        kept = core.keep_out([(0, 0, 0), (0.5, 0, 0), (2, 0, 0)], [(0, 0.1)], 0.6)
        self.assertEqual(kept, [(2, 0, 0)])

    def test_intersection_markings_follow_the_rules(self):
        lay = core.intersection_layout(self.PATH, 200.0, self.MAIN, self.CROSS)
        first = lay['approaches'][0]                   # out along +y: inbound drivers travel -y
        stripes = [p for p in first['paint'] if p[3] in (3.3,)]
        self.assertEqual(len(stripes), first['stripes'])
        ys = sorted({round(p[1], 3) for p in stripes})
        self.assertEqual(len(ys), 2)                   # two bands
        self.assertAlmostEqual(ys[1] - ys[0], 3.3 + 0.5)
        cross_half = 2 * 3.3
        self.assertAlmostEqual(ys[0] - 200.0, cross_half + 1.0 + 3.3 / 2)
        xs = sorted({round(p[0], 3) for p in stripes})
        self.assertAlmostEqual(xs[1] - xs[0], 1.0)     # stripe pitch
        stop = [p for p in first['paint'] if p[3] == 0.45][0]
        far_edge = 200.0 + cross_half + 1.0 + 3.3 + 0.5 + 3.3
        self.assertAlmostEqual(stop[1] - 0.45 / 2 - far_edge, 3.0)          # 3 m behind the crosswalk
        self.assertAlmostEqual(stop[4], 4 * 3.4)                            # spans the inbound lanes only
        self.assertEqual(len(first['arrows']), 4)
        tip = first['arrows'][0][0]
        self.assertAlmostEqual(tip[1] - (stop[1] + 0.45 / 2), 3.0)
        self.assertAlmostEqual(first['arrows'][0][1] % (2 * math.pi), (math.pi / 2 + math.pi) % (2 * math.pi))
        # right-hand traffic: drivers heading -y keep to their right, x < 0
        self.assertTrue(all(a[0][0] < 0 for a in first['arrows']))
        self.assertLess(stop[0], 0)
        self.assertGreater(first['signal']['point'][0] * -1, 4 * 3.4)      # near-side pole behind the kerb, inbound side
        lo, hi = lay['clear']
        self.assertTrue(lo < 200 - far_edge + 200 and hi > far_edge)


class SightlineTest(unittest.TestCase):
    RULE = {'camera': (0.0, 17.5, 22.0), 'forward': (0.0, 1.0), 'horizon_v': 0.40, 'keep_sky_v': 0.33, 'lens_mm': 24}

    def test_cap_puts_the_roof_on_the_sky_line(self):
        lot = {'centre': (-30.0, 117.5, 0.0), 'heading': math.pi / 2, 'width': 20.0, 'depth': 20.0}
        cap = core.sightline_cap(lot, self.RULE)
        tan_y = 36 / 48
        near = 117.5 - 10 - 17.5                     # nearest corner depth
        v = 0.40 - (cap - 22.0) / (2 * near * tan_y)
        self.assertAlmostEqual(v, 0.33, 6)

    def test_lots_out_of_view_are_not_limited(self):
        behind = {'centre': (-30.0, -50.0, 0.0), 'heading': math.pi / 2, 'width': 20.0, 'depth': 20.0}
        aside = {'centre': (-300.0, 40.0, 0.0), 'heading': math.pi / 2, 'width': 20.0, 'depth': 20.0}
        self.assertIsNone(core.sightline_cap(behind, self.RULE))
        self.assertIsNone(core.sightline_cap(aside, self.RULE))
