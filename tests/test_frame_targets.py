"""Frame pictures -> framing targets (studio/frame_targets.py, concept --frame): what a picture's reading becomes, how
a built moment is judged against it, and how cut-together shots on one composition are caught (patterns)."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image

from studio import concept, frame_targets as ft, patterns
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, load_shot, shot_path

READ = {'subject_box': [0.4, 0.0, 0.6, 0.3], 'whole_box': [0.4, -0.5, 0.6, 0.3], 'cut_edges': ['top'], 'horizon_v': 0.8,
        'view_height': 'up', 'view_deg': 20, 'side': 'side', 'lens': 'wide', 'fore': 'spray', 'subject_tilt_deg': None, 'shows': [],
        'context': [{'name': 'the landing ship', 'box': [0.3, 0.6, 0.7, 0.66]}, {'name': 'the coast', 'box': None}], 'note': 'it drops in'}


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
        self.assertEqual(len(problems), 4, problems)                        # centre, size, no cut edge, level not up
        self.assertIn('looks level', problems[-1])
        self.assertIn('not on screen', ft.compare(t, None)[0])

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
        ends = [{'shot_id': 'a', 'first': (box, 0.0), 'last': (box, 2.0), 'transition': 'cut'},
                {'shot_id': 'b', 'first': ([0.41, 0.31, 0.61, 0.82], 5.0), 'last': (box, 0.0), 'transition': 'match cut on the flame'},
                {'shot_id': 'c', 'first': (box, 1.0), 'last': ([0.1, 0.1, 0.3, 0.3], -30.0)},
                {'shot_id': 'd', 'first': (box, -30.0), 'last': None}]
        lines = patterns.framing_repeats(ends)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith('FRAMING_REPEATED: a ends and b starts'))


if __name__ == '__main__':
    unittest.main()
