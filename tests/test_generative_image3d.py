from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from studio.assets import approve_asset
from studio.common import StudioError, read_json, write_json
from studio.generative import fal_client as fal
from studio.generative import image3d


class Image3dTest(unittest.TestCase):
    def test_requires_paid_flags_and_registers_review_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            picture = root / 'beam.png'; Image.new('RGB', (8, 8)).save(picture)
            with patch.object(fal, 'LEDGER', root / 'ledger.jsonl'), patch.object(image3d, 'REPO', root), \
                    patch.object(fal, '_json', side_effect=AssertionError('no network without --allow-paid')):
                with self.assertRaises(StudioError) as error:
                    image3d.image_to_3d(picture, 'beam', real_dimension={'dimension': 'longest', 'meters': .6}, asset_root=root / 'lib')
                self.assertEqual(error.exception.code, 'BUDGET_EXCEEDED')
            def fake_paid(endpoint, arguments, dest, **kwargs):
                mesh = Path(dest) / 'model.glb'; Path(dest).mkdir(parents=True, exist_ok=True); mesh.write_bytes(b'glb')
                return {'request_id': 'r1', 'estimated_usd': .4, 'files': [{'path': str(mesh)}]}
            with patch.object(image3d, 'REPO', root), patch.object(image3d, 'paid_call', side_effect=fake_paid):
                result = image3d.image_to_3d(picture, 'beam', real_dimension={'dimension': 'longest', 'meters': .6},
                                             allow_paid=True, max_usd=1, asset_root=root / 'lib')
            self.assertEqual((result['source']['use_status'], result['source']['ai_generated']), ('review_only', True))
            self.assertEqual(result['scale_basis'], {'dimension': 'longest', 'meters': .6})

    def test_approval_is_human_and_pinned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / 'p.png'; Image.new('RGB', (4, 4)).save(preview)
            manifest = root / 'asset.json'
            write_json(manifest, {'asset_id': 'x', 'status': 'fetched', 'inspection': {'preview_paths': [str(preview)]}})
            with self.assertRaises(StudioError):
                approve_asset(manifest, 'approved', 'kim', 'looks right from all sides')
            write_json(manifest, {'asset_id': 'x', 'status': 'prepared', 'prepared_scene_sha256': 'abc', 'inspection': {'preview_paths': [str(preview)]}})
            with self.assertRaises(StudioError):
                approve_asset(manifest, 'approved', 'agent', 'ok', reviewer_kind='agent')
            result = approve_asset(manifest, 'approved', 'kim', 'User: 앞뒤옆 다 봤고 괜찮음')
            self.assertEqual(read_json(manifest)['approval']['prepared_scene_sha256'], 'abc')
            self.assertTrue(Path(result['artifacts'][0]).is_file())


if __name__ == '__main__':
    unittest.main()
