"""The reference's label size (studio2/label_measure.py): a reader's letter boxes become rows, the rows a pattern library
read through studio.patterns, and the reader's own error is measured on labels drawn at known sizes."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image

from studio.common import read_json, write_json
from studio2 import label_measure


def fake_reader(scale=1.0):
    """Answers every frame with one label whose box is `scale` times the drawn height (the calibration's labels say
    their own height in their file name) and a subtitle 0.035 tall."""
    def reader(images, prompt, schema, target):
        frames = []
        for n, image in enumerate(images):
            name = Path(image).stem
            h = int(name.rsplit('_', 1)[1]) / 1000 if name.startswith('cal_') else 0.04
            frames.append({'image': n, 'labels': [{'text': '필터', 'box': [0.1, 0.5, 0.3, 0.5 + h * scale], 'lines': 1}],
                           'subtitle': {'text': '자막', 'box': [0.2, 0.6, 0.8, 0.635], 'lines': 1}})
        write_json(target, {'answer': {'frames': frames}})
    return reader


class LabelMeasureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_label_is_its_box_over_its_lines(self):
        self.assertAlmostEqual(label_measure.height_of({'box': [0, 0.5, 1, 0.58], 'lines': 2}), 0.04)

    def test_rows_become_a_library_read_through_patterns(self):
        readings = [{'labels': [{'text': 'a', 'box': [0, 0.1, 0.2, 0.1 + 0.03 + 0.002 * (n % 5)], 'lines': 1}],
                     'subtitle': {'text': 's', 'box': [0, 0.6, 1, 0.635], 'lines': 1}} for n in range(40)]
        rows = label_measure.rows_of(readings, [f'g{n % 8}' for n in range(40)])
        with mock.patch('studio.patterns.LIBRARY', self.root / 'lib'):
            data, items = label_measure.library(rows, 'test_labels', 40)
            from studio.patterns import load, reading
            got = reading(load('test_labels'), 'all.label_text_height.median')
        self.assertAlmostEqual(got['value'], 0.034, places=3)
        self.assertNotIn('per_group_median', data['sections']['all']['items']['label_text_height'])   # numbers only

    def test_the_readers_measured_bias_is_taken_out(self):
        readings = [{'labels': [{'text': 'a', 'box': [0, 0.1, 0.2, 0.1 + 0.0338], 'lines': 1}],
                     'subtitle': {'text': 's', 'box': [0, 0.6, 1, 0.6 + 0.0338], 'lines': 1}} for _ in range(3)]
        rows = label_measure.rows_of(readings, ['g'] * 3, reader_ratio=1.126)
        self.assertAlmostEqual(rows[0]['label_text_height'], 0.03, places=3)    # the ink, as the renderer draws it
        self.assertAlmostEqual(rows[0]['label_to_subtitle'], 1.0, places=3)     # a ratio has no bias to take out
        full = self.root / 'labels_full.json'
        write_json(full, {'frames': [{'reel': f'g{n % 8}', 'reading': readings[0]} for n in range(40)]})
        cal = self.root / 'calibration.json'
        write_json(cal, {'summary': {'ratio_median': 1.126}})
        with mock.patch('studio.patterns.LIBRARY', self.root / 'lib'):
            got = label_measure.rebuild(full, 'test_labels', cal)
            from studio.patterns import load
            data = load('test_labels')
        self.assertEqual(data['reader_ratio'], 1.126)
        self.assertAlmostEqual(data['sections']['all']['items']['label_text_height']['median'], 0.03, places=3)

    def test_a_frame_with_no_label_is_no_row(self):
        self.assertEqual(label_measure.rows_of([{'labels': [], 'subtitle': None}], ['g']), [])

    def test_the_readers_error_is_measured(self):
        background = self.root / 'bg.png'
        Image.new('RGB', (540, 960), (90, 110, 130)).save(background)
        got = label_measure.calibrate([background], self.root / 'cal', reader=fake_reader(1.1))
        self.assertAlmostEqual(got['summary']['ratio_median'], 1.1, delta=0.08)
        self.assertEqual(got['summary']['read'], 4)


if __name__ == '__main__':
    unittest.main()
