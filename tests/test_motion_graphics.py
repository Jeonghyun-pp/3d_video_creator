"""Motion graphics in the edit layer: a counter counts and keeps its size, a title can sit on a panel, bars grow to
their value inside the safe rect."""
import unittest
from pathlib import Path

from PIL import Image, ImageFont

from studio import titles
from studio.charts import render_charts
from studio.common import StudioError

FONT = Path(__file__).resolve().parents[1] / 'library/fonts/pretendard/Pretendard-Bold.otf'


class MotionGraphicsTest(unittest.TestCase):
    def test_counter_counts_and_holds_its_size(self):
        title = {'title_id': 'n', 'text': '기둥 {n}개', 'start_frame': 0, 'end_frame': 31, 'count': {'from': 0, 'to': 80}}
        self.assertEqual([titles.text_at(title, f) for f in (0, 15, 30)], ['기둥 0개', '기둥 40개', '기둥 80개'])
        masters = titles.Masters([title], FONT, 360)
        a, b = masters.get(title, 0), masters.get(title, 30)
        self.assertIsNot(a, b)
        self.assertEqual(a.unit, b.unit)
        self.assertEqual(titles.Master(title, FONT, 360, '기둥 0개').image.height, titles.Master(title, FONT, 360, '기둥 80개').image.height)

    def test_a_title_on_a_panel(self):
        plain = titles.Master({'title_id': 't', 'text': '현장 점검', 'start_frame': 0, 'end_frame': 5}, FONT, 360)
        boxed = titles.Master({'title_id': 't', 'text': '현장 점검', 'start_frame': 0, 'end_frame': 5,
                               'box': {'fill_srgb': [0.86, 0.1, 0.08]}}, FONT, 360)
        self.assertGreater(boxed.image.width, plain.image.width)
        self.assertEqual(boxed.image.convert('RGBA').getpixel((boxed.image.width // 2, 3))[:3], (219, 26, 20))

    def test_bars_grow_to_their_value(self):
        chart = {'chart_id': 'cols', 'items': [{'label': '설계대로', 'value': 30}, {'label': '다르게', 'value': 50, 'color_srgb': [0.9, 0.2, 0.1]}],
                 'unit': '개', 'start_frame': 0, 'end_frame': 60, 'grow_frames': 10, 'anchor': [0.15, 0.3], 'width_frac': 0.7}
        font_at = lambda size, weight: ImageFont.truetype(str(FONT), size)  # noqa: E731
        safe = (0.11 * 360, 0.14 * 640, 0.89 * 360, 0.65 * 640)
        early, late = Image.new('RGBA', (360, 640)), Image.new('RGBA', (360, 640))
        render_charts([chart], 2, font_at, 360, 640, safe, early, 's')
        render_charts([chart], 40, font_at, 360, 640, safe, late, 's')
        red = lambda im: sum(1 for p in im.get_flattened_data() if p[0] > 200 and p[1] < 80 and p[3] > 200)  # noqa: E731
        self.assertGreater(red(late), red(early) * 2)
        chart['anchor'] = [0.15, 0.62]
        with self.assertRaises(StudioError):
            render_charts([chart], 40, font_at, 360, 640, safe, late, 's')


if __name__ == '__main__':
    unittest.main()
