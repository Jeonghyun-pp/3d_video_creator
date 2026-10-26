"""The decision chain a shot list carries (studio/show_how.py): the reel's point and the one scene that proves it (V1), each
cut's show_how - what the viewer learns, the rows on screen that prove it, the showing way, the beat's picture kept or left
with why (V2) - and checks that the fields agree with each other. Why (2026-10-26, the user): the agent must decide per
scene what the viewer must learn, what on screen proves it, where and at what scale, and which motion delivers it, each
step following from the one before."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from studio import decisions, reel_flow, show_how
from studio.common import CODE, read_json


def cut(sid, **extra):
    return {'shot_id': sid, 'purpose': 'a test cut, what it is for', 'line_ids': [], 'duration_s': 3.0, **extra}


CLIMAX = cut('s11', show=[{'at': 0.35, 'parts': ['dust_speck'], 'how': 'exterior', 'key': True, 'why': 'the speck lands'}],
             direction={'change': [{'id': 'speck_lands', 'parts': ['dust_speck'], 'kind': 'move', 'from': 0, 'to': 0.02,
                                    'at': [0.1, 0.35], 'why': 'it lands'}]})
POINT = {'says': 'one speck of dust kills a whole chip', 'proof': {'what': 'a speck lands on a chip and the chip goes dark',
                                                                    'rows': ['s11/speck_lands']},
         'opening': {'does': 'asks why the hand may not touch the box', 'why': 'the question the proof answers'}}


class PointTest(unittest.TestCase):
    def codes(self, body, silenced=()):
        errors, warnings = show_how.point_lines(body, silenced)
        return [e.split(':')[0] for e in errors], [w.split(':')[0] for w in warnings]

    def test_a_shot_list_says_its_point_and_the_scene_that_proves_it(self):
        self.assertIn('POINT_UNDECIDED', self.codes({'shots': [CLIMAX]})[0])
        self.assertEqual(self.codes({'point': POINT, 'shots': [CLIMAX]}), ([], []))

    def test_the_proof_is_a_row_the_shot_list_has(self):
        # s11 of the fab: the climax says a chip dies, and no row of it makes the chip go dark
        dark = {**POINT, 'proof': {**POINT['proof'], 'rows': ['s11/chip_goes_dark']}}
        self.assertIn('POINT_UNSHOWN', self.codes({'point': dark, 'shots': [CLIMAX]})[1])
        elsewhere = {**POINT, 'proof': {**POINT['proof'], 'rows': ['s12/speck_lands']}}
        self.assertIn('POINT_UNSHOWN', self.codes({'point': elsewhere, 'shots': [CLIMAX]})[1])
        # a show row is named by its moment
        held = {**POINT, 'proof': {**POINT['proof'], 'rows': ['s11/show@0.35']}}
        self.assertEqual(self.codes({'point': held, 'shots': [CLIMAX]}), ([], []))
        self.assertIn('POINT_UNSHOWN', self.codes({'point': {**held, 'proof': {**held['proof'], 'rows': ['s11/show@end']}},
                                                   'shots': [CLIMAX]})[1])

    def test_an_unshown_point_is_answered_like_a_flow_finding(self):
        dark = {**POINT, 'proof': {**POINT['proof'], 'rows': ['s11/chip_goes_dark']}}
        self.assertIn('POINT_UNSHOWN', reel_flow.DEPARTS)
        found = show_how.point_lines({'point': dark, 'shots': [CLIMAX]})[1]
        self.assertTrue(reel_flow.unresolved(found))
        self.assertEqual(self.codes({'point': dark, 'shots': [CLIMAX]}, silenced={'POINT_UNSHOWN'}), ([], []))   # the reel departs

    def test_the_schema_holds_the_point(self):
        body = {'point': POINT, 'shots': [CLIMAX]}
        decisions.validate_body('shotlist', body)
        with self.assertRaises(Exception):
            decisions.validate_body('shotlist', {'point': {**POINT, 'proof': {'what': 'w', 'rows': ['no-slash']}}, 'shots': [CLIMAX]})



SPECK = {'point': 'one speck on a chip kills the whole chip', 'proof': ['speck_lands'],
         'way': {'id': 'hold_change', 'rejected': {'id': 'dive_scale', 'why': 'the size was shown in the cut before'}}, 'picture': 'beat'}
MOVE = {'recipe': 'watch', 'reveals': 'the speck landing on its chip', 'key_at': 0.35, 'why': 'the camera stays so the landing is all that moves'}


def told(sid='s11', show_how=SPECK, move=MOVE, **extra):
    """A cut of the climax beat with its decision chain (the speck lands, held)."""
    direction = {**CLIMAX['direction'], **({'show_how': show_how} if show_how else {}), 'move': move}
    return {**CLIMAX, 'shot_id': sid, 'story_beat_ids': ['b6'], 'direction': direction, **extra}


STORY = {'beats': [{'story_beat_id': 'b6', 'picture_idea': 'a speck lands on the grid and one chip goes dark'},
                   {'story_beat_id': 'b7'}]}


class ShowHowTest(unittest.TestCase):
    def codes(self, shots, story=STORY, journey=None, **kw):
        body = {'shots': shots, **({'journey': journey} if journey else {})}
        errors, warnings = show_how.cut_lines(body, story=story, **kw)
        return [e.split(':')[0] for e in errors], [w.split(':')[0] for w in warnings]

    def test_every_cut_says_how_it_shows_what_its_viewer_learns(self):
        self.assertIn('SHOW_HOW_UNDECIDED', self.codes([told(show_how=None)])[0])
        self.assertEqual(self.codes([told()]), ([], []))

    def test_its_proof_is_its_own_rows(self):
        self.assertIn('SHOW_HOW_UNDECIDED', self.codes([told(show_how={**SPECK, 'proof': ['chip_goes_dark']})])[0])
        self.assertEqual(self.codes([told(show_how={**SPECK, 'proof': ['show@0.35']})]), ([], []))

    def test_the_way_is_a_row_of_the_table_and_one_not_picked_is_named(self):
        self.assertIn('WAY_UNKNOWN', self.codes([told(show_how={**SPECK, 'way': {**SPECK['way'], 'id': 'fly_through'}})])[0])
        self.assertIn('WAY_UNKNOWN', self.codes([told(show_how={**SPECK, 'way': {'id': 'hold_change', 'rejected': {
            'id': 'hold_change', 'why': 'the same way named twice'}}})])[0])

    def test_the_motion_is_one_its_way_delivers(self):
        flown = {**MOVE, 'recipe': 'fly_in'}   # a fly-in is a means of follow or dive_scale, never chosen alone for its look
        self.assertIn('MOVE_NOT_OF_WAY', self.codes([told(move=flown)])[1])
        self.assertIn('MOVE_NOT_OF_WAY', reel_flow.DEPARTS)
        departed = told(move=flown, pattern={'departs': [{'item': 'move_not_of_way', 'why': 'the landing is seen as the camera arrives'}]})
        self.assertNotIn('MOVE_NOT_OF_WAY', self.codes([departed])[1])

    def test_a_new_way_is_one_row(self):
        table = read_json(CODE / 'library/showing_ways.json')
        table['ways'].append({'id': 'shadow_play', 'shows': 'a shape by its shadow', 'recipes': ['watch'], 'needs': 'a light',
                              'example': 'a hand casts the shape of the part on the wall'})
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'ways.json').write_text(json.dumps(table))
            with mock.patch.object(show_how, 'TABLE', Path(tmp, 'ways.json')):
                self.assertEqual(self.codes([told(show_how={**SPECK, 'way': {**SPECK['way'], 'id': 'shadow_play'}})]), ([], []))

    def test_every_way_moves_by_recipes_the_library_has(self):
        from studio.recipe_moves import book
        for way in show_how.ways():
            self.assertTrue(way['recipes'] and set(way['recipes']) <= set(book()), way['id'])
            self.assertTrue(way['shows'] and way['needs'] and way['example'], way['id'])

    def test_a_beats_picture_is_kept_or_left_with_why(self):
        # s01 of the fab: the beat's picture was a box snatched up past a reaching hand; the cut flew in instead and said nothing
        silent = told(show_how={k: v for k, v in SPECK.items() if k != 'picture'})
        self.assertIn('PICTURE_DROPPED', self.codes([silent])[1])
        self.assertIn('PICTURE_DROPPED', reel_flow.DEPARTS)
        left = told(show_how={**SPECK, 'picture': {'departs': 'the chip is shown from inside its layers, not from above the grid'}})
        self.assertNotIn('PICTURE_DROPPED', self.codes([left])[1])
        self.assertNotIn('PICTURE_DROPPED', self.codes([silent, told('s12')])[1])   # another cut of the beat keeps it
        lines = {'l6': {'line_id': 'l6', 'story_beat_id': 'b6'}}   # the beat a cut tells through its lines
        by_line = {**silent, 'line_ids': ['l6']}
        del by_line['story_beat_ids']
        self.assertIn('PICTURE_DROPPED', self.codes([by_line], lines=lines)[1])

    def test_the_key_moment_is_where_the_key_row_is(self):
        late = told(move={**MOVE, 'key_at': 0.8})
        self.assertIn('INTENT_MISMATCH', self.codes([late])[1])
        self.assertIn('INTENT_MISMATCH', reel_flow.DEPARTS)
        self.assertEqual(self.codes([told(move={**MOVE, 'key_at': 0.3})]), ([], []))   # the same frame of a watcher's strip
        # inside the window of a change on the key part: the moment runs
        self.assertEqual(self.codes([told(move={**MOVE, 'key_at': 0.2})]), ([], []))
        departed = {**late, 'pattern': {'departs': [{'item': 'intent_mismatch', 'why': 'the key is the settle after the landing'}]}}
        self.assertNotIn('INTENT_MISMATCH', self.codes([departed])[1])

    def test_what_the_motion_reveals_is_what_the_cut_shows(self):
        sky = told(move={**MOVE, 'reveals': 'the vast blue sky above the building'})
        self.assertIn('INTENT_MISMATCH', self.codes([sky])[1])
        named = told(move={**MOVE, 'reveals': 'the grain coming down onto its square'})
        self.assertIn('INTENT_MISMATCH', self.codes([named])[1])
        words = {'dust_speck': 'a grain of dust from a sleeve'}   # what the part is, in its own words
        self.assertNotIn('INTENT_MISMATCH', self.codes([named], words_of=lambda i: words.get(i, ''))[1])

    def test_a_travel_cut_arrives_where_the_cut_looks_or_its_lines_speak(self):
        places = [{'place': 'fab_building', 'scale': 'building', 'why': 'w'}, {'place': 'foup', 'scale': 'object', 'why': 'w'}]
        part = {'from': 0, 'to': 1, 'role': 'travel', 'why': 'w'}
        away = told(frame={'focus': ['dust_speck'], 'size': 'close'})
        away['direction'] = {**away['direction'], 'journey': part}
        self.assertIn('INTENT_MISMATCH', self.codes([away], journey=places)[1])
        landed = {**away, 'frame': {'focus': ['foup'], 'size': 'close'}}
        self.assertNotIn('INTENT_MISMATCH', self.codes([landed], journey=places)[1])
        self.assertNotIn('INTENT_MISMATCH', self.codes([away], journey=places, about=lambda s: {'foup'})[1])

    def test_explains_is_folded_into_the_point(self):
        with self.assertRaises(Exception):
            decisions.validate_body('shotlist', {'shots': [{**told(), 'explains': 'the chip dies'}]})
        decisions.validate_body('shotlist', {'shots': [told()]})


    def test_a_watcher_is_told_what_the_cut_is_for(self):
        """2026-10-26: the take judge read shot.purpose, which the build stores as goal - it was told only the cut's id."""
        shot = {'shot_id': 's11', 'goal': 'the climax: a speck kills a chip', 'direction': {'show_how': SPECK}}
        words = show_how.purpose_words(shot)
        self.assertIn('the climax: a speck kills a chip', words)
        self.assertIn(SPECK['point'], words)
        self.assertEqual(show_how.purpose_words({'shot_id': 's1'}), 's1')


if __name__ == '__main__':
    unittest.main()
