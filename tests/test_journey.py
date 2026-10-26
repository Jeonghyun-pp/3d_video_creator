"""The reel's camera journey (studio/journey.py): the shot list says the places the camera goes through, at what scale and
why, and each cut says where it goes from and to and what it is for (travel, show, rest). The director writes it; the
engine holds only what follows from it - a cut names places the journey has, a travel cut flies only through places the
3D builds, and a cut that jumps past places without handing over to a new place is a flow finding answered with why.
Why (2026-10-25): each cut's camera was chosen alone; nothing planned how the reel moves through its world."""
import unittest

from studio import journey, reel_flow

WORLD = {'elements': [
    {'id': 'fab_building', 'function': 'set', 'owner': {'shape': 'blender', 'look': 'blender'}, 'blockout': {'kind': 'box', 'size_m': [80, 60, 20]}},
    {'id': 'cleanroom', 'function': 'set', 'owner': {'shape': 'blender', 'look': 'blender'}, 'ids': ['cleanroom_bay']},
    {'id': 'foup', 'function': 'subject', 'owner': {'shape': 'blender', 'look': 'blender'}},
    {'id': 'skyline', 'function': 'context', 'owner': {'shape': 'generative', 'look': 'generative'}}]}

PLACES = [{'place': 'fab_building', 'scale': 'building', 'why': 'where the chips are made', 'passable': ['fab_building/roof']},
          {'place': 'cleanroom', 'scale': 'room', 'why': 'the clean air the line works in'},
          {'place': 'foup', 'scale': 'object', 'why': 'the box the wafers travel in'}]


def shot(sid, frm, to, role='show', handover=None, **extra):
    row = {'shot_id': sid, 'purpose': 'p', 'line_ids': [], 'duration_s': 2.0,
           'direction': {'journey': {'from': frm, 'to': to, 'role': role, 'why': 'a test cut'}}, **extra}
    if handover:
        row['handover'] = {'kind': handover, 'why': 'a test hand-over'}
    return row


class JourneyTest(unittest.TestCase):
    def codes(self, body, world=WORLD):
        errors, warnings = journey.lines(body, world)
        return [e.split(':')[0] for e in errors], [w.split(':')[0] for w in warnings]

    def test_a_shot_list_says_its_journey_and_every_cut_its_part_in_it(self):
        self.assertIn('JOURNEY_UNSAID', self.codes({'shots': [shot('a', 0, 0)]})[0])
        bare = {'journey': PLACES, 'shots': [{'shot_id': 'a', 'purpose': 'p', 'line_ids': [], 'duration_s': 2.0}]}
        self.assertIn('JOURNEY_UNSAID', self.codes(bare)[0])
        self.assertEqual(self.codes({'journey': PLACES, 'shots': [shot('a', 0, 2, 'travel'), shot('b', 2, 2)]}), ([], []))

    def test_one_place_said_with_why_is_a_journey(self):
        one = {'journey': [{'place': 'foup', 'scale': 'object', 'why': 'the whole story happens at the box'}],
               'shots': [shot('a', 0, 0), shot('b', 0, 0, 'rest')]}
        self.assertEqual(self.codes(one), ([], []))

    def test_a_cut_names_places_the_journey_has(self):
        self.assertIn('JOURNEY_PLACE_UNKNOWN', self.codes({'journey': PLACES, 'shots': [shot('a', 0, 5, 'travel')]})[0])

    def test_a_travel_cut_flies_only_through_places_the_3d_builds(self):
        sky = PLACES + [{'place': 'skyline', 'scale': 'outside', 'why': 'the city around it'}]
        self.assertIn('JOURNEY_PLACE_UNBUILT', self.codes({'journey': sky, 'shots': [shot('a', 3, 1, 'travel')]})[0])
        named = PLACES + [{'place': 'the sky above', 'scale': 'outside', 'why': 'a name, nothing built'}]
        self.assertIn('JOURNEY_PLACE_UNBUILT', self.codes({'journey': named, 'shots': [shot('a', 3, 2, 'travel')]})[0])
        # a cut that shows or rests at an unbuilt place is not flying through it (its picture is another check's)
        self.assertEqual(self.codes({'journey': sky, 'shots': [shot('a', 3, 3, 'show')]}), ([], []))

    def test_the_build_writes_the_places_a_cut_passes_and_drift_reads_past_them(self):
        body = {'journey': PLACES}
        got = journey.passes_of(body, {'from': 0, 'to': 2})
        self.assertEqual([p['place'] for p in got], ['fab_building', 'cleanroom', 'foup'])
        self.assertEqual(got[0]['passable'], ['fab_building/roof'])
        self.assertNotIn('passable', got[1])
        self.assertEqual(journey.passes_of(body, {'from': 0, 'to': 7}), [])
        listed = {'journey': {'from': 0, 'to': 2, 'role': 'travel', 'why': 'w'}}
        self.assertEqual(journey.as_listed({'journey': {**listed['journey'], 'passes': got}}), listed)

    def test_the_places_a_travel_cut_passes_in_order(self):
        self.assertEqual(journey.passes({'from': 0, 'to': 2}), [0, 1, 2])
        self.assertEqual(journey.passes({'from': 2, 'to': 0}), [2, 1, 0])
        self.assertEqual(journey.passable({'journey': PLACES}, 0), ['fab_building/roof'])
        self.assertEqual(journey.passable({'journey': PLACES}, 1), [])


