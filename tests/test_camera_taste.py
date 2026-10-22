"""camera_taste: the user's reference camera profile and where a shot's camera sits in it (2026-10-12: "카메라 움직임이
역동적이면 좋을것같은데 그걸 확인할 방법이 없네"). Pure functions on recorded measurements; no Blender."""
import unittest

from studio import camera_taste as ct


def cam(axis, x=0.0, y=0.0, scale=0.0, roll=0.0, parallax=0.0):
    return {'axis': axis, 'rate_per_s': {'x': x, 'y': y, 'scale': scale, 'roll_deg': roll}, 'parallax_per_s': parallax}


SHOTS = ([{'camera': cam('scale_in', scale=s, parallax=0.03), 'camera_envelope': {'burst_share': 0.3, 'hold_frac': 0.4}, 'len_s': 2.5}
          for s in (0.05, 0.08, 0.1, 0.12, 0.2)]
         + [{'camera': cam('x', x=t, parallax=0.04, roll=r), 'len_s': 3.3} for t, r in ((0.05, 0.1), (0.1, 0.2), (0.12, 0.5), (0.2, 1.0), (0.3, 4.0))]
         + [{'camera': cam('still'), 'len_s': 2.5} for _ in range(4)])


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.profile = ct.build_profile(SHOTS, 'reference', 'the user picked it', ['group'])

    def test_kinds_and_shares(self):
        kinds = self.profile['kinds']
        self.assertEqual(sorted(kinds), ['hold', 'push', 'travel'])
        self.assertAlmostEqual(kinds['hold']['share'], 4 / 14, places=3)
        self.assertEqual(kinds['push']['scale']['p50'], 0.1)
        self.assertEqual(kinds['travel']['length_s']['p50'], 3.3)

    def test_flat_and_harsh_against_the_shots_of_the_same_kind(self):
        slow = ct.judge(self.profile, {'camera': cam('x', x=0.02, parallax=0.04), 'length_s': 3.0})
        self.assertTrue(slow['lines'] and slow['lines'][0].startswith('CAMERA_FLAT'), slow)
        fast = ct.judge(self.profile, {'camera': cam('scale_in', scale=0.5), 'length_s': 2.0})
        self.assertTrue(fast['lines'][0].startswith('CAMERA_HARSH'), fast)
        middle = ct.judge(self.profile, {'camera': cam('x', x=0.12, parallax=0.04), 'length_s': 3.3})
        self.assertEqual(middle['lines'], [])
        self.assertIn('travel p', middle['words'])

    def test_rolling_past_nearly_every_reference_shot_is_harsh(self):
        """2026-10-12: matching a picture's lean rolled s20's camera 22 deg/s; the reference stays under ~5."""
        rolled = ct.judge(self.profile, {'camera': cam('scale_out', scale=0.1, roll=22.0), 'length_s': 2.0})
        self.assertTrue(any('turns 22.0 deg/s' in l for l in rolled['lines']), rolled)
        gentle = ct.judge(self.profile, {'camera': cam('scale_out', scale=0.1, roll=1.5), 'length_s': 2.0})
        self.assertFalse(any('turns' in l for l in gentle['lines']))   # below the floor: never harsh

    def test_a_held_shot_alone_is_not_flat_but_a_static_reel_is(self):
        held = ct.judge(self.profile, {'camera': cam('still'), 'length_s': 2.0})
        self.assertEqual(held['lines'], [])
        reel = [{'camera': cam('still')}] * 7 + [{'camera': cam('x', x=0.1)}] * 3
        self.assertTrue(ct.reel_lines(self.profile, reel)[0].startswith('CAMERA_FLAT'))
        self.assertEqual(ct.reel_lines(self.profile, [{'camera': cam('still')}] * 3 + [{'camera': cam('x', x=0.1)}] * 7), [])

    def test_asking_for_more_motion_raises_the_flat_line(self):
        shot = {'camera': cam('x', x=0.12, parallax=0.04), 'length_s': 3.3}   # p40 of the reference's travel shots
        self.assertEqual(ct.judge(self.profile, shot)['lines'], [])
        livelier = {**self.profile, 'flat_below': 0.5}
        self.assertTrue(ct.judge(livelier, shot)['lines'][0].startswith('CAMERA_FLAT'))

    def test_learning_needs_the_users_words(self):
        with self.assertRaises(Exception):
            ct.learn(['group'], 'reference', '')


