"""generate inputs: the role picks what the model sees (the look render); explain also needs the control clay as its QA baseline."""
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

    def test_explain_gets_the_look_render_and_needs_the_clay_baseline(self):
        with self.assertRaises(StudioError) as caught:   # no look render yet: says what to render, never falls back to clay
            fill_inputs(self.path, 's')
        self.assertIn('render submit --profile review', caught.exception.recovery)
        self.render('review')
        fill_inputs(self.path, 's')
        got = self.kinds()
        self.assertTrue(got['previs'].endswith('renders/reviewv0001/clip.mp4') and got['control'].endswith('control/abc/depth.mp4'))
        self.assertEqual(got['reference_image'], 'stills/look.png')
        for kind in ('clay', 'depth'):   # the clay baseline is gone: the take could not be checked, so nothing is filled
            (self.control / f'{kind}.mp4').unlink()
        with self.assertRaises(StudioError) as caught:
            fill_inputs(self.path, 's')
        self.assertIn('control_clay', caught.exception.message)

    def test_mood_gets_the_look_render_or_says_what_to_render(self):
        shot = load_shot(self.path, 's'); shot['route']['role'] = 'mood'; write_json(shot_path(self.path, 's'), shot)
        with self.assertRaises(StudioError) as caught:
            fill_inputs(self.path, 's')
        self.assertIn('render submit --profile review', caught.exception.recovery)
        self.render('layout')            # wrong profile: never a look render
        self.render('review')
        fill_inputs(self.path, 's')
        self.assertTrue(self.kinds()['previs'].endswith('renders/reviewv0001/clip.mp4'))

    def test_a_generated_still_is_added_by_name_only_with_its_provenance(self):
        self.render('review')
        still = self.path / 'images/abc/0.png'; still.parent.mkdir(parents=True); still.write_bytes(b'png')
        with self.assertRaises(StudioError) as caught:          # no provenance beside it: not known to be ours
            fill_inputs(self.path, 's', references=['images/abc/0.png'])
        self.assertEqual(caught.exception.code, 'REFERENCE_NOT_SENDABLE')
        write_json(still.parent / 'provenance.json', {'ai_generated': True, 'approval': {'user_words': 'ok'}})
        fill_inputs(self.path, 's', references=['images/abc/0.png'])
        refs = [i['path'] for i in load_shot(self.path, 's')['route']['generative']['inputs'] if i['kind'] == 'reference_image']
        self.assertEqual(refs, ['stills/look.png', 'images/abc/0.png'])
        fill_inputs(self.path, 's', references=['images/abc/0.png'])   # a refill does not duplicate it
        self.assertEqual(len([i for i in load_shot(self.path, 's')['route']['generative']['inputs'] if i['path'] == 'images/abc/0.png']), 1)

    def test_a_clay_filled_shot_sends_the_mixed_input_and_its_concept(self):
        from unittest.mock import patch
        from studio.common import write_json as wj
        self.render('review')
        shot = load_shot(self.path, 's'); shot['screen'] = {'generate_only': ['room']}
        shot['route']['generative']['model'] = 'seedance-2.5'; shot['route']['generative']['inputs'] = []
        write_json(shot_path(self.path, 's'), shot)
        mixed = self.path / 'shots/s/mixed_previs/k/previs.mp4'; mixed.parent.mkdir(parents=True); mixed.write_bytes(b'mixed')
        with patch('studio.generative.keep.mixed_previs', return_value=mixed):
            with self.assertRaises(StudioError) as caught:                 # clay alone is copied as clay: a picture is needed
                fill_inputs(self.path, 's')
            self.assertEqual(caught.exception.code, 'REFERENCE_REQUIRED')
            concept = self.path / 'concepts/s/c01.png'; concept.parent.mkdir(parents=True); concept.write_bytes(b'png')
            wj(self.path / 'decisions/concept/s.json', {'shot_id': 's', 'concept_id': 'c01', 'path': 'concepts/s/c01.png'})
            fill_inputs(self.path, 's')
        got = load_shot(self.path, 's')['route']['generative']['inputs']
        self.assertEqual([(i['kind'], i['path']) for i in got][:2], [('previs', 'shots/s/mixed_previs/k/previs.mp4'), ('reference_image', 'concepts/s/c01.png')])
        self.assertFalse(any('look_key_' in i['path'] for i in got))     # the concept carries the look, not the clay render

    def test_w9_clay_sent_and_w10_stale_inputs(self):
        self.render('review')
        fill_inputs(self.path, 's')
        shot = load_shot(self.path, 's'); shot['render']['look_preset'] = 'photoreal_night'
        self.assertEqual([w['code'] for w in input_warnings(self.path, shot)], [])
        for role in ('explain', 'mood'):   # every role sends the look render: clay as previs is said for both
            shot['route']['role'] = role
            shot['route']['generative']['inputs'][0]['path'] = 'shots/s/control/abc/clay.mp4'
            self.assertIn('W9_input_clay', [w['code'] for w in input_warnings(self.path, shot)])
        shot['scene_version'] = 'v0002'                  # rebuilt: the inputs no longer belong to the current version
        codes = [w['code'] for w in input_warnings(self.path, shot)]
        self.assertIn('W10_inputs_stale', codes)

