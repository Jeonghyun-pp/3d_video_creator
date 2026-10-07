"""photo_match: view records are checked, and the photo-vs-render metrics read what they claim (same = 1, a shift is
recovered by aligned IoU, a scale shows in extent_ratio, a part box is found)."""
import tempfile
import unittest.mock
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


CUBE = [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]]
FACES = [(0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
SOLID = {'block': [[[c - 0.5 for c in CUBE[i]] for i in f] for f in FACES]}
CAM = {'target': [0, 0, 0], 'distance_m': 5.0, 'azimuth_deg': 30.0, 'elevation_deg': 25.0, 'lens_mm': 50.0, 'width': 300, 'height': 200}


class PhotoViewCheckTest(unittest.TestCase):
    """fidelity.build_report photo checks: the subject's triangles at the photo camera against the photo's mask."""
    def setUp(self):
        from PIL import ImageOps
        from studio.photo_match import projected
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        (self.project / 'references/r/views').mkdir(parents=True)
        mask, boxes = projected(CAM, SOLID, (300, 200), boxes_for=['block'])
        ImageOps.invert(mask).convert('RGB').save(self.project / 'references/r/photo.png')   # dark subject on white
        self.boxes = boxes
        self.record = {'id': 'v', 'image': 'references/r/photo.png', 'licence': 'local_only', 'subject_id': 'c', 'mask': {}, 'points': [],
                       'parts': {'block': boxes['block']}, 'camera': CAM}
        from studio.common import write_json
        write_json(self.project / 'references/r/views/v.json', self.record)

    def tearDown(self):
        self.tmp.cleanup()

    def report(self, solid, **view):
        from studio.fidelity import build_report
        spec = {'subject_id': 'c', 'identity': 'cube', 'subject_mode': 'specific_real', 'dimensions': [], 'features': [], 'builders': [],
                'photo_views': [{'id': 'front', 'view': 'r/v', 'min_iou': 0.9, 'parts_min_box_iou': 0.8, **view}]}
        geometry = {'parts': {}, 'silhouettes': {}, **({'solid': solid} if solid is not None else {})}
        with unittest.mock.patch('studio.gates.severity_for', return_value={}):
            return build_report(spec, geometry, self.project, self.project)

    def test_matching_subject_passes_and_a_bigger_one_fails(self):
        same = self.report(SOLID)
        self.assertTrue(same['passed'], same['failures'])
        self.assertGreater(next(c for c in same['checks'] if c['id'] == 'front')['measured'], 0.98)
        self.assertTrue((self.project / 'photo_front.png').is_file())
        big = {'block': [[[c * 1.6 for c in p] for p in t] for t in SOLID['block']]}
        report = self.report(big)
        self.assertFalse(report['passed'])
        self.assertTrue(any(f.startswith('photo front:') for f in report['failures']), report['failures'])
        self.assertTrue(any(f.startswith('photo front.block:') for f in report['failures']), report['failures'])

    def test_unfitted_or_unmeasured_views_fail_with_a_reason(self):
        self.assertIn('rebuild', next(c for c in self.report(None)['checks'] if c['id'] == 'front')['note'])
        missing = self.report(SOLID, view='r/none')
        self.assertIn('does not exist', next(c for c in missing['checks'] if c['id'] == 'front')['note'])

    def test_lint_knows_photo_views(self):
        from studio.common import read_json
        from studio.subjects import lint_spec
        spec = read_json(Path(__file__).resolve().parent / 'fixtures/winch_spec/subjects/winch/spec.json')
        spec['photo_views'] = [{'id': 'front', 'view': 'r/v', 'min_iou': 0.85, 'exclude_parts': ['handle']}]
        spec['deviations'] = spec.get('deviations', []) + [{'id': 'soft', 'check': 'photo:front', 'reason': 'cutaway view removes the cover from the photo silhouette', 'min_iou': 0.8}]
        self.assertFalse([e for e in lint_spec(spec)['errors'] if 'photo' in e or 'soft' in e])
        spec['photo_views'].append({'id': 'front', 'view': 'r/w', 'min_iou': 0.85, 'exclude_parts': ['nope']})
        spec['deviations'][-1]['check'] = 'photo:side'
        errors = ' '.join(lint_spec(spec)['errors'])
        for word in ('declared twice', 'unknown part nope', "no photo check 'side'"):
            self.assertIn(word, errors)
