"""Four rules an intent camera keeps of its picture beyond size, side of level and lens (camera_moves_core.picture_terms,
intent_score, framed_pose): where the picture's other things sit round the subject, the subject staying in frame with the
part it must show, a lens no wider than the frame needs and no edge cut the picture does not cut, a long thing seen so
its length reads. A barge by a pier with a buoy, a café counter, a gripper arm - not the reel the rules came from (N+1)."""
import math
from pathlib import Path
import sys
import unittest

OPS = Path(__file__).resolve().parents[1] / 'studio/blender_ops'
sys.path.insert(0, str(OPS))
import camera_frame_core as framing  # noqa: E402
import camera_moves_core as core  # noqa: E402

ASPECT = 9 / 16
BARGE = ((-20.0, -2.0, 0.0), (20.0, 2.0, 2.0))   # 40 m long, 4 m wide, 2 m high, lying along x


def picture(subject=(0.3, 0.4, 0.7, 0.6), cut=(), context=()):
    return {'subject': {'box': list(subject), 'cut_edges': list(cut)}, 'context': [{'name': n, 'box': list(b)} for n, b in context]}


def shown(subject=(0.3, 0.4, 0.7, 0.6), cut=(), context=None, sight=None, long=False):
    return {'subject_box': list(subject) if subject else None, 'cut_sides': list(cut), 'context': dict(context or {}),
            'sight_deg': sight, 'long': long}


class ScreenBoxTest(unittest.TestCase):
    def test_a_box_straight_ahead_projects_round_the_centre(self):
        eye = (0.0, -50.0, 1.0)
        box = framing.screen_box(((-1.0, -1.0, 0.0), (1.0, 1.0, 2.0)), eye, (0.0, 0.0), 35.0, ASPECT)
        self.assertAlmostEqual((box[0] + box[2]) / 2, 0.5, places=3)
        self.assertAlmostEqual((box[1] + box[3]) / 2, 0.5, places=2)
        self.assertLess(box[2] - box[0], 0.2)

    def test_a_box_behind_the_camera_has_none(self):
        self.assertIsNone(framing.screen_box(((-1, -100, 0), (1, -90, 2)), (0.0, -50.0, 1.0), (0.0, 0.0), 35.0, ASPECT))


class FramedPoseWidthTest(unittest.TestCase):
    def test_a_long_low_thing_seen_side_on_fits_the_frame_width_it_was_drawn(self):
        # a barge drawn whole, 0.8 of the frame wide and 0.1 tall: placed by its height alone it runs off both sides
        fr = {'height_share': 0.1, 'width_share': 0.8, 'centre': [0.5, 0.5], 'cut_edges': []}
        eye, _aim = core.framed_pose(BARGE, fr, (5.0, 0.0), 24.0, ASPECT)
        box = framing.screen_box(BARGE, eye, (5.0, 0.0), 24.0, ASPECT)
        self.assertLessEqual(box[2] - box[0], 0.9)   # the nearer corners project a little wider: perspective
        self.assertGreater(box[0], 0.0)
        self.assertLess(box[2], 1.0)

    def test_a_picture_cut_at_its_sides_keeps_the_height_fit(self):
        cut = {'height_share': 0.5, 'width_share': 3.0, 'centre': [0.5, 0.5], 'cut_edges': ['left', 'right']}
        by_height = {'height_share': 0.5, 'centre': [0.5, 0.5]}
        self.assertEqual(core.framed_pose(BARGE, cut, (5.0, 0.0), 24.0, ASPECT), core.framed_pose(BARGE, by_height, (5.0, 0.0), 24.0, ASPECT))


