"""richness(): more modelled structure -> more edge fraction, coverage and distinct (trackable) tiles."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from PIL import Image, ImageDraw

from studio.qa_generative import richness


def clay_video(path, boxes, frames=12, size=(448, 796)):
    """Grey 'clay' frames: flat background plus the given number of shaded boxes at fixed pseudo-random spots."""
    tmp = Path(path).parent / f'f_{boxes}'
    tmp.mkdir()
    for f in range(frames):
        image = Image.new('RGB', size, (128, 128, 128))
        draw = ImageDraw.Draw(image)
        for k in range(boxes):
            x, y = (k * 97 + 13) % (size[0] - 60), (k * 151 + 29) % (size[1] - 60)
            w, h = 20 + (k * 37) % 40, 20 + (k * 53) % 40
            draw.rectangle((x + f, y, x + f + w, y + h), fill=(60 + (k * 41) % 150,) * 3)
        image.save(tmp / f'{f:04d}.png')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-framerate', '30', '-i', str(tmp / '%04d.png'), '-pix_fmt', 'yuv420p', '-c:v', 'libx264',
                    '-crf', '12', str(path)], check=True)


class RichnessTest(unittest.TestCase):
    def test_more_structure_scores_higher(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = []
            for boxes in (0, 6, 40):
                video = Path(tmp) / f'clay_{boxes}.mp4'
                clay_video(video, boxes)
                results.append(richness(video, samples=4))
            flat, some, many = results
            self.assertEqual(flat['edge_fraction'], 0)
            self.assertEqual(flat['frames_below_min_edges'], 4)
            self.assertLess(some['edge_fraction'], many['edge_fraction'])
            self.assertLess(some['coverage'], many['coverage'])
            self.assertLessEqual(some['distinct_tiles'], many['distinct_tiles'])
            self.assertGreater(many['distinct_tiles'], 0)


if __name__ == '__main__':
    unittest.main()
