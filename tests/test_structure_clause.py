"""The structure clause: measured screen order and thickness of the explained parts, in prompt words (any subject)."""
import unittest

from studio.generative.clip import colour_word, structure_clause

ASPECT = 16 / 9


def band(x0, y0, x1, y1, run_v=None, run_h=None):
    return {'bbox': [x0, y0, x1, y1], 'centre': [(x0 + x1) / 2, (y0 + y1) / 2], 'run_v': run_v if run_v is not None else y1 - y0,
            'run_h': run_h if run_h is not None else x1 - x0, 'aspect': ASPECT}


class StructureClauseTest(unittest.TestCase):
    def test_lying_layers_top_to_bottom_with_thickness_shares(self):
        layout = {'a/top': band(0, 0.30, 1, 0.33), 'a/mid': band(0, 0.33, 1, 0.45), 'a/low': band(0, 0.45, 1, 0.50),
                  'a/gone': None}
        line, info = structure_clause(layout, {'a/top': 'brown top', 'a/mid': 'grey middle', 'a/low': 'black low'})
        self.assertEqual(info['stacks']['from top to bottom'], ['a/top', 'a/mid', 'a/low'])
        self.assertIn('brown top, about 15 %; grey middle, about 60 %; black low, about 25 %', line)
        self.assertEqual(info['unseen'], ['a/gone'])

    def test_perspective_tilt_uses_the_median_run_not_the_box(self):
        # a tilted layer: its box spans 0.2 of the height, its thickness per column is 0.02 - the share follows the run
        layout = {'a/tilted': band(0, 0.30, 1, 0.50, run_v=0.02), 'a/flat': band(0, 0.50, 1, 0.52)}
        line, _ = structure_clause(layout, {})
        self.assertIn('a/tilted, about 50 %; a/flat, about 50 %', line)

    def test_an_l_shaped_junction_is_two_stacks_and_blobs_are_pieces(self):
        layout = {'f/l1': band(0, 0.4, 0.6, 0.45), 'f/l2': band(0, 0.45, 0.6, 0.5),
                  'f/w1': band(0.6, 0, 0.63, 0.9), 'f/w2': band(0.63, 0, 0.75, 0.9), 'f/blob': band(0.2, 0.6, 0.4, 0.95)}
        line, info = structure_clause(layout, {})
        self.assertEqual(info['stacks'], {'from top to bottom': ['f/l1', 'f/l2'], 'from left to right': ['f/w1', 'f/w2']})
        self.assertEqual(info['pieces'], ['f/blob'])
        self.assertIn('standing layers from left to right', line)

    def test_nothing_to_order_says_nothing(self):
        self.assertIsNone(structure_clause({'a/one': band(0, 0.4, 1, 0.45), 'a/blob': band(0.3, 0.3, 0.6, 0.7)}, {})[0])
        self.assertIsNone(structure_clause({}, {})[0])

    def test_thin_layer_is_named_thin(self):
        line, _ = structure_clause({'a/skin': band(0, 0.3, 1, 0.301), 'a/core': band(0, 0.301, 1, 0.6)}, {})
        self.assertIn('a/skin, thin', line)

    def test_colour_words(self):
        self.assertEqual(colour_word([0.018, 0.021, 0.025]), 'black')
        self.assertEqual(colour_word([0.46, 0.29, 0.14]), 'brown')
        self.assertEqual(colour_word([0.48, 0.43, 0.36]), 'grey')
        self.assertEqual(colour_word([0.88, 0.86, 0.8]), 'light grey')
        self.assertEqual(colour_word([0.95, 0.95, 0.95]), 'white')
        self.assertEqual(colour_word([0.1, 0.2, 0.8]), 'blue')


if __name__ == '__main__':
    unittest.main()
