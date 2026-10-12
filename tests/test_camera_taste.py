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


if __name__ == '__main__':
    unittest.main()
