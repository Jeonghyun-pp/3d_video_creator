"""Caption size follows the reference's measure: a style value (style.captions.letter_height, the ink height of the
subtitle's letters as a share of the frame height, with its evidence) that v1's edit draws at, chosen for a v2 project
from the reference's subtitle letters (the cutaway grammar's subtitle_text field) and shown against their middle half.
Why (2026-10-26, phase 0): v1's captions (size_frac .039 of the width) were about 60 % of the reference's subtitle letters."""
from pathlib import Path
import tempfile
import unittest

from PIL import ImageDraw, Image

from studio import decisions
from studio.common import read_json, write_json
from studio.edit import caption_font, caption_style
from tests import studio2_fixture as fx


def ink(font, word='가나다'):
    box = ImageDraw.Draw(Image.new('L', (8, 8))).textbbox((0, 0), word, font=font)
    return box[3] - box[1]


class CaptionSizeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_letter_height_draws_letters_that_tall(self):
        style = {'captions': {'letter_height': 0.032, 'evidence': 'a test'}}
        font, _ = caption_font(style, self.root, 1080, 1920)
        self.assertLessEqual(abs(ink(font) - 0.032 * 1920), 1.5)

    def test_without_one_the_old_size_holds(self):
        font, _ = caption_font({}, self.root, 1080, 1920)
        self.assertEqual(font.size, round(1080 * caption_style({})['size_frac']))

    def test_a_v2_project_takes_the_references_subtitle_letters_with_evidence(self):
        from studio2 import grammar, project
        out = project.init('capt', {'request': 'a caption test', 'shots': [{'shot_id': 's1', 'frame_count': 30}]}, self.root)
        from studio.project import validate_schema
        style = read_json(Path(out['project_path']) / 'style.json')
        validate_schema(style, 'style')   # the style schema knows the choice and its evidence
        captions = style['captions']
        got = grammar.read('subtitle_text')
        self.assertAlmostEqual(captions['letter_height'], got['all.subtitle_text_height.median']['measured'], places=4)
        self.assertIn('subtitle', captions['evidence'])
        self.assertIn(f"{got['all.subtitle_text_height.p25']['measured']:.3f}", captions['evidence'])

    def test_the_preview_keeps_labels_clear_of_captions_at_their_size(self):
        from studio2.motion import caption_band
        path = fx.project(self.root)
        small = caption_band(path, 1080, 1920)
        style = read_json(path / 'style.json')
        style['captions'] = {'letter_height': 0.04, 'evidence': 'a test'}
        write_json(path / 'style.json', style)
        big = caption_band(path, 1080, 1920)
        self.assertLess(big[0], small[0])   # taller letters: the band starts higher

    def test_letters_outside_the_references_middle_half_are_said(self):
        path = fx.project(self.root)
        style = read_json(path / 'style.json')
        style['captions'] = {'letter_height': 0.019, 'evidence': 'a test'}
        write_json(path / 'style.json', style)
        warnings = decisions.lint(path, 'cuts', fx.body())['warnings']
        self.assertTrue(any(w.startswith('CAPTION_SIZE_OFF') for w in warnings), warnings)
        style['captions']['letter_height'] = 0.032
        write_json(path / 'style.json', style)
        warnings = decisions.lint(path, 'cuts', fx.body())['warnings']
        self.assertFalse(any(w.startswith('CAPTION_SIZE_OFF') for w in warnings), warnings)


if __name__ == '__main__':
    unittest.main()
