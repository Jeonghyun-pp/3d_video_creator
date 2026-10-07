"""generate keep (studio/generative/keep.py): inside the keep mask the Blender render, outside it the generated take."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from studio.generative.keep import merge


def clip(path, colour, size=(64, 48), frames=6):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=c={colour}:s={size[0]}x{size[1]}:r=30',
                    '-frames:v', str(frames), '-pix_fmt', 'yuv420p', str(path)], check=True)


class KeepMergeTest(unittest.TestCase):
    def test_render_inside_the_mask_take_outside(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            clip(tmp / 'take.mp4', 'red'); clip(tmp / 'render.mp4', 'blue')
            for i in range(6):   # left half kept
                mask = Image.new('L', (64, 48), 0); mask.paste(255, (0, 0, 32, 48)); mask.save(tmp / f'm_{i:06d}.png')
            merge(tmp / 'take.mp4', tmp / 'render.mp4', str(tmp / 'm_%06d.png'), tmp / 'out.mp4', 6, (64, 48))
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(tmp / 'out.mp4'), '-vf', 'select=eq(n\\,3)', '-frames:v', '1',
                            str(tmp / 'f.png')], check=True)
            frame = Image.open(tmp / 'f.png').convert('RGB')
            left, right = frame.getpixel((8, 24)), frame.getpixel((56, 24))
            self.assertGreater(left[2], 180); self.assertLess(left[0], 60)      # kept: the render's blue
            self.assertGreater(right[0], 180); self.assertLess(right[2], 60)    # elsewhere: the take's red
            out = subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0', '-show_entries',
                                  'stream=nb_read_frames,color_space', '-of', 'csv=p=0', str(tmp / 'out.mp4')], capture_output=True, text=True).stdout
            self.assertIn('6', out); self.assertIn('bt709', out)


if __name__ == '__main__':
    unittest.main()
