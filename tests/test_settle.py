"""The camera arrives, then the key change happens; a cut does not hold on nothing at its end; a long cut whose content
keeps moving is not cut short (2026-10-25, plan M4). The director says when the camera is still (direction.move.settle_at);
a key change planned before it contradicts the plan (KEY_BEFORE_SETTLE, an error); a built key change with the camera
still moving and a still, silent tail past the study set's are the reader's (KEY_CHANGE_MOVING, DEAD_TAIL - a reason
read with the picture; a rest the director planned says so in direction.rest)."""
import unittest
from unittest import mock

from studio import camera_comfort, change_moves, excuses, reel_flow
from studio.decisions import settle_lines

MOVE = {'recipe': 'fly_in', 'reveals': 'the box', 'key_at': 0.8, 'settle_at': 0.6, 'why': 'in, then the lid lifts',
        'settings': {'height_share': 0.5, 'azimuth_deg': 30}}


def shot(key_at=0.8, change_at=(0.75, 0.95), settle_at=0.6, **extra):
    move = {**MOVE, 'settle_at': settle_at} if settle_at is not None else {k: v for k, v in MOVE.items() if k != 'settle_at'}
    return {'shot_id': 's01', 'duration_frames': 120, 'duration_s': 4.0,
            'show': [{'at': 'start', 'parts': ['foup'], 'how': 'exterior', 'why': 'the box'},
                     {'at': key_at, 'parts': ['foup/lid'], 'how': 'exterior', 'why': 'the lid lifts', 'key': True}],
            'direction': {'move': move, **({'change': [{'id': 'lift', 'parts': ['foup/lid'], 'kind': 'move', 'from': 0, 'to': 0.1,
                                                         'at': list(change_at), 'why': 'the lid lifts'}]} if change_at else {}), **extra}}


class SettleLintTest(unittest.TestCase):
    def test_a_key_change_planned_before_the_camera_is_still_is_refused(self):
        self.assertEqual(settle_lines({'shots': [shot()]}), [])
        early = settle_lines({'shots': [shot(key_at=0.5, change_at=None)]})
        self.assertTrue(early and early[0].startswith('KEY_BEFORE_SETTLE: s01'))
        moving = settle_lines({'shots': [shot(change_at=(0.55, 0.9))]})   # its change starts while the camera still moves
        self.assertTrue(moving and moving[0].startswith('KEY_BEFORE_SETTLE'))
        self.assertEqual(settle_lines({'shots': [shot(key_at=0.3, settle_at=None)]}), [])   # nothing said, nothing contradicted


def follow(moving, arrive):
    return {'jerk': 0.5, 'jerk_at_s': 0.1, 'arrive_s': arrive, 'moving_s': moving, 'window_s': 0.25, 'rest': 0.02}


class SettleMeasureTest(unittest.TestCase):
    def test_a_key_change_with_the_camera_moving_is_said(self):
        self.assertEqual(change_moves.key_moving_lines(shot(), follow([[0.0, 2.4]], 2.4), 4.0), [])
        lines = change_moves.key_moving_lines(shot(), follow([[0.0, 3.4]], 3.4), 4.0)   # still moving through 3.0-3.8 s
        self.assertTrue(lines and lines[0].startswith('KEY_CHANGE_MOVING: s01'))
        bare = shot(change_at=None)   # a key moment without a change: the moment itself, a window either side
        self.assertTrue(change_moves.key_moving_lines(bare, follow([[3.0, 3.4]], 3.4), 4.0))

    def test_a_still_silent_tail_past_the_study_set_is_said(self):
        limit = camera_comfort.goal('settled')['limits']['tail_s']
        quiet = shot(change_at=(0.1, 0.2), key_at=0.2, settle_at=None)
        lines = change_moves.tail_lines(quiet, follow([[0.0, 0.5]], 0.5), 4.0, spoken_s=1.0)
        self.assertTrue(lines and lines[0].startswith('DEAD_TAIL: s01'))
        self.assertIn(f'{limit:g}', lines[0])
        self.assertEqual(change_moves.tail_lines(quiet, follow([[0.0, 3.0]], 3.0), 4.0, spoken_s=1.0), [])   # the camera moves on
        self.assertEqual(change_moves.tail_lines(quiet, follow([[0.0, 0.5]], 0.5), 4.0, spoken_s=3.5), [])   # the words run on


class ReasonTableTest(unittest.TestCase):
    def test_every_reason_floor_is_a_goal_row_and_a_new_one_needs_no_code(self):
        from studio import storyboard
        from studio.common import StudioError
        codes = {r['code'] for g in camera_comfort.goals() for r in g.get('reasons') or []}
        self.assertLessEqual({'CAMERA_TOO_FAST', 'SHOT_NOTHING_SHOWS', 'CHANGE_TOO_SMALL', 'CHANGE_MASKED', 'KEY_CHANGE_MOVING', 'DEAD_TAIL'}, codes)
        self.assertLessEqual(codes, set(storyboard.because_codes()))
        rows = camera_comfort.goals() + [{'id': 'novel', 'reasons': [{'code': 'NOVEL_FINDING', 'reads': 'novel'}]}]
        take = {'id': 'B', 'novel': ['NOVEL_FINDING: something to answer']}
        with mock.patch.object(camera_comfort, 'goals', return_value=rows):
            with self.assertRaises(StudioError) as caught:
                storyboard._agent_floors('s', {}, take, {})
            self.assertEqual(caught.exception.code, 'NOVEL_FINDING')
            storyboard._agent_floors('s', {}, take, {'NOVEL_FINDING': 'what the cut gains as it is'})

    def test_a_planned_rest_answers_a_dead_tail_where_it_is_written(self):
        from studio import storyboard
        take = {'id': 'B', 'dead_tail': ['DEAD_TAIL: s01 holds 2.5 s still and silent']}
        storyboard._agent_floors('s', {}, take, {}, shot={'direction': {'rest': 'the last line lands over the held box'}})
        row = next(r for r in excuses.rows() if r['id'] == 'rest')
        self.assertEqual((row['source'], row['path']), ('shot', 'direction.rest'))


class LongCutTest(unittest.TestCase):
    def test_a_long_cut_whose_quiet_a_change_covers_is_not_cut_short(self):
        busy = {'shot_id': 'b', 'len_s': 6.0, 'role': 'explain', 'spoken_s': 2.0, 'change_spans': [[1.5, 5.8]]}
        held = {'shot_id': 'h', 'len_s': 6.0, 'role': 'explain', 'spoken_s': 2.0, 'change_spans': []}
        lines = reel_flow.lines([busy])
        self.assertFalse([l for l in lines if l.startswith(('SHOT_HOLDS_UNSPOKEN', 'PACE_SLOW'))])
        lines = reel_flow.lines([held])
        self.assertTrue([l for l in lines if l.startswith('SHOT_HOLDS_UNSPOKEN')])
        self.assertTrue([l for l in lines if l.startswith('PACE_SLOW')])


if __name__ == '__main__':
    unittest.main()
