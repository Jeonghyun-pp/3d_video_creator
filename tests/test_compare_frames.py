import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / 'tools'))
import compare_frames as cf  # noqa: E402

BASE = {'frame': 0, 'at': 0.0, 'position': [0.0, -100.0, 0.0], 'aim': [0.0, 0.0, 0.0], 'lens_mm': 35.0,
        'offset_from_picture': {'azimuth_deg': 0.0, 'elevation_deg': 0.0},
        'achieved': {'height_share': 0.5, 'centre': [0.5, 0.4]}, 'steps': 2, 'limited_by': None, 'view': [0.0, 0.0]}


def changed(**edits):
    row = copy.deepcopy(BASE)
    for key, value in edits.items():
        if key in ('height_share', 'centre'):
            row['achieved'][key] = value
        else:
            row[key] = value
    return row


class CompareRowsTest(unittest.TestCase):
    def test_within_tolerance_passes(self):
        # 0.9 m on a 100 m camera distance, 0.8% height, 0.007 centre, empty engaged
        row = changed(position=[0.9, -100.0, 0.0], height_share=0.504, centre=[0.505, 0.405], engaged=[], steps=3)
        self.assertEqual(cf.compare_rows(BASE, row), [])

    def test_each_criterion_fails(self):
        cases = {'lens_mm': changed(lens_mm=50.0),
                 'offset_from_picture': changed(offset_from_picture={'azimuth_deg': 5.0, 'elevation_deg': 0.0}),
                 'position moved': changed(position=[1.1, -100.0, 0.0]),
                 'height_share': changed(height_share=0.506),
                 'centre': changed(centre=[0.508, 0.408]),
                 'engaged': changed(engaged=['support'])}
        for word, row in cases.items():
            with self.subTest(word):
                failures = cf.compare_rows(BASE, row)
                self.assertEqual(len(failures), 1, failures)
                self.assertIn(word, failures[0])

    def test_engaged_is_compared_with_the_baseline_row(self):
        # an intent baseline records how its poses were reached: the same mechanisms pass, another one does not
        reached = changed(engaged=['intent', 'placed'])
        self.assertEqual(cf.compare_rows(reached, changed(engaged=['placed', 'intent'])), [])
        self.assertEqual(len(cf.compare_rows(reached, changed(engaged=['intent', 'placed', 'refined']))), 1)

    def test_missing_achieved_fails(self):
        self.assertTrue(cf.compare_rows(BASE, changed(achieved=None)))


class CompareProjectTest(unittest.TestCase):
    def project(self, root, rows, version='v0002'):
        d = Path(root) / 'shots' / 's01' / 'versions'
        (d / 'v0001').mkdir(parents=True)
        (d / 'failed_x').mkdir()
        (d / version).mkdir(exist_ok=True)
        (d / version / 'camera_move_report.json').write_text(json.dumps({'frames': rows}))

    def test_latest_version_and_record_roundtrip(self):
        with tempfile.TemporaryDirectory() as root:
            self.project(root, [BASE, changed(frame=10)])
            baseline = cf.record(root, ['s01'])
            self.assertEqual(baseline['shots']['s01']['version'], 'v0002')
            self.assertTrue(cf.compare(baseline, root)['ok'])

    def test_row_count_and_cli_exit(self):
        with tempfile.TemporaryDirectory() as root:
            self.project(root, [BASE])
            path = Path(root) / 'b.json'
            path.write_text(json.dumps({'shots': {'s01': {'frames': [BASE, BASE]}}}))
            result = cf.compare(str(path), root, ['s01'])
            self.assertFalse(result['ok'])
            self.assertIn('rows vs baseline', result['shots']['s01']['failures'][0])
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(cf.main(['--baseline', str(path), '--project', root, '--shots', 's01']), 1)
            self.assertFalse(json.loads(out.getvalue())['ok'])


if __name__ == '__main__':
    unittest.main()
