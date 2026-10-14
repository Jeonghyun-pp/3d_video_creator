"""Frame pictures -> framing targets (studio/frame_targets.py, concept --frame): what a picture's reading becomes, how
a built moment is judged against it, and how cut-together shots on one composition are caught (patterns)."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image

from studio import concept, frame_targets as ft, patterns
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, load_shot, shot_path, validate_schema

READ = {'subject_box': [0.4, 0.0, 0.6, 0.3], 'whole_box': [0.4, -0.5, 0.6, 0.3], 'cut_edges': ['top'], 'horizon_v': 0.8,
        'view_height': 'up', 'view_deg': 20, 'side': 'side', 'lens': 'wide', 'fore': 'spray', 'subject_tilt_deg': None, 'shows': [], 'objects': [],
        'context': [{'name': 'the landing ship', 'box': [0.3, 0.6, 0.7, 0.66]}, {'name': 'the coast', 'box': None}], 'note': 'it drops in'}


class LayoutTest(unittest.TestCase):
    """2026-10-12: the booster sat as drawn while a second stage nobody drew ran off the top (s03); a picture drew
    deployed legs on the pad and side boosters the scene does not have. The whole frame is compared thing by thing."""
    def test_the_frame_is_compared_thing_by_thing(self):
        drawn = [{'name': 'booster', 'box': [0.36, 0.08, 0.52, 0.82], 'cut_edges': [], 'state': 'legs folded'},
                 {'name': 'launch_pad', 'box': [0.70, 0.15, 0.85, 0.80], 'cut_edges': [], 'state': None},
                 {'name': 'other: side boosters', 'box': [0.20, 0.30, 0.35, 0.90], 'cut_edges': [], 'state': None}]
        same = ft.compare_layout(drawn[:2], {'booster': [0.37, 0.09, 0.52, 0.81], 'launch_pad': [0.70, 0.14, 0.86, 0.80]}, 1.0)
        self.assertEqual((same['score'], same['problems']), (1.0, []))
        stacked = ft.compare_layout(drawn, {'booster': [0.37, 0.09, 0.52, 0.81], 'launch_pad': [0.36, 0.0, 0.86, 0.80]}, 1.0)
        said = ' '.join(stacked['problems'])
        self.assertIn('launch_pad', said)                 # the stack above the booster: elsewhere and cut at the top
        self.assertIn('side boosters', said)              # drawn, not in the scene
        self.assertLess(stacked['score'], ft.LAYOUT_MIN)
        extra = ft.compare_layout(drawn[:1], {'booster': [0.37, 0.09, 0.52, 0.81], 'upper_stage': [0.40, 0.0, 0.48, 0.3]})
        self.assertIn('the build shows upper_stage', ' '.join(extra['problems']))
        small = ft.compare_layout(drawn[:1], {'booster': [0.37, 0.09, 0.52, 0.81], 'upper_stage': [0.40, 0.0, 0.48, 0.05]})
        self.assertEqual(small['problems'], [])           # a sliver is not a different picture


class TargetTest(unittest.TestCase):
    def test_the_whole_subject_places_the_camera_and_the_horizon_gives_the_angle(self):
        t = ft.target_of(READ, None, (900, 1600), 15.0)
        self.assertEqual(t['height_share'], 0.8)
        self.assertEqual(t['centre'], [0.5, -0.1])                        # above the frame: it enters from the top
        self.assertEqual(t['check'], {'box': [0.4, 0.0, 0.6, 0.3], 'cut_edges': ['top']})
        self.assertEqual(t['edges'], {'left': 0.4, 'right': 0.6, 'bottom': 0.3})   # the solve places what shows, not the guess
        self.assertEqual(t['lens_mm'], 24.0)
        self.assertLess(t['view']['elevation_deg'], -10)                  # a low horizon: the camera looks up
        self.assertEqual((t['elevation_from'], t['view']['azimuth_deg']), ('horizon', 15.0))
        tan_v = ft.tangent_v(24.0, 900 / 1600)
        self.assertAlmostEqual(0.5 + ft.math.tan(ft.math.radians(-t['view']['elevation_deg'])) / (2 * tan_v), 0.8, places=4)

    def test_without_a_horizon_the_read_angle_is_used_and_a_mark_wins(self):
        no_horizon = {**READ, 'horizon_v': None}
        self.assertEqual(ft.target_of(no_horizon, None, (9, 16), 0)['view']['elevation_deg'], -20)
        self.assertEqual(ft.target_of({**no_horizon, 'view_height': 'down'}, None, (9, 16), 0)['view']['elevation_deg'], 20)
        marked = ft.target_of(READ, {'view_deg_signed': -40.0, 'lens_mm': 18.0, 'subject_box': [0.3, 0.2, 0.7, 0.9], 'whole_box': None}, (9, 16), 0)
        self.assertEqual((marked['view']['elevation_deg'], marked['lens_mm'], marked['elevation_from']), (-40.0, 18.0, 'marked'))
        self.assertEqual(marked['height_share'], 0.7)                      # the marked box, nothing cut
        self.assertNotIn('edges', marked)                                   # a marked whole box is placed whole
        self.assertIsNone(ft.target_of({**READ, 'subject_box': None}, None, (9, 16), 0))

    def test_a_built_moment_is_judged_on_place_size_edges_and_angle(self):
        t = {'at': 0.0, **ft.target_of(READ, None, (900, 1600), 0.0)}
        self.assertEqual(ft.compare(t, [0.41, 0.0, 0.61, 0.31], -25.0), [])
        problems = ft.compare(t, [0.3, 0.4, 0.7, 0.9], 2.0)
        self.assertEqual(len(problems), 3, problems)                        # centre, size, no cut edge (the angle: view_problems)
        self.assertIn('not on screen', ft.compare(t, None)[0])

    def test_the_view_is_judged_by_why_it_moved_not_by_how_far(self):
        """2026-10-13: the camera keeps the picture's view; it says when it moved and why, and a target set from an
        earlier reading is stale. A gap in degrees alone says nothing (the rocket's 10-20 deg turns hunted for an engine)."""
        picture = {'view': {'elevation_deg': -4.0, 'azimuth_deg': 0.0}}
        same = {'at': 1.0, 'view': {'elevation_deg': -4.0, 'azimuth_deg': 0.0}}
        self.assertEqual(ft.view_problems(same, picture, {'offset_from_picture': {'azimuth_deg': 0.0, 'elevation_deg': 0.0}}), [])
        stale = ft.view_problems({**same, 'view': {'elevation_deg': 5.5, 'azimuth_deg': 0.0}}, picture, None)
        self.assertIn('FRAME_TARGET_STALE', stale[0][1])
        moved = ft.view_problems(same, picture, {'offset_from_picture': {'azimuth_deg': 0.0, 'elevation_deg': 10.0},
                                                 'moved_because': 'must-see [engine] at 0 px'})
        self.assertIn('because must-see', moved[0][1])

    def test_must_see_parts_and_the_things_around_are_judged_too(self):
        t = {'at': 1.0, **ft.target_of(READ, None, (900, 1600), 0.0)}
        self.assertEqual(t['context'], {'the landing ship': [0.3, 0.6, 0.7, 0.66]})
        box = [0.41, 0.0, 0.61, 0.31]
        self.assertEqual(ft.compare(t, box, -25.0, {'arm': (14.0, 12)}, {'the landing ship': [0.2, 0.6, 0.8, 0.68]}), [])
        problems = ft.compare(t, box, -25.0, {'arm': (3.0, 12)}, {'the landing ship': [0.0, 0.5, 1.0, 1.0]})
        self.assertEqual(len(problems), 2, problems)
        self.assertIn('must-see arm shows 3 px', problems[0])
        self.assertIn('the landing ship 0.50 of the frame tall, the picture 0.06', problems[1])
        kept = {**t, 'departs': [{'item': 'context', 'why': 'the ship close on purpose'}, {'item': 'must_see', 'why': 'never'}]}
        self.assertEqual(len(ft.compare(kept, box, -25.0, {'arm': (3.0, 12)}, {'the landing ship': [0.0, 0.5, 1.0, 1.0]})), 1)   # must-see stays


class PicturesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.p = Path(init_project('frames', {'request': 'frames', 'shots': [{'shot_id': 's', 'frame_count': 60}]}, root)['project_path'])
        self.image = root / 'a.png'
        Image.new('RGB', (90, 160), (200, 120, 60)).save(self.image)
        shot = load_shot(self.p, 's')
        shot['screen'] = {'subject': ['booster']}
        write_json(shot_path(self.p, 's'), shot)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_new_reading_keeps_what_the_user_and_the_eye_recorded(self):
        """2026-10-12 audit: re-reading a picture (forced, or after the subject words changed) dropped its departs (with the
        user's words), its eye reviews and a reading written by hand. Kept for the same picture; a hand reading is not
        read over (the user chose so)."""
        concept.add(self.p, 's', self.image, 'first', frame='start')
        concept.add(self.p, 's', self.image, 'last', frame='end')
        def fake(images, prompt, schema, target):
            answer = {'pictures': [{**READ, 'index': i} for i in range(len(images))]}
            write_json(target, {'answer': answer})
            return {'answer': answer}
        with mock.patch('studio.screen_grammar.codex_json', fake):
            ft.measure(self.p, 's')
        ft.depart(self.p, 's', 'c01', 'size', 'the user wants it closer', user_words='더 가까이 괜찮아')
        ft.review(self.p, 's', 'c01', {'objects': True, 'extent': True, 'state': True, 'viewpoint': True}, 'compared the picture with the built moment: same', 'eye', version='v0001')
        by_hand = {**READ, 'context': [], 'subject_box': [0.2, 0.2, 0.6, 0.8]}
        ft.mark(self.p, 's', 'c02', reading=by_hand, subject='booster', note='read by eye')
        with mock.patch('studio.screen_grammar.codex_json', fake):
            again = ft.measure(self.p, 's', force=True)
        store = ft._store(self.p, 's')['pictures']
        self.assertEqual([d['item'] for d in store['c01'].get('departs') or []], ['size'])
        self.assertEqual(len(store['c01'].get('reviews') or []), 1)
        self.assertNotIn('c02', again['read'])
        self.assertEqual(store['c02']['read']['subject_box'], [0.2, 0.2, 0.6, 0.8])
        self.assertEqual(store['c02']['provenance']['kind'], 'manual')

    def test_frame_pictures_say_their_moment_and_the_newest_of_a_moment_counts(self):
        concept.add(self.p, 's', self.image, 'first', frame='start')
        concept.add(self.p, 's', self.image, 'last', frame='end')
        newer = concept.add(self.p, 's', self.image, 'last again', frame='end')
        concept.add(self.p, 's', self.image, 'a plain concept')
        self.assertEqual([(c['frame']['role'], c['concept_id']) for c in concept.frames(self.p, 's')], [('start', 'c01'), ('end', newer['concept_id'])])
        for bad in ({'frame': 'key'}, {'frame': 'key', 'at': 1.0}, {'frame': 'end', 'at': 0.5}, {'frame': 'middle'}, {'at': 0.5}):
            with self.assertRaises(StudioError):
                concept.add(self.p, 's', self.image, **bad)
        self.assertEqual(concept.add(self.p, 's', self.image, frame='key', at=0.4)['frame'], {'role': 'key', 'at': 0.4})

    def test_a_beat_picture_is_reused_with_its_source(self):
        concept.add_beat(self.p, 'b1', self.image, 'the sea at dusk')
        added = concept.add(self.p, 's', frame='start', from_beat='b1/p01')
        self.assertEqual((added['source'], added['prompt'], added['from_beat']['ref']), ('world_beat', 'the sea at dusk', 'b1/p01'))
        with self.assertRaises(StudioError):
            concept.add(self.p, 's', frame='start', from_beat='b9/p01')

    def test_measure_reads_new_pictures_once_mark_corrects_and_apply_writes_the_move(self):
        concept.add(self.p, 's', self.image, 'first', frame='start')
        concept.add(self.p, 's', self.image, 'last', frame='end')
        calls = []

        def fake(images, prompt, schema, target):
            calls.append((len(images), prompt))
            answer = {'pictures': [{**READ, 'index': i} for i in range(len(images))]}
            write_json(target, {'answer': answer})
            return {'answer': answer}
        with mock.patch('studio.screen_grammar.codex_json', fake):
            first = ft.measure(self.p, 's')
            again = ft.measure(self.p, 's')
        self.assertEqual((len(calls), first['read'], again['read']), (1, ['c01', 'c02'], []))   # cached by picture hash
        self.assertIn('booster', calls[0][1])
        self.assertTrue(Path(first['check']).is_file())
        marked = ft.mark(self.p, 's', 'c02', box=[0.3, 0.2, 0.7, 0.9], whole=[0.3, 0.2, 0.7, 0.9], view_deg=-30)
        self.assertEqual(marked['targets'][1]['height_share'], 0.7)
        with self.assertRaises(StudioError):
            ft.mark(self.p, 's', 'c02', box=[0.5, 0.5, 0.4, 0.9])
        with self.assertRaises(StudioError):
            ft.apply(self.p, 's', must_see=['engine'])                     # not a screen.details part: no size to meet
        shot = load_shot(self.p, 's'); shot['screen']['details'] = [{'id': 'booster/arm', 'min_px': 12, 'why': 'the arm tilts the engine'}]
        write_json(shot_path(self.p, 's'), shot)
        moved = ft.apply(self.p, 's', path_kind='arc')
        self.assertEqual((moved['move']['type'], moved['move']['params']),
                         ('frame_to_frame', {'target': 'booster', 'path': 'arc', 'follow': 0.5, 'pace': 'subject'}))
        self.assertEqual([f.get('must_see') for f in moved['move']['frames']], [None, ['booster/arm']])   # the shot ends on it
        self.assertNotIn('must_see', ft.apply(self.p, 's', must_see=[])['move']['frames'][1])
        self.assertEqual([f['picture']['concept_id'] for f in moved['move']['frames']], ['c01', 'c02'])
        self.assertEqual(load_shot(self.p, 's')['camera']['move']['frames'][1]['view']['elevation_deg'], -30.0)
        with self.assertRaises(StudioError):
            ft.depart(self.p, 's', 'c02', 'must_see', 'the arm can hide here because the next shot shows it')
        with self.assertRaises(StudioError):
            ft.depart(self.p, 's', 'c02', 'angle', 'nicer')
        with self.assertRaises(StudioError) as viewpoint:   # the picture's viewpoint: only in the user's words (2026-10-11, s06)
            ft.depart(self.p, 's', 'c02', 'angle', 'three of nine engines read best from straight below')
        self.assertEqual(viewpoint.exception.code, 'FRAME_DEPART_LIMIT')
        ft.depart(self.p, 's', 'c02', 'angle', 'three of nine engines read best from straight below', user_words='밑에서 올려다보는 게 나아')
        self.assertEqual(ft.targets(self.p, 's')[1]['departs'][0]['item'], 'angle')
        ft.depart(self.p, 's', 'c02', 'size', 'the engine section fills the frame to read three of nine')
        ft.depart(self.p, 's', 'c02', 'edges', 'the body above runs on into the next shot s13')
        with self.assertRaises(StudioError) as third:        # two without the user's words, then the picture is redrawn
            ft.depart(self.p, 's', 'c02', 'place', 'the engines sit lower in the frame than drawn')
        self.assertIn('redraw the picture', str(third.exception))
        self.assertFalse(ft.abandoned(ft.targets(self.p, 's')[1]['departs']))
        self.assertTrue(ft.abandoned([{'item': i, 'why': 'w'} for i in ('size', 'edges', 'place')]))
        self.assertTrue(ft.abandoned([{'item': 'tilt', 'why': 'w'}]))
        self.assertEqual([d['item'] for d in ft.undepart(self.p, 's', 'c02', ['size'])['departs']], ['angle', 'edges'])
        self.assertEqual(ft.undepart(self.p, 's', 'c02')['departs'], [])
        store = read_json(self.p / 'concepts' / 's' / 'frames.json')
        self.assertEqual(store['pictures']['c02']['marked']['view_deg_signed'], -30)

    def test_what_a_picture_shows_is_what_that_moment_must_show_and_its_lean_is_kept(self):
        """2026-10-11, s06: the last picture showed the pipe reaching the engines; nothing made the engines must-see."""
        concept.add(self.p, 's', self.image, frame='start')
        concept.add(self.p, 's', self.image, frame='end')
        for cid, shows, tilt in (('c01', ['booster/lox_tube'], 15.0), ('c02', ['booster/lox_tube', 'booster/engine_00'], 0.0)):
            ft.mark(self.p, 's', cid, reading={**READ, 'context': [], 'shows': shows, 'subject_tilt_deg': tilt}, subject='booster', note='read by eye')
        with self.assertRaises(StudioError) as undeclared:
            ft.apply(self.p, 's')
        self.assertEqual(undeclared.exception.code, 'FRAME_SHOWS_UNDECLARED')
        shot = load_shot(self.p, 's')
        shot['screen']['details'] = [{'id': 'booster/lox_tube', 'min_px': 12, 'why': 'the pipe'}, {'id': 'booster/engine_00', 'min_px': 20, 'why': 'where it arrives'}]
        write_json(shot_path(self.p, 's'), shot)
        frames = ft.apply(self.p, 's')['move']['frames']
        self.assertEqual([f.get('must_see') for f in frames], [['booster/lox_tube'], ['booster/engine_00', 'booster/lox_tube']])
        self.assertEqual([f.get('tilt_deg') for f in frames], [15.0, 0.0])
        ft.mark(self.p, 's', 'c02', tilt_deg=-5.0, shows=['booster/engine_00'])
        self.assertEqual((ft.targets(self.p, 's')[1]['tilt_deg'], ft.targets(self.p, 's')[1]['shows']), (-5.0, ['booster/engine_00']))
        target = {**ft.targets(self.p, 's')[1]}
        box = target['check']['box']
        self.assertTrue(any('leans' in q for q in ft.compare(target, box, None, None, None, tilt_deg=12.0)))
        self.assertFalse(any('leans' in q for q in ft.compare(target, box, None, None, None, tilt_deg=-1.0)))

    def test_with_a_show_the_shot_list_decides_what_must_show_and_extras_are_noted(self):
        concept.add(self.p, 's', self.image, frame='start')
        concept.add(self.p, 's', self.image, frame='end')
        for cid in ('c01', 'c02'):
            ft.mark(self.p, 's', cid, reading={**READ, 'context': [], 'shows': ['booster/engine_00', 'booster/fin_0']}, subject='booster', note='read by eye')
        shot = load_shot(self.p, 's')
        shot['show'] = [{'at': 'end', 'parts': ['booster/engine_00'], 'how': 'exterior', 'why': 'the engine'}]
        shot['screen']['details'] = [{'id': 'booster/engine_00', 'min_px': 32, 'why': 'the engine'}]
        write_json(shot_path(self.p, 's'), shot)
        applied = ft.apply(self.p, 's')
        self.assertEqual(applied['move']['frames'][-1].get('must_see'), ['booster/engine_00'])
        self.assertTrue(applied['warnings'][0].startswith("FRAME_SHOWS_EXTRA: the pictures also show ['booster/fin_0']"))

    def test_a_picture_drawn_another_way_than_the_shot_list_says_is_drawn_again(self):
        """2026-10-13: the shot list decides how each moment shows (show_core); the picture follows, then the camera."""
        concept.add(self.p, 's', self.image, frame='start')
        concept.add(self.p, 's', self.image, frame='end')
        for cid in ('c01', 'c02'):
            ft.mark(self.p, 's', cid, reading={**READ, 'context': []}, subject='booster', note='read by hand')
        shot = load_shot(self.p, 's')
        shot['direction'] = {'camera': 'push into the side', 'reveal': 'section_cut'}   # an earlier shot list's word for it
        write_json(shot_path(self.p, 's'), shot)
        with self.assertRaises(StudioError) as caught:
            ft.apply(self.p, 's')                            # read before how was read: not taken for an outside view
        self.assertEqual(caught.exception.code, 'FRAME_SHOW_UNREAD')
        ft.mark(self.p, 's', 'c02', how='exterior', note='read again: drawn from outside')
        with self.assertRaises(StudioError) as caught:
            ft.apply(self.p, 's')                            # drawn from outside, the shot list says section
        self.assertEqual(caught.exception.code, 'FRAME_SHOW_DIFFERS')
        ft.mark(self.p, 's', 'c02', how='section', note='it is drawn cut open')
        frames = ft.apply(self.p, 's')['move']['frames']
        self.assertEqual((frames[0].get('show'), frames[1]['show']['how']), (None, 'section'))
        brief = concept.brief(self.p, 's', 'end')
        self.assertEqual(brief['how'], 'section')
        self.assertIn('cut through by a flat plane', brief['prompt'])

    def test_an_opening_holds_to_the_end_and_see_through_ways_frame_alike(self):
        """2026-10-13: a shot opened at the start with no end row was briefed and checked 'exterior' at its end; a
        picture read as transparency where the list says xray frames the same."""
        concept.add(self.p, 's', self.image, frame='start')
        concept.add(self.p, 's', self.image, frame='end')
        ft.mark(self.p, 's', 'c01', reading={**READ, 'context': [], 'how': 'transparency'}, subject='booster', note='read by hand')
        ft.mark(self.p, 's', 'c02', reading={**READ, 'context': [], 'how': 'exterior'}, subject='booster', note='read by hand')
        shot = load_shot(self.p, 's')
        shot['show'] = [{'at': 'start', 'parts': ['booster/engine'], 'how': 'xray', 'why': 'the engine inside'}]
        shot['screen'] = {**shot['screen'], 'details': [{'id': 'booster/engine', 'min_px': 32, 'why': 'the engine inside'}]}
        write_json(shot_path(self.p, 's'), shot)
        self.assertEqual(concept.brief(self.p, 's', 'end')['how'], 'xray')
        with self.assertRaises(StudioError) as caught:
            ft.apply(self.p, 's')
        self.assertIn('c02 at 1.0 shows it exterior, the shot list says xray', caught.exception.message)
        self.assertNotIn('c01', caught.exception.message)
        ft.mark(self.p, 's', 'c02', how='xray', note='seen through')
        ft.apply(self.p, 's')

    def test_a_shot_the_approved_list_left_without_show_notes_what_its_pictures_show(self):
        from studio import decisions
        concept.add(self.p, 's', self.image, frame='start')
        concept.add(self.p, 's', self.image, frame='end')
        for cid in ('c01', 'c02'):
            ft.mark(self.p, 's', cid, reading={**READ, 'context': [], 'shows': ['booster/fin_0']}, subject='booster', note='read by eye')
        with self.assertRaises(StudioError) as caught:
            ft.apply(self.p, 's')
        self.assertEqual(caught.exception.code, 'FRAME_SHOWS_UNDECLARED')
        envelope = {'status': 'approved', 'body': {'shots': [{'shot_id': 's'}, {'shot_id': 'x', 'show': []}]}}
        with mock.patch.object(decisions, 'envelope', return_value=envelope):
            self.assertTrue(ft.apply(self.p, 's')['warnings'][0].startswith('FRAME_SHOWS_EXTRA'))
        with mock.patch.object(decisions, 'envelope', return_value={**envelope, 'status': 'proposed'}):
            self.assertFalse(decisions.show_decided(self.p, 's'))

    def test_a_later_moment_is_drawn_from_the_same_shots_earlier_picture(self):
        """2026-10-13: an end picture drawn without its start came out as another vehicle; one shot is one camera."""
        self.assertFalse(any('same shot' in a['why'] for a in concept.brief(self.p, 's', 'end')['attach']))
        start = concept.add(self.p, 's', self.image, frame='start')
        brief = concept.brief(self.p, 's', 'end')
        self.assertIn(start['path'], [a['path'] for a in brief['attach']])
        self.assertIn('same shot as the attached earlier picture', brief['prompt'])
        self.assertNotIn(start['path'], [a['path'] for a in concept.brief(self.p, 's', 'start')['attach']])

    def test_each_moment_must_show_its_own_parts(self):
        """2026-10-13 re-audit: every detail went onto the last frame, so a part shown cut open at the start became a
        must-see of the closed end."""
        concept.add(self.p, 's', self.image, frame='start')
        concept.add(self.p, 's', self.image, frame='end')
        ft.mark(self.p, 's', 'c01', reading={**READ, 'context': [], 'how': 'section'}, subject='booster', note='read by hand')
        ft.mark(self.p, 's', 'c02', reading={**READ, 'context': [], 'how': 'exterior'}, subject='booster', note='read by hand')
        shot = load_shot(self.p, 's')
        shot['show'] = [{'at': 'start', 'parts': ['booster/engine'], 'how': 'section', 'why': 'the engine inside'},
                        {'at': 'end', 'parts': [], 'how': 'exterior', 'why': 'closed again'}]
        shot['screen'] = {**shot['screen'], 'details': [{'id': 'booster/engine', 'min_px': 32, 'why': 'the engine inside'}]}
        write_json(shot_path(self.p, 's'), shot)
        frames = ft.apply(self.p, 's')['move']['frames']
        self.assertEqual((frames[0].get('must_see'), frames[1].get('must_see')), (['booster/engine'], None))

    def bike(self, chain_rows=None):
        """A bicycle (not the rocket): its frame 1 m tall, its chain 1 cm thick, the whole bike a tenth of the frame tall
        seen from the side; a survey with every direction's share."""
        from studio.blender_ops import exposure_core
        from studio.common import file_hash
        spec = self.p / 'subjects' / 'bike' / 'spec.json'
        write_json(spec, {'subject_id': 'bike', 'identity': 'a bicycle', 'builders': [{'part_id': 'frame'}, {'part_id': 'chain'}]})
        row = lambda box, reach, share: {'best_share': share, 'best_dir': [0, -1, 0], 'hidden_by': [], 'per_direction': [share] * 64,  # noqa: E731
                                         'exposable_share': share, 'enclosed_by': {}, 'box': box, 'reach_m': reach}
        parts = {'frame': row([[-0.9, -0.05, 0.0], [0.9, 0.05, 1.0]], 0.1, 1.0),
                 'chain': {**row([[-0.5, 0.0, 0.3], [0.5, 0.01, 0.31]], 0.0141, 0.9), **(chain_rows or {})}}
        write_json(spec.parent / 'exposure.json', {'schema_version': exposure_core.SCHEMA_VERSION, 'spec_sha256': file_hash(spec), 'threshold': 0.5,
                                                   'directions': 64, 'samples': 300, 'parts': parts})
        concept.add(self.p, 's', self.image, frame='start')
        concept.add(self.p, 's', self.image, frame='end')
        whole = {**READ, 'subject_box': [0.3, 0.5, 0.7, 0.6], 'whole_box': [0.3, 0.5, 0.7, 0.6], 'cut_edges': [], 'horizon_v': None,
                 'view_height': 'level', 'view_deg': 0, 'context': [], 'shows': ['bike/frame', 'bike/chain']}
        for cid in ('c01', 'c02'):
            ft.mark(self.p, 's', cid, reading=whole, subject='bike', note='the whole bike, side on')
        shot = load_shot(self.p, 's')
        shot['screen'] = {'subject': ['bike'], 'details': [{'id': 'bike/frame', 'min_px': 32, 'why': 'the frame'},
                                                           {'id': 'bike/chain', 'min_px': 32, 'why': 'the chain drives the wheel'}]}
        shot['show'] = [{'at': 'start', 'parts': ['bike/frame'], 'how': 'exterior', 'why': 'the bike'},
                        {'at': 'end', 'parts': ['bike/chain'], 'how': 'exterior', 'why': 'the chain'}]
        write_json(shot_path(self.p, 's'), shot)

    def set_min_px(self, part, px):
        shot = load_shot(self.p, 's')
        shot['screen']['details'] = [{**d, 'min_px': px} if d['id'] == part else d for d in shot['screen']['details']]
        write_json(shot_path(self.p, 's'), shot)

    def test_a_part_the_shot_list_shows_later_is_not_required_earlier(self):
        """A3-4 (2026-10-14): the start picture showed the legs the shot list unfolds only at the end; they became a
        must-see of the start, where they are folded away. The shot list says which moment shows what."""
        self.bike()
        self.set_min_px('bike/chain', 8)
        frames = ft.apply(self.p, 's')['move']['frames']
        self.assertEqual([f.get('must_see') for f in frames], [['bike/frame'], ['bike/chain']])

    def test_a_moment_inside_a_held_opening_keeps_what_it_shows(self):
        """Review 2026-10-14: a picture at t=0.5 inside a section opened at the start (held, no row of its own) shows
        the part that section opens; it is that moment's too, not 'another moment's'."""
        self.bike()
        self.set_min_px('bike/chain', 8)
        shot = load_shot(self.p, 's')
        shot['show'] = [{'at': 'start', 'parts': ['bike/frame'], 'how': 'section', 'why': 'the frame tube inside'},
                        {'at': 'end', 'parts': ['bike/chain'], 'how': 'exterior', 'why': 'the chain'}]
        write_json(shot_path(self.p, 's'), shot)
        mid = concept.add(self.p, 's', self.image, frame='key', at=0.5)
        reading = {**READ, 'subject_box': [0.3, 0.5, 0.7, 0.6], 'whole_box': [0.3, 0.5, 0.7, 0.6], 'cut_edges': [], 'horizon_v': None,
                   'view_height': 'level', 'view_deg': 0, 'context': [], 'shows': ['bike/frame', 'bike/chain']}
        for cid in ('c01', mid['concept_id']):
            ft.mark(self.p, 's', cid, reading={**reading, 'how': 'section'}, subject='bike', note='cut open')
        ft.mark(self.p, 's', 'c02', reading={**reading, 'how': 'exterior'}, subject='bike', note='outside')
        frames = ft.apply(self.p, 's')['move']['frames']
        self.assertEqual([(f['at'], f.get('must_see')) for f in frames], [(0.0, ['bike/frame']), (0.5, ['bike/frame']), (1.0, ['bike/chain'])])

    def test_a_part_too_thin_for_the_pictures_framing_is_refused_before_the_build(self):
        """A3-5 (2026-10-14): a leg 12 px wide at the picture's framing, 32 px needed, failed only in the build. The
        part's own size, the picture's scale and the most the camera comes closer (CLOSER_MAX) tell it before."""
        self.bike()
        with self.assertRaises(StudioError) as caught:
            ft.apply(self.p, 's')
        self.assertEqual(caught.exception.code, 'MUST_SEE_UNREACHABLE')
        self.assertIn('bike/chain', caught.exception.message)
        self.set_min_px('bike/chain', 8)
        ft.apply(self.p, 's')

    def test_an_outside_moment_whose_view_hides_its_part_is_named(self):
        dirs = __import__('studio.blender_ops.exposure_core', fromlist=['directions']).directions()
        self.bike({'per_direction': [0.0 if d[1] < 0 else 0.9 for d in dirs]})   # the chain runs on the far side
        self.set_min_px('bike/chain', 8)
        warnings = ft.apply(self.p, 's')['warnings']
        self.assertTrue(any(w.startswith('SHOW_VIEW_HIDES') and 'bike/chain' in w for w in warnings), warnings)

    def test_apply_preserves_whole_boxes_far_outside_the_frame(self):
        concept.add(self.p, 's', self.image, frame='start')
        concept.add(self.p, 's', self.image, frame='end')
        for cid, box, whole, edge in (
                ('c01', [0.008, 0.119, 0.853, 1.0], [0.008, 0.119, 0.853, 3.65], 'bottom'),
                ('c02', [0.008, 0.0, 0.853, 0.881], [0.008, -2.65, 0.853, 0.881], 'top')):
            ft.mark(self.p, 's', cid, reading={**READ, 'subject_box': box, 'whole_box': whole,
                                             'cut_edges': [edge], 'context': []},
                    whole=whole, subject='booster', note='whole booster extends past the picture')
        frames = ft.apply(self.p, 's')['move']['frames']
        self.assertEqual([f['height_share'] for f in frames], [3.531, 3.531])
        self.assertEqual([f['centre'] for f in frames], [[0.4305, 1.8845], [0.4305, -0.8845]])
        self.assertNotIn('edges', frames[0])  # the explicit whole-box mark is the framing target
        shot = load_shot(self.p, 's')
        self.assertEqual(shot['camera']['move']['frames'], frames)
        for invalid in (0, -1):
            shot['camera']['move']['frames'][0]['height_share'] = invalid
            with self.assertRaises(StudioError):
                validate_schema(shot, 'shot')

    def test_a_picture_painted_over_the_build_is_read_from_the_build(self):
        """2026-10-12: s21's picture drew a viewpoint no camera could take; painted over the built frame, it cannot."""
        concept.add(self.p, 's', self.image, frame='end')
        with self.assertRaises(StudioError):
            ft.derive(self.p, 's', 'c01')                       # not painted over anything
        shot = load_shot(self.p, 's'); shot['scene_version'] = 'v0001'; write_json(shot_path(self.p, 's'), shot)
        version = shot_path(self.p, 's').parent / 'versions/v0001'; version.mkdir(parents=True)
        write_json(version / 'frame_report.json', {'frames': [{'frame': 59, 'shapes': {'subject': {'bbox': [0.4, 0.0, 0.6, 0.7]}}}]})
        write_json(version / 'camera_move_report.json', {'frames': [{'frame': 59, 'view': [-16.0, 25.0], 'lens_mm': 24.0, 'tilt_deg': 2.0,
                                                                     'must_see': ['booster/fin_0'], 'achieved': {'box': [0.4, -0.3, 0.6, 0.7]}}]})
        clay = Path(self.tmp.name) / 'clay.png'
        Image.new('RGB', (90, 160), (120, 120, 120)).save(clay)
        painted = concept.add(self.p, 's', self.image, frame='end', derived_from=clay)
        self.assertEqual(painted['derived_from']['supersedes'], 'c01')
        read = ft.derive(self.p, 's', painted['concept_id'])['reading']
        self.assertEqual((read['cut_edges'], read['view_height'], read['subject_tilt_deg'], read['shows']), (['top'], 'up', 2.0, ['booster/fin_0']))
        target = ft.targets(self.p, 's')[-1]
        self.assertEqual((target['view']['elevation_deg'], target['view']['azimuth_deg'], target['lens_mm']), (-16.0, 25.0, 24.0))

    def test_an_eye_review_is_kept_per_version(self):
        concept.add(self.p, 's', self.image, frame='start')
        ft.mark(self.p, 's', 'c01', reading={**READ, 'context': []}, subject='booster', note='read by eye')
        with self.assertRaises(StudioError):
            ft.review(self.p, 's', 'c01', {'objects': True}, 'only one answer given here', 'reviewer')
        result = ft.review(self.p, 's', 'c01', {'objects': True, 'extent': False, 'state': True, 'viewpoint': True},
                           'the second stage runs off the top; the picture shows the booster alone', 'reviewer', version='v0009')
        self.assertFalse(result['matches'])
        self.assertEqual(ft.targets(self.p, 's')[0]['reviews'][0]['version'], 'v0009')

    def test_manual_readings_initialize_without_codex_and_are_cached_with_provenance(self):
        concept.add(self.p, 's', self.image, frame='start')
        concept.add(self.p, 's', self.image, frame='end')
        manual = {**READ, 'context': []}
        with mock.patch('studio.screen_grammar.codex_json', side_effect=AssertionError('offline reader must not run')):
            for cid in ('c01', 'c02'):
                result = ft.mark(self.p, 's', cid, reading=manual, subject='booster', note='agent inspected the picture; automated reader unavailable')
                self.assertTrue(Path(result['check']).is_file())
                self.assertEqual(result['provenance']['kind'], 'manual')
            self.assertEqual(ft.measure(self.p, 's')['read'], [])
        moved = ft.apply(self.p, 's', follow=0.5, path_kind='arc')
        self.assertEqual(len(moved['move']['frames']), 2)
        store = read_json(self.p / 'concepts' / 's' / 'frames.json')
        self.assertNotIn('labels', store['pictures']['c01'])   # never fake the automated reader's output
        self.assertEqual(store['pictures']['c01']['read'], manual)

    def test_manual_initialization_rejects_incomplete_invalid_or_unidentified_readings_without_writing(self):
        concept.add(self.p, 's', self.image, frame='start')
        manual = {**READ, 'context': []}
        cases = [({'subject_box': READ['subject_box']}, 'looked directly'),
                 (manual, None), ([], 'looked directly'),
                 ({**manual, 'whole_box': [0.5, 0.5, 0.4, 0.6]}, 'looked directly'),
                 ({**manual, 'subject_box': [0.2, float('nan'), 0.8, 0.9]}, 'looked directly'),
                 (READ, 'looked directly')]
        for reading, note in cases:
            with self.subTest(reading=reading), self.assertRaises(StudioError):
                ft.mark(self.p, 's', 'c01', reading=reading, note=note)
            self.assertFalse((self.p / 'concepts' / 's' / 'frames.json').exists())
        with self.assertRaises(StudioError):
            ft.mark(self.p, 's', 'c99', reading=manual, note='looked directly')
        shot = load_shot(self.p, 's'); shot['screen']['subject'] = ['booster/engine']
        write_json(shot_path(self.p, 's'), shot)
        with self.assertRaises(StudioError):
            ft.mark(self.p, 's', 'c01', reading=manual, note='looked directly')
        self.assertEqual(ft.mark(self.p, 's', 'c01', reading=manual, subject='centre engine', note='looked directly')['provenance']['kind'], 'manual')

    def test_mark_refuses_a_changed_concept_instead_of_binding_an_old_reading_to_it(self):
        added = concept.add(self.p, 's', self.image, frame='start')
        ft.mark(self.p, 's', added['concept_id'], reading={**READ, 'context': []}, note='looked directly')
        record_path = self.p / 'concepts' / 's' / 'c01.json'
        record = read_json(record_path)
        record['sha256'] = 'changed-picture-hash'
        write_json(record_path, record)
        with self.assertRaises(StudioError) as failure:
            ft.mark(self.p, 's', 'c01', view_deg=-30)
        self.assertEqual(failure.exception.code, 'CONCEPT_STALE')


class RepeatTest(unittest.TestCase):
    def test_cut_together_shots_on_one_composition_are_flagged_unless_matched(self):
        box = [0.4, 0.3, 0.6, 0.8]
        ends = [{'shot_id': 'a', 'first': (box, 0.0), 'last': (box, 2.0), 'handover': 'new_place'},
                {'shot_id': 'b', 'first': ([0.41, 0.31, 0.61, 0.82], 5.0), 'last': (box, 0.0), 'handover': 'match_action'},
                {'shot_id': 'c', 'first': (box, 1.0), 'last': ([0.1, 0.1, 0.3, 0.3], -30.0)},
                {'shot_id': 'd', 'first': (box, -30.0), 'last': None}]
        lines = patterns.framing_repeats(ends)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith('FRAMING_REPEATED: a ends and b starts'))


if __name__ == '__main__':
    unittest.main()
