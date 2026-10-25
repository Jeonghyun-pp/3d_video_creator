"""A closed mesh's winding is judged on its signed volume itself (studio/blender_ops/winding.py)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'studio' / 'blender_ops'))
import winding  # noqa: E402


class WindingTest(unittest.TestCase):
    def test_a_small_part_winding_outward_is_not_inside_out(self):
        """2026-10-25 (s04): a 0.4 m x 12 mm x 6 mm rib (2.9e-5 m3) was rounded to 0.0 and refused."""
        self.assertFalse(winding.winds_inward(2.85e-5))
        self.assertTrue(winding.winds_inward(-2.85e-5))
        self.assertTrue(winding.winds_inward(0.0))
        self.assertEqual(winding.shown(2.8512e-5), 2.851e-5)

    def test_the_reveal_judges_the_volume_not_its_rounding(self):
        source = (Path(__file__).resolve().parents[1] / 'studio' / 'blender_ops' / 'reveal.py').read_text()
        self.assertIn('winds_inward(volume)', source)
        self.assertNotIn('round(signed_volume(obj), 3)', source)


if __name__ == '__main__':
    unittest.main()
