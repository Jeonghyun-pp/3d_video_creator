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


class RenderJobGatesTest(unittest.TestCase):
    """jobs.py / render_worker.py: layout is Workbench, a fallback render has its own fingerprint, gates run at resume."""

    def setUp(self):
        from studio.project import init_project
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(init_project('jobs_test', {'request': 'jobs', 'shots': [{'shot_id': 's', 'frame_count': 2}]}, self.tmp.name)['project_path'])

    def tearDown(self):
        self.tmp.cleanup()

    def version(self, subjects=()):
        from studio.common import file_hash, read_json, write_json
        directory = self.project / 'shots/s/versions/v0001'; directory.mkdir(parents=True)
        (directory / 'scene.blend').write_bytes(b'scene')
        write_json(directory / 'dependencies.json', {'scene_sha256': file_hash(directory / 'scene.blend')})
        shot = {**read_json(self.project / 'shots/s/shot.json'), 'scene_version': 'v0001', 'subjects': [{'subject_id': s} for s in subjects]}
        write_json(directory / 'shot.snapshot.json', shot)
        for subject in subjects:   # measured against a spec that has since changed
            write_json(self.project / 'subjects' / subject / 'spec.json', {'subject_id': subject, 'edited': True})
        write_json(directory / 'fidelity_report.json', {'passed': True, 'subjects': [{'subject_id': s, 'spec_sha256': 'old', 'passed': True, 'failures': []} for s in subjects]})
        return directory, shot

    def test_layout_renders_with_workbench(self):
        self.assertEqual(render_settings(PROJECT, SHOT, 'layout', [0], env={})['engine'], 'BLENDER_WORKBENCH')
        self.assertEqual(render_settings(PROJECT, SHOT, 'look', [0], env={})['engine'], 'CYCLES')

    def test_look_render_and_resume_check_fidelity_without_the_cli(self):
        from studio.common import StudioError, file_hash, write_json
        from studio.jobs import resume_job, submit_render
        directory, shot = self.version(['part'])
        with self.assertRaises(StudioError) as caught:
            submit_render(self.project, 's', 'v0001', 'look', [0])
        self.assertEqual(caught.exception.code, 'FIDELITY_STALE')
        job_path = next(self.project.glob('runs/*')) / 'jobs/job_1/job.json'
        write_json(job_path, {'job_id': 'job_1', 'status': 'failed', 'scene_path': str(directory / 'scene.blend'), 'scene_sha256': file_hash(directory / 'scene.blend'),
                              'shot': shot, 'scene_version': 'v0001', 'profile': 'look', 'cancel_path': str(job_path.parent / 'cancel'), 'attempt': 1})
        with self.assertRaises(StudioError) as caught:
            resume_job(self.project, 'job_1')
        self.assertEqual(caught.exception.code, 'FIDELITY_STALE')

    def job(self, log_text):
        from unittest import mock
        from studio import render_worker
        from studio.common import StudioError, file_hash, read_json, write_json
        directory, shot = self.version()
        run = next(self.project.glob('runs/*'))
        job_path = run / 'jobs/job_1/job.json'
        out = self.project / 'shots/s/renders/fp_requested'
        settings = {'width': 4, 'height': 4, 'fps': 30, 'samples': 16, 'engine': 'CYCLES', 'device': 'GPU', 'png_depth': '8', 'profile': 'look'}
        write_json(job_path, {'job_id': 'job_1', 'run_id': run.name, 'project_id': 'jobs_test', 'project_dir': str(self.project), 'shot_id': 's',
                              'scene_version': 'v0001', 'profile': 'look', 'fingerprint': 'fp_requested', 'scene_path': str(directory / 'scene.blend'),
                              'scene_sha256': file_hash(directory / 'scene.blend'), 'shot': shot, 'render_settings': settings, 'frames': [0],
                              'output_dir': str(out), 'status': 'queued', 'worker_token': 't', 'attempt': 1, 'render_wall_seconds': 600,
                              'cancel_path': str(job_path.parent / 'cancel'), 'progress_path': str(job_path.parent / 'progress.json'),
                              'created_at': '2026-10-06T00:00:00+00:00', 'updated_at': '2026-10-06T00:00:00+00:00'})
        calls = []

        def fake(command, job, job_path_, deadline, logfile):
            calls.append(dict(job['render_settings']))
            if len(calls) == 1:
                logfile.write_text(log_text)
                raise StudioError('RENDER_FAILED', 'Process exited 1', retryable=True)
            frames = Path(job['output_dir']) / 'frames'; frames.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (4, 4)).save(frames / 'frame_000000.png')
            write_json(Path(job['output_dir']) / 'renderer_actual.json', {'engine': job['render_settings']['engine']})
        with mock.patch.object(render_worker, '_run_process', side_effect=fake), mock.patch.object(render_worker, 'blender_binary', return_value='blender'):
            render_worker.run_worker(job_path, 't')
        return read_json(job_path), calls

    def test_fallback_render_gets_its_own_fingerprint(self):
        from studio.common import read_json
        job, calls = self.job('Metal: device lost\n')
        self.assertEqual(job['status'], 'complete')
        self.assertEqual((job['fallback_from'], calls[-1]['engine'], calls[-1]['device']), ('fp_requested', 'CYCLES', 'CPU'))
        self.assertNotEqual(job['fingerprint'], 'fp_requested')
        self.assertTrue(job['output_dir'].endswith(job['fingerprint']))
        self.assertEqual(read_json(Path(job['output_dir']) / 'render.json')['fingerprint'], job['fingerprint'])
        self.assertFalse((self.project / 'shots/s/renders/fp_requested/render.json').exists())

    def test_script_error_is_not_retried_on_cpu(self):
        job, calls = self.job('Traceback (most recent call last):\n  NameError: x\n')
        self.assertEqual((job['status'], len(calls)), ('failed', 1))