if __name__ == '__main__':
    unittest.main()


class GeneratedCutTest(unittest.TestCase):
    """2026-10-20 (studio/maker.py): a cut the model makes has no Blender build; it starts from its own start picture,
    sent as its first frame (image to video), and its prompt says what is in it and what happens."""

    def setUp(self):
        from PIL import Image
        from studio import concept
        from studio.world import planned_route
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(init_project('gen_cut', {'request': 'a lake', 'shots': [{'shot_id': 's', 'frame_count': 60}]}, self.temp.name)['project_path'])
        picture = Path(self.temp.name) / 'start.png'
        Image.new('RGB', (90, 160), (30, 90, 160)).save(picture)
        concept.add(self.path, 's', picture, 'the lake at dawn', frame='start')
        shot = load_shot(self.path, 's')
        shot['route'] = planned_route(shot, {'mode': 'generative', 'role': 'mood', 'generate_only': []}, (), 30)
        shot['goal'] = 'ships wait on a calm lake as the sun rises'
        shot['route']['generative']['prompt_spec'] = {'look': 'soft dawn light'}
        write_json(shot_path(self.path, 's'), shot)

    def tearDown(self):
        self.temp.cleanup()

    def test_the_start_picture_is_the_first_frame_and_the_prompt_says_the_cut(self):
        from studio.generative.clip import assemble_prompt
        filled = fill_inputs(self.path, 's')
        route = load_shot(self.path, 's')['route']['generative']
        self.assertEqual(([i['kind'] for i in filled['inputs']], route['operation']), (['first_frame'], 'image_to_video'))
        self.assertTrue(route['inputs'][0]['path'].startswith('concepts/s/'))
        text = assemble_prompt(self.path, 's')['text']
        self.assertIn('Start exactly from the given first image', text)
        self.assertIn('What happens: ships wait on a calm lake', text)
        with self.assertRaises(StudioError):
            fill_inputs(self.path, 's', first_frame='nowhere.png')

    def test_only_its_own_start_picture_is_taken(self):
        from PIL import Image
        from studio import concept
        other = Path(self.temp.name) / 'end.png'
        Image.new('RGB', (90, 160), (200, 90, 60)).save(other)
        project = Path(init_project('gen_end', {'request': 'x', 'shots': [{'shot_id': 's', 'frame_count': 60}]}, self.temp.name)['project_path'])
        concept.add(project, 's', other, 'the end', frame='end')   # an end picture only: not where the cut starts
        from studio.world import planned_route
        shot = load_shot(project, 's')
        shot['route'] = planned_route(shot, {'mode': 'generative', 'role': 'mood', 'generate_only': []}, (), 30)
        write_json(shot_path(project, 's'), shot)
        with self.assertRaises(StudioError) as caught:
            fill_inputs(project, 's')
        self.assertEqual(caught.exception.code, 'INPUT_MISSING')
