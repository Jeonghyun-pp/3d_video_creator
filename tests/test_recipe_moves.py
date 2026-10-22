import ast
from pathlib import Path
import unittest

from studio.blender_ops.camera_moves_core import MOVES, PARAMS
from studio.common import StudioError
from studio.recipe_moves import FOCUS_VALUES, TOKENS, book, check_intent, recipe_move

ROOT = Path(__file__).resolve().parents[1]


def shot(show=None, move=None):
    return {'shot_id': 's01', 'frame': {'focus': ['boat'], 'size': 'medium'},
            'show': show if show is not None else [{'at': 'start', 'parts': ['boat'], 'how': 'exterior'},
                                                   {'at': 0.6, 'parts': ['boat/tank'], 'how': 'cutaway'}],
            'direction': {'move': move or {'recipe': 'dig_in', 'reveals': 'the tank', 'key_at': 0.7, 'why': 'the line names the tank'}}}


class RecipeMovesTest(unittest.TestCase):
    def test_every_make_is_a_known_move_with_known_values(self):
        for rid, r in book().items():
            make = r.get('make')
            if not make:
                continue
            self.assertIn(make['move'], MOVES, rid)
            known = set(PARAMS[make['move']]) | {'lens_mm'} | set(FOCUS_VALUES)
            self.assertLessEqual(set(make['params']) | set(make['choose']), known, rid)
            self.assertIn(make.get('focus', '$subject'), TOKENS, rid)
            for name, (low, high, default) in make['choose'].items():
                self.assertTrue(default == 'pattern' or low <= default <= high, (rid, name))

    def test_recipe_makes_its_move_from_the_scene(self):
        made = recipe_move(shot())
        move = made['move']
        self.assertEqual(move['type'], book()['dig_in']['make']['move'])
        self.assertEqual(move['params']['target'], 'boat')
        self.assertEqual(move['params']['detail'], 'boat/tank')     # the part shown at the key moment
        self.assertEqual(move['recipe']['id'], 'dig_in')
        self.assertTrue(all(c['by'] == 'default' for c in made['chosen'].values()))   # said, not silent
        self.assertEqual(move['focus']['parts'], ['boat/tank'])   # the framing pose is solved on what the recipe frames
        low, high, _ = book()['dig_in']['make']['choose']['height_share']
        self.assertEqual(move['focus']['height_share'], round((low + high) / 2, 4))   # no reference: the range's middle
        sized = recipe_move(shot(), pattern=5.0)['move']['focus']['height_share']
        self.assertEqual(sized, high)   # the reference's size, kept inside the recipe's range

    def test_director_values_inside_the_range_are_used_and_said(self):
        made = recipe_move(shot(move={'recipe': 'dig_in', 'reveals': 'the tank', 'key_at': 0.7, 'why': 'the line names the tank',
                                      'settings': {'elevation_deg': -10}}))
        self.assertEqual(made['move']['params']['elevation_deg'], -10)
        self.assertEqual(made['chosen']['elevation_deg']['by'], 'director')

    def test_values_outside_the_range_and_unknown_recipes_are_refused(self):
        self.assertTrue(check_intent({'recipe': 'dig_in', 'settings': {'elevation_deg': 80}}))
        self.assertTrue(check_intent({'recipe': 'dig_in', 'settings': {'radius_m': 3}}))
        self.assertTrue(check_intent({'recipe': 'no_such_feel'}))
        with self.assertRaises(StudioError) as caught:
            recipe_move(shot(move={'recipe': 'dig_in', 'reveals': 'x', 'key_at': 0.7, 'why': 'xxxxxxxx', 'settings': {'elevation_deg': 80}}))
        self.assertEqual(caught.exception.code, 'INPUT_INVALID')

    def test_a_recipe_without_its_needs_is_refused_with_why(self):
        bare = shot(show=[{'at': 'start', 'parts': ['boat'], 'how': 'exterior'}])
        with self.assertRaises(StudioError) as caught:
            recipe_move(bare)                     # dig_in moves to a key part: the whole boat is not one
        self.assertEqual(caught.exception.code, 'RECIPE_NEEDS')
        opened = [r for r in book().values() if 'key_opens' in (r.get('make') or {}).get('needs', [])]
        if opened:
            with self.assertRaises(StudioError):
                recipe_move(shot(show=[{'at': 'start', 'parts': ['boat/tank'], 'how': 'exterior'}],
                                 move={'recipe': opened[0]['id'], 'reveals': 'x', 'key_at': 0.5, 'why': 'xxxxxxxx'}))

    def test_a_recipe_without_make_is_said_unmade(self):
        unmade = [rid for rid, r in book().items() if not r.get('make')]
        if unmade:
            with self.assertRaises(StudioError) as caught:
                recipe_move(shot(move={'recipe': unmade[0], 'reveals': 'x', 'key_at': 0.5, 'why': 'xxxxxxxx'}))
            self.assertEqual(caught.exception.code, 'RECIPE_UNMADE')

    def test_no_code_branches_on_a_recipe_id(self):
        """A recipe is a library row: no engine code names one (N+1 - a new recipe needs no new branch)."""
        ids = set(book())
        found = []
        for file in sorted((ROOT / 'studio').rglob('*.py')) + sorted((ROOT / 'scripts').rglob('*.py')):
            for node in ast.walk(ast.parse(file.read_text())):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in ids:
                    found.append(f'{file.relative_to(ROOT)}:{node.lineno} {node.value}')
        self.assertEqual(found, [])

    def test_shotlist_without_a_move_is_held(self):
        from studio.decisions import move_lines
        lines = move_lines({'shots': [{'shot_id': 's01', 'direction': {'camera': 'held still, side on'}},
                                      {'shot_id': 's02', 'direction': {'move': {'recipe': 'dig_in', 'reveals': 'x', 'key_at': 0.5,
                                                                                'why': 'xxxxxxxx'}}}]})
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith('MOVE_UNDECIDED: s01'))


if __name__ == '__main__':
    unittest.main()
