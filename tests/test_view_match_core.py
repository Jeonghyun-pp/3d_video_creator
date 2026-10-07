"""view_match_core + photo_match.solve_pose: the orbit camera projects like camera_rig_core, a synthetic photo's camera
is recovered from its points, and inputs with no single answer are refused."""
import math
import random
import unittest

from studio.blender_ops.view_match_core import eye, full, pose_delta, project_points, residual_px
from studio.photo_match import solve_pose

CAMERA = {'target': [0.05, -0.02, 0.2], 'distance_m': 2.0, 'azimuth_deg': 35.0, 'elevation_deg': 22.0, 'roll_deg': 3.0,
          'lens_mm': 35.0, 'width': 1200, 'height': 800}


def points(seed=3, n=10):
    rng = random.Random(seed)
    return [(rng.uniform(-0.4, 0.4), rng.uniform(-0.3, 0.3), rng.uniform(0.0, 0.5)) for _ in range(n)]


class ProjectionTest(unittest.TestCase):
    def test_target_projects_to_the_centre_and_shift_moves_it(self):
        self.assertEqual([round(v, 12) for v in project_points(CAMERA, [CAMERA['target']])[0]], [0.5, 0.5])
        shifted = project_points({**CAMERA, 'shift_x': 0.1, 'shift_y': 0.05}, [CAMERA['target']])[0]
        self.assertAlmostEqual(shifted[0], 0.5 - 0.1, places=12)                 # width is the larger side
        self.assertAlmostEqual(shifted[1], 0.5 + 0.05 * 1200 / 800, places=12)

    def test_orbit_eye_and_field_of_view(self):
        top = {**CAMERA, 'azimuth_deg': 0.0, 'elevation_deg': 0.0, 'roll_deg': 0.0, 'target': [0, 0, 0]}
        self.assertEqual([round(v, 12) for v in eye(top)], [2.0, 0.0, 0.0])
        half_width = 2.0 * 36.0 / (2 * 35.0)   # a point at the frame's right edge, sensor fit horizontal
        self.assertAlmostEqual(project_points(top, [(0, half_width, 0)])[0][0], 1.0, places=12)
        self.assertIsNone(project_points(top, [(3, 0, 0)])[0])   # behind the camera

    def test_unknown_or_missing_keys_refused(self):
        with self.assertRaisesRegex(ValueError, 'not read'):
            full({**CAMERA, 'fov': 40})
        with self.assertRaisesRegex(ValueError, 'needs'):
            full({k: v for k, v in CAMERA.items() if k != 'lens_mm'})


class SolvePoseTest(unittest.TestCase):
    def test_recovers_synthetic_cameras(self):
        pts = points()
        for cam in (CAMERA,
                    {**CAMERA, 'azimuth_deg': -120.0, 'elevation_deg': 40.0, 'lens_mm': 28.0, 'distance_m': 1.4, 'width': 800, 'height': 1000},
                    {**CAMERA, 'azimuth_deg': 170.0, 'elevation_deg': -10.0, 'roll_deg': -5.0, 'lens_mm': 85.0, 'distance_m': 4.0}):
            solved = solve_pose(pts, project_points(cam, pts), cam['width'], cam['height'])
            delta = pose_delta(cam, solved['camera'])
            self.assertLess(delta['view_deg'], 1.0, delta)
            self.assertLess(delta['lens_ratio'], 0.02, delta)
            self.assertLess(delta['eye_share'], 0.02, delta)

    def test_pixel_noise_stays_close(self):
        rng = random.Random(7)
        pts = points(n=14)
        image = [(x + rng.gauss(0, 1.5 / 1200), y + rng.gauss(0, 1.5 / 800)) for x, y in project_points(CAMERA, pts)]
        solved = solve_pose(pts, image, 1200, 800)
        self.assertLess(solved['residual_px'], 3.0)
        self.assertLess(pose_delta(CAMERA, solved['camera'])['view_deg'], 3.0)
        self.assertLess(residual_px(solved['camera'], pts, image), residual_px({**CAMERA, 'azimuth_deg': 40.0}, pts, image))

    def test_refuses_inputs_without_a_single_answer(self):
        pts = points()
        image = project_points(CAMERA, pts)
        with self.assertRaisesRegex(ValueError, '>= 6'):
            solve_pose(pts[:5], image[:5], 1200, 800)
        line = [(t, 2 * t, 0.5 * t) for t in range(8)]
        with self.assertRaisesRegex(ValueError, 'one line'):
            solve_pose(line, [(0.1 * i, 0.2) for i in range(8)], 1200, 800)
        with self.assertRaisesRegex(ValueError, 'bunched'):
            solve_pose(pts, [(0.5 + 0.001 * i, 0.5) for i in range(len(pts))], 1200, 800)
        with self.assertRaisesRegex(ValueError, 'but'):
            solve_pose(pts, image[:-1], 1200, 800)

class OutlierTest(unittest.TestCase):
    """A mark on the wrong landmark is named, not just folded into the RMS (2026-10-07: four 'flange corners')."""
    def test_a_wrong_landmark_is_named_and_good_sets_have_none(self):
        from studio.photo_match import outliers, point_errors
        pts = points(n=10)
        image = project_points(CAMERA, pts)
        names = [f'p{i}' for i in range(len(pts))]
        self.assertEqual(outliers(pts, image, 1200, 800, CAMERA, names)['outliers'], [])
        wrong = list(image)
        wrong[3] = image[7]   # mark 3 put on landmark 7
        solved = solve_pose(pts, wrong, 1200, 800)
        found = outliers(pts, wrong, 1200, 800, solved['camera'], names)
        self.assertEqual([f['i'] for f in found['outliers']], [3], found)   # only mark 3 is wrong; mark 7 is still right
        self.assertTrue(found['fits'] and found['outliers'][0]['rest_residual_px'] <= 3.0)
        two = list(wrong)
        two[5] = image[1]   # a second wrong mark: still found, both of them
        found = outliers(pts, two, 1200, 800, solve_pose(pts, two, 1200, 800)['camera'], names)
        self.assertEqual(sorted(f['i'] for f in found['outliers']), [3, 5], found)
        bent = [(x + 0.03 * math.sin(7 * i), y + 0.03 * math.cos(5 * i)) for i, (x, y) in enumerate(image)]   # nothing agrees
        broad = outliers(pts, bent, 1200, 800, solve_pose(pts, bent, 1200, 800)['camera'], names)
        self.assertFalse(broad['fits'])
        self.assertIn('model differs', broad['note'])
        rows = point_errors(CAMERA, pts, image, names)
        self.assertTrue(all(r['error_px'] < 1e-6 for r in rows))


if __name__ == '__main__':
    unittest.main()
