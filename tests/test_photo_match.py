"""photo_match: view records are checked, and the photo-vs-render metrics read what they claim (same = 1, a shift is
recovered by aligned IoU, a scale shows in extent_ratio, a part box is found)."""
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from studio.common import StudioError
from studio.photo_match import add_view, check_view, compare_images

SIZE = (300, 200)
PART = '#40a0e0'


def photo(path, box):
    image = Image.new('RGB', SIZE, 'white')
    ImageDraw.Draw(image).rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=(30, 30, 30))   # PIL fills inclusive
    image.save(path)


def render(dir_, box, name):
    ids = Image.new('RGBA', SIZE, (0, 0, 0, 0))
    ImageDraw.Draw(ids).rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=(0x40, 0xa0, 0xe0, 255))
    look = Image.new('RGB', SIZE, 'white')
    ImageDraw.Draw(look).rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=(30, 30, 30))
    ids.save(dir_ / f'{name}_id.png'); look.save(dir_ / f'{name}_look.png')
    return dir_ / f'{name}_id.png', dir_ / f'{name}_look.png'


class PhotoMatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        (self.project / 'references/r').mkdir(parents=True)
        photo(self.project / 'references/r/photo.png', (100, 50, 200, 150))
        self.record = {'id': 'v', 'image': 'references/r/photo.png', 'licence': 'local_only', 'subject_id': 's',
                       'mask': {}, 'points': [], 'parts': {'block': [100, 50, 200, 150]}}

    def tearDown(self):
        self.tmp.cleanup()

    def compare(self, box, name):
        ids, look = render(self.project, box, name)
        return compare_images(self.project, self.record, ids, look, {PART: 's/block'}, self.project / 'out')

    def test_same_silhouette_scores_one(self):
        m = self.compare((100, 50, 200, 150), 'same')
        self.assertEqual(m['iou'], 1.0)
        self.assertEqual(m['extent_ratio'], [1.0, 1.0])
        self.assertEqual(m['parts']['block']['box_iou'], 1.0)
        self.assertGreater(m['edges']['iou'], 0.9)
        self.assertTrue(Path(m['sheet']).is_file())

    def test_shift_is_recovered_and_scale_is_reported(self):
        shifted = self.compare((110, 55, 210, 155), 'shift')
        self.assertLess(shifted['iou'], 0.85)
        self.assertGreater(shifted['aligned_iou'], 0.97)
        scaled = self.compare((100, 50, 250, 150), 'scale')
        self.assertAlmostEqual(scaled['extent_ratio'][0], 150 / 100, places=1)
        self.assertGreater(scaled['parts']['block']['centre_error'], 0.05)

    def test_view_records_are_checked(self):
        self.assertEqual(check_view(self.project, self.record), [])
        bad = {**self.record, 'licence': 'unknown', 'colour': 1, 'points': [{'px': [1, 2]}], 'parts': {'x': [5, 5, 1, 1]}}
        problems = ' '.join(check_view(self.project, bad))
        for word in ('licence', "'colour'", 'points[0]', 'parts.x'):
            self.assertIn(word, problems)
        with self.assertRaises(StudioError):
            add_view(self.project, 'r/v', '../outside.png', 'local_only', 's')
        written = add_view(self.project, 'r/v', 'references/r/photo.png', 'local_only', 's',
                           points=[{'anchor': 's/block/+z', 'px': [150, 50]}])
        self.assertTrue(Path(written['path']).is_file())


if __name__ == '__main__':
    unittest.main()