class JourneyFlowTest(unittest.TestCase):
    def rows(self, shots):
        return reel_flow.plan_rows(shots)

    def test_a_jump_past_places_is_a_flow_finding_unless_it_hands_over_to_a_new_place(self):
        jump = [shot('a', 0, 0), shot('b', 2, 2)]
        found = [l for l in reel_flow.lines(self.rows(jump)) if l.startswith('JOURNEY_JUMP')]
        self.assertEqual(len(found), 1)
        self.assertIn('JOURNEY_JUMP', reel_flow.DEPARTS)
        self.assertTrue(reel_flow.unresolved(found))   # answered: fixed, or departed with why
        handed = [shot('a', 0, 0, handover='new_place'), shot('b', 2, 2)]
        self.assertFalse([l for l in reel_flow.lines(self.rows(handed)) if l.startswith('JOURNEY_JUMP')])
        departed = [shot('a', 0, 0), shot('b', 2, 2, pattern={'departs': [{'item': 'journey_jump', 'why': 'a cold open at the box'}]})]
        self.assertFalse([l for l in reel_flow.lines(self.rows(departed)) if l.startswith('JOURNEY_JUMP')])
        step = [shot('a', 0, 1, 'travel'), shot('b', 2, 2)]   # the next place over: no jump
        self.assertFalse([l for l in reel_flow.lines(self.rows(step)) if l.startswith('JOURNEY_JUMP')])

    def test_a_cut_that_starts_at_another_place_opens_it_wide(self):
        # the journey says where a new place begins: a cut that starts somewhere the cut before did not end opens it
        cold = [dict(shot('a', 0, 0), role='hook'), dict(shot('b', 1, 1), role='detail', direction={
            'journey': {'from': 1, 'to': 1, 'role': 'show', 'why': 'w'}, 'scale_step': 'closer'})]
        self.assertTrue([l for l in reel_flow.lines(self.rows(cold)) if l.startswith('ESTABLISH_MISSING')])
        flown = [dict(shot('a', 0, 1, 'travel'), role='hook'), dict(shot('b', 1, 1), direction={
            'journey': {'from': 1, 'to': 1, 'role': 'show', 'why': 'w'}, 'scale_step': 'closer'})]
        self.assertFalse([l for l in reel_flow.lines(self.rows(flown)) if l.startswith('ESTABLISH_MISSING')])   # flown into


DIVE_WORLD = {'elements': [
    {'id': 'fab', 'function': 'set', 'owner': {'shape': 'blender', 'look': 'blender'}, 'blockout': {'kind': 'box', 'size_m': [80, 60, 20]}},
    {'id': 'wafer', 'function': 'subject', 'owner': {'shape': 'blender', 'look': 'blender'}, 'blockout': {'kind': 'cylinder', 'size_m': [0.3, 0.3, 0.001]}},
    {'id': 'chip', 'function': 'subject', 'owner': {'shape': 'blender', 'look': 'blender'}, 'blockout': {'kind': 'box', 'size_m': [0.01, 0.01, 0.0008]}},
    {'id': 'wiring', 'function': 'subject', 'owner': {'shape': 'blender', 'look': 'blender'}, 'blockout': {'kind': 'box', 'size_m': [2e-5, 2e-5, 1e-5]}},
    {'id': 'transistor', 'function': 'subject', 'owner': {'shape': 'blender', 'look': 'blender'}, 'blockout': {'kind': 'box', 'size_m': [5e-8, 5e-8, 5e-8]}}]}