class FeelTest(unittest.TestCase):
    """2026-10-12, the user: "숫자는 같아도 다른 느낌을 줄수 있잖아" - recipes name a feel; liveliness is not speed alone."""
    def setUp(self):
        shots = [{**s, 'feel': {'streak': v}} for s, v in zip(SHOTS, (0.0, 0.01, 0.02, 0.03, 0.04) * 3)]
        self.profile = {**ct.build_profile(shots, 'reference', 'the user picked it', ['group']), 'flat_below': 0.4}

    def test_a_slow_camera_with_things_streaking_past_is_not_flat(self):
        slow = {'camera': cam('x', x=0.02, parallax=0.04), 'length_s': 3.0}
        self.assertTrue(ct.judge(self.profile, slow)['lines'][0].startswith('CAMERA_FLAT'))
        passing = {**slow, 'feel': {'streak': 0.06, 'near': 0.1}}
        self.assertEqual(ct.judge(self.profile, passing)['lines'], [])
        gentle = {'camera': cam('scale_in', scale=0.03, parallax=0.03), 'length_s': 2.5, 'envelope': {'burst_share': 0.3, 'hold_frac': 0.4}}
        self.assertTrue(ct.judge(self.profile, gentle)['lines'][0].startswith('CAMERA_FLAT'))
        settling = {**gentle, 'envelope': {'burst_share': 0.9, 'hold_frac': 0.5}, 'feel': {'rhythm': 'burst_settle', 'streak': 0.0}}
        self.assertEqual(ct.judge(self.profile, settling)['lines'], [])   # a hard rush then a settle, at the same slow rate

    def test_too_much_streaking_is_harsh(self):
        strobing = {'camera': cam('x', x=0.12, parallax=0.04), 'length_s': 3.3, 'feel': {'streak': 0.4}}
        self.assertTrue(any('streaks past' in l for l in ct.judge(self.profile, strobing)['lines']))
        below_floor = {**strobing, 'feel': {'streak': 0.08}}   # above the reference, below the floor
        self.assertFalse(any('streaks past' in l for l in ct.judge(self.profile, below_floor)['lines']))

    def test_recipes(self):
        self.assertEqual(ct.recipe({'heading_on': 0.8}, 'push')['recipe'], 'dig_in')
        self.assertEqual(ct.recipe({'heading_off': 0.1}, 'push')['recipe'], 'dig_in')   # pixels: near the centre stands in
        self.assertIsNone(ct.recipe({'heading_on': 0.1, 'heading_off': 0.1}, 'push')['recipe'])   # the box is known: it decides
        self.assertEqual(ct.recipe({'streak': 0.1, 'near': 0.2}, 'travel')['recipe'], 'pass_by')
        self.assertEqual(ct.recipe({'counter': -0.8}, 'travel')['recipe'], 'against')
        self.assertEqual(ct.recipe({'pitch_deg': 15, 'swirl': 0.5, 'rhythm': 'build'}, 'hold')['recipe'], 'look_up')
        self.assertEqual(ct.recipe({'pinned': 0.1}, 'travel')['recipe'], 'ride_along')
        self.assertIsNone(ct.recipe({}, 'travel')['recipe'])
        # a held pixel-measured shot: swirl and rhythm are known, the looking-up that defines look_up is not - it is a
        # camera at rest watching (watch, 2026-10-22), not a look-up
        self.assertEqual(ct.recipe({'swirl': 0.2, 'rhythm': 'hold'}, 'hold')['recipe'], 'watch')

    def test_more_known_values_name_the_shot_and_ties_are_mixed(self):
        both = ct.recipe({'streak': 0.1, 'near': 0.2, 'pinned': 0.1}, 'travel')
        self.assertEqual((both['recipe'], both['also']), ('pass_by', ['ride_along']))
        tie = ct.recipe({'counter': -0.8, 'pinned': 0.1}, 'travel')
        self.assertIsNone(tie['recipe']); self.assertEqual(sorted(tie['also']), ['against', 'ride_along'])

    def test_a_new_recipe_is_data_only(self):
        """N+1: a recipe the code has never seen classifies without a code change."""
        book = ct.recipes() + [{'id': 'drift', 'min_known': 2, 'when': [{'value': 'kind', 'in': ['travel']}, {'value': 'swirl', 'min': 1, 'max': 4},
                                                                        {'value': 'eye_rate', 'max': 0.2}]}]
        self.assertEqual(ct.recipe({'swirl': 2.0, 'eye_rate': 0.1}, 'travel', book)['recipe'], 'drift')

    def test_the_profile_keeps_the_reference_mix(self):
        mix = self.profile['recipes']
        self.assertEqual(mix['measured_shots'], 14)
        self.assertAlmostEqual(sum(mix['shares'].values()) + mix['unnamed_share'], 1.0, places=2)


