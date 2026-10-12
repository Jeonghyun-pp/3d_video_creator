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

    def test_grid_sheet_lines_sit_on_photo_coordinates(self):
        from studio.photo_match import add_view, grid_sheet
        add_view(self.project, 'r/v', 'references/r/photo.png', 'local_only', 's', points=[{'anchor': 's/block/+z', 'px': [150, 50]}])
        out = grid_sheet(self.project, 'r/v', box=[100, 50, 200, 150], step=25)
        sheet = Image.open(out['sheet']).convert('RGB')
        self.assertEqual((out['scale'], out['step'], sheet.size), (14.0, 25, (1400, 1400)))
        self.assertEqual(sheet.getpixel((round((125 - 100) * 14), 710)), (255, 0, 255))   # the x = 125 line, off any y line
        self.assertEqual(sheet.getpixel((710, round((75 - 50) * 14))), (0, 160, 255))    # the y = 75 line, off any x line
        with self.assertRaises(StudioError):
            grid_sheet(self.project, 'r/v', box=[0, 0, 999, 10])

    def test_sheets_are_written_only_inside_the_project(self):
        """2026-10-12 audit: out_dir came from the workbench caller unchecked; a sheet could be written anywhere."""
        from studio.critique import workbench_critique
        from studio.photo_match import add_view, compare_view, grid_sheet
        add_view(self.project, 'r/v', 'references/r/photo.png', 'local_only', 's', points=[{'anchor': 's/block/+z', 'px': [150, 50]}])
        def never(*a, **k):
            raise AssertionError('nothing renders for a refused out_dir')
        with tempfile.TemporaryDirectory() as outside:
            for out_dir in ('../escape', outside):
                for run in (lambda: grid_sheet(self.project, 'r/v', out_dir=out_dir),
                            lambda: compare_view(self.project, never, 'r/v', out_dir=out_dir),
                            lambda: workbench_critique(self.project, never, 'references/r/photo.png', out_dir=out_dir)):
                    with self.subTest(out_dir=out_dir), self.assertRaises(StudioError) as caught:
                        run()
                    self.assertEqual(caught.exception.code, 'INPUT_INVALID')
        inside = grid_sheet(self.project, 'r/v', out_dir='notes/grid')
        self.assertTrue(Path(inside['sheet']).resolve().is_relative_to(Path(self.project).resolve()))

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

    def test_writing_a_view_again_keeps_its_fitted_pose(self):
        """2026-10-12 audit: re-adding a fitted view (to add part boxes) kept its camera but dropped the subject pose it was
        fitted in, so fidelity then judged the photo in the default pose."""
        from studio.common import read_json, write_json
        from studio.photo_match import view_path
        points = [{'anchor': 's/block/+z', 'px': [150, 50]}]
        add_view(self.project, 'r/v', self.record['image'], 'local_only', 's', points=points)
        record = read_json(view_path(self.project, 'r/v'))
        record.update({'camera': {'lens_mm': 35}, 'residual_px': 1.0, 'pose': {'frame': 66}, 'pose_frame': 66, 'iou': 0.8})
        write_json(view_path(self.project, 'r/v'), record)
        add_view(self.project, 'r/v', self.record['image'], 'local_only', 's', points=points, parts={'block': [10, 10, 50, 50]})
        again = read_json(view_path(self.project, 'r/v'))
        self.assertEqual((again.get('pose'), again.get('pose_frame'), again.get('iou')), ({'frame': 66}, 66, 0.8))

    def test_fit_uses_one_pose_and_reports_the_final_camera(self):
        from studio.blender_ops.view_match_core import project_points, residual_px
        from studio.common import read_json
        from studio.photo_match import fit_camera, point_errors, view_path
        names = [f's/block/p{i}' for i in range(len(CUBE))]
        pixels = project_points(CAM, CUBE)
        points = [{'anchor': name, 'px': [xy[0] * SIZE[0], xy[1] * SIZE[1]]} for name, xy in zip(names, pixels)]
        add_view(self.project, 'r/v', self.record['image'], 'local_only', 's', points=points)
        shifted, _ = render(self.project, (110, 50, 210, 150), 'before')
        same, _ = render(self.project, (100, 50, 200, 150), 'after')
        calls = []

        def call(tool, args):
            calls.append((tool, args))
            self.assertEqual(args['frame'], 12)
            if tool == 'anchors':
                return {'points': dict(zip(names, CUBE))}
            self.assertTrue(args['isolate_subject'])
            image = shifted if args['views'][0]['azimuth_deg'] == CAM['azimuth_deg'] else same
            return {'images': {'v': {'id': str(image)}}, 'palette': {PART: 's/block'}}

        def refine(cost, start, *args, **kwargs):
            chosen = [0.52, *start[1:]]
            return chosen, cost(chosen), 1

        with unittest.mock.patch('studio.photo_match.solve_pose', return_value={'camera': {**CAM, 'roll_deg': 0}, 'residual_px': 0, 'evals': 1}), \
                unittest.mock.patch('studio.photo_match.nelder_mead', side_effect=refine):
            result = fit_camera(self.project, call, 'r/v', frame=12)
        self.assertGreater(len(calls), 1)
        self.assertNotEqual(result['camera']['azimuth_deg'], CAM['azimuth_deg'])
        expected = round(residual_px(result['camera'], CUBE, pixels), 3)
        self.assertGreater(expected, 0)
        self.assertEqual(result['residual_px'], expected)
        self.assertEqual(read_json(view_path(self.project, 'r/v'))['residual_px'], expected)
        self.assertEqual(result['points'], point_errors(result['camera'], CUBE, pixels, names))
        self.assertTrue(result['marks']['fits'])

    def test_photo_render_isolates_subject_but_scene_view_keeps_context(self):
        from studio.photo_match import _render
        for subject in ('s', None):
            call = unittest.mock.Mock(return_value={'images': {'v': {'id': 'image.png'}}, 'palette': {}})
            _render(call, {**self.record, 'subject_id': subject}, CAM, ['id'], 200)
            self.assertEqual(call.call_args.args[1]['isolate_subject'], bool(subject))


