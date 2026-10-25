"""A camera pose inside a closed thing that is not the subject is refused (studio/blender_ops/inside_core.py,
camera_moves._inside_solid, gate CAMERA_IN_SOLID)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'studio' / 'blender_ops'))
import inside_core  # noqa: E402


class InsideTest(unittest.TestCase):
    def test_a_camera_in_a_cabinet_box_is_asked_one_in_the_aisle_is_not(self):
        """2026-10-25 (s03): the camera at (1.26, -6.26, 2.59) stood in the tool at x 1.05..2.85, y -7.2..-5.6."""
        cabinet = [(x, y, z) for x in (1.05, 2.85) for y in (-7.2, -5.6) for z in (0.0, 2.2 + 0.5)]
        self.assertTrue(inside_core.within(cabinet, (1.26, -6.26, 2.59)))
        self.assertFalse(inside_core.within(cabinet, (-0.35, -5.74, 2.59)))
        self.assertTrue(inside_core.enclosed(14, 14, 12))
        self.assertFalse(inside_core.enclosed(5, 14, 12))   # beside one face

    def test_the_pose_refusal_asks_it_and_the_gate_is_broken(self):
        source = (ROOT / 'studio' / 'blender_ops' / 'camera_moves.py').read_text()
        self.assertIn("_inside_solid(frame + 1, position, skip, cache)", source)
        from studio import gates
        self.assertEqual(gates.SOFTENABLE['CAMERA_IN_SOLID'][0], 'broken')


if __name__ == '__main__':
    unittest.main()
