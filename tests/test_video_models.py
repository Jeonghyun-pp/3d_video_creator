"""The paid video's models (library/video_models.json): one adapter maps our request onto each model's own fields (first
and last picture, length snapped to what it allows, audio and the prompt rewriter off, what to avoid only in a field the
model has), and a model is priced only from fal_client.PRICING (frozen) - without a row there it is refused
(PRICE_UNRECORDED) and the sheet says it needs the user's freeze words. Tested with a stubbed fal call and a recorded
fixture video: nothing is spent. Why (2026-10-27): fal deprecates fal-ai/veo3.1/ on 2026-11-17."""
import base64
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from studio.common import StudioError, file_hash, read_json

PORTRAIT = {'output': {'width': 1080, 'height': 1920}}
REWRITE_FIELDS = ('auto_fix', 'enable_prompt_expansion', 'prompt_optimizer', 'enhance_prompt', 'expand_prompt')


class ArgumentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        (self.path / 'first.png').write_bytes(b'f')
        (self.path / 'last.png').write_bytes(b'l')

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, model, operation='image_to_video', seconds=4, last=False, **spec):
        from studio.generative.clip import build_arguments
        inputs = [{'kind': 'first_frame', 'path': 'first.png'}] + ([{'kind': 'last_frame', 'path': 'last.png'}] if last else [])
        base = {'model': model, 'operation': operation, 'duration_seconds': seconds, 'inputs': inputs}
        return build_arguments(self.path, PORTRAIT, {**base, **spec}, 'p', uri=lambda f: Path(f).name)

    def test_each_model_gets_our_request_in_its_own_fields(self):
        self.assertEqual(self.args('veo-3.1'), {'prompt': 'p', 'image_url': 'first.png', 'duration': '4s', 'aspect_ratio': '9:16',
                                                'resolution': '1080p', 'generate_audio': False, 'auto_fix': False})
        self.assertEqual(self.args('veo-3.1', 'first_last_to_video', last=True),
                         {'prompt': 'p', 'first_frame_url': 'first.png', 'last_frame_url': 'last.png', 'duration': '4s', 'aspect_ratio': '9:16',
                          'resolution': '1080p', 'generate_audio': False, 'auto_fix': False})
        self.assertEqual(self.args('gemini-omni-flash-1.1'), {'prompt': 'p', 'image_url': 'first.png', 'duration': 4, 'aspect_ratio': '9:16',
                                                              'resolution': '1080p'})
        self.assertEqual(self.args('gemini-omni-flash-1.1', 'first_last_to_video', last=True)['end_image_url'], 'last.png')
        self.assertEqual(self.args('kling-3.0-pro'), {'prompt': 'p', 'start_image_url': 'first.png', 'duration': '4', 'generate_audio': False})
        got = self.args('kling-3.0-pro', 'first_last_to_video', last=True)
        self.assertEqual((got['start_image_url'], got['end_image_url']), ('first.png', 'last.png'))

    def test_a_first_and_last_request_needs_the_last_picture_and_an_image_request_never_sends_it(self):
        with self.assertRaises(StudioError) as caught:
            self.args('kling-3.0-pro', 'first_last_to_video')
        self.assertEqual(caught.exception.code, 'ROUTE_INPUT_MISSING')
        self.assertNotIn('end_image_url', self.args('kling-3.0-pro', last=True))   # image_to_video: the first picture only

    def test_the_prompt_rewriter_is_off_wherever_a_model_has_one(self):
        from studio.generative.clip import video_models
        for model, row in video_models()['models'].items():
            for operation, op in row['operations'].items():
                got = self.args(model, operation, last=bool(op['last'])) if op['first'] else None
                if got is None:
                    continue
                if row['prompt_rewrite']:
                    self.assertIs(got[row['prompt_rewrite']['field']], False, (model, operation))
                else:   # a model without a rewriter field gets none of the known names
                    self.assertFalse(set(got) & set(REWRITE_FIELDS), (model, operation))

    def test_what_to_avoid_goes_only_in_a_field_the_model_has(self):
        self.assertEqual(self.args('kling-3.0-pro', negative_prompt='text, camera cut')['negative_prompt'], 'text, camera cut')
        self.assertEqual(self.args('veo-3.1', 'first_last_to_video', last=True, negative_prompt='text')['negative_prompt'], 'text')
        self.assertNotIn('negative_prompt', self.args('gemini-omni-flash-1.1'))
        with self.assertRaises(StudioError) as caught:   # Omni has no such field: refused, never dropped
            self.args('gemini-omni-flash-1.1', negative_prompt='text')
        self.assertEqual(caught.exception.code, 'INPUT_INVALID')
        with self.assertRaises(StudioError) as caught:   # nor a seed it cannot take
            self.args('kling-3.0-pro', seed=7)
        self.assertEqual(caught.exception.code, 'INPUT_INVALID')
        self.assertEqual(self.args('veo-3.1', seed=7)['seed'], 7)

    def test_the_length_snaps_up_to_one_the_model_allows(self):
        from studio.generative.clip import _billed_output_seconds
        cases = {('veo-3.1', 3.2): '4s', ('veo-3.1', 4.5): '6s', ('gemini-omni-flash-1.1', 2.5): 3, ('gemini-omni-flash-1.1', 4.2): 5,
                 ('gemini-omni-flash-1.1', 10): 10, ('kling-3.0-pro', 3.5): '4', ('kling-3.0-pro', 15): '15'}
        for (model, seconds), want in cases.items():
            self.assertEqual(self.args(model, seconds=seconds)['duration'], want, (model, seconds))
            self.assertEqual(_billed_output_seconds({'model': model, 'operation': 'image_to_video', 'duration_seconds': seconds}),
                             int(str(want).rstrip('s')), (model, seconds))
        self.assertEqual(self.args('veo-3.1', seconds=3, trim_start_seconds=1.5)['duration'], '6s')   # the trimmed head is generated too
        for model, seconds in (('veo-3.1', 8.5), ('gemini-omni-flash-1.1', 10.5), ('kling-3.0-pro', 16)):
            with self.assertRaises(StudioError) as caught:
                self.args(model, seconds=seconds)
            self.assertEqual(caught.exception.code, 'INPUT_INVALID')

    def test_a_price_comes_only_from_the_frozen_table(self):
        from studio.generative import fal_client
        from studio.generative.clip import price_usd, video_models
        self.assertEqual(price_usd('fal-ai/veo3.1/image-to-video', 4), 0.8)
        for model, row in video_models()['models'].items():
            for op in row['operations'].values():
                if op['endpoint'] in fal_client.PRICING:
                    continue
                with self.assertRaises(StudioError) as caught:
                    price_usd(op['endpoint'], 4)
                self.assertEqual(caught.exception.code, 'PRICE_UNRECORDED', op['endpoint'])
                self.assertIn('freeze', caught.exception.recovery)
        with mock.patch.dict(fal_client.PRICING, {'fal-ai/kling-video/v3/pro/image-to-video': {'usd_per_second': 0.112}}):
            self.assertEqual(price_usd('fal-ai/kling-video/v3/pro/image-to-video', 5), 0.56)

    def test_the_routes_are_the_tables_and_an_unpriced_one_has_no_estimate(self):
        from studio.generative.clip import video_models
        from studio.routing import MODELS, estimate
        for model, row in video_models()['models'].items():
            self.assertEqual(MODELS[model]['operations'], {op: r['endpoint'] for op, r in row['operations'].items()})
        spec = {'model': 'kling-3.0-pro', 'operation': 'image_to_video', 'duration_seconds': 4, 'max_attempts': 1}
        self.assertEqual(estimate({'duration_frames': 120}, {'mode': 'generative', 'generative': spec})['est_cost_usd'], None)

    def test_each_recorded_price_is_for_what_is_sent(self):
        """The fal price read for an endpoint is for the resolution and audio the row sends: a proposal made from it
        prices the request that leaves."""
        from studio.generative.clip import video_models
        for model, row in video_models()['models'].items():
            for op in row['operations'].values():
                if not op['first']:
                    continue
                basis = row['fal'][op['endpoint']]['price_basis']
                self.assertEqual(basis['resolution'], row['send'].get('resolution'), model)
                if 'generate_audio' in row['send']:
                    self.assertIs(basis['audio'], row['send']['generate_audio'], model)
                self.assertTrue(row['fal'][op['endpoint']]['read'].startswith('2026-'), model)