class TakesAndReelTest(unittest.TestCase):
    def test_takes_that_all_feel_the_same_are_one_idea(self):
        from studio.storyboard import feel_warnings
        same = [{'id': i, 'dynamics': {'recipe': 'dig_in'}} for i in 'ABC']
        self.assertTrue(feel_warnings(same)[0].startswith('TAKES_ONE_FEEL'))
        varied = [{'id': 'A', 'dynamics': {'recipe': 'dig_in'}}, {'id': 'B', 'dynamics': {'recipe': 'pass_by'}}]
        self.assertEqual(feel_warnings(varied), [])
        missed = [{'id': 'A', 'recipe': 'pass_by', 'dynamics': {'recipe': None, 'also': []}}, {'id': 'B', 'dynamics': {'recipe': 'dig_in'}}]
        self.assertTrue(feel_warnings(missed)[0].startswith('TAKE_RECIPE_DIFFERS: take A was built as pass_by'))

    def test_a_reel_of_one_feel(self):
        profile = {'recipes': {'shares': {'dig_in': 0.3, 'pass_by': 0.2}}}
        self.assertTrue(ct.recipe_lines(profile, ['dig_in'] * 6 + ['pass_by'] * 2)[0].startswith('RECIPE_MONOTONE'))
        self.assertEqual(ct.recipe_lines(profile, ['dig_in'] * 4 + ['pass_by'] * 4), [])


class InsideAndJoinsTest(unittest.TestCase):
    def test_going_inside_is_a_recipe_from_data_alone(self):
        self.assertEqual(ct.recipe({'inside_gain': 0.4}, 'push')['recipe'], 'go_inside')
        self.assertIsNone(ct.recipe({'inside_gain': 0.05}, 'push')['recipe'])

    def test_the_reference_mix_of_cuts_and_reveals(self):
        shots = [{'camera': cam('x', x=0.1), 'join_in': k, 'labels': [{'reveal_method': r}]}
                 for k, r in (('carry', 'section_cut'), ('settle_burst', 'none'), ('carry', 'none'), ('reverse', 'none'))]
        profile = ct.build_profile(shots, 'reference', 'the user picked it', ['group'])
        self.assertEqual(profile['joins']['shares'], {'carry': 0.5, 'reverse': 0.25, 'settle_burst': 0.25})
        self.assertEqual(profile['reveals']['shares']['section_cut'], 0.25)

    def test_a_reel_that_breaks_its_cuts(self):
        profile = {'joins': {'shares': {'carry': 0.06, 'reverse': 0.03, 'turn': 0.1, 'stop': 0.25, 'still': 0.33, 'settle_burst': 0.23}}}
        self.assertTrue(ct.join_lines(profile, ['reverse', 'turn', 'carry', 'reverse'])[0].startswith('JOINS_ROUGH'))
        self.assertEqual(ct.join_lines(profile, ['stop', 'settle_burst', 'stop', 'still', 'reverse']), [])   # settling is the grammar