if __name__ == '__main__':
    unittest.main()


CUBE = [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]]
FACES = [(0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
SOLID = {'block': [[[c - 0.5 for c in CUBE[i]] for i in f] for f in FACES]}
CAM = {'target': [0, 0, 0], 'distance_m': 5.0, 'azimuth_deg': 30.0, 'elevation_deg': 25.0, 'lens_mm': 50.0, 'width': 300, 'height': 200}
SIDE = {**CAM, 'azimuth_deg': 100.0}   # 70 deg round from CAM


class PhotoViewCheckTest(unittest.TestCase):
    """fidelity.build_report photo checks: the subject's triangles at the photo camera against the photo's mask."""
    def setUp(self):
        from PIL import ImageOps
        from studio.photo_match import projected
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        (self.project / 'references/r/views').mkdir(parents=True)
        from studio.common import write_json
        for name, camera in (('v', CAM), ('w', SIDE)):   # a real subject is checked from two sides (fidelity.PHOTO_MIN_VIEWS)
            mask, boxes = projected(camera, SOLID, (300, 200), boxes_for=['block'])
            ImageOps.invert(mask).convert('RGB').save(self.project / f'references/r/{name}.png')   # dark subject on white
            record = {'id': name, 'image': f'references/r/{name}.png', 'licence': 'local_only', 'subject_id': 'c', 'mask': {}, 'points': [],
                      'parts': {'block': boxes['block']}, 'camera': camera}
            write_json(self.project / f'references/r/views/{name}.json', record)

    def tearDown(self):
        self.tmp.cleanup()

    def report(self, solid, views=2, **view):
        from studio.fidelity import build_report
        spec = {'subject_id': 'c', 'identity': 'cube', 'subject_mode': 'specific_real', 'dimensions': [], 'features': [], 'builders': [],
                'photo_views': [{'id': 'front', 'view': 'r/v', 'min_iou': 0.9, 'parts_min_box_iou': 0.8, **view},
                                {'id': 'side', 'view': 'r/w', 'min_iou': 0.9}][:views]}
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

    def test_a_real_subject_needs_photos_from_two_sides(self):
        """2026-10-11: the booster was modelled from text alone and read as a grey model; photo_views was optional."""
        from studio.fidelity import PHOTO_MIN_SPREAD_DEG
        coverage = lambda report: next(c for c in report['checks'] if c['id'] == 'coverage')   # noqa: E731
        both = self.report(SOLID)
        self.assertTrue(coverage(both)['passed'] and both['passed'], both['failures'])
        self.assertGreater(coverage(both)['measured']['spread_deg'], PHOTO_MIN_SPREAD_DEG)
        one = self.report(SOLID, views=1)
        self.assertFalse(one['passed'])
        self.assertIn('photo coverage', ' '.join(one['failures']))
        self.assertIn('reference collect', coverage(one)['note'])
        from studio.common import read_json, write_json   # two photos from one side check one side
        record = read_json(self.project / 'references/r/views/w.json')
        write_json(self.project / 'references/r/views/w.json', {**record, 'camera': {**CAM, 'azimuth_deg': 40.0}})
        twice = self.report(SOLID)
        self.assertFalse(coverage(twice)['passed'])
        self.assertLess(coverage(twice)['measured']['spread_deg'], PHOTO_MIN_SPREAD_DEG)
        with unittest.mock.patch('studio.gates.severity_for', return_value={'fidelity_illustrative': 'warning'}):   # schematic: a warning
            from studio.fidelity import build_report
            spec = {'subject_id': 'c', 'identity': 'cube', 'subject_mode': 'schematic', 'dimensions': [], 'features': [], 'builders': []}
            soft = build_report(spec, {'parts': {}, 'silhouettes': {}}, self.project)
        self.assertTrue(soft['passed'])
        self.assertIn('photo coverage', ' '.join(soft['advisories']))

    def test_a_photo_deviation_can_lower_the_part_bar_too(self):
        """2026-10-11: the user accepted the booster at its measured match ("사진 기준은 낮춰도 돼"); the part boxes
        were held at the view's 0.7 with no way to record that."""
        from studio.fidelity import build_report
        big = {'block': [[[c * 1.25 for c in p] for p in t] for t in SOLID['block']]}
        strict = self.report(big)
        self.assertTrue(any(f.startswith('photo front.block:') for f in strict['failures']), strict['failures'])
        spec = {'subject_id': 'c', 'identity': 'cube', 'subject_mode': 'specific_real', 'dimensions': [], 'features': [], 'builders': [],
                'photo_views': [{'id': 'front', 'view': 'r/v', 'min_iou': 0.9, 'parts_min_box_iou': 0.8}, {'id': 'side', 'view': 'r/w', 'min_iou': 0.9}],
                'deviations': [{'id': 'ok', 'check': 'photo:front', 'min_iou': 0.3, 'parts_min_box_iou': 0.3, 'reason': 'accepted by the user at this match'},
                               {'id': 'ok2', 'check': 'photo:side', 'min_iou': 0.3, 'reason': 'accepted by the user at this match'}]}
        with unittest.mock.patch('studio.gates.severity_for', return_value={}):
            eased = build_report(spec, {'parts': {}, 'silhouettes': {}, 'solid': big}, self.project)
        row = next(c for c in eased['checks'] if c['id'] == 'front.block')
        self.assertTrue(row['passed'] and row['expected'] == 'box IoU >= 0.3' and 'part bar 0.8 -> 0.3' in row['note'], row)
        self.assertTrue(eased['passed'], eased['failures'])

    def test_the_photo_is_judged_in_its_recorded_pose(self):
        """2026-10-11: s03 (legs folded on the pad) was judged against the landed photo and failed 19 checks."""
        from studio.fidelity import build_report
        folded = {'block': [[[c * 1.6 for c in p] for p in t] for t in SOLID['block']]}   # this shot's own pose: no match
        spec = {'subject_id': 'c', 'identity': 'cube', 'subject_mode': 'specific_real', 'dimensions': [], 'features': [], 'builders': [],
                'photo_views': [{'id': 'front', 'view': 'r/v', 'min_iou': 0.9}, {'id': 'side', 'view': 'r/w', 'min_iou': 0.9}]}
        with unittest.mock.patch('studio.gates.severity_for', return_value={}):
            own = build_report(spec, {'parts': {}, 'silhouettes': {}, 'solid': folded}, self.project)
            posed = build_report(spec, {'parts': {}, 'silhouettes': {}, 'solid': folded,
                                        'solid_posed': {'front': SOLID, 'side': SOLID}}, self.project)
        self.assertFalse(own['passed'])
        self.assertTrue(any('pose_only' in a for a in own['advisories']), own['advisories'])
        self.assertTrue(posed['passed'], posed['failures'])
        self.assertIn("photo's pose", next(c for c in posed['checks'] if c['id'] == 'front')['note'])

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

    def test_lint_photo_coverage_deviation_needs_waive_searches_and_the_users_words(self):
        from studio.common import read_json
        from studio.subjects import lint_spec
        spec = read_json(Path(__file__).resolve().parent / 'fixtures/winch_spec/subjects/winch/spec.json')
        spec['subject_mode'] = 'specific_real'
        waive = {'id': 'nophoto', 'check': 'photo:coverage', 'reason': 'no licensed photograph of this prototype exists', 'waive': True}
        errors = ' '.join(lint_spec({**spec, 'deviations': [waive]})['errors'])
        self.assertIn('needs searched', errors)
        self.assertIn('user_evidence', errors)   # skipping photos of a real subject is the user's call
        ok = {**waive, 'searched': ['winch prototype photo'], 'user_evidence': 'no photo, go ahead'}
        self.assertFalse([e for e in lint_spec({**spec, 'deviations': [ok]})['errors'] if 'nophoto' in e])
        bad = {**ok, 'waive': None, 'min_iou': 0.5}
        bad.pop('waive')
        self.assertIn('photo:coverage deviation takes exactly waive', ' '.join(lint_spec({**spec, 'deviations': [bad]})['errors']))
        part = {'id': 'parts', 'check': 'photo:coverage', 'reason': 'not a view, cannot lower a part bar', 'waive': True,
                'searched': ['x y z'], 'user_evidence': 'ok go', 'parts_min_box_iou': 0.5}
        self.assertIn('parts_min_box_iou belongs to a photo:<view>', ' '.join(lint_spec({**spec, 'deviations': [part]})['errors']))
        spec['photo_views'] = [{'id': 'coverage', 'view': 'r/v', 'min_iou': 0.85}]
        self.assertIn("'coverage' is the photo coverage check", ' '.join(lint_spec(spec)['errors']))
