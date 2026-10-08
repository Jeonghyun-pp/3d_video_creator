"""Generated stills: one reviewed request per sheet, no words in the pixels, edits pinned to their source, and a
generated texture becomes a three-map texture set only in the user's words."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from studio.common import StudioError, write_json
from studio.generative import images


class ImagesTest(unittest.TestCase):
    def test_requests_need_no_text_and_a_source_for_edits(self):
        with self.assertRaises(StudioError):
            images._request('sky', 'plate', 'a dusk sky over a city skyline', 1, '9:16')            # no "no text"
        with self.assertRaises(StudioError):
            images._request('sky', 'edit', 'the same plate at dusk, no text, no logos', 1, '9:16')   # no source
        request, _ = images._request('tile', 'texture', 'worn concrete tiles, no text', 2, '9:16')
        self.assertTrue(request['prompt'].startswith('Seamless tileable') and request['aspect'] == '1:1')

    def test_texture_registration_derives_maps(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / 'images/r1').mkdir(parents=True)
            Image.new('RGB', (64, 64), (120, 110, 100)).save(tmp / 'images/r1/0.png')
            write_json(tmp / 'images/r1/provenance.json', {'purpose': 'texture', 'model': 'fal-ai/nano-banana-pro', 'prompt': 'p',
                                                            'license_id': 'fal-output', 'license_evidence': 'https://fal.ai/terms', 'created_at': 'now'})
            lib = tmp / 'repo'
            with mock.patch.object(images, 'project_dir', return_value=tmp), mock.patch.object(images, 'REPO', lib):
                out = images.register_texture(tmp, 'images/r1/0.png', 'gen_tiles', 0.6, '이 타일 텍스처 써도 돼')
                manifest = __import__('json').loads(Path(out['manifest']).read_text())
                self.assertEqual(sorted(f['role'] for f in manifest['files']), ['base_color', 'displacement', 'roughness'])
                self.assertEqual(manifest['source']['use_status'], 'cleared')
                self.assertEqual(manifest['texset']['tile_m'], 0.6)
                with self.assertRaises(StudioError):
                    images.register_texture(tmp, 'images/r1/0.png', 'gen_tiles2', 0.6, 'ok')     # not the user's words


if __name__ == '__main__':
    unittest.main()
