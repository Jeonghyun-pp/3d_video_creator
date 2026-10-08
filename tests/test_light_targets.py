"""Region light targets: the multipliers that move each region toward its brightness, from per-group contributions."""
import unittest

from studio.blender_ops.regions_core import l_star_to_y, y_to_l_star
from studio.light_targets import check, needed_ratios, proposals, solve

# archcut3 s03: the ceiling (top) far too bright, the floor (bottom) about right; the key lights the floor, the
# practicals light the ceiling
CONTRIB = {'key': {'top-centre': 0.02, 'bottom-centre': 0.30}, 'practicals': {'top-centre': 0.40, 'bottom-centre': 0.05},
           'world': {'top-centre': 0.01, 'bottom-centre': 0.01}}


class SolveTest(unittest.TestCase):
    def test_l_star_round_trip(self):
        for L in (5, 40, 77):
            self.assertAlmostEqual(y_to_l_star(l_star_to_y(L)), L, places=6)

    def test_the_light_that_lights_a_region_is_the_one_changed(self):
        ratios = needed_ratios({'top-centre': 77, 'bottom-centre': 60}, [{'region': 'top-centre', 'luminance': 58}, {'region': 'bottom-centre', 'luminance': 60}])
        m, error = solve(CONTRIB, ratios)
        self.assertLess(m['practicals'], 0.7)                  # the ceiling's light comes down
        self.assertAlmostEqual(m['key'], 1.0, delta=0.15)      # the floor's light stays
        self.assertLess(error, 0.05)
        out = proposals(m, [{'name': 'key', 'irradiance': 4.0}], CONTRIB)
        self.assertIn('practicals', out[0]['hint'])              # not a rig light: said, not set

    def test_a_uniform_change_is_exposure(self):
        ratios = needed_ratios({'top-centre': 40, 'bottom-centre': 40}, [{'region': 'top-centre', 'luminance': 50}, {'region': 'bottom-centre', 'luminance': 50}])
        m, _ = solve(CONTRIB, ratios)
        out = proposals(m, [], CONTRIB, ratios)
        self.assertEqual(out[0]['path'], '/render/grade/exposure_offset_ev')
        self.assertGreater(out[0]['delta'], 0)

    def test_a_rig_light_gets_a_shot_value(self):
        m = {'key': 1.5, 'practicals': 1.0, 'world': 1.0}
        out = proposals(m, [{'name': 'key', 'irradiance': 4.0}], CONTRIB)
        self.assertEqual(out[0], {'op': 'set', 'path': '/render/lighting/rig/key/irradiance', 'value': 6.0, 'why': 'key x1.50'})

    def test_regions_are_checked(self):
        self.assertEqual(check([{'region': 'top-left'}, {'region': 'class:hall/ceiling'}]), [])
        self.assertTrue(check([{'region': 'ceiling'}]))


if __name__ == '__main__':
    unittest.main()
