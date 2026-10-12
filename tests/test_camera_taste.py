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
        # a held pixel-measured shot: swirl and rhythm are known, the looking-up that defines look_up is not
        self.assertIsNone(ct.recipe({'swirl': 0.2, 'rhythm': 'hold'}, 'hold')['recipe'])

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


if __name__ == '__main__':
    unittest.main()
