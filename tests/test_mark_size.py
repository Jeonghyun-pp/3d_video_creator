"""A drawn mark's line is a share of the frame at any scale (studio/blender_ops/mark_size.py)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'studio' / 'blender_ops'))
import mark_size  # noqa: E402


class StrokeTest(unittest.TestCase):
    def test_a_macro_mark_is_not_floored_to_centimetres(self):
        """2026-10-25 (s11): a 3 mm speck outlined in a 5 cm frame came out a 4 cm red disc."""
        self.assertAlmostEqual(mark_size.stroke_radius(0.0003, 0.04), 0.0003)   # 0.6 % of a 5 cm frame
        self.assertAlmostEqual(mark_size.stroke_radius(0.5, 0.04), 0.5)         # a whole-boat frame: thick enough to read

    def test_metres_only_where_nothing_places_the_mark(self):
        self.assertEqual(mark_size.stroke_radius(0.0, 0.04), 0.04)

    def test_graphics_uses_it(self):
        source = (Path(__file__).resolve().parents[1] / 'studio' / 'blender_ops' / 'graphics.py').read_text()
        self.assertIn('stroke_radius(', source)
        self.assertNotIn('max(DEFAULT_RADIUS_M, _on_screen', source)


if __name__ == '__main__':
    unittest.main()
