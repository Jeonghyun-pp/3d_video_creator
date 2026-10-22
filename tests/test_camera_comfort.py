"""The comfort goal reads the camera's own fastest half second, not the picture's (camera_comfort)."""
import unittest

from studio import camera_comfort as comfort

FPS = 30


def pair(frame, ds=0.0, tx=0.0, camera_ds=0.0, camera_tx=0.0):
    return {'ok': True, 'frame': frame, 'ds': ds, 'tx': tx, 'ty': 0.0, 'rot': 0.0,
            'camera': {'ds': camera_ds, 'tx': camera_tx, 'ty': 0.0, 'rot': 0.0}}


class PeaksTest(unittest.TestCase):
    def test_a_thing_rushing_past_a_still_camera_is_not_the_camera(self):
        """r3 s01/s07: a stone fell past a locked camera - the picture swelled, the camera never moved."""
        pairs = [pair(f, ds=0.3, tx=0.2) for f in range(60)]
        got = comfort.peaks(pairs, FPS)
        self.assertEqual((got['scale'], got['travel']), (0.0, 0.0))
        self.assertEqual(comfort.lines(got), [])

    def test_a_rush_is_its_fastest_window_not_its_mean(self):
        """r3 s05: a slow start, then the whole hull to one tank in half a second."""
        pairs = [pair(f, camera_ds=0.002) for f in range(60)] + [pair(60 + f, camera_ds=0.08) for f in range(15)]
        got = comfort.peaks(pairs, FPS)
        self.assertAlmostEqual(got['scale'], 1.2, places=3)
        self.assertEqual(got['at_s']['scale'], 2.0)
        lines = comfort.lines(got)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith('CAMERA_TOO_FAST:'))
        self.assertIn('scale', lines[0])
        mean = sum(p['camera']['ds'] for p in pairs) / len(pairs) * FPS
        self.assertLess(mean, comfort.goal()['limits']['scale'])   # its mean alone would pass

    def test_unmeasured_versions_say_nothing(self):
        self.assertIsNone(comfort.peaks([{'ok': True, 'frame': 0, 'ds': 0.1}], FPS))
        self.assertEqual(comfort.lines(None), [])

    def test_every_limit_says_what_to_try(self):
        row = comfort.goal()
        self.assertEqual(set(row['limits']), set(comfort.AXES))
        self.assertEqual(set(row['try']), set(comfort.AXES))
        self.assertTrue(row['limits_why'])


if __name__ == '__main__':
    unittest.main()
