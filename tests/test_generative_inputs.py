"""generate inputs: the role picks what the model sees (explain: control clay + depth; mood: the look render)."""
from pathlib import Path
import tempfile
import unittest

from studio.common import StudioError, file_hash, write_json
from studio.generative.inputs import fill_inputs, input_warnings
from studio.project import init_project, load_shot, shot_path


class InputsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        gen = {'provider': 'fal', 'model': 'wan-2.2-vace', 'operation': 'video_to_video', 'prompt_ref': 'prompts/s.txt', 'duration_seconds': 2.0,
               'usd_per_second': None, 'max_attempts': 1, 'text_in_frame': False, 'ai_disclosure': True,
               'inputs': [{'kind': 'reference_image', 'path': 'stills/look.png'}]}
        brief = {'request': 'inputs test', 'shots': [{'shot_id': 's', 'frame_count': 60, 'route_features': ['exact_geometry', 'photoreal_beyond_assets'], 'generative': gen}]}
        self.path = Path(init_project('inputs_test', brief, self.temp.name)['project_path'])
        shot = load_shot(self.path, 's'); shot['scene_version'] = 'v0001'; write_json(shot_path(self.path, 's'), shot)
        self.control = self.path / 'shots/s/control/abc'
        self.control.mkdir(parents=True)
        for kind in ('clay', 'depth'):
            (self.control / f'{kind}.mp4').write_bytes(kind.encode())
        write_json(self.control / 'control.json', {'scene_version': 'v0001', 'fingerprint': 'f' * 24, 'created_at': '2026-10-05T00:00:00Z',
                                                   'files': {k: {'path': str(self.control / f'{k}.mp4')} for k in ('clay', 'depth')}})

    def tearDown(self):
        self.temp.cleanup()

    def render(self, profile='review', version='v0001'):
        directory = self.path / f'shots/s/renders/{profile}{version}'
        directory.mkdir(parents=True)
        clip = directory / 'clip.mp4'; clip.write_bytes(f'{profile}{version}'.encode())
        write_json(directory / 'render.json', {'status': 'complete', 'scene_version': version, 'profile': profile, 'full_sequence': True,
                                               'clip_path': str(clip), 'clip_sha256': file_hash(clip), 'fingerprint': 'r' * 24, 'created_at': '2026-10-05T01:00:00Z'})
        return clip

    def kinds(self):
        return {i['kind']: i['path'] for i in load_shot(self.path, 's')['route']['generative']['inputs']}

    def test_explain_gets_clay_and_depth_and_keeps_references(self):
        fill_inputs(self.path, 's')
        got = self.kinds()
        self.assertTrue(got['previs'].endswith('control/abc/clay.mp4') and got['control'].endswith('control/abc/depth.mp4'))
        self.assertEqual(got['reference_image'], 'stills/look.png')

    def test_mood_gets_the_look_render_or_says_what_to_render(self):
        shot = load_shot(self.path, 's'); shot['route']['role'] = 'mood'; write_json(shot_path(self.path, 's'), shot)
        with self.assertRaises(StudioError) as caught:
            fill_inputs(self.path, 's')
        self.assertIn('render submit --profile review', caught.exception.recovery)
        self.render('layout')            # wrong profile: never a look render
        self.render('review')
        fill_inputs(self.path, 's')
        self.assertTrue(self.kinds()['previs'].endswith('renders/reviewv0001/clip.mp4'))

    def test_w9_mood_with_clay_and_w10_stale_inputs(self):
        fill_inputs(self.path, 's')                      # explain inputs (clay)
        shot = load_shot(self.path, 's'); shot['route']['role'] = 'mood'; shot['render']['look_preset'] = 'photoreal_night'
        codes = [w['code'] for w in input_warnings(self.path, shot)]
        self.assertIn('W9_mood_input_clay', codes)
        shot['scene_version'] = 'v0002'                  # rebuilt: the clay no longer belongs to the current version
        codes = [w['code'] for w in input_warnings(self.path, shot)]
        self.assertIn('W10_inputs_stale', codes)


if __name__ == '__main__':
    unittest.main()