DIVE = [{'place': 'fab', 'scale': 'building', 'why': 'where it is made'},
        {'place': 'wafer', 'scale': 'object', 'why': 'what the line makes'},
        {'place': 'chip', 'scale': 'inside', 'why': 'one chip on it'},
        {'place': 'wiring', 'scale': 'inside', 'why': 'the layers inside the chip'},
        {'place': 'transistor', 'scale': 'inside', 'why': 'the switch the dust kills'}]


class ScaleStepTest(unittest.TestCase):
    """journey.scale is read: a step of more than one scale level (a named scale, or a factor of ten in true size where
    the world builds the place at a size) is crossed only by a dive - the cut pushes into the next place and hands
    over scale_through to a cut that starts there."""

    def codes(self, shots, places=DIVE, world=DIVE_WORLD):
        errors, _ = journey.lines({'journey': places, 'shots': shots}, world)
        return [e.split(':')[0] for e in errors]

    def test_levels_are_named_scales_and_factors_of_ten_in_true_size(self):
        self.assertEqual(journey.scale_gap(DIVE[0], DIVE[1], DIVE_WORLD), 2)     # building -> object, 80 m -> 0.3 m
        self.assertEqual(journey.scale_gap(DIVE[1], DIVE[2], DIVE_WORLD), 1)     # 0.3 m -> 1 cm: 1.5 decades... named 1
        self.assertEqual(journey.scale_gap(DIVE[2], DIVE[3], DIVE_WORLD), 3)     # both 'inside', 1 cm -> 20 um: sizes say 3
        self.assertEqual(journey.scale_gap(DIVE[3], DIVE[4], DIVE_WORLD), 3)     # a finer scale needs no new name
        self.assertEqual(journey.scale_gap(PLACES[0], PLACES[1], WORLD), 1)      # no size known: the named scales
        self.assertTrue(journey.inward(DIVE[2], DIVE[3], DIVE_WORLD))
        self.assertFalse(journey.inward(DIVE[3], DIVE[2], DIVE_WORLD))

    def test_one_camera_does_not_fly_across_more_than_one_level(self):
        self.assertIn('JOURNEY_SCALE_JUMP', self.codes([shot('a', 0, 2, 'travel'), shot('b', 2, 2)]))
        # pushing into the next place and handing over a dive is how it is crossed
        dive = [shot('a', 0, 1, 'travel', handover='scale_through'), shot('b', 1, 2, 'travel', handover='scale_through'),
                shot('c', 2, 3, 'travel', handover='scale_through'), shot('d', 3, 4, 'travel', handover='scale_through'),
                shot('e', 4, 4)]
        self.assertEqual(self.codes(dive), [])
        # a one-level step may still be one camera (the fab's roof to its bay)
        self.assertEqual(self.codes([shot('a', 0, 2, 'travel'), shot('b', 2, 2)], PLACES, WORLD), [])

    def test_a_plain_cut_across_more_than_one_level_is_a_jump(self):
        self.assertIn('JOURNEY_SCALE_JUMP', self.codes([shot('a', 0, 0), shot('b', 1, 1)]))
        self.assertIn('JOURNEY_SCALE_JUMP', self.codes([shot('a', 2, 3, 'travel', handover='new_place'), shot('b', 3, 4)]))
        self.assertEqual(self.codes([shot('a', 0, 1, 'travel', handover='scale_through'), shot('b', 1, 1)]), [])

    def test_a_dive_hands_the_place_it_pushes_into_to_the_cut_that_starts_there(self):
        self.assertIn('SCALE_THROUGH_PLACE', self.codes([shot('a', 0, 0, handover='scale_through'), shot('b', 1, 1)]))
        self.assertIn('SCALE_THROUGH_PLACE', self.codes([shot('a', 1, 1), shot('b', 1, 2, 'travel', handover='scale_through')]))   # to nothing
        self.assertIn('SCALE_THROUGH_OUTWARD', self.codes([shot('a', 3, 2, 'travel', handover='scale_through'), shot('b', 2, 2)]))


if __name__ == '__main__':
    unittest.main()
