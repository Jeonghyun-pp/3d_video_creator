"""2D titles (studio/titles.py): recede stays inside the title safe rect, anchored on its ink centroid, no shimmer."""
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from studio import titles
from studio.common import StudioError
from studio.edit import make_overlays

TITLE = {'title_id': 't1', 'text': '삼성역\n기둥철근', 'start_frame': 0, 'end_frame': 40, 'anim': 'recede',
         'anchor': [0.5, 0.49], 'width_frac': 0.70, 'weight': 'ExtraBold'}
AUDIO = {'speech_status': 'final', 'cues': []}


def centroid(image):
    alpha = image.getchannel('A')
    data, total, sx, sy = alpha.load(), 0, 0.0, 0.0
    for y in range(image.height):
        for x in range(image.width):
            a = data[x, y]
            if a:
                total += a; sx += a * (x + .5); sy += a * (y + .5)
    return sx / total, sy / total


class TitleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def overlays(self, shot, name='edit', frames=40, size=(270, 480)):
        return make_overlays(self.root / name, [{'shot': shot, 'audio': AUDIO, 'frame_count': frames}], *size, {}, self.root)

    def test_recede_shrinks_monotonically_inside_the_safe_rect(self):
        out = self.overlays({'shot_id': 's01', 'labels': [], 'titles': [TITLE]})
        rows = [b for b in out['text_boxes'] if b['kind'] == 'title']
        self.assertEqual(len(rows), 40)
        widths = [b['bbox'][2] - b['bbox'][0] for b in rows]
        self.assertTrue(all(b <= a for a, b in zip(widths, widths[1:])), widths)
        self.assertAlmostEqual(widths[0] / 270, 0.70, delta=0.03)
        safe = out['title_safe_rect_pixels']
        self.assertTrue(all(safe[0] - 1 <= b['bbox'][0] and b['bbox'][2] <= safe[2] + 1 for b in rows))

    def test_ink_centroid_stays_on_the_anchor(self):
        self.overlays({'shot_id': 's01', 'labels': [], 'titles': [TITLE]})
        for frame in (1, 12, 25, 36):
            with Image.open(self.root / f'edit/overlays/{frame:06d}.png') as image:
                x, y = centroid(image)
            self.assertAlmostEqual(x, 0.5 * 270, delta=0.35)
            self.assertAlmostEqual(y, 0.49 * 480, delta=0.35)

    def test_a_title_that_leaves_the_safe_rect_is_refused(self):
        wide = {**TITLE, 'width_frac': 0.78, 'anchor': [0.3, 0.49]}
        with self.assertRaises(StudioError) as caught:
            self.overlays({'shot_id': 's01', 'labels': [], 'titles': [wide]})
        self.assertEqual(caught.exception.code, 'TITLE_OUT_OF_SAFE')

    def test_unknown_weight_is_an_error_not_a_fallback(self):
        with self.assertRaises(StudioError):
            self.overlays({'shot_id': 's01', 'labels': [], 'titles': [{**TITLE, 'weight': 'Ultra Wide'}]})

    def test_scale_curve_endpoints(self):
        self.assertAlmostEqual(titles.scale_at(TITLE, 0), 1.0)
        self.assertAlmostEqual(titles.scale_at(TITLE, 39), titles.RECEDE_CURVE[-1])
        self.assertEqual(titles.scale_at({**TITLE, 'anim': 'hold'}, 20), 1.0)


if __name__ == '__main__':
    unittest.main()
