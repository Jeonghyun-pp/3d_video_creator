"""The comfort check's turn is the camera's own roll, read from the camera (studio/blender_ops/camera_roll.py)."""
import math
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'studio' / 'blender_ops'))
import camera_roll  # noqa: E402


def look(yaw_deg, pitch_deg, roll_deg=0.0):
    """A camera matrix (world from camera, looking down -Z) at a yaw, pitch and roll."""
    def rz(a):
        c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
        return [[c, -s, 0], [s, c, 0], [0, 0, 1]]

    def rx(a):
        c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
        return [[1, 0, 0], [0, c, -s], [0, s, c]]

    def mul(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    return mul(mul(rz(yaw_deg), rx(90 + pitch_deg)), rz(roll_deg))


class RollTest(unittest.TestCase):
    def test_a_sliding_camera_that_keeps_its_heading_does_not_roll(self):
        """2026-10-25 (s03): yaw -20 and pitch -5 on every frame read as a 19.7 deg turn in half a second."""
        self.assertAlmostEqual(camera_roll.roll_rad(look(-20, -5), look(-20, -5)), 0.0)

    def test_a_pan_is_not_a_roll_and_a_roll_is(self):
        self.assertAlmostEqual(camera_roll.roll_rad(look(-20, 0), look(-18, 0)), 0.0, places=6)
        self.assertAlmostEqual(math.degrees(camera_roll.roll_rad(look(-20, -5, 0), look(-20, -5, 2))), 2.0, places=4)

    def test_the_synthetic_measure_reads_the_camera(self):
        source = (Path(__file__).resolve().parents[1] / 'studio' / 'blender_ops' / 'grammar_synth.py').read_text()
        self.assertIn("'rot': round(roll_rad(", source)


if __name__ == '__main__':
    unittest.main()
