"""The registration measure on the host (studio/blender_ops/register_core.py, numpy): a line drawing moved and scaled is
laid back; the search's edges are not trusted; what a fit leaves unpainted. Skipped where numpy is not installed (the
Blender smoke tests/studio/picture3d_core_smoke.py runs the same measure in Blender's python)."""
import importlib.util
import sys
import unittest
from pathlib import Path

HAS_NUMPY = importlib.util.find_spec('numpy') is not None


@unittest.skipUnless(HAS_NUMPY, 'numpy is not installed on the host')
class RegisterCoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'studio/blender_ops'))
        import register_core
        cls.np, cls.rc = np, register_core
        H, W = 1280, 720                                     # the passes' size (the search's steps are in its pixels)
        lines = np.zeros((H, W), bool)                       # a box and a cross
        lines[400:402, 160:560] = lines[880:882, 160:560] = lines[400:882, 160:162] = lines[400:882, 558:560] = True
        lines[600:602, 240:480] = lines[480:800, 359:361] = True
        cls.lines, cls.region = lines, register_core.dilate(lines, 40)

    def test_a_moved_and_scaled_drawing_is_laid_back(self):
        fit = self.rc.register(self.rc.warp(self.lines, 1.04, 32, 24), self.lines, self.region)
        self.assertAlmostEqual(fit['scale'], 1 / 1.04, delta=0.03)
        self.assertGreater(fit['subject_f1'], 0.8)
        self.assertFalse(fit['at_search_edge'])

    def test_a_fit_at_the_scale_bound_is_not_trusted(self):
        fit = self.rc.register(self.rc.warp(self.lines, 1 / 1.6, 0, 0), self.lines, self.region)   # needs 1.6, past the search
        self.assertTrue(fit['at_search_edge'])

    def test_what_a_fit_leaves_unpainted(self):
        self.assertEqual(self.rc.unpainted(1.2, [0.0, 0.0]), 0.0)                 # enlarged: only crops
        self.assertAlmostEqual(self.rc.unpainted(0.9, [0.02, 0.0]), 0.07, places=4)
        self.assertAlmostEqual(self.rc.unpainted(1.0, [-0.12, 0.0]), 0.12, places=4)


if __name__ == '__main__':
    unittest.main()
