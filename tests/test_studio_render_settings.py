import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from PIL import Image

from studio.common import h264_args
from studio.jobs import freeze_renderer, render_settings

PROJECT = {'output': {'width': 1080, 'height': 1920, 'fps': 30}}
SHOT = {'render': {'engine': 'CYCLES'}}


class RenderSettingsTest(unittest.TestCase):
    def test_profile_defaults_and_animation_floor(self):
        self.assertEqual(render_settings(PROJECT, SHOT, 'look', [0, 90], env={})['samples'], 64)
        self.assertEqual(render_settings(PROJECT, SHOT, 'layout', list(range(60)), env={})['samples'], 16)
        final = render_settings(PROJECT, SHOT, 'final', list(range(60)), env={})
        self.assertEqual((final['samples'], final['png_depth'], final['adaptive_threshold'], final['width']), (128, '16', .01, 1080))
        self.assertTrue(final['animation'])

    def test_explicit_samples_skip_the_floor(self):
        self.assertEqual(render_settings(PROJECT, SHOT, 'review', list(range(60)), samples_override=8, env={})['samples'], 8)
        shot = {'render': {'engine': 'CYCLES', 'samples': 24}}
        self.assertEqual(render_settings(PROJECT, shot, 'review', list(range(60)), env={})['samples'], 24)

    def test_device_defaults_to_gpu_and_respects_env(self):
        self.assertEqual(render_settings(PROJECT, SHOT, 'look', [0], env={})['device'], 'GPU')
        self.assertEqual(render_settings(PROJECT, SHOT, 'look', [0], env={'STUDIO_RENDER_DEVICE': 'CPU'})['device'], 'CPU')

    def test_legacy_job_resume_freezes_only_recorded_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            job_path = Path(temporary) / 'job' / 'job.json'
            code = job_path.parent / 'code'; code.mkdir(parents=True)
            from studio.common import file_hash, REPO
            hashes = {}
            for name in ('render_frames.py', 'scene_tools.py'):
                (code / name).write_bytes((REPO / 'studio/blender_ops' / name).read_bytes()); hashes[name] = file_hash(code / name)
            job = {'execution_code_hashes': hashes}
            freeze_renderer(job, job_path)
            self.assertEqual(set(job['execution_code_hashes']), {'render_frames.py', 'scene_tools.py'})
            fresh = {}
            freeze_renderer(fresh, Path(temporary) / 'new' / 'job.json')
            self.assertEqual(set(fresh['execution_code_hashes']), {'render_frames.py', 'scene_tools.py', 'render_profile.py'})


class Bt709Test(unittest.TestCase):
    def test_h264_bt709_roundtrip(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            Image.new('RGB', (64, 64), (200, 50, 50)).save(root / 'f.png')
            def encode(args, name):
                out = root / name
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-loop', '1', '-i', str(root / 'f.png'), '-frames:v', '2', *args, str(out)], check=True)
                return out
            good = encode(h264_args(), 'good.mp4')
            legacy = encode(['-c:v', 'libx264', '-pix_fmt', 'yuv420p'], 'legacy.mp4')
            tags = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(good)], capture_output=True, text=True).stdout)['streams'][0]
            self.assertEqual((tags.get('color_space'), tags.get('color_primaries'), tags.get('color_transfer')), ('bt709', 'bt709', 'bt709'))
            def decoded(path):  # a player decodes tagged/untagged HD video as BT.709
                raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-vf', 'scale=in_color_matrix=bt709:in_range=tv,format=rgb24',
                                      '-frames:v', '1', '-f', 'rawvideo', '-'], capture_output=True, check=True).stdout
                return raw[32 * 64 * 3 + 32 * 3: 32 * 64 * 3 + 32 * 3 + 3]
            self.assertTrue(all(abs(a - b) <= 3 for a, b in zip(decoded(good), (200, 50, 50))), list(decoded(good)))
            self.assertTrue(any(abs(a - b) > 3 for a, b in zip(decoded(legacy), (200, 50, 50))), list(decoded(legacy)))


if __name__ == '__main__':
    unittest.main()
