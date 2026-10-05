"""Composition styles (studio/composition_style.py): horizon and sky are measured right; learn keeps hashes only."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from studio import composition_style as cs


def street(horizon_v, skyline_v, size=(240, 428)):
    """Synthetic one-point street: sky above the skyline, kerb and lane lines converging on (0.5, horizon_v)."""
    w, h = size
    image = Image.new('RGB', size, (30, 40, 80))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, skyline_v * h, w, h), fill=(70, 70, 75))
    vx, vy = w / 2, horizon_v * h
    for x in (-0.6, -0.2, 0.2, 0.6, 1.0, 1.4, 1.8):
        draw.line((vx, vy, x * w, h), fill=(230, 230, 220), width=3)
    for side in (-1, 1):                                  # facades: converging roof lines
        for k in range(4):
            y0 = skyline_v * h + k * 30
            draw.line((vx + side * 8, vy, vx + side * w, y0 - (vy - y0) * 0.4), fill=(200, 190, 150), width=2)
    return image


class CompositionTest(unittest.TestCase):
    def test_horizon_and_sky_of_a_synthetic_street(self):
        row = cs.frame_features(street(0.40, 0.33).resize((cs.WIDTH, round(cs.WIDTH * 428 / 240))))
        self.assertAlmostEqual(row['vp_v'], 0.40, delta=0.03)
        self.assertAlmostEqual(row['skyline_c'], 0.33, delta=0.04)
        self.assertAlmostEqual(row['sky_share'], 0.33, delta=0.06)
        low = cs.frame_features(street(0.20, 0.12).resize((cs.WIDTH, round(cs.WIDTH * 428 / 240))))
        self.assertLess(low['sky_share'], row['sky_share'])

    def test_learn_keeps_hashes_and_check_flags_a_low_horizon(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cs, 'STYLES', Path(temp) / 'styles'):
            def clip(name, v, sky):
                frames = Path(temp) / name; frames.mkdir()
                for i in range(6):
                    street(v, sky).save(frames / f'{i:02d}.png')
                out = Path(temp) / f'{name}.mp4'
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-framerate', '6', '-i', str(frames / '%02d.png'), '-c:v', 'libx264',
                                '-crf', '4', '-pix_fmt', 'yuv420p', str(out)], check=True)
                return str(out)
            ref = clip('ref', 0.40, 0.33)
            cs.learn('open', [ref])
            stored = (Path(temp) / 'styles' / 'open.json').read_text()
            self.assertNotIn(ref, stored)
            self.assertTrue(cs.check('open', ref)['ok'])
            self.assertFalse(cs.check('open', clip('down', 0.19, 0.08))['ok'])


if __name__ == '__main__':
    unittest.main()
