"""Generation review (HITL) end to end without paying: sheet -> approval bound to the request -> padded Seedance input ->
clip with the shot's frame count; prompts from the subjects index; reference provenance."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from studio.common import StudioError, read_json, write_json
from studio.generative import clip as clipmod
from studio.generative.review import build_review, pad_seconds, request_fingerprint
from studio.project import init_project, load_shot, shot_path
from studio.routing import approve, assert_route, lint


def video(path, seconds, size='360x640', fps=30):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc=s={size}:r={fps}:d={seconds}', '-pix_fmt', 'yuv420p', str(path)], check=True)


def duration(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)], capture_output=True, text=True)
    return float(out.stdout)


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        gen = {'provider': 'fal', 'model': 'seedance-2.5', 'operation': 'video_to_video', 'prompt_ref': 'prompts/hall.txt',
               'duration_seconds': 88 / 30, 'usd_per_second': None, 'max_attempts': 1, 'text_in_frame': False, 'ai_disclosure': True,
               'inputs': [{'kind': 'previs', 'path': 'previs/hall.mp4'}, {'kind': 'reference_image', 'path': 'stills/look.png'}],
               'prompt_spec': {'look': 'cool LED light on fair-faced concrete', 'add': ['two inspectors in orange vests'],
                               'forbid': ['ceiling louvres']}}
        brief = {'request': 'review test', 'output': {'width': 360, 'height': 640, 'fps': 30, 'target_seconds': 88 / 30},
                 'route_policy': {'allow_generative': True, 'budget_usd': 5, 'turnaround_required': True},
                 'shots': [{'shot_id': 'hall', 'frame_count': 88, 'route_features': ['exact_geometry', 'photoreal_beyond_assets'], 'generative': gen}]}
        self.path = Path(init_project('review_test', brief, self.temp.name)['project_path'])
        for d in ('previs', 'stills', 'prompts'):
            (self.path / d).mkdir()
        video(self.path / 'previs/hall.mp4', 88 / 30)
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=gray:s=64x64', '-frames:v', '1', str(self.path / 'stills/look.png')], check=True)
        shot = load_shot(self.path, 'hall'); shot['scene_version'] = 'v0001'; shot['route']['role'] = 'mood'
        write_json(shot_path(self.path, 'hall'), shot)
        version = self.path / 'shots/hall/versions/v0001'; version.mkdir(parents=True)
        write_json(version / 'subjects_index.json', {'schema_version': 1, 'subjects': [
            {'subject_id': f'escalator-{i:03d}', 'identity': 'S1000 escalator, 30 deg: steps, balustrades',
             'features': [{'description': '31 steps on the incline', 'part_ids': ['steps']}],
             'builders': [{'part_id': 'steps', 'builder': 'array', 'params': {'count': 31, 'item': {'builder': 'box'}}}],
             'on_screen_frames': [0, 44] if i < 2 else []} for i in range(3)]})

    def tearDown(self):
        self.temp.cleanup()

    def test_prompt_from_subjects_index_and_prompt_spec(self):
        text = clipmod.assemble_prompt(self.path, 'hall')['text']
        self.assertIn('2 x S1000 escalator (the set of 31 identical blocks = 31 steps on the incline)', text)   # off-screen one dropped
        self.assertTrue(text.splitlines()[1].startswith('Look: cool LED light'))
        self.assertIn('Add: two inspectors in orange vests.', text)
        self.assertIn('ceiling louvres', text.splitlines()[-1])
        self.assertLess(len(text.split()), clipmod.PROMPT_WORD_LIMIT)

    def test_sheet_approval_padding_and_generation(self):
        clipmod.assemble_prompt(self.path, 'hall')
        shot = load_shot(self.path, 'hall')
        self.assertAlmostEqual(pad_seconds(shot['route']['generative']), round(4 - 88 / 30, 3))
        review = build_review(self.path)
        self.assertTrue(Path(review['sheet']).is_file() and Path(review['sheet_md']).read_text().count('two inspectors') == 2)   # items table + final prompt
        approve(self.path, 'all', '이 시트대로 생성해', review_id=review['review_id'])
        sent = {}

        def paid(endpoint, arguments, dest, **kwargs):
            sent.update(endpoint=endpoint, arguments=arguments, kwargs=kwargs)
            out = Path(dest); out.mkdir(parents=True, exist_ok=True)
            video(out / 'raw.mp4', 4.0, '720x1280', 24)
            return {'request_id': 'r1', 'estimated_usd': 2.0, 'files': [{'path': str(out / 'raw.mp4')}]}
        with patch.object(clipmod, 'paid_call', side_effect=paid):
            result = clipmod.generate_clip(self.path, 'hall', allow_paid=True, max_usd=3)
        padded = next((Path(result['clip_path']).parent / 'request/padded').glob('*.mp4'))
        self.assertAlmostEqual(duration(padded), 4.0, delta=0.07)                 # held to Seedance's minimum
        self.assertAlmostEqual(sent['kwargs']['input_seconds'], 4.0, delta=0.07)
        self.assertEqual((result['frame_count'], result['policy']['role'], result['policy']['usable']), (88, 'mood', True))
        self.assertEqual(sent['arguments']['duration'], '4')

    def test_edit_after_approval_is_stale_and_reference_provenance(self):
        clipmod.assemble_prompt(self.path, 'hall')
        review = build_review(self.path)
        approve(self.path, 'hall', '좋아 이대로 진행해 줘', review_id=review['review_id'])
        assert_route(load_shot(self.path, 'hall'), 'generate', self.path)
        shot = load_shot(self.path, 'hall'); shot['route']['generative']['seed'] = 9; write_json(shot_path(self.path, 'hall'), shot)
        with self.assertRaises(StudioError) as caught:
            assert_route(load_shot(self.path, 'hall'), 'generate', self.path)
        self.assertEqual(caught.exception.code, 'ROUTE_APPROVAL_STALE')
        (self.path / 'reference').mkdir()
        (self.path / 'reference/frame.png').write_bytes((self.path / 'stills/look.png').read_bytes())
        shot['route']['generative']['inputs'][1]['path'] = 'reference/frame.png'; write_json(shot_path(self.path, 'hall'), shot)
        self.assertIn('E7_reference_not_cleared', [e['code'] for e in lint(self.path)['errors']])
        with self.assertRaises(StudioError) as caught:
            assert_route(load_shot(self.path, 'hall'), 'generate', self.path)
        self.assertEqual(caught.exception.code, 'REFERENCE_NOT_CLEARED')

    def test_review_history_keeps_the_users_change_request(self):
        clipmod.assemble_prompt(self.path, 'hall')
        first = build_review(self.path)
        shot = load_shot(self.path, 'hall'); shot['route']['generative']['prompt_spec']['add'].append('wet floor reflections')
        write_json(shot_path(self.path, 'hall'), shot); clipmod.assemble_prompt(self.path, 'hall')
        second = build_review(self.path, after=first['review_id'], user_words='바닥은 젖은 느낌으로 해줘')
        self.assertNotEqual(first['review_id'], second['review_id'])
        history = read_json(self.path / 'reviews' / f"gen_{second['review_id']}" / 'review.json')['history']
        self.assertEqual(history, [{'review_id': first['review_id'], 'user_words': '바닥은 젖은 느낌으로 해줘'}])
        with self.assertRaises(StudioError) as caught:   # the first sheet no longer matches the request
            approve(self.path, 'hall', '첫 번째 시트로 진행해 주세요', review_id=first['review_id'])
        self.assertEqual(caught.exception.code, 'ROUTE_REVIEW_STALE')


if __name__ == '__main__':
    unittest.main()
