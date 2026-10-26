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

    def test_an_outline_is_sized_where_its_part_stands_not_at_its_origin(self):
        """2026-10-26 (fab s26): a 10 mm chip built with its origin at the world's middle, a metre below it, was outlined
        with a line sized for a frame a metre further off - a red blob wider than the chip."""
        box = ((0.0, 0.0, 1.0), (0.01, 0.01, 1.0004))
        self.assertEqual(mark_size.stands_at((0.0, 0.0, 0.0), box), (0.005, 0.005, 1.0002))
        self.assertEqual(mark_size.stands_at((1.0, 2.0, 3.0), None), (1.0, 2.0, 3.0))   # a point with no box is the point
        source = (Path(__file__).resolve().parents[1] / 'studio' / 'blender_ops' / 'graphics.py').read_text()
        self.assertIn('stands_at(', source)

    def test_graphics_uses_it(self):
        source = (Path(__file__).resolve().parents[1] / 'studio' / 'blender_ops' / 'graphics.py').read_text()
        self.assertIn('stroke_radius(', source)
        self.assertNotIn('max(DEFAULT_RADIUS_M, _on_screen', source)


if __name__ == '__main__':
    unittest.main()
