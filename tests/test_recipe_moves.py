import ast
from pathlib import Path
import unittest

from studio.blender_ops.camera_moves_core import MOVES, PARAMS
from studio.common import StudioError
from studio.recipe_moves import FOCUS_VALUES, TOKENS, book, check_intent, check_moments, framing_of, inherit, recipe_move

ROOT = Path(__file__).resolve().parents[1]
MOMENTS = [{'at': 0.0, 'size': 0.3, 'view': {'elevation_deg': 10, 'azimuth_deg': 20}, 'why': 'the whole boat'},
           {'at': 0.4, 'size': 0.5, 'view': {'elevation_deg': 5, 'azimuth_deg': 30}, 'lens_mm': 28, 'parts': ['boat/tank'], 'why': 'arrive at the tank'},
           {'at': 0.6, 'size': 0.5, 'view': {'elevation_deg': 5, 'azimuth_deg': 30}, 'lens_mm': 28, 'parts': ['boat/tank'], 'why': 'rest while it fills'},
           {'at': 1.0, 'size': 0.9, 'view': {'elevation_deg': 15, 'azimuth_deg': 70}, 'cut': True, 'why': 'go round and in'}]


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

    def test_a_recipe_the_engine_cannot_make_is_held_unless_written_as_moments(self):
        """2026-10-23 r3 s10: 'against' has no make; no take of it was built and the takes went on without it."""
        unmade = next(rid for rid, r in book().items() if not r.get('make'))
        lines = check_intent({'recipe': unmade, 'reveals': 'x', 'key_at': 0.5, 'why': 'xxxxxxxx'})
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith('MOVE_UNMADE'))
        self.assertEqual(check_intent({'recipe': unmade, 'key_at': 0.5, 'moments': MOMENTS}), [])

    def test_moments_are_the_cameras_framings_in_order(self):
        made = recipe_move(shot(move={'recipe': 'dig_in', 'reveals': 'the tank', 'key_at': 0.5, 'why': 'arrive, rest, go round',
                                      'moments': MOMENTS, 'path': 'arc'}), centre=[0.5, 0.4])
        move = made['move']
        self.assertEqual(move['type'], 'frame_to_frame')
        self.assertEqual(move['params'], {'target': 'boat', 'path': 'arc', 'follow': 0.5})
        self.assertEqual([f['at'] for f in move['frames']], [0.0, 0.4, 0.6, 1.0])
        self.assertEqual(move['frames'][1]['must_see'], ['boat/tank'])
        self.assertEqual(move['frames'][1]['centre'], [0.5, 0.4])
        self.assertEqual(move['frames'][3]['cut_edges'], ['top', 'bottom'])
        self.assertEqual(move['frames'][1]['height_share'], move['frames'][2]['height_share'])   # the rest: the same framing twice
        self.assertTrue(all(c['by'] == 'director' for c in made['chosen'].values()))

    def test_moments_are_checked(self):
        self.assertTrue(check_moments(MOMENTS[:1]))
        self.assertTrue(check_moments([MOMENTS[1], MOMENTS[0]]))                 # out of order
        self.assertTrue(check_moments([MOMENTS[0], {**MOMENTS[1], 'size': 0}]))
        self.assertTrue(check_moments([MOMENTS[0], {**MOMENTS[1], 'view': {'elevation_deg': 95, 'azimuth_deg': 0}}]))
        self.assertTrue(check_intent({'recipe': 'dig_in', 'moments': MOMENTS, 'settings': {'elevation_deg': 10}}))

    def test_a_compared_take_inherits_the_directors_framing(self):
        """2026-10-23 r3: compared takes took recipe defaults; ten of fourteen picked cuts were framed by nobody."""
        kept, outside = inherit(({'height_share': 0.55, 'elevation_deg': 10, 'azimuth_deg': 40, 'span': 0.3}, '$key'), 'dig_in')
        self.assertEqual(kept, {'height_share': 0.55, 'elevation_deg': 10, 'azimuth_deg': 40})   # dig_in chooses no span
        low = book()['look_up']['make']['choose']['elevation_deg'][1]
        kept, outside = inherit(({'elevation_deg': low + 20, 'height_share': 0.55}, '$subject'), 'look_up')
        self.assertIn('elevation_deg', outside)                                  # looking down is not looking up: said
        self.assertEqual(kept, {'height_share': 0.55})
        key = framing_of({'recipe': 'dig_in', 'key_at': 0.55, 'moments': MOMENTS})
        self.assertEqual(key, ({'height_share': 0.5, 'elevation_deg': 5, 'azimuth_deg': 30, 'lens_mm': 28}, '$subject'))
        # r3 s05: a moment sizing the whole hull 1.6 is no size for dig_in's tank - the size is left, the angle kept
        kept, outside = inherit(({'height_share': 1.6, 'elevation_deg': 10, 'azimuth_deg': 130}, '$subject'), 'dig_in')
        self.assertEqual((kept, outside), ({'elevation_deg': 10, 'azimuth_deg': 130}, {}))

    def test_a_shows_openings_are_cut_toward_the_moves_view_for_any_camera(self):
        """2026-10-22: openings came only from pictured moments; a recipe's move into a cut-open hull saw nothing."""
        from studio.blender_ops.camera_moves_core import show_moments
        moving = {**shot(), 'camera': {'move': recipe_move(shot())['move']}}
        rows = show_moments(moving)
        self.assertEqual([r['at'] for r in rows], [0.0, 0.6])
        self.assertEqual(rows[1]['show']['how'], 'cutaway')
        self.assertEqual(rows[1]['view']['elevation_deg'], book()['dig_in']['make']['choose']['elevation_deg'][2])
        closed = {**moving, 'show': [{'at': 'start', 'parts': ['boat'], 'how': 'exterior'}]}
        self.assertEqual(show_moments(closed), [])                      # nothing opens: nothing to cut
        with self.assertRaises(ValueError) as caught:                   # an opening and no direction to cut toward: said
            show_moments({**moving, 'camera': {'move': {'type': 'waypoints', 'params': {'points': []}}}})
        self.assertIn('SHOW_VIEW_UNKNOWN', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