class PictureTermsTest(unittest.TestCase):
    PIER = ('pier', (0.35, 0.75, 0.65, 0.85))   # drawn below the barge

    def test_a_thing_drawn_below_and_shown_below_costs_nothing(self):
        terms = core.picture_terms(picture(context=[self.PIER]), shown(context={'pier': [0.35, 0.72, 0.65, 0.82]}), ASPECT)
        self.assertEqual((terms['cost'].get('layout_flip', 0), terms['cost'].get('layout_lost', 0)), (0, 0))
        self.assertEqual(terms['said'], [])

    def test_a_thing_drawn_below_shown_above_is_a_flip(self):
        terms = core.picture_terms(picture(context=[self.PIER]), shown(context={'pier': [0.35, 0.1, 0.65, 0.2]}), ASPECT)
        self.assertEqual(terms['cost']['layout_flip'], 1)
        self.assertIn('pier', terms['said'][0])

    def test_a_thing_flipped_both_ways_says_each_relation_on_its_own(self):
        lamp = ('lamp 2', (0.05, 0.75, 0.2, 0.85))   # drawn below and left of the barge
        terms = core.picture_terms(picture(context=[lamp]), shown(context={'lamp 2': [0.8, 0.1, 0.95, 0.2]}), ASPECT)
        self.assertEqual(len(terms['said']), 2, terms['said'])
        self.assertEqual(sorted(d['key'] for d in terms['lost']), [('lamp 2', 'x'), ('lamp 2', 'y')])
        self.assertTrue(all(d['kind'] == 'layout_flip' for d in terms['lost']))

    def test_a_thing_drawn_in_the_frame_and_shown_out_of_it_is_lost(self):
        for gone in (None, [1.3, 0.7, 1.6, 0.9]):
            terms = core.picture_terms(picture(context=[self.PIER]), shown(context={'pier': gone}), ASPECT)
            self.assertEqual(terms['cost']['layout_lost'], 1, gone)

    def test_a_thing_the_scene_cannot_name_is_left_out(self):
        terms = core.picture_terms(picture(context=[self.PIER]), shown(context={}), ASPECT)
        self.assertEqual(terms['cost'].get('layout_lost', 0), 0)

    def test_the_subject_left_with_little_of_the_frame_falls_short_of_its_owner(self):
        short = core.picture_terms(picture(subject=(0.2, 0.2, 0.8, 0.8)), shown(subject=(0.45, 0.45, 0.55, 0.55)), ASPECT)
        kept = core.picture_terms(picture(subject=(0.2, 0.2, 0.8, 0.8)), shown(subject=(0.25, 0.3, 0.75, 0.7)), ASPECT)
        self.assertGreater(short['cost']['owner_short'], 0)
        self.assertTrue(any('subject' in s for s in short['said']))
        self.assertEqual(kept['cost'].get('owner_short', 0), 0)

    def test_an_edge_the_picture_does_not_cut_costs_per_side(self):
        terms = core.picture_terms(picture(cut=('top',)), shown(cut=('top', 'left', 'right')), ASPECT)
        self.assertEqual(terms['cost']['cut_edge'], 2)
        self.assertEqual(core.picture_terms(picture(cut=('top',)), shown(cut=('top',)), ASPECT)['cost'].get('cut_edge', 0), 0)

    def test_a_long_thing_seen_end_on_when_drawn_long_is_said(self):
        drawn_long = picture(subject=(0.1, 0.45, 0.9, 0.55))
        end_on = core.picture_terms(drawn_long, shown(subject=(0.45, 0.4, 0.55, 0.6), sight=10.0, long=True), ASPECT)
        side_on = core.picture_terms(drawn_long, shown(subject=(0.1, 0.45, 0.9, 0.55), sight=80.0, long=True), ASPECT)
        self.assertEqual(end_on['cost']['end_on'], 1)
        self.assertGreater(end_on['cost']['aspect'], side_on['cost'].get('aspect', 0))
        self.assertEqual(side_on['cost'].get('end_on', 0), 0)
        drawn_end = picture(subject=(0.4, 0.4, 0.6, 0.55))   # drawn end-on itself: not a departure
        self.assertEqual(core.picture_terms(drawn_end, shown(subject=(0.4, 0.4, 0.6, 0.55), sight=10.0, long=True), ASPECT)['cost'].get('end_on', 0), 0)

    def test_a_picture_drawn_steeply_up_a_tall_thing_is_not_end_on_when_the_pose_is_as_steep(self):
        tall = picture(subject=(0.4, 0.05, 0.6, 0.95))   # drawn long: a tower from below
        as_steep = shown(subject=(0.4, 0.05, 0.6, 0.95), sight=30.0, long=True)
        steeper = shown(subject=(0.45, 0.4, 0.55, 0.6), sight=12.0, long=True)
        self.assertEqual(core.picture_terms(tall, {**as_steep, 'picture_sight_deg': 32.0}, ASPECT)['cost'].get('end_on', 0), 0)
        self.assertEqual(core.picture_terms(tall, {**steeper, 'picture_sight_deg': 32.0}, ASPECT)['cost'].get('end_on', 0), 1)

    def test_a_whole_thing_drawn_broadside_and_shown_narrow_costs_its_shape(self):
        broadside = picture(subject=(0.05, 0.4, 0.95, 0.6))   # a ship drawn wide (its true width-to-height 2.5)
        narrow = core.picture_terms(broadside, shown(subject=(0.4, 0.4, 0.6, 0.6)), ASPECT)
        wide = core.picture_terms(broadside, shown(subject=(0.06, 0.42, 0.94, 0.6)), ASPECT)
        self.assertGreater(narrow['cost']['aspect'], 1.0)
        self.assertLess(wide['cost'].get('aspect', 0), 0.2)
        cut = core.picture_terms(picture(subject=(0.05, 0.4, 0.95, 0.6), cut=('left',)), shown(subject=(0.4, 0.4, 0.6, 0.6), cut=('left',)), ASPECT)
        self.assertEqual(cut['cost'].get('aspect', 0), 0)   # a cut box's shape is the frame's

    def test_without_a_picture_layout_nothing_is_added(self):
        self.assertEqual(core.picture_terms(None, shown(), ASPECT), {'cost': {}, 'said': [], 'lost': []})