def fixture_video(target, seconds=4, fps=24):
    import subprocess
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc=size=216x384:rate={fps}', '-t', str(seconds), '-pix_fmt', 'yuv420p',
                    str(target)], check=True)
    return target


class FakeFal:
    """Stands in for fal_client.paid_call: records what was sent, answers with a recorded clip of the asked length."""

    def __init__(self):
        self.calls = []

    def __call__(self, endpoint, arguments, dest, **kw):
        self.calls.append({'endpoint': endpoint, 'arguments': arguments, **kw})
        video = fixture_video(Path(dest) / 'files' / 'video__out.mp4', seconds=int(str(arguments['duration']).rstrip('s')))
        return {'endpoint': endpoint, 'request_id': f'req-{len(self.calls)}', 'estimated_usd': kw['max_usd'],
                'files': [{'trail': 'video', 'path': str(video), 'sha256': file_hash(video), 'bytes': video.stat().st_size}]}


KLING = {'model': 'kling-3.0-pro', 'send_last': True}


class SheetModelTest(unittest.TestCase):
    def setUp(self):
        from tests.test_studio2_sheet import made_reel
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        made_reel(self)   # c1: a first picture; c2: first and last
        self.env = mock.patch.dict(os.environ, {'STUDIO_UNATTENDED': ''})
        self.env.start()
        self.fal = FakeFal()
        self.patch = mock.patch('studio.generative.clip.paid_call', side_effect=self.fal)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def request(self, cut_id):
        from studio import decisions
        from studio2 import sheet
        body = decisions.envelope(self.path, 'cuts')['body']
        return sheet.request_of(self.path, body, next(c for c in body['cuts'] if c['cut_id'] == cut_id))

    def test_the_current_choice_sends_the_first_picture_only(self):
        got = self.request('c2')
        self.assertEqual((got['model'], got['operation'], got['endpoint']), ('veo-3.1', 'image_to_video', 'fal-ai/veo3.1/image-to-video'))
        self.assertEqual([i['kind'] for i in got['spec']['inputs']], ['first_frame'])
        self.assertEqual(got['usd'], 0.8)

    def test_a_cut_with_a_picked_last_picture_is_sent_first_and_last(self):
        from studio.generative import fal_client
        from studio2 import frames, sheet, video
        with mock.patch('studio.generative.clip.paid_video', return_value=KLING), \
                mock.patch.dict(fal_client.PRICING, {'fal-ai/kling-video/v3/pro/image-to-video': {'usd_per_second': 0.112}}):
            self.assertEqual(self.request('c1')['operation'], 'image_to_video')        # no last picture: the first only
            self.assertEqual(self.request('c2')['operation'], 'first_last_to_video')
            sheet.build(self.path)
            sheet.approve(self.path, '좋아요 다 진행해 주세요')
            video.make(self.path, 'c2')
        [call] = self.fal.calls
        args = call['arguments']
        cut = next(c for c in self.body['cuts'] if c['cut_id'] == 'c2')
        self.assertEqual(call['endpoint'], 'fal-ai/kling-video/v3/pro/image-to-video')
        self.assertEqual(base64.b64decode(args['start_image_url'].split(',', 1)[1]), frames.picked(self.path, cut, 'first').read_bytes())
        self.assertEqual(base64.b64decode(args['end_image_url'].split(',', 1)[1]), frames.picked(self.path, cut, 'last').read_bytes())
        self.assertIs(args['generate_audio'], False)
        self.assertIn('camera cut', args['negative_prompt'])
        self.assertEqual(call['max_usd'], round(0.112 * int(args['duration']), 2))   # the frozen table's price, approved

    def test_an_unpriced_model_is_said_on_the_sheet_and_never_approved_or_paid(self):
        from studio2 import sheet, video
        with mock.patch('studio.generative.clip.paid_video', return_value=KLING):
            got = sheet.build(self.path)
            rows = read_json(Path(got['sheet']))['cuts']
            self.assertEqual([r['usd'] for r in rows], [None, None])
            self.assertTrue(all('freeze words' in r['price_note'] for r in rows))
            page = Path(got['page']).read_text(encoding='utf-8')
            self.assertIn('PRICE_UNRECORDED', page)
            self.assertIn("needs the user's freeze words", page)
            self.assertTrue(any('PRICE_UNRECORDED' in w for w in got['warnings']))
            with self.assertRaises(StudioError) as caught:
                sheet.approve(self.path, '좋아요 다 진행해 주세요')
            self.assertEqual(caught.exception.code, 'PRICE_UNRECORDED')
            self.assertFalse((self.path / sheet.SHEET / 'approvals.json').is_file())
            with self.assertRaises(StudioError) as caught:
                video.make(self.path, 'c1')
            self.assertEqual(caught.exception.code, 'PRICE_UNRECORDED')
        self.assertEqual(self.fal.calls, [])

    def test_a_model_without_a_negative_field_refuses_the_cuts_negative_prompt(self):
        with mock.patch('studio.generative.clip.paid_video', return_value={'model': 'gemini-omni-flash-1.1', 'send_last': True}):
            with self.assertRaises(StudioError) as caught:
                self.request('c1')
        self.assertEqual(caught.exception.code, 'INPUT_INVALID')
        self.assertIn('negative', caught.exception.message)


if __name__ == '__main__':
    unittest.main()
