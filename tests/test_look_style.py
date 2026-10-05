"""Look styles (studio/look_style.py): bright points are counted right; learn stores only hashes; check flags outliers."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from studio import look_style


def clip(path, dots, size=(448, 256), colour=(255, 230, 180), background=(20, 22, 30)):
    frames = Path(path).parent / 'frames'
    frames.mkdir(exist_ok=True)
    for i in range(8):
        image = Image.new('RGB', size, background)
        draw = ImageDraw.Draw(image)
        for k in range(dots):
            x, y = 12 + (k * 37) % (size[0] - 24), 12 + (k * 23) % (size[1] - 24)
            draw.rectangle((x, y, x + 2, y + 2), fill=colour)
        image.save(frames / f'f{i:02d}.png')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-framerate', '8', '-i', str(frames / 'f%02d.png'), '-c:v', 'libx264', '-crf', '0',
                    '-pix_fmt', 'yuv444p', str(path)], check=True)
    return path


class LookStyleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_counts_small_bright_points(self):
        rows = look_style.features(str(clip(self.root / 'a.mp4', 40)))
        mp = 448 * 256 / 1e6
        self.assertTrue(all(abs(r['bright_points_per_mp'] * mp - 40) <= 3 for r in rows), [r['bright_points_per_mp'] * mp for r in rows])
        self.assertTrue(all(r['luma_p50'] < 40 for r in rows))

    def test_learn_keeps_hashes_only_and_check_flags_a_sparse_clip(self):
        with patch.object(look_style, 'STYLES', self.root / 'styles'):
            (self.root / 'dense').mkdir()
            dense = clip(self.root / 'dense' / 'v.mp4', 120)
            result = look_style.learn('night', [str(dense)])
            stored = (self.root / 'styles' / 'night.json').read_text()
            self.assertNotIn(str(dense), stored)                       # no path, no frames: the reference stays private
            self.assertTrue(result['warnings'])                         # single reference
            (self.root / 'sparse').mkdir()
            sparse = look_style.check('night', str(clip(self.root / 'sparse' / 'v.mp4', 5)))
            self.assertIn('bright_points_per_mp', sparse['misses'])
            self.assertTrue(look_style.check('night', str(dense))['ok'])


if __name__ == '__main__':
    unittest.main()