class RevealPlanTest(unittest.TestCase):
    """2026-10-12, the user: where to go inside is the planner's judgement; the check keeps it from being skipped."""
    PROFILE = {'reveals': {'shares': {'none': 0.6, 'section_cut': 0.4}}}

    def test_a_plan_with_no_inside(self):
        from studio.decisions import reveal_lines
        body = {'shots': [{'shot_id': 's01', 'direction': {'camera': 'push'}}]}
        self.assertTrue(reveal_lines(body, self.PROFILE)[0].startswith('REVEAL_UNPLANNED'))
        planned = {'shots': [{'shot_id': 's01', 'direction': {'reveal': 'section_cut'}}]}
        self.assertEqual(reveal_lines(planned, self.PROFILE), [])
        shown = {'shots': [{'shot_id': 's01', 'show': [{'at': 'end', 'parts': ['a/b'], 'how': 'xray', 'why': 'the inside'}]}]}
        self.assertEqual(reveal_lines(shown, self.PROFILE), [])   # a show moment that opens is a planned inside
        declined = {'shots': [{'shot_id': 's01', 'pattern': {'departs': [{'item': 'reveal_method', 'why': 'a sealed part: nothing inside to show'}]}}]}
        self.assertEqual(reveal_lines(declined, self.PROFILE), [])
        self.assertEqual(reveal_lines(body, {'reveals': {'shares': {'none': 0.97}}}), [])   # a reference that rarely opens anything

    def test_the_plan_vocabulary_is_the_labels_vocabulary(self):
        import json
        from studio.common import REPO
        from studio.screen_grammar import REVEALS
        found = []
        def walk(node):
            if isinstance(node, dict):
                if 'reveal' in node.get('properties', {}):
                    found.append(node['properties']['reveal']['enum'])
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        # the shot list's direction and the shot's copy of it (decisions._materialize_shotlist) - 2026-10-12: approving the
        # first plan with a reveal failed on the shot's schema
        for name in ('decision', 'shot'):
            walk(json.loads((REPO / f'schemas/studio-v1/{name}.schema.json').read_text()))
        self.assertEqual(found, [REVEALS, REVEALS])


class MeasuredIdsTest(unittest.TestCase):
    """2026-10-12: a waypoints shot measured no subject (the move's target alone was read) and an old version was measured
    over a later plan's length. The subjects follow the frame probe's rule; the parts what the shot explains."""
    def test_subjects_by_the_probe_rule_and_parts_by_what_is_explained(self):
        from unittest import mock
        shot = {'camera': {'move': {'type': 'waypoints', 'params': {'aim': 'r06.aim', 'parts': ['booster/lox_tube']}}},
                'screen': {'subject': ['booster/piston_assembly_0'], 'details': [{'id': 'booster/fuel_tank'}]}}
        with mock.patch('studio.blender.probe_inputs', return_value={'subjects': ['booster', 'booster/piston_assembly_0']}), \
                mock.patch.object(ct, 'load_project_fps', return_value=30):
            self.assertEqual(ct.measured_ids('root', shot), (['booster', 'booster/piston_assembly_0'], ['booster/fuel_tank']))
            del shot['screen']['details']
            self.assertEqual(ct.measured_ids('root', shot)[1], ['booster/lox_tube'])
            del shot['camera']['move']['params']['parts']
            self.assertEqual(ct.measured_ids('root', shot)[1], ['booster/piston_assembly_0'])


if __name__ == '__main__':
    unittest.main()
