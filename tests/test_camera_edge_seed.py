"""Cropped long subjects can have a hidden centre far from the visible edge."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'studio/blender_ops'))
import camera_frame_core as framing
import camera_moves_core as moves
from tests.test_camera_frame_core import box_points


class EdgeSeedTest(unittest.TestCase):
    def test_wide_feet_below_a_cropped_body_reach_edges_from_fallback(self):
        box = ((-8.4, -8.4, 29.4), (8.4, 8.4, 79))
        frame = {'height_share': 2.056, 'centre': [.5085, -.372],
                 'view': {'elevation_deg': -17.48, 'azimuth_deg': -15},
                 'edges': {'left': .044, 'right': .973, 'bottom': .656}}
        points = (box_points((-1.85, -1.85, 32), (1.85, 1.85, 73.2), 20)
                  + box_points((-8, -1, 29.4), (8, 1, 31), 20)
                  + box_points((-1, -8, 29.4), (1, 8, 31), 20))
        with self.assertRaisesRegex(ValueError, 'FRAME_UNSOLVED'):
            framing.solve(lambda p: points, moves.frame_seed(box, frame, 24, 9/16)[0],
                          (-17.48, -15), frame, 24, 9/16)
        # the same frame read as a picture (A4: its cut rebuilt by frame_segment): the second pass clips the points
        # behind the camera and caps the steps, so the plain seed reaches it too
        picture = {**frame, **moves.frame_segment(frame)}
        rescued = framing.solve(lambda p: points, moves.frame_seed(box, frame, 24, 9/16)[0], (-17.48, -15), picture, 24, 9/16)
        self.assertEqual(rescued['engaged'][0], 'rescue')     # the second pass, after the first failed to converge
        self.assertTrue({'clip', 'cap'} <= set(rescued['engaged']), rescued['engaged'])
        got = rescued['achieved']['box']
        self.assertAlmostEqual(got[2] - got[0], .929, delta=.929 * framing.SIZE_TOL)
        self.assertAlmostEqual((got[0] + got[2]) / 2, .5085, delta=framing.PLACE_TOL)
        self.assertAlmostEqual(got[3], .656, delta=framing.PLACE_TOL)
        seed = moves.frame_edge_seed(box, frame, 24, 9/16)
        # the edge seed's first pass (camera_moves._pose tries it before any rescue) is the legacy pose, nothing engaged
        first = framing.solve(lambda p: points, seed, (-17.48, -15), picture, 24, 9/16, passes=('first',))
        legacy = framing.solve(lambda p: points, seed, (-17.48, -15), frame, 24, 9/16)
        self.assertEqual((first['position'], first['engaged'], first['pass']), (legacy['position'], [], 'first'))
        with self.assertRaises(framing.FrameSolveError) as plain_first:
            framing.solve(lambda p: points, moves.frame_seed(box, frame, 24, 9/16)[0], (-17.48, -15), picture, 24, 9/16, passes=('first',))
        self.assertTrue(plain_first.exception.rescuable)
        result = framing.solve(lambda p: points, seed, (-17.48, -15), frame, 24, 9/16)
        actual = result['achieved']['box']
        self.assertAlmostEqual(actual[2] - actual[0], .929, delta=.929 * framing.SIZE_TOL)
        self.assertAlmostEqual((actual[0] + actual[2])/2, .5085, delta=framing.PLACE_TOL)
        self.assertAlmostEqual(actual[3], .656, delta=framing.PLACE_TOL)
        for got, want in zip(framing.view_of(result['position'], result['aim']), (-17.48, -15)):
            self.assertAlmostEqual(got, want, places=4)

    def test_no_edges_leaves_normal_seed_alone(self):
        self.assertIsNone(moves.frame_edge_seed(((-1,-1,0),(1,1,10)),
                          {'view': {'elevation_deg': 0}, 'height_share': .6}, 24, 9/16))
