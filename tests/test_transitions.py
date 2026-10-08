"""Transitions keep every shot's frame count (narration and subtitles are timed to them) and change only the frames
around the cut."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from studio import edit


def segment(path, colour, frames=30, size=(64, 48)):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=c={colour}:s={size[0]}x{size[1]}:r=30', '-frames:v', str(frames),
                    '-pix_fmt', 'yuv420p', str(path)], check=True)


def frames_of(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0', '-show_entries', 'stream=nb_read_frames',
                          '-of', 'csv=p=0', str(path)], capture_output=True, text=True).stdout
    return int(out.strip())


def pixel(path, n, tmp):
    target = tmp / f'p{n}.png'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(path), '-vf', f'select=eq(n\\,{n})', '-frames:v', '1', str(target)], check=True)
    return Image.open(target).convert('RGB').getpixel((32, 24))


class TransitionTest(unittest.TestCase):
    def run_kind(self, kind, **extra):
        tmp = Path(tempfile.mkdtemp())
        a, b = tmp / 'video_000.mp4', tmp / 'video_001.mp4'
        segment(a, 'red'); segment(b, 'blue')
        shots = [{'shot': {}, 'frame_count': 30}, {'shot': {'transition': {'kind': kind, **extra}}, 'frame_count': 30}]
        out = edit.apply_transitions([a, b], shots, 30, 64, 48, tmp)
        self.assertEqual([frames_of(p) for p in out], [30, 30])
        return out, tmp

    def test_dip_goes_through_black(self):
        (a, b), tmp = self.run_kind('dip', frames=10)
        self.assertLess(max(pixel(a, 29, tmp)), 40); self.assertLess(max(pixel(b, 0, tmp)), 40)
        self.assertGreater(pixel(a, 10, tmp)[0], 200); self.assertGreater(pixel(b, 20, tmp)[2], 200)   # away from the cut: untouched

    def test_dissolve_lays_the_last_frame_over_the_head(self):
        (a, b), tmp = self.run_kind('dissolve', frames=8)
        first = pixel(b, 0, tmp)
        self.assertGreater(first[0], 150)                     # starts on the outgoing red
        self.assertGreater(pixel(b, 15, tmp)[2], 200)         # ends on its own blue

    def test_whip_keeps_the_counts(self):
        (a, b), _ = self.run_kind('whip', frames=6)
        self.assertNotEqual((a.name, b.name), ('video_000.mp4', 'video_001.mp4'))   # both sides of the cut re-encoded

    def test_cut_changes_nothing(self):
        (a, b), _ = self.run_kind('cut')
        self.assertEqual((a.name, b.name), ('video_000.mp4', 'video_001.mp4'))


if __name__ == '__main__':
    unittest.main()
