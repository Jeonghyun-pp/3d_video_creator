import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from studio.common import StudioError
from studio.generative import fal_client as fal

EP = 'fal-ai/hyper3d/rodin/v2.5'


class FakeFal:
    def __init__(self, pending=0, post_error=None):
        self.posts, self.pending, self.post_error = 0, pending, post_error

    def __call__(self, url, data=None, auth=True, method=None):
        if data is not None:
            self.posts += 1
            if self.post_error:
                raise HTTPError(url, self.post_error, 'err', {}, io.BytesIO())
            return {'request_id': 'r1', 'status_url': fal.QUEUE + 'status', 'response_url': fal.QUEUE + 'result'}
        if url.endswith('status'):
            if self.pending:
                self.pending -= 1
                return {'status': 'IN_PROGRESS'}
            return {'status': 'COMPLETED'}
        return {'model_mesh': {'url': 'https://cdn.example/model.glb', 'file_name': 'model.glb'}}


def fake_download(url, destination):
    destination.write_bytes(b'glb')


class FalClientTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(fal, 'LEDGER', self.root / 'ledger.jsonl'), patch.object(fal, '_download', side_effect=fake_download)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def call(self, fake, dest='req', **kwargs):
        args = {'project_id': 'p', 'allow_paid': True, 'max_usd': 1, 'poll_interval_s': 0, **kwargs}
        with patch.object(fal, '_json', side_effect=fake):
            return fal.paid_call(EP, {'input_image_urls': ['x']}, self.root / dest, **args)

    def test_complete_then_cached_without_network(self):
        fake = FakeFal()
        result = self.call(fake)
        self.assertEqual((fake.posts, result['request_id'], fal.ledger_total('p')), (1, 'r1', 0.40))
        again = FakeFal()
        self.call(again)
        self.assertEqual(again.posts, 0)

    def test_budget_and_flags_block_before_post(self):
        for kwargs in ({'allow_paid': False}, {'max_usd': 0.1}, {'budget_usd': 0.2}):
            fake = FakeFal()
            with self.subTest(**kwargs), self.assertRaises(StudioError):
                self.call(fake, dest='b' + str(len(kwargs)), **kwargs)
            self.assertEqual(fake.posts, 0)

    def test_ambiguous_state_never_reposts(self):
        dest = self.root / 'ambiguous'; dest.mkdir()
        (dest / 'state.json').write_text(json.dumps({'state': 'submitted'}))
        fake = FakeFal()
        with self.assertRaises(StudioError) as error:
            self.call(fake, dest='ambiguous')
        self.assertEqual((error.exception.code, fake.posts), ('GENERATION_REQUEST_UNKNOWN', 0))

    def test_timeout_resume_posts_once(self):
        fake = FakeFal(pending=10)
        with self.assertRaises(StudioError) as error:
            self.call(fake, poll_timeout_s=-1)
        self.assertEqual(error.exception.code, 'GENERATION_PENDING')
        fake.pending = 0
        self.call(fake)
        self.assertEqual(fake.posts, 1)

    def test_4xx_releases_reservation(self):
        fake = FakeFal(post_error=422)
        with self.assertRaises(StudioError) as error:
            self.call(fake)
        self.assertEqual((error.exception.code, fal.ledger_total('p')), ('GENERATION_REJECTED', 0))

    def test_unknown_and_blocked_endpoints(self):
        with self.assertRaises(StudioError):
            fal.estimate_usd('fal-ai/hunyuan-3d/v3.1/pro/image-to-3d')
        with self.assertRaises(StudioError):
            fal.estimate_usd('fal-ai/some-new-model')
        self.assertEqual(fal.estimate_usd('fal-ai/veo3.1/image-to-video', 8), 1.6)

    def test_key_never_written_and_only_sent_to_queue(self):
        with patch.dict('os.environ', {'FAL_KEY': 'SECRET-KEY-123'}):
            self.call(FakeFal())
            with self.assertRaises(StudioError):
                fal._json('https://cdn.example/x')
        for path in self.root.rglob('*'):
            if path.is_file():
                self.assertNotIn(b'SECRET-KEY-123', path.read_bytes())


if __name__ == '__main__':
    unittest.main()
