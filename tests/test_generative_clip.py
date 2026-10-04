from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from studio.common import StudioError, read_json
from studio.generative import clip as clipmod
from studio.project import init_project, load_shot
from studio.routing import approve


def lavfi(path, seconds, fps, size='640x360'):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc=s={size}:r={fps}:d={seconds}', '-pix_fmt', 'yuv420p', str(path)], check=True)


class ClipTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        gen = {'provider': 'fal', 'model': 'veo-3.1', 'operation': 'image_to_video', 'prompt_ref': 'prompts/city.txt', 'duration_seconds': 8,
               'usd_per_second': None, 'max_attempts': 1, 'text_in_frame': False, 'ai_disclosure': True,
               'inputs': [{'kind': 'first_frame', 'path': 'frames/first.png'}]}
        brief = {'request': 'clip test', 'output': {'width': 1080, 'height': 1920, 'fps': 30, 'target_seconds': 8},
                 'shots': [{'shot_id': 'city', 'frame_count': 240, 'route_features': ['real_place_atmosphere'], 'generative': gen}]}
        self.path = Path(init_project('clip_test', brief, self.temp.name)['project_path'])
        (self.path / 'prompts').mkdir(); (self.path / 'prompts/city.txt').write_text('Dusk river city aerial. No text, no letters.')
        (self.path / 'frames').mkdir()
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=64x64', '-frames:v', '1', str(self.path / 'frames/first.png')], check=True)
        approve(self.path, 'city', 'User: 도시 인트로 생성 승인 2달러', budget_usd=2)
        self.posts = 0

    def tearDown(self):
        self.temp.cleanup()

    def fake_paid(self, seconds):
        def paid(endpoint, arguments, dest, **kwargs):
            self.posts += 1
            self.assertEqual(endpoint, 'fal-ai/veo3.1/image-to-video')
            self.assertEqual((arguments['duration'], arguments['aspect_ratio'], arguments['generate_audio']), ('8s', '9:16', False))
            Path(dest).mkdir(parents=True, exist_ok=True)
            video = Path(dest) / 'out.mp4'; lavfi(video, seconds, 24, '1080x1920')
            return {'request_id': 'r1', 'estimated_usd': 1.6, 'files': [{'path': str(video)}]}
        return paid

    def test_generate_retimes_to_30fps_bt709_and_reuses(self):
        with patch.object(clipmod, 'paid_call', side_effect=self.fake_paid(8)):
            result = clipmod.generate_clip(self.path, 'city', allow_paid=True, max_usd=2)
            again = clipmod.generate_clip(self.path, 'city', allow_paid=True, max_usd=2)
        self.assertEqual(self.posts, 1); self.assertTrue(again['reused'])
        info = clipmod._probe(result['clip_path'])
        self.assertEqual((info['frames'], round(info['fps']), info['color_space']), (240, 30, 'bt709'))
        self.assertEqual((result['ai_generated'], result['use_status'], result['profile']), (True, 'review_only', 'final'))

    def test_too_short_is_rejected(self):
        with patch.object(clipmod, 'paid_call', side_effect=self.fake_paid(6)), self.assertRaises(StudioError) as error:
            clipmod.generate_clip(self.path, 'city', allow_paid=True, max_usd=2)
        self.assertEqual(error.exception.code, 'GENERATION_TOO_SHORT')

    def test_selection_and_edit_lookup(self):
        from studio.edit import latest_render
        with patch.object(clipmod, 'paid_call', side_effect=self.fake_paid(8)):
            first = clipmod.generate_clip(self.path, 'city', allow_paid=True, max_usd=2)
        second = self.path / 'shots/city/generated/other'
        shutil.copytree(Path(first['clip_path']).parent, second)
        manifest = read_json(second / 'clip.json'); manifest['clip_path'] = str(second / 'clip.mp4')
        from studio.common import write_json; write_json(second / 'clip.json', manifest)
        with self.assertRaises(StudioError) as error:
            latest_render(self.path, load_shot(self.path, 'city'), 240, 'rough')
        self.assertEqual(error.exception.code, 'GENERATION_SELECTION_REQUIRED')
        clipmod.select_take(self.path, 'city', 'other')
        chosen = latest_render(self.path, load_shot(self.path, 'city'), 240, 'rough')
        self.assertTrue(chosen['generated']); self.assertEqual(chosen['clip'], second / 'clip.mp4')

    def test_unapproved_shot_never_calls_fal(self):
        from studio.blender import revise_shot
        from studio.common import write_json
        shot = load_shot(self.path, 'city')
        change = self.path / 'c.json'
        write_json(change, {'base_revision': shot['revision'], 'scope': 'route', 'targets': [], 'change': {'route': {**shot['route'], 'status': 'proposed'}}, 'preserve': []})
        revise_shot(self.path, 'city', change)
        with patch.object(clipmod, 'paid_call', side_effect=AssertionError('paid call without approval')), self.assertRaises(StudioError) as error:
            clipmod.generate_clip(self.path, 'city', allow_paid=True, max_usd=2)
        self.assertEqual(error.exception.code, 'ROUTE_APPROVAL_REQUIRED')


if __name__ == '__main__':
    unittest.main()


class PromptConventionTest(unittest.TestCase):
    def test_hybrid_prompt_maps_shapes_tints_placeholders_and_reference_windows(self):
        import shutil
        import tempfile
        from studio.common import read_json, write_json
        from studio.generative.clip import assemble_prompt
        from studio.routing import lint_prompt
        root = Path(__file__).resolve().parents[1] / 'projects/harness_validation/jet_canyon_rig'
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / 'jet'
            (project / 'shots/chase').mkdir(parents=True)
            for name in ('project.json', 'style.json'):
                shutil.copy(root / name, project / name)
            shutil.copytree(root / 'subjects', project / 'subjects')
            spec = read_json(project / 'subjects/p51d/spec.json')
            spec['deviations'] = [{'id': 'big_prop', 'check': 'dimension:dim.span', 'factor': 1.1, 'reason': 'wing span stretched ten percent so the tips reach the frame edge'}]
            write_json(project / 'subjects/p51d/spec.json', spec)
            shot = read_json(root / 'shots/chase/shot.json')
            shot['route']['generative']['previs'] = {'orientation_colors': True, 'placeholders': [
                {'start_frame': 60, 'end_frame': 120, 'part_ids': ['p51d/canopy'], 'description': 'a pilot in a leather flying helmet'}]}
            shot['route']['generative']['inputs'].append({'kind': 'reference_image', 'path': 'refs/a.png', 'start_s': 0, 'end_s': 3.5})
            write_json(project / 'shots/chase/shot.json', shot)
            text = assemble_prompt(project, 'chase')['text']
        self.assertEqual(lint_prompt(text, 'hybrid'), [])
        self.assertIn('long rounded body', text)
        self.assertIn('set of 4 identical thin wing-shaped surfaces = four-blade', text.replace('  ', ' '))
        self.assertIn('red-tinted faces point to the front', text)
        self.assertIn('From 2.0 s to 4.0 s the black areas of the input video are where a pilot in a leather flying helmet appears', text)
        self.assertIn('Reference image 1 applies from 0.0 s to 3.5 s.', text)
        self.assertIn('Intentional changes from the real object: wing span stretched ten percent so the tips reach the frame edge (deliberate, keep it).', text)
        self.assertNotIn('"', text)
