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

    def test_a_picture_drawn_another_way_than_the_shot_list_says_is_drawn_again(self):
        """2026-10-13: the shot list decides how each moment shows (show_core); the picture follows, then the camera."""
        concept.add(self.p, 's', self.image, frame='start')
        concept.add(self.p, 's', self.image, frame='end')
        for cid in ('c01', 'c02'):
            ft.mark(self.p, 's', cid, reading={**READ, 'context': []}, subject='booster', note='read by hand')
        shot = load_shot(self.p, 's')
        shot['show'] = [{'at': 'end', 'parts': [], 'how': 'section', 'why': 'the engine inside'}]
        write_json(shot_path(self.p, 's'), shot)
        with self.assertRaises(StudioError) as caught:
            ft.apply(self.p, 's')                            # the end picture was read as an outside view
        self.assertEqual(caught.exception.code, 'FRAME_SHOW_DIFFERS')
        ft.mark(self.p, 's', 'c02', how='section', note='it is drawn cut open')
        frames = ft.apply(self.p, 's')['move']['frames']
        self.assertEqual((frames[0].get('show'), frames[1]['show']['how']), (None, 'section'))
        brief = concept.brief(self.p, 's', 'end')
        self.assertEqual(brief['how'], 'section')
        self.assertIn('cut through by a flat plane', brief['prompt'])

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
