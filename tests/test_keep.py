"""generate keep (studio/generative/keep.py): inside the keep mask the Blender render, outside it the generated take."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from studio.generative.keep import colour_transfer, keep_reasons, label_parts, merge, plan, seam_match


def clip(path, colour, size=(64, 48), frames=6):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=c={colour}:s={size[0]}x{size[1]}:r=30',
                    '-frames:v', str(frames), '-pix_fmt', 'yuv420p', str(path)], check=True)


class KeepMergeTest(unittest.TestCase):
    def test_keep_reasons_only_what_must_be_exact(self):
        shot = {'shot_id': 's', 'screen': {'keep': ['f/wall']}, 'scene': {'decals': [{'id': 'sign'}]},
                'labels': [{'anchor': 'f/wood/teaching_face'}, {'anchor': 'f/wall/face'}]}
        qa = {'parts': {'lost': ['f/eps']}, 'structure': {'passed': False}}
        ids = {'f', 'f/eps', 'f/wall', 'f/wood'}   # the version's objects: anchors resolve to the longest id they start with
        self.assertEqual(keep_reasons(shot, qa, ids), {'f/eps': 'lost', 'f/wall': 'declared', 'f/wood': 'label', 'sign': 'decal'})
        shot['labels'].append({'anchor': 'pump/center'})   # names no object of this version: left out
        self.assertEqual(label_parts(shot, ids | {'pump'}), ['f/wood', 'f/wall', 'pump'])
        self.assertEqual(label_parts(shot, ids), ['f/wood', 'f/wall'])
        qa['structure']['passed'] = True     # measured anchors: labelled parts may stay generated
        self.assertNotIn('f/wood', keep_reasons(shot, qa, ids))
        self.assertEqual(keep_reasons({'shot_id': 's'}, {'parts': {'lost': []}}), {})   # nothing: the take stays 100 % generated

    def test_colour_transfer_matches_mean_and_spread_and_refuses_a_relit_take(self):
        near = colour_transfer([(120, 30), (110, 30), (100, 30)], [(100, 25), (100, 25), (100, 25)])
        self.assertFalse(near['far']); self.assertEqual(near['gain'], [1.2, 1.2, 1.2]); self.assertEqual(near['offset'], [0.0, -10.0, -20.0])
        relit = colour_transfer([(200, 30), (190, 30), (180, 30)], [(60, 30), (60, 30), (60, 30)])
        self.assertTrue(relit['far'])

    def test_seam_grade_brings_the_kept_part_toward_the_take(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            clip(tmp / 'take.mp4', '0x8C8C8C', size=(96, 64)); clip(tmp / 'render.mp4', '0x707070', size=(96, 64))
            for i in range(6):
                mask = Image.new('L', (96, 64), 0); mask.paste(255, (32, 16, 64, 48)); mask.save(tmp / f'm_{i:06d}.png')
            seam = seam_match(tmp / 'take.mp4', tmp / 'render.mp4', str(tmp / 'm_%06d.png'), 6, (96, 64))
            self.assertTrue(seam['applied']); self.assertGreater(seam['offset'][0], 15)
            merge(tmp / 'take.mp4', tmp / 'render.mp4', str(tmp / 'm_%06d.png'), tmp / 'out.mp4', 6, (96, 64), grade=seam)
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(tmp / 'out.mp4'), '-vf', 'select=eq(n\\,3)', '-frames:v', '1',
                            str(tmp / 'f.png')], check=True)
            inside = Image.open(tmp / 'f.png').convert('RGB').getpixel((48, 32))
            self.assertLess(abs(inside[0] - 0x8C), 8)   # the grey render inside now reads like the take around it

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


    def test_generate_only_is_the_inverse(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            clip(tmp / 'take.mp4', 'red'); clip(tmp / 'render.mp4', 'blue')
            for i in range(6):   # the person: left half; only it comes from the take
                mask = Image.new('L', (64, 48), 0); mask.paste(255, (0, 0, 32, 48)); mask.save(tmp / f'm_{i:06d}.png')
            merge(tmp / 'take.mp4', tmp / 'render.mp4', str(tmp / 'm_%06d.png'), tmp / 'out.mp4', 6, (64, 48), invert=True)
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(tmp / 'out.mp4'), '-vf', 'select=eq(n\\,3)', '-frames:v', '1',
                            str(tmp / 'f.png')], check=True)
            frame = Image.open(tmp / 'f.png').convert('RGB')
            self.assertGreater(frame.getpixel((8, 24))[0], 180)    # the region handed to the model: the take
            self.assertGreater(frame.getpixel((56, 24))[2], 180)   # everything else: Blender

    def test_plan(self):
        shot = {'shot_id': 's', 'screen': {'keep': ['col'], 'generate_only': ['inspector', '@background']}}
        self.assertEqual(plan(shot), ('generate_only', ['inspector'], True))
        self.assertEqual(plan(shot, ['col']), ('keep', ['col'], False))     # explicit parts: keep those
        self.assertEqual(plan({'shot_id': 's', 'screen': {'keep': ['col']}}), ('keep', ['col'], False))


if __name__ == '__main__':
    unittest.main()
