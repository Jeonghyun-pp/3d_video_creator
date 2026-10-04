from pathlib import Path
import shutil
import statistics
import subprocess
import tempfile
import time
import unittest

from studio.qa_generative import ANCHOR_MAX_ERROR_RATIO, IOU_THRESHOLD, flicker, generative_checks, morph, structure, text

FRAMES = 240
# Points on testsrc whose structure survives a hue rotation (bar/circle corners, not the rainbow band).
POINTS = [(0.5, 0.5), (0.8, 0.7)]


def encode(path, args):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *args, '-pix_fmt', 'yuv420p', '-c:v', 'libx264', '-crf', '18', str(path)], check=True)


def anchors(points=POINTS, frames=FRAMES):
    return [{'frame': f, 'u': u, 'v': v, 'visible': True, 'label_id': f'l{i}'} for f in range(frames) for i, (u, v) in enumerate(points)]


class GenerativeQATest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.base = root / 'base.mp4'
        encode(cls.base, ['-f', 'lavfi', '-i', 'testsrc=s=448x256:r=30', '-frames:v', str(FRAMES)])
        variants = {'flash': "eq=brightness=0.5:enable='eq(n,40)'",
                    'restyle': 'gblur=sigma=1.5,hue=h=90:s=1.3,eq=brightness=0.06',
                    'shift5': 'crop=iw*0.95:ih:0:0,pad=448:ih:iw*0.05263:0',   # content moves right by 5 % of the width
                    'shift6': 'pad=iw+6:ih:6:0,crop=448:ih:0:0'}               # content moves right by exactly 6 px
        for name, vf in variants.items():
            setattr(cls, name, root / f'{name}.mp4')
            encode(root / f'{name}.mp4', ['-i', str(cls.base), '-vf', vf])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_smooth_clip_has_no_flicker_or_morph_flags(self):
        self.assertEqual(flicker(self.base)['flagged'], [])
        self.assertEqual(morph(self.base)['flagged'], [])

    def test_flash_frame_is_flagged(self):
        result = flicker(self.flash)
        self.assertEqual(result['status'], 'warning')
        self.assertIn(40, [f['frame'] for f in result['flagged']])
        self.assertIn(40, [f['frame'] for f in morph(self.flash)['flagged']])

    def test_identical_passes(self):
        result = structure(self.base, self.base, anchors())
        self.assertTrue(result['passed'], result['reasons'])
        self.assertEqual(result['iou']['median'], 1.0)
        self.assertEqual(result['anchor_error']['max_ratio'], 0.0)

    def test_blurred_hue_shifted_restyle_passes(self):
        result = structure(self.base, self.restyle, anchors())
        self.assertTrue(result['passed'], result['reasons'])
        self.assertGreaterEqual(result['iou']['median'], IOU_THRESHOLD)
        self.assertLessEqual(result['anchor_error']['max_ratio'], ANCHOR_MAX_ERROR_RATIO)

    def test_five_percent_shift_fails_on_edges_alone_and_with_anchors(self):
        edges_only = structure(self.base, self.shift5)
        self.assertFalse(edges_only['passed'])
        self.assertLess(edges_only['iou']['median'], IOU_THRESHOLD)
        self.assertFalse(structure(self.base, self.shift5, anchors())['passed'])

    def test_anchor_tracking_recovers_known_shift(self):
        start = time.monotonic()
        result = structure(self.base, self.shift6, anchors())
        self.assertLess(time.monotonic() - start, 60)   # 240 frames at 448 px
        samples = result['anchor_error']['samples']
        self.assertGreater(len(samples), 20)
        for sample in samples:
            self.assertLessEqual(abs(sample['dx_px'] - 6), 1, sample)
            self.assertLessEqual(abs(sample['dy_px']), 1, sample)
        self.assertAlmostEqual(result['anchor_error']['max_ratio'] * 448, 6, delta=1)
        self.assertFalse(result['passed'])   # 6 px = 1.3 % > 1 %
        measured = [row for row in result['anchors_2d'] if row['label_id'] == 'l0']
        self.assertAlmostEqual(statistics.median(row['u'] for row in measured), 0.5 + 6 / 448, delta=1 / 448)

    def test_text_and_aggregate(self):
        if not shutil.which('tesseract'):
            self.assertEqual(text(self.base)['status'], 'not_available')
        result = generative_checks(self.flash, self.base, anchors())
        self.assertTrue(result['passed'])        # one flash frame is a warning, not a structure failure
        self.assertTrue(any(w.startswith('flicker') for w in result['warnings']))
        self.assertIsNone(generative_checks(self.base)['passed'])


if __name__ == '__main__':
    unittest.main()