class ScoreTest(unittest.TestCase):
    def test_a_thing_kept_on_its_side_beats_a_view_a_step_nearer_the_picture(self):
        near_flipped = core.intent_score({'azimuth_deg': 15.0, 'elevation_deg': 0.0}, False, 1.0, terms={'layout_flip': 1})
        far_kept = core.intent_score({'azimuth_deg': 30.0, 'elevation_deg': 0.0}, False, 1.0, terms={})
        self.assertLess(far_kept, near_flipped)

    def test_the_less_wide_lens_wins_when_nothing_else_differs(self):
        score = lambda mm: core.intent_score({'azimuth_deg': 0.0, 'elevation_deg': 0.0}, True, 1.0,  # noqa: E731
                                             terms={'lens_stop': core.lens_stops(24.0, mm)})
        self.assertLess(score(18.0), score(14.0))
        self.assertEqual(core.lens_stops(24.0, 24.0), 0.0)

    def test_without_terms_the_score_is_as_before(self):
        self.assertEqual(core.intent_score({'azimuth_deg': 30.0, 'elevation_deg': 0.0}, True, 1.5, 1),
                         round(3.0 + 1.0 + 0.25 - 0.25, 4))


class PictureLayoutTest(unittest.TestCase):
    """frame_targets.picture_layout on a café whose pictures were read with the counter beside the diners."""

    def scene(self):
        from tests.test_frame_reading_checks import BASE, cafe
        scene = cafe(self)
        read = {**BASE, 'context': [{'name': 'the long espresso counter', 'box': [0.6, 0.6, 0.9, 0.8]},
                                    {'name': 'a small corner cafe with big windows', 'box': [0.0, 0.0, 1.0, 1.0]}]}
        scene.read([read, read])
        return scene

    def intent(self, shot, solve='intent'):
        return {**shot, 'camera': {**shot['camera'], 'move': {'type': 'frame_to_frame', 'params': {'solve': solve}, 'frames': []}}}

    def test_named_things_carry_their_ids_and_the_rest_is_listed_unnamed(self):
        from studio.frame_targets import picture_layout
        scene = self.scene()
        layout = picture_layout(scene.p, self.intent(scene.shot()))
        self.assertEqual(sorted(layout), ['c01', 'c02'])
        self.assertEqual(layout['c01']['context'], [{'name': 'the long espresso counter', 'ids': ['counter'], 'box': [0.6, 0.6, 0.9, 0.8]}])
        self.assertEqual(layout['c01']['unnamed'], ['a small corner cafe with big windows'])
        self.assertEqual(layout['c01']['subject'], {'box': [0.4, 0.3, 0.6, 0.7], 'cut_edges': []})

    def test_a_picture_changed_since_its_reading_is_left_out(self):
        from studio import frame_targets
        from studio.common import write_json
        from studio.frame_targets import picture_layout
        scene = self.scene()
        store = frame_targets._store(scene.p, 's')
        store['pictures']['c02']['sha256'] = 'read-off-an-older-picture'
        write_json(frame_targets._store_path(scene.p, 's'), store)
        self.assertEqual(sorted(picture_layout(scene.p, self.intent(scene.shot()))), ['c01'])

    def test_unreadable_readings_skip_the_layout_with_a_note(self):
        from unittest import mock
        from studio import frame_targets
        from studio.common import StudioError
        scene = self.scene()
        notes = []
        with mock.patch.object(frame_targets, 'targets', side_effect=StudioError('INPUT_INVALID', 'store broken')):
            self.assertEqual(frame_targets.picture_layout(scene.p, self.intent(scene.shot()), notes), {})
        self.assertIn('store broken', notes[0])

    def test_only_an_intent_move_builds_it(self):
        from studio.blender import _picture_layout
        scene = self.scene()
        self.assertEqual(_picture_layout(scene.p, self.intent(scene.shot(), solve=None)), {})
        self.assertEqual(_picture_layout(scene.p, self.intent(scene.shot(), solve='exact')), {})
        self.assertIn('picture_layout', _picture_layout(scene.p, self.intent(scene.shot())))
        self.assertIn('picture_layout', _picture_layout(scene.p, self.intent(scene.shot(), solve=None), 'intent'))   # frames check --solve intent
        self.assertEqual(_picture_layout(scene.p, self.intent(scene.shot()), 'exact'), {})


