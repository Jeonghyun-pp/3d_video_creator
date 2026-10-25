"""Flying in (2026-10-25): the camera comes from outside its journey's first place down one line of sight to land on the
shot's subject, through only the slabs the journey declares passable (each crossing a mark the reveal cutter binds to),
and arrives by the director's settle_at - eased in, cruising, eased out, then still. Pure parts here; the Blender side
(a cutter at each declared crossing, an undeclared one refused) is tests/studio/fly_in_smoke.py."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'studio/blender_ops'))
import camera_moves_core as moves  # noqa: E402
import camera_rig_core as rig  # noqa: E402

from studio.common import StudioError  # noqa: E402
from studio.recipe_moves import book, check_intent, recipe_move, settle_timing  # noqa: E402

GEO = {'points': {}, 'boxes': {
    'hall': ((-20.0, -15.0, 0.0), (20.0, 15.0, 10.0)),
    'bay': ((-8.0, -6.0, 0.0), (8.0, 6.0, 4.0)),
    'roof': ((-20.0, -15.0, 10.0), (20.0, 15.0, 10.4)),
    'ceiling': ((-8.0, -6.0, 4.0), (8.0, 6.0, 4.2)),
    'wall': ((19.6, -15.0, 0.0), (20.0, 15.0, 10.0)),
    'foup': ((-0.2, -0.2, 0.8), (0.2, 0.2, 1.2))}}
for ref, box in list(GEO['boxes'].items()):
    GEO['points'][ref] = moves.centre(box)


def fly(**params):
    move = {'type': 'fly_to', 'lens_mm': 24.0,
            'params': {'places': ['hall', 'bay', 'foup'], 'target': 'foup', 'passable': ['roof', 'ceiling'],
                       'height_share': 0.5, 'azimuth_deg': 0.0, 'elevation_deg': 55.0, **params}}
    return moves.plan(move, GEO)


def inside(point, box, pad=1e-6):
    return all(box[0][i] - pad <= point[i] <= box[1][i] + pad for i in range(3))


class FlyInPlanTest(unittest.TestCase):
    def test_one_line_from_above_the_first_place_to_the_subject_framed(self):
        plan = fly()
        start, end = plan['waypoints'][0], plan['waypoints'][-1]
        self.assertGreater(start[2], GEO['boxes']['hall'][1][2])                    # outside, above the hall
        target = GEO['points']['foup']
        self.assertEqual(plan['aim'], target)                                       # the destination is in view
        distance = moves._len(moves._sub(end, target))
        self.assertAlmostEqual(distance, moves.fit_distance(GEO['boxes']['foup'], 24.0, 0.5), places=3)
        ray = moves._norm(moves._sub(start, target))
        for p in plan['waypoints']:   # every waypoint on the one line of sight to the subject
            off = moves._sub(moves._sub(p, target), moves._mul(ray, sum(a * b for a, b in zip(moves._sub(p, target), ray))))
            self.assertLess(moves._len(off), 1e-6)

    def test_each_declared_slab_crossed_is_a_mark_in_order(self):
        plan = fly()
        self.assertTrue(inside(plan['waypoints'][plan['marks']['pass0']], GEO['boxes']['roof']))
        self.assertTrue(inside(plan['waypoints'][plan['marks']['pass1']], GEO['boxes']['ceiling']))
        self.assertLess(plan['marks']['pass0'], plan['marks']['pass1'])
        self.assertEqual(plan['notes']['passages'], {'pass0': 'roof', 'pass1': 'ceiling'})

    def test_a_declared_slab_the_line_misses_and_a_place_off_the_line_are_said(self):
        plan = fly(passable=['roof', 'wall'], places=['hall', 'wall', 'foup'])
        self.assertEqual(plan['notes']['passable_unused'], ['wall'])
        self.assertEqual(plan['notes']['places_missed'], ['wall'])
        self.assertNotIn('pass1', plan['marks'])

    def test_it_comes_down_into_its_places(self):
        with self.assertRaises(ValueError):
            fly(elevation_deg=0.0)


class ApproachTimingTest(unittest.TestCase):
    def test_closing_in_eases_the_subjects_size_on_screen_not_the_distance(self):
        """2026-10-25 (fly_in smoke): eased in metres, the last metres to a 0.4 m box doubled its size in a quarter
        second (jerk 2.4); eased in log distance its size grows as the timing says."""
        plan = fly()
        self.assertEqual(plan['notes']['approach'], {'from_m': plan['notes']['reach_m'], 'to_m': plan['notes']['land_m']})
        base = settle_timing(0.6, 0.4)
        warped = moves.approach_timing(rig.timing_curve(base), **plan['notes']['approach'])
        curve, eased = rig.timing_curve(warped), rig.timing_curve(base)
        reach, land = plan['notes']['approach']['from_m'], plan['notes']['approach']['to_m']
        for t in (0.1, 0.2, 0.3, 0.45, 0.55):   # the log of the distance left follows the timing
            left = reach - (reach - land) * curve(t)
            self.assertAlmostEqual(math.log(reach / left) / math.log(reach / land), eased(t), delta=0.02)
        self.assertAlmostEqual(curve(0.6), 1.0, places=4)
        self.assertIsNone(moves.approach_timing(rig.timing_curve(base), 5.0, 5.0))   # nothing to close in on: as it was


class SettleTimingTest(unittest.TestCase):
    def test_it_arrives_at_settle_at_and_holds(self):
        for cruise in (0.0, 0.4):
            curve = rig.timing_curve(settle_timing(0.6, cruise))
            values = [curve(i / 100) for i in range(101)]
            self.assertEqual(values, sorted(values))                        # never goes back
            self.assertAlmostEqual(curve(0.6), 1.0, places=6)
            self.assertAlmostEqual(curve(0.9), 1.0, places=6)               # still after it
            self.assertLess(curve(0.05), 0.05)                              # eased in

    def test_the_cruise_is_even(self):
        curve = rig.timing_curve(settle_timing(0.8, 0.5))
        speeds = [(curve(t + 0.01) - curve(t)) / 0.01 for t in (0.3, 0.35, 0.4, 0.45)]
        self.assertLess(max(speeds) - min(speeds), 0.1 * max(speeds))


def journey_shot(move=None, passes=True):
    part = {'from': 0, 'to': 2, 'role': 'travel', 'why': 'into the bay to the box'}
    if passes:
        part['passes'] = [{'place': 'hall', 'scale': 'building', 'passable': ['roof']},
                          {'place': 'bay', 'scale': 'room', 'passable': ['ceiling']},
                          {'place': 'foup', 'scale': 'object'}]
    return {'shot_id': 's01', 'frame': {'focus': ['foup'], 'size': 'close'},
            'show': [{'at': 'start', 'parts': ['foup'], 'how': 'exterior'}],
            'direction': {'journey': part, 'move': move or {
                'recipe': 'fly_in', 'reveals': 'the box the wafers ride in', 'key_at': 0.8, 'settle_at': 0.6,
                'why': 'from outside the fab down to the box', 'settings': {'height_share': 0.5, 'azimuth_deg': 30}}}}


class FlyInRecipeTest(unittest.TestCase):
    def test_the_recipe_flies_the_cuts_journey_through_its_declared_slabs(self):
        made = recipe_move(journey_shot())
        move = made['move']
        self.assertEqual(move['type'], 'fly_to')
        self.assertEqual(move['params']['places'], ['hall', 'bay', 'foup'])
        self.assertEqual(move['params']['passable'], ['roof', 'ceiling'])
        self.assertEqual(move['params']['target'], 'foup')
        self.assertEqual(move['params']['height_share'], 0.5)      # the framing size is a value the move reads
        self.assertNotIn('cruise_share', move['params'])           # a timing value shapes the timing, not the path
        self.assertEqual(move['timing'], settle_timing(0.6, book()['fly_in']['make']['choose']['cruise_share'][2]))

    def test_it_needs_a_journey_and_a_settle(self):
        with self.assertRaises(StudioError) as caught:
            recipe_move(journey_shot(passes=False))
        self.assertEqual(caught.exception.code, 'RECIPE_NEEDS')
        unsettled = {'recipe': 'fly_in', 'reveals': 'x', 'key_at': 0.8, 'why': 'xxxxxxxx', 'settings': {'height_share': 0.5, 'azimuth_deg': 30}}
        self.assertTrue(any('MOVE_SETTLE_UNSAID' in e for e in check_intent(unsettled)))

    def test_any_recipe_settles_when_the_director_says_so(self):
        move = {'recipe': 'dig_in', 'reveals': 'the tank', 'key_at': 0.7, 'settle_at': 0.5, 'why': 'arrive, then the tank fills',
                'settings': {'height_share': 0.5, 'azimuth_deg': 30}}
        shot = {'shot_id': 's', 'frame': {'focus': ['boat']}, 'direction': {'move': move},
                'show': [{'at': 'start', 'parts': ['boat'], 'how': 'exterior'}, {'at': 0.6, 'parts': ['boat/tank'], 'how': 'cutaway', 'why': 'w'}]}
        self.assertEqual(recipe_move(shot)['move']['timing'], settle_timing(0.5))


if __name__ == '__main__':
    unittest.main()
