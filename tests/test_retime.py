"""Generated takes to 30 fps (2026-10-08): duplicating frames of a 24 fps take stutters (every fourth frame repeats);
the default retime interpolates with RIFE. Measured against a clip drawn at the exact 30 fps instants (testsrc2 is
time-based), interpolation is nearer the truth and never repeats a frame; GENERATED_JUDDER says when a clip does."""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from studio.common import StudioError, rife_binary
from studio.generative.clip import RETIME_DEFAULT, retime
from studio.qa_generative import judder, repeat_share

try:
    rife_binary()
    HAVE_RIFE = True
except StudioError:
    HAVE_RIFE = False


def synthetic(path, fps, seconds=2):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc2=s=360x640:r={fps}:d={seconds}', '-c:v', 'libx264',
                    '-crf', '12', '-pix_fmt', 'yuv420p', str(path)], check=True)


def psnr(a, b, frames):
    out = subprocess.run(['ffmpeg', '-v', 'info', '-i', str(a), '-i', str(b), '-lavfi', '[0:v][1:v]psnr', '-frames:v', str(frames),
                          '-f', 'null', '-'], capture_output=True, text=True).stderr
    return float(re.findall(r'average:([0-9.]+)', out)[-1])


class RepeatShareTest(unittest.TestCase):
    def test_isolated_repeats_count_and_holds_do_not(self):
        pulldown = [5.0, 5.0, 5.0, 0.01] * 10                 # every fourth pair a repeat
        self.assertGreater(repeat_share(pulldown), 0.2)
        hold = [5.0] * 10 + [0.01] * 20 + [5.0] * 10           # a real hold
        self.assertEqual(repeat_share(hold), 0.0)


@unittest.skipUnless(HAVE_RIFE, 'RIFE not installed (scripts/install_rife.sh)')
class RetimeTest(unittest.TestCase):
    def test_interpolate_is_the_default_and_beats_duplicate(self):
        self.assertEqual(RETIME_DEFAULT, 'interpolate')
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            synthetic(tmp / 'take24.mp4', 24)
            synthetic(tmp / 'truth30.mp4', 30)
            frames = 57
            for method in ('duplicate', 'interpolate'):
                source = retime(tmp / 'take24.mp4', tmp / f'{method}.mp4', frames, 360, 640, 0.0, method)
                self.assertAlmostEqual(source['fps'], 24.0, places=3)
            smooth, choppy = psnr(tmp / 'interpolate.mp4', tmp / 'truth30.mp4', frames), psnr(tmp / 'duplicate.mp4', tmp / 'truth30.mp4', frames)
            self.assertGreater(smooth, choppy + 2.0)                     # measured 31.8 vs 27.1 dB at 720x1280
            self.assertEqual(judder(str(tmp / 'interpolate.mp4'))['warnings'], [])
            self.assertTrue(judder(str(tmp / 'duplicate.mp4'))['warnings'][0].startswith('GENERATED_JUDDER'))


if __name__ == '__main__':
    unittest.main()
