"""look match (studio/look_style.py): a render graded toward the picture its shot is built toward."""
import unittest

from PIL import Image

from studio import look_style as ls


class LookMatchTest(unittest.TestCase):
    def test_kelvin_whites_are_neutral_at_d65_and_warm_below(self):
        r, g, b = ls.kelvin_white(6500)
        self.assertAlmostEqual(r / b, 1.0, delta=0.03)
        self.assertGreater(ls._ratio(3000), ls._ratio(5000))

    def test_the_white_balance_moves_the_render_warmth_to_the_picture(self):
        current, ours, theirs = 4000.0, 0.8, 1.3                 # a blue render against a warm picture
        new = ls.white_balance_for(current, ours, theirs)
        self.assertGreater(new, current)                          # warmer picture: higher setting
        after = ours * ls._ratio(current) / ls._ratio(new)        # the picture's R/B goes as 1 / ratio(T)
        self.assertAlmostEqual(after, theirs, delta=0.02)
        self.assertLess(ls.white_balance_for(6500, 1.4, 1.0), 6500)

    def test_stats_read_warmth_and_key_and_skip_black_and_clipped(self):
        warm = Image.new('RGB', (40, 40), (200, 150, 100))
        cool = Image.new('RGB', (40, 40), (100, 150, 200))
        self.assertGreater(ls.image_stats(warm)['warmth'], 1.5)
        self.assertLess(ls.image_stats(cool)['warmth'], 0.7)
        black = Image.new('RGB', (40, 40), (0, 0, 0)); black.paste((128, 128, 128), (0, 0, 10, 10))
        self.assertAlmostEqual(ls.image_stats(black)['warmth'], 1.0, places=3)   # only the measurable grey patch counts
        dark, bright = ls.image_stats(Image.new('RGB', (8, 8), (60, 60, 60))), ls.image_stats(Image.new('RGB', (8, 8), (180, 180, 180)))
        self.assertGreater(bright['key'], dark['key'] * 4)


if __name__ == '__main__':
    unittest.main()
