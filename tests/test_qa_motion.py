from pathlib import Path
import subprocess
import tempfile
import unittest

from studio.qa_motion import compare_motion, measure_motion, shot_motion


def clip(path, source, vf='null'):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', source, '-vf', vf, '-frames:v', '60', '-pix_fmt', 'yuv420p', str(path)], check=True)


class MotionMetricTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.still = root / 'still.mp4'; clip(cls.still, 'color=c=gray:s=320x180:r=30')
        cls.moving = root / 'moving.mp4'; clip(cls.moving, 'testsrc=s=320x180:r=30', 'scroll=h=0.02')
        cls.edit = root / 'edit.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(cls.still), '-i', str(cls.moving), '-filter_complex',
                        '[0:v][1:v]concat=n=2:v=1[v]', '-map', '[v]', '-pix_fmt', 'yuv420p', str(cls.edit)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_still_is_zero_and_moving_is_positive(self):
        still, moving = measure_motion(self.still), measure_motion(self.moving)
        self.assertEqual(still['pairs'], 59)
        self.assertLess(still['mad_mean'], 0.05); self.assertEqual(still['still_ratio'], 1.0)
        self.assertGreater(moving['mad_mean'], 1.0)
        self.assertEqual(measure_motion(self.moving), moving)

    def test_reference_ratio(self):
        result = compare_motion(self.moving, self.moving)
        self.assertEqual(result['ratio'], 1.0)

    def test_shot_energy_warnings_exclude_cut(self):
        rows = shot_motion(self.edit, [{'shot_id': 'a', 'start_frame': 0, 'frame_count': 60, 'energy': 'high'},
                                       {'shot_id': 'b', 'start_frame': 60, 'frame_count': 60, 'energy': None}])
        self.assertEqual(rows[0]['pairs'], 59)
        self.assertTrue(any(w.startswith('motion_low') for w in rows[0]['warnings']))
        self.assertTrue(any(w.startswith('motion_stalls') for w in rows[0]['warnings']))
        self.assertEqual(rows[1]['energy'], 'calm')


if __name__ == '__main__':
    unittest.main()