class FramedPoseCutTest(unittest.TestCase):
    def test_a_picture_cut_only_at_the_top_keeps_its_width(self):
        top = {'height_share': 0.1, 'width_share': 0.8, 'centre': [0.5, 0.5], 'cut_edges': ['top']}
        eye, _aim = core.framed_pose(BARGE, top, (5.0, 0.0), 24.0, ASPECT)
        box = framing.screen_box(BARGE, eye, (5.0, 0.0), 24.0, ASPECT)
        self.assertLess(box[2] - box[0], 0.9)

    def test_an_older_row_reads_its_cuts_from_the_sides_its_edges_leave(self):
        older = {'height_share': 0.5, 'width_share': 3.0, 'centre': [0.5, 0.5], 'edges': {'top': 0.1, 'bottom': 0.9}}
        by_height = {'height_share': 0.5, 'centre': [0.5, 0.5]}
        self.assertEqual(core.framed_pose(BARGE, older, (5.0, 0.0), 24.0, ASPECT), core.framed_pose(BARGE, by_height, (5.0, 0.0), 24.0, ASPECT))


class SizeEdgeTest(unittest.TestCase):
    """A subject measured a hair across a size-class edge is the same composition (a 0.46 picture shown at 0.42 across
    the 0.45 edge); a clear change of class is still said. A whole picture refines into its band, not onto its edge."""

    def intent(self, h):
        return core.intent_of({'height_share': h, 'view': {'elevation_deg': 10.0, 'azimuth_deg': 0.0}, 'lens_mm': 35.0, 'cut_edges': []})

    def test_a_hair_across_the_edge_is_not_a_departure(self):
        near = core.intent_departures(self.intent(0.46), {'azimuth_deg': 0.0}, 35.0, {'height_share': 0.42, 'whole_in_frame': True})
        self.assertEqual(near, [])
        far = core.intent_departures(self.intent(0.46), {'azimuth_deg': 0.0}, 35.0, {'height_share': 0.30, 'whole_in_frame': True})
        self.assertEqual(len(far), 1)

    def test_size_wording_is_the_same_for_every_class(self):
        names = [name for _top, name in core.SIZE_CLASSES]
        # whole pictures (a cut one is judged by whether it is shown whole, not by class) against every other class
        for picture_h, shown_h in ((0.10, 0.6), (0.6, 0.10), (0.3, 1.5), (0.7, 3.0)):
            lost = core.intent_losses(self.intent(picture_h), {'azimuth_deg': 0.0}, 35.0, {'height_share': shown_h, 'whole_in_frame': True})
            size = [d for d in lost if d['kind'] == 'size']
            self.assertEqual(len(size), 1, (picture_h, shown_h))
            text = size[0]['text']
            self.assertIn(core.size_class(shown_h)[0], text)
            self.assertIn(f"for the picture's {core.size_class(picture_h)[0]} ", text)
            self.assertEqual(size[0]['value'], abs(names.index(core.size_class(shown_h)[0]) - names.index(core.size_class(picture_h)[0])))

    def test_the_top_bottom_and_a_narrow_band(self):
        top = core.size_class(3.0)[1]
        self.assertTrue(core.size_kept(10.0, top))                 # the top class has no upper edge
        self.assertEqual(core.size_target(10.0, top), 10.0)        # nor is it pulled down
        bottom = core.size_class(0.05)[1]
        self.assertTrue(core.size_kept(0.0, bottom))
        self.assertAlmostEqual(core.size_target(0.9, [0.88, 0.95]), (0.88 + 0.95) / 2)   # too narrow for both margins

    def test_the_refine_aims_inside_the_band(self):
        low, high = core.size_class(0.46)[1]
        target = core.size_target(0.456, [low, high])
        self.assertTrue(low < target < high and target > 0.456, target)
        self.assertAlmostEqual(core.size_target(0.6, [low, high]), 0.6)

if __name__ == '__main__':
    unittest.main()
