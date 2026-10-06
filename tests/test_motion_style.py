from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from PIL import Image, ImageDraw

from studio import motion_style


def synthetic(path, offsets, size=(240, 426), fps=30):
    """A textured card translated by offsets[i] px per frame (burst, then hold)."""
    tmp = Path(path).parent / 'frames'
    tmp.mkdir()
    base = Image.new('L', (size[0] * 3, size[1]), 0)
    d = ImageDraw.Draw(base)
    for i in range(0, size[0] * 3, 12):
        d.rectangle((i, 0, i + 5, size[1]), fill=200 if (i // 12) % 2 else 90)
    x = 0.0
    for i, step in enumerate(offsets):
        x += step
        base.crop((int(x), 0, int(x) + size[0], size[1])).save(tmp / f'{i:04d}.png')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-framerate', str(fps), '-i', str(tmp / '%04d.png'), '-pix_fmt', 'yuv420p', str(path)], check=True)
    shutil.rmtree(tmp)


class MotionStyleTest(unittest.TestCase):
    def test_burst_then_hold_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / 'burst.mp4'
            offsets = [0] + [8] * 25 + [3] * 25 + [0] * 40  # 90 frames: rush, slow, still
            synthetic(video, offsets)
            (_, count, env), = motion_style.video_shots(video, cuts=[])
            self.assertEqual(count, 91)
            self.assertGreater(env['burst_share'], 0.45)
            self.assertAlmostEqual(env['hold_frac'], 40 / 90, delta=0.1)
            self.assertLess(env['peak_t'], 0.3)

    def test_pulldown_repeats_are_not_holds(self):
        diffs = [5.0, 5.0, 5.0, 5.0, 0.0] * 20
        kept, ratio = motion_style.drop_pulldown(diffs)
        self.assertEqual(ratio, 0.19); self.assertEqual(len(kept), 81)  # the trailing repeat has no next neighbour
        self.assertEqual(motion_style.drop_pulldown([5.0] * 50)[1], 0.0)

    def test_judge_and_check(self):
        style = {'features': {f: {'p25': 1.0, 'median': 2.0, 'p75': 3.0, 'n': 5} for f in motion_style.FEATURES}}
        inside = {f: 2.0 for f in motion_style.FEATURES}
        self.assertTrue(all(v['ok'] for v in motion_style.judge(style, inside).values()))
        outside = dict(inside, burst_share=9.0)
        self.assertFalse(motion_style.judge(style, outside)['burst_share']['ok'])
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(motion_style, 'STYLES', Path(tmp)):
            video = Path(tmp) / 'v.mp4'
            synthetic(video, [0] + [6] * 30 + [0] * 30)
            learned = motion_style.learn('unit', [video])
            self.assertEqual(learned['shots'], 1); self.assertTrue(learned['warnings'])
            result = motion_style.check('unit', video)  # same detected shot as learned
            self.assertEqual(result['in_style'], 1)


if __name__ == '__main__':
    unittest.main()


class StyleBlurTest(unittest.TestCase):
    def test_style_blur_target_reaches_every_styled_shot(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'studio/blender_ops'))
        import camera_moves_core as core
        from studio.blender import _motion_style
        style = {'defaults': {'motion_blur': {'target_blur_px': 14}}}
        self.assertEqual(core.realism_with_style({}, style), {'target_blur_px': 14})
        self.assertEqual(core.realism_with_style({'realism': {'target_blur_px': 4}}, style), {'target_blur_px': 4})
        self.assertEqual(core.realism_with_style({'realism': {'shake': 'none'}}, None), {'shake': 'none'})
        self.assertIsNotNone(_motion_style({'camera': {'motion_style': 'archcutaway'}}))    # no move: still the shot's style
