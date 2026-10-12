"""2026-10-12 audit: a decal card's cache key missed defaults (a red card and bare letters shared one image); a character
registration that failed left a half version that every later load picked."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from studio.common import REPO, StudioError

FONT = REPO / 'library/fonts/pretendard'


class DecalCacheTest(unittest.TestCase):
    def test_a_card_and_bare_letters_are_different_images(self):
        from studio.decals import draw
        from studio.common import font_file
        font = font_file({}, REPO)
        with tempfile.TemporaryDirectory() as tmp:
            card = draw({'id': 'a', 'text': 'hi', 'size_m': 0.5}, font, tmp)
            bare = draw({'id': 'b', 'text': 'hi', 'size_m': 0.5, 'fill_srgb': None}, font, tmp)
            self.assertNotEqual(card, bare)
            from PIL import Image
            self.assertEqual(Image.open(bare).getpixel((0, 0))[3], 0)   # transparent: bare letters
            draw({'id': 'c', 'text': 'hi', 'size_m': 0.5, 'color_srgb': None}, font, tmp)   # null colour: the default, no TypeError


class CharacterStagingTest(unittest.TestCase):
    def test_a_failed_registration_leaves_no_version(self):
        from studio import characters
        with tempfile.TemporaryDirectory() as tmp:
            fbx = Path(tmp) / 'walker.fbx'; fbx.write_bytes(b'fbx')
            with mock.patch.object(characters, 'ROOT', Path(tmp) / 'lib'), \
                    mock.patch.object(characters, 'run_command', side_effect=StudioError('COMMAND_FAILED', 'blender died')), \
                    mock.patch.object(characters, 'blender_binary', return_value='/usr/bin/true'):
                with self.assertRaises(StudioError):
                    characters.register('walker', fbx, characters.LICENCES[0], 'https://example.org/walker', {})
                self.assertEqual(list((Path(tmp) / 'lib' / 'walker').glob('v*')) if (Path(tmp) / 'lib' / 'walker').is_dir() else [], [])


if __name__ == '__main__':
    unittest.main()
