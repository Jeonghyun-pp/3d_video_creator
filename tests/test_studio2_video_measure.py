"""What a generated clip does over time, measured (studio2/video_measure.py): made clips whose camera and timing are known -
a push of a known size, a push that drifts back out, a flicker, a dropped stretch, a duplicated frame."""
from pathlib import Path
import random
import subprocess
import tempfile
import unittest


def textured(target, w=540, h=960, seed=3):
    """A still with structure everywhere (blocks of grey), so a zoom or shift of it is measurable."""
    from PIL import Image, ImageDraw
    rnd = random.Random(seed)
    im = Image.new('L', (w, h), 128)
    d = ImageDraw.Draw(im)
    for _ in range(900):
        x, y, s = rnd.randrange(w), rnd.randrange(h), rnd.randrange(8, 60)
        d.rectangle([x, y, x + s, y + s], fill=rnd.randrange(256))
    im.convert('RGB').save(target)
    return target


def clip_of(still, target, zoom, frames=60, vf=''):
    """A 30 fps clip of the still zoomed about its middle by the ffmpeg expression `zoom` (in = the frame number)."""
    graph = (f"scale=1080:1920,zoompan=z='{zoom}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=540x960:fps=30" + (',' + vf if vf else ''))
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-loop', '1', '-i', str(still), '-vf', graph, '-frames:v', str(frames), '-pix_fmt', 'yuv420p',
                    str(target)], check=True)
    return target


class MeasureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.still = textured(root / 'still.png')
        cls.push = clip_of(cls.still, root / 'push.mp4', '1+0.1*in/59')
        cls.back = clip_of(cls.still, root / 'back.mp4', 'if(lt(in,30),1+0.12*in/30,1.12-0.12*(in-30)/29)')
        cls.flicker = clip_of(cls.still, root / 'flicker.mp4', '1+0.1*in/59', vf="eq=brightness=0.05:enable='between(n,20,40)*mod(n,2)'")
        cls.root = root

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_a_push_of_a_known_size_is_measured_its_size_and_not_reversed(self):
        from studio2 import video_measure as vm
        got = vm.measure(self.push, sign=1, asked_amount=0.1)
        self.assertAlmostEqual(got['camera']['amount'], 0.1, delta=0.025)
        self.assertLess(got['camera']['reverse'], 0.1)
        self.assertAlmostEqual(got['camera']['ratio'], 1.0, delta=0.25)

    def test_a_push_that_drifts_back_out_is_a_reverse(self):
        from studio2 import video_measure as vm
        got = vm.measure(self.back, sign=1, asked_amount=0.12)
        self.assertGreater(got['camera']['reverse'], 0.6)     # it came back nearly all the way (fab c03, 2026-10-27)

    def test_a_held_camera_that_moves_is_measured_moving(self):
        from studio2 import video_measure as vm
        self.assertGreater(vm.measure(self.push, sign=0)['camera']['held'], 0.07)

    def test_a_flicker_stands_out_of_a_steady_clip(self):
        from studio2 import video_measure as vm
        steady, flick = vm.measure(self.push)['flicker']['score'], vm.measure(self.flicker)['flicker']['score']
        self.assertLess(steady, 1.0)
        self.assertGreater(flick, 5 * max(steady, 0.5))

    def test_a_dropped_stretch_is_a_jump_and_a_duplicated_frame_a_repeat(self):
        from studio2 import video_measure as vm
        images = vm.frames(self.push)
        clean = vm.jumps(images)
        dropped = vm.jumps(images[:25] + images[37:])
        doubled = vm.jumps([images[i - 1] if i % 4 == 0 and i else im for i, im in enumerate(images)])
        self.assertGreater(dropped['jump'], 2.5 * clean['jump'])
        self.assertEqual(clean['repeat'], 0)
        self.assertGreater(doubled['repeat'], 0.15)


if __name__ == '__main__':
    unittest.main()
