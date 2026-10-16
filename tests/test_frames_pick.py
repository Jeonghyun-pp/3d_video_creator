"""A moment's camera picked from its candidates sheet (studio frames candidates / frames pick): recorded with its picture
in the user's own words, carried by frames apply into move.frames[] with the moment's intent. A cafe, not the reel the
sheet came from (N+1)."""
import unittest

from studio import frame_targets as ft, frames_check
from studio.common import StudioError, write_json
from studio.project import validate_shot

from tests.test_cheats import cafe, say
from tests.test_frame_reading_checks import BASE


def sheet(scene):
    """A candidates sheet as frames candidates writes it: two moments, two poses each."""
    moments = []
    for at, concept in ((0.0, 'c01'), (1.0, 'c02')):
        moments.append({'at': at, 'concept_id': concept, 'candidates': [
            {'n': n, 'view': [5.0, 30.0 * n], 'offset': {'azimuth_deg': 30.0 * n, 'elevation_deg': 0.0}, 'lens_mm': 35.0 - 11.0 * n,
             'score': 0.5 + n, 'must_see_px': {}, 'departed': [], 'placement': {'aim': 'picture', 'closer': 1.0},
             'eye': [0.0, -8.0 + n, 1.6], 'aim': [0.0, 0.0, 1.0], 'roll_deg': 0.0, 'image': f'{concept}_{n}.png'} for n in (0, 1)]})
    write_json(scene.p / 'concepts' / 's' / 'candidates' / 'candidates.json', {'shot_id': 's', 'moments': moments})


class PickTest(unittest.TestCase):
    def test_a_pick_stays_when_its_picture_is_read_again(self):
        frames_check.pick(self.scene.p, 's', 'c02', 1, '오른쪽에서 보는 게 좋아')
        from unittest import mock

        def fake(images, prompt, schema, target):
            answer = {'pictures': [{**BASE, 'index': i} for i in range(len(images))]}
            write_json(target, {'answer': answer})
            return {'answer': answer}
        with mock.patch('studio.screen_grammar.codex_json', fake):   # the same pictures read again (forced)
            ft.measure(self.scene.p, 's', force=True)
        self.assertNotEqual(ft._store(self.scene.p, 's')['pictures']['c02']['read_at'], None)
        self.assertEqual(ft._store(self.scene.p, 's')['pictures']['c02']['chosen']['candidate'], 1)

    def setUp(self):
        self.scene = cafe(self)
        self.scene.read([BASE, BASE])
        say(self.scene, ['diner'])
        sheet(self.scene)

    def test_a_pick_is_recorded_with_its_picture_in_the_users_words(self):
        out = frames_check.pick(self.scene.p, 's', 'c02', 1, '오른쪽에서 보는 게 좋아')
        chosen = ft._store(self.scene.p, 's')['pictures']['c02']['chosen']
        self.assertEqual((chosen['view'], chosen['lens_mm'], chosen['by'], chosen['user_words'], chosen['candidate']),
                         ([5.0, 30.0], 24.0, 'user', '오른쪽에서 보는 게 좋아', 1))
        self.assertEqual(out['concept_id'], 'c02')
        frames_check.pick(self.scene.p, 's', '0.0', 0, 'first one is fine')   # by its at
        self.assertEqual(ft._store(self.scene.p, 's')['pictures']['c01']['chosen']['candidate'], 0)

    def test_a_pick_needs_the_users_words_a_moment_and_a_candidate_on_the_sheet(self):
        for words in (None, 'ok', 'User (paraphrased): the right one'):
            with self.assertRaises(StudioError):
                frames_check.pick(self.scene.p, 's', 'c02', 1, words)
        with self.assertRaises(StudioError):
            frames_check.pick(self.scene.p, 's', 'c09', 0, 'that one please')
        with self.assertRaises(StudioError):
            frames_check.pick(self.scene.p, 's', 'c02', 5, 'that one please')
        self.assertNotIn('chosen', ft._store(self.scene.p, 's')['pictures']['c02'])

    def test_frames_apply_carries_the_pick_and_each_moments_intent(self):
        frames_check.pick(self.scene.p, 's', 'c02', 1, '오른쪽에서 보는 게 좋아')
        ft.apply(self.scene.p, 's', target='diner')
        shot = self.scene.shot()
        shot['camera']['move']['params']['solve'] = 'exact'                         # asked for by name: an exact shot as it was
        write_json(self.scene.p / 'shots' / 's' / 'shot.json', shot)
        exact = ft.apply(self.scene.p, 's', target='diner')['move']
        self.assertFalse(any('intent' in fr for fr in exact['frames']))
        del shot['camera']['move']['params']['solve']                               # none named: the default, intent
        write_json(self.scene.p / 'shots' / 's' / 'shot.json', shot)
        move = ft.apply(self.scene.p, 's', target='diner')['move']
        first, last = move['frames']
        self.assertNotIn('chosen', first)
        self.assertEqual((last['chosen']['view'], last['chosen']['by']), ([5.0, 30.0], 'user'))
        for fr in move['frames']:
            self.assertEqual(fr['intent'], ft.intent_of(fr))
            self.assertIn(fr['intent']['size']['class'], ('원경', '전체', '꽉 참', '클로즈업', '익스트림 클로즈업'))
        validate_shot(self.scene.shot())                                          # the schema carries both

    def test_apply_keeps_how_the_shot_solves(self):
        ft.apply(self.scene.p, 's', target='diner')
        shot = self.scene.shot()
        for solve in ('intent', 'exact'):
            shot['camera']['move']['params']['solve'] = solve
            write_json(self.scene.p / 'shots' / 's' / 'shot.json', shot)
            self.assertEqual(ft.apply(self.scene.p, 's', target='diner')['move']['params']['solve'], solve)


if __name__ == '__main__':
    unittest.main()
