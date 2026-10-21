"""A5 (2026-10-14): what a shot's frame pictures were read for, against what it builds, and its pictured moments as
built - checks that each take one row of gates.SOFTENABLE. Scenes here are not the reel the checks came from (N+1): a
cafe whose pictures were read for the diners while the move frames the counter, a street crossing with its tree on the
wrong side, a diver turned head-on, a van boxed with the crane lifting it, a cyclist the camera falls behind, a factory
in fog."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image

from studio import concept, frame_targets as ft
from studio.common import StudioError, read_json, write_json
from studio.gates import SOFTENABLE, severity_map
from studio.project import init_project, load_shot, shot_path
from tests.decisions_fixture import settle

BASE = {'subject_box': [0.4, 0.3, 0.6, 0.7], 'whole_box': [0.4, 0.3, 0.6, 0.7], 'cut_edges': [], 'horizon_v': None,
        'view_height': 'level', 'view_deg': 5, 'side': 'side', 'lens': 'normal', 'fore': None, 'subject_tilt_deg': None,
        'shows': [], 'objects': [], 'context': [], 'note': 'read by eye'}


def element(eid, function, words, ids=None):
    return {'id': eid, 'function': function, 'description': words, 'owner': ['blender'], **({'ids': ids} if ids else {})}


class Scene:
    """A project with a world, one shot showing `shown` and two frame pictures (start, end)."""

    def __init__(self, test, elements, shown, frames=60):
        self.tmp = tempfile.TemporaryDirectory()
        test.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.p = Path(init_project('a5', {'request': 'a5', 'shots': [{'shot_id': 's', 'frame_count': frames}]}, root)['project_path'])
        settle(self.p)
        write_json(self.p / 'world.json', {'schema_version': 1, 'elements': elements})
        shot = load_shot(self.p, 's')
        shot['screen'] = {**(shot.get('screen') or {}), 'elements': shown}
        write_json(shot_path(self.p, 's'), shot)
        image = root / 'pic.png'
        Image.new('RGB', (90, 160), (90, 120, 160)).save(image)
        concept.add(self.p, 's', image, 'first', frame='start')
        concept.add(self.p, 's', image, 'last', frame='end')

    def world(self, change):
        world = read_json(self.p / 'world.json')
        change(world['elements'])
        write_json(self.p / 'world.json', world)

    def read(self, readings, subject=None):
        """Read both pictures the way frames measure does (the reader mocked): `readings` per picture."""
        def fake(images, prompt, schema, target):
            answer = {'pictures': [{**readings[i], 'index': i} for i in range(len(images))]}
            write_json(target, {'answer': answer})
            return {'answer': answer}
        with mock.patch('studio.screen_grammar.codex_json', fake):
            return ft.measure(self.p, 's', subject)

    def shot(self):
        return load_shot(self.p, 's')

    def codes(self):
        return [c['code'] for c in ft.reading_checks(self.p, self.shot())]


def cafe(test):
    return Scene(test, [element('diners', 'agent', 'diners at the tables, generic people', ['diners']),
                        element('counter', 'set', 'the long espresso counter', ['counter']),
                        element('room', 'context', 'a small corner cafe with big windows')], ['diners', 'counter', 'room'])


class ReadingTest(unittest.TestCase):
    def test_the_move_framing_another_thing_than_was_read_is_refused(self):
        """The cafe's pictures were read for the diners; a move framing the counter sizes the counter to the diners' box."""
        scene = cafe(self)
        scene.read([BASE, BASE])
        self.assertEqual(ft._store(scene.p, 's')['subject'], 'diners at the tables, generic people')
        with self.assertRaises(StudioError) as refused:
            ft.apply(scene.p, 's', target='counter')
        self.assertEqual(refused.exception.code, 'FRAME_TARGET_STALE')
        self.assertIn('counter', str(refused.exception))
        ft.apply(scene.p, 's', target='diners')                       # the thing read: written, nothing said
        self.assertEqual(scene.codes(), [])
        shot = scene.shot()
        shot['camera']['move']['params']['target'] = 'counter'       # the target changed by hand after the reading
        write_json(shot_path(scene.p, 's'), shot)
        self.assertEqual(scene.codes(), ['FRAME_TARGET_STALE'])
        self.assertEqual(SOFTENABLE['FRAME_TARGET_STALE'][0], 'broken')
        self.assertEqual(severity_map({})['FRAME_TARGET_STALE'], 'error')

    def test_words_given_by_hand_say_what_the_camera_frames(self):
        scene = cafe(self)
        scene.read([BASE, BASE], subject='the espresso counter and its machines')
        ft.apply(scene.p, 's', target='counter')
        self.assertEqual(scene.codes(), [])                          # read for the counter by name: nothing stale
        scene.read([BASE, BASE])                                     # read again for the world's words: no longer given by hand
        self.assertFalse(ft._store(scene.p, 's')['subject_given'])

    def test_words_given_for_parts_serve_every_later_reading_of_those_parts(self):
        """A shot framing one part (a grinder's burr inside the counter): its words are given once; a later reading -
        measure again, a set of variants, a redo - takes them while the shot frames those parts, and asks again after."""
        scene = cafe(self)
        shot = scene.shot()
        shot['screen']['subject'] = ['counter/grinder_burr']
        write_json(shot_path(scene.p, 's'), shot)
        with self.assertRaises(StudioError):
            scene.read([BASE, BASE])                                   # no words for a part: asked
        scene.read([BASE, BASE], subject='the grinder burr behind the glass')
        self.assertEqual(scene.read([BASE, BASE])['subject'], 'the grinder burr behind the glass')
        self.assertEqual(ft.words_for(scene.p, scene.shot()), 'the grinder burr behind the glass')
        self.assertTrue(ft._store(scene.p, 's')['subject_given'])
        shot['screen']['subject'] = ['counter/steam_wand']             # another part: those words are not its words
        write_json(shot_path(scene.p, 's'), shot)
        with self.assertRaises(StudioError):
            ft.words_for(scene.p, scene.shot())

    def test_pictures_read_for_other_words_are_stale(self):
        scene = cafe(self)
        scene.read([BASE, BASE])
        ft.apply(scene.p, 's', target='diners')
        scene.world(lambda es: es[0].update(description='a waiter carrying a tray between the tables'))
        self.assertEqual(scene.codes()[0], 'FRAME_READING_STALE')    # the words the boxes were read for are not the shot's now
        self.assertEqual(SOFTENABLE['FRAME_READING_STALE'][0], 'broken')
        self.assertIn('FRAME_READING_STALE', [c['code'] for c in ft.reading_checks(scene.p, scene.shot())])

    def test_things_linked_after_the_reading_are_said_and_read_again(self):
        scene = cafe(self)
        scene.read([BASE, BASE])
        ft.apply(scene.p, 's', target='diners')
        self.assertEqual(scene.codes(), [])
        self.assertIn('room', ft._store(scene.p, 's')['named'])        # not built yet, still named to the reader (2026-10-20)
        scene.world(lambda es: es[2].update(ids=['room_shell']))      # the room built after the pictures were read
        self.assertEqual(ft.reading_checks(scene.p, scene.shot()), [])   # it was asked by name: its box is in the reading
        store = ft._store(scene.p, 's'); store.pop('named')           # a reading from before things were named
        write_json(ft._store_path(scene.p, 's'), store)
        checks = ft.reading_checks(scene.p, scene.shot())
        self.assertEqual([c['code'] for c in checks], ['FRAME_READING_PARTIAL'])
        self.assertIn('room', checks[0]['text'])
        self.assertEqual(SOFTENABLE['FRAME_READING_PARTIAL'][0], 'taste')
        again = scene.read([BASE, BASE])
        self.assertEqual(again['read'], ['c01', 'c02'])               # measure reads them again for it
        self.assertEqual(scene.codes(), [])

    def test_a_reading_from_before_the_store_recorded_what_it_asked(self):
        """A store written before A5 records no elements: the names its readings placed stand for what was asked."""
        scene = cafe(self)
        scene.read([{**BASE, 'objects': [{'name': 'diners', 'box': [0.1, 0.4, 0.9, 0.9], 'cut_edges': [], 'state': None}]}] * 2)
        store = ft._store(scene.p, 's')
        for key in ('elements', 'subject_given', 'subject_ids'):
            store.pop(key)
        write_json(scene.p / 'concepts' / 's' / 'frames.json', store)
        ft.apply(scene.p, 's', target='diners')
        gaps = ft.reading_gaps(scene.p, scene.shot(), ft._store(scene.p, 's'))
        self.assertEqual(gaps, {'subject': None, 'unread': ['counter'], 'recorded': False})   # the counter was never placed
        checks = ft.reading_checks(scene.p, scene.shot())
        self.assertEqual([c['code'] for c in checks], ['FRAME_READING_UNRECORDED'])
        self.assertIn('predates recording what the reader was asked', checks[0]['text'])   # not asked, or asked and not drawn
        calls = []
        with mock.patch('studio.screen_grammar.codex_json', lambda *a: calls.append(a)):
            self.assertEqual(ft.measure(scene.p, 's')['read'], [])                       # said, never a paid re-read by itself
        self.assertEqual(calls, [])

    def test_people_stand_around_a_subject_and_the_subject_is_not_around_itself(self):
        scene = cafe(self)
        shot = scene.shot()
        shot['screen']['subject'] = ['counter']
        write_json(shot_path(scene.p, 's'), shot)
        self.assertEqual(ft.context_elements(scene.p, scene.shot()), {'diners at the tables': ['diners']})
        self.assertIn('diners', ft.layout_elements(scene.p, scene.shot()))
        self.assertIn('agent', ft.LAYOUT_FUNCTIONS)


def built(frames, **shapes_by_frame):
    return {'output_size': [1080, 1920], 'frames': [{'frame': f, 'shapes': shapes_by_frame.get(f'f{f}', {})} for f in frames]}


def key(box):
    return {'px': 100, 'share': 0.01, 'bbox': box}


class BuiltMomentTest(unittest.TestCase):
    def crossing(self):
        """A car at a street crossing; the picture has a tree on the car's left."""
        scene = Scene(self, [element('car', 'subject', 'a small hatchback waiting at the lights', ['car']),
                             element('tree', 'set', 'a plane tree on the corner', ['tree'])], ['car', 'tree'])
        drawn = {**BASE, 'objects': [{'name': 'car', 'box': [0.4, 0.5, 0.6, 0.7], 'cut_edges': [], 'state': None},
                                     {'name': 'tree', 'box': [0.05, 0.1, 0.3, 0.8], 'cut_edges': [], 'state': None}],
                 'subject_box': [0.4, 0.5, 0.6, 0.7], 'whole_box': [0.4, 0.5, 0.6, 0.7],
                 'context': [{'name': 'a plane tree on the corner', 'box': [0.05, 0.1, 0.3, 0.8]}]}
        scene.read([drawn, drawn])
        ft.apply(scene.p, 's', target='car')
        return scene

    def test_a_thing_on_the_other_side_of_the_subject_is_said(self):
        scene = self.crossing()
        rows = [{'at': 0.0, 'frame': 0}, {'at': 1.0, 'frame': 59}]
        mirrored = built([0, 59], f0={'key:car': key([0.4, 0.5, 0.6, 0.7]), 'key:tree': key([0.7, 0.1, 0.95, 0.8])},
                         f59={'key:car': key([0.4, 0.5, 0.6, 0.7]), 'key:tree': key([0.05, 0.1, 0.3, 0.8])})
        lines = ft.gate_lines(scene.p, scene.shot(), mirrored, {}, {'frames': rows})
        self.assertEqual([(line['code'], line['text'].split(':')[0]) for line in lines], [('FRAME_SIDE_MISS', 's picture c01 t=0.0')])
        self.assertIn('tree stands -0.33 of the frame from the subject in the picture, +0.33 in the build', lines[0]['text'])
        self.assertEqual(SOFTENABLE['FRAME_SIDE_MISS'][0], 'taste')

    def test_side_misses_skip_things_that_span_the_subject(self):
        self.assertEqual(ft.side_misses([0.4, 0, 0.6, 1], {'road': [0.0, 0.8, 1.0, 1.0]}, [0.4, 0, 0.6, 1], {'road': [0.0, 0.7, 1.0, 1.0]}), [])
        self.assertEqual(ft.side_misses([0.4, 0, 0.6, 1], {'kiosk': [0.0, 0.5, 0.2, 0.9]}, [0.4, 0, 0.6, 1], {'kiosk': [0.1, 0.5, 0.3, 0.9]}), [])

    def test_a_subject_seen_end_on_is_not_judged_by_its_lean(self):
        """A diver mid-somersault: the end picture leans her -20 deg; built, her body points at the camera."""
        scene = Scene(self, [element('diver', 'subject', 'a diver in a tuck somersault', ['diver'])], ['diver'])
        leaning = {**BASE, 'subject_tilt_deg': -20.0}
        scene.read([leaning, leaning])
        ft.apply(scene.p, 's', target='diver')
        report = built([0, 59], f0={'key:diver': key([0.4, 0.3, 0.6, 0.7])}, f59={'key:diver': key([0.4, 0.3, 0.6, 0.7])})
        rows = [{'at': 0.0, 'frame': 0, 'built': {'tilt_deg': -21.0, 'sight_deg': 80.0}},
                {'at': 1.0, 'frame': 59, 'built': {'tilt_deg': 35.0, 'sight_deg': 12.0}}]
        moments = ft.moments(scene.p, scene.shot(), report, {}, {'frames': rows})
        self.assertEqual([m['end_on'] for m in moments], [None, 12.0])
        self.assertFalse([p for m in moments for p in m['problems'] if 'leans' in p])   # 35 vs -20 is not compared end-on
        lines = ft.gate_lines(scene.p, scene.shot(), report, {}, {'frames': rows})
        self.assertEqual([line['code'] for line in lines], ['SUBJECT_END_ON'])
        side_on = [rows[0], {**rows[1], 'built': {'tilt_deg': 35.0, 'sight_deg': 70.0}}]
        moments = ft.moments(scene.p, scene.shot(), report, {}, {'frames': side_on})
        self.assertTrue([p for p in moments[1]['problems'] if 'leans +35' in p])         # side-on: the built lean is judged
        self.assertEqual(SOFTENABLE['SUBJECT_END_ON'][0], 'taste')

    def test_the_lean_and_the_line_of_sight_come_from_the_camera_that_renders(self):
        from studio.blender_ops.camera_frame_core import seen_lean
        right, up = (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)   # a level camera at y = -10 looking +y
        upright = seen_lean((0, 0, 1), (0, -10, 0), (0, 0, 0), right, up)
        self.assertEqual(upright, {'tilt_deg': 0.0, 'sight_deg': 90.0})
        head_on = seen_lean((0.1, 1.0, 0.05), (0, -10, 0), (0, 0, 0), right, up)
        self.assertLess(head_on['sight_deg'], 10)
        rolled = seen_lean((0, 0, 1), (0, -10, 0), (0, 0, 0), (0.9659, 0.0, -0.2588), (0.2588, 0.0, 0.9659))   # rolled 15 deg
        self.assertAlmostEqual(rolled['tilt_deg'], -15.0, delta=0.05)

    def test_a_box_holding_more_than_the_target_is_a_misread(self):
        """A van lifted by a dock crane: the picture's box holds the crane's mast too, eight times as tall as wide."""
        van = [5.5, 2.2, 2.0]
        self.assertIn('times as long', ft.misread([0.45, 0.1, 0.55, 0.6], [], (1080, 1920), van))
        self.assertIsNone(ft.misread([0.3, 0.45, 0.7, 0.6], [], (1080, 1920), van))            # the van alone
        self.assertIsNone(ft.misread([0.45, 0.0, 0.55, 0.6], ['top'], (1080, 1920), van))     # cut: its length is not shown
        self.assertIn('the same picture gives', ft.misread([0.3, 0.1, 0.7, 0.6], ['top'], (1080, 1920), van, own=[0.3, 0.45, 0.7, 0.6]))
        # a car's roof over a wall: the box of what shows is slimmer than the car; the reading says what stands in front
        car = [4.2, 1.8, 1.5]
        self.assertIsNone(ft.misread([0.3, 0.6, 0.7, 0.62], [], (1080, 1920), car, fore='a garden wall'))
        scene = Scene(self, [element('van', 'subject', 'a delivery van lifted off the quay', ['van'])], ['van'])
        tall = {**BASE, 'subject_box': [0.45, 0.1, 0.55, 0.6], 'whole_box': [0.45, 0.1, 0.55, 0.6]}
        scene.read([tall, tall])
        ft.apply(scene.p, 's', target='van')
        report = built([0, 59], f0={'key:van': key([0.45, 0.4, 0.55, 0.45])}, f59={'key:van': key([0.45, 0.4, 0.55, 0.45])})
        lines = ft.gate_lines(scene.p, scene.shot(), report, {}, {'frames': [{'at': 0.0, 'frame': 0, 'spans_m': van},
                                                                             {'at': 1.0, 'frame': 59, 'spans_m': van}]})
        self.assertEqual([line['code'] for line in lines], ['FRAME_SUBJECT_MISREAD'] * 2)
        self.assertEqual(severity_map({})['FRAME_SUBJECT_MISREAD'], 'error')

    def test_a_reading_that_boxes_its_target_smaller_than_its_subject_is_refused_before_the_build(self):
        scene = Scene(self, [element('van', 'subject', 'a delivery van lifted off the quay', ['van'])], ['van'])
        boxed = {**BASE, 'subject_box': [0.3, 0.1, 0.7, 0.6], 'whole_box': [0.3, 0.1, 0.7, 0.6],
                 'objects': [{'name': 'van', 'box': [0.3, 0.45, 0.7, 0.6], 'cut_edges': [], 'state': None}]}
        scene.read([boxed, BASE])
        ft.apply(scene.p, 's', target='van')
        checks = ft.reading_checks(scene.p, scene.shot())
        self.assertEqual([(c['code'], 'c01' in c['text']) for c in checks], [('FRAME_SUBJECT_MISREAD', True)])

    def test_a_moving_subject_away_from_where_the_camera_was_solved_is_said(self):
        """A cyclist crossing the frame: the camera was solved to hold her mid-frame; built, she has ridden off its side."""
        scene = Scene(self, [element('cyclist', 'subject', 'a cyclist on a city street', ['cyclist'])], ['cyclist'])
        scene.read([BASE, BASE])
        ft.apply(scene.p, 's', target='cyclist')
        report = built([0, 59], f0={'key:cyclist': key([0.4, 0.3, 0.6, 0.7])}, f59={'key:cyclist': key([0.75, 0.3, 0.95, 0.7])})
        held = {'posed_box': [0.4, 0.3, 0.6, 0.7], 'box': [0.41, 0.3, 0.61, 0.7]}
        rows = [{'at': 0.0, 'frame': 0, 'spans_m': [1.8, 0.6, 0.5], 'built': held},
                {'at': 1.0, 'frame': 59, 'spans_m': [1.8, 0.6, 0.5], 'built': {**held, 'box': [0.75, 0.3, 0.95, 0.7]}}]
        lines = ft.gate_lines(scene.p, scene.shot(), report, {}, {'frames': rows, 'subject_travel_m': 12.0})
        self.assertEqual([(line['code'], 't=1.0' in line['text']) for line in lines], [('MOVING_SUBJECT_OFF', True)])
        # a wobble of a few millimetres is not travel (its own length decides), and a part hidden at the picture moment is
        # not drift: both boxes are the same points with nothing hidden
        self.assertEqual(ft.gate_lines(scene.p, scene.shot(), report, {}, {'frames': rows, 'subject_travel_m': 0.005}), [])
        hidden = built([0, 59], f0={'key:cyclist': key([0.4, 0.3, 0.6, 0.7])}, f59={'key:cyclist': key([0.4, 0.3, 0.6, 0.4])})
        steady = [{**r, 'built': held} for r in rows]
        self.assertEqual(ft.gate_lines(scene.p, scene.shot(), hidden, {}, {'frames': steady, 'subject_travel_m': 12.0}), [])
        self.assertEqual(SOFTENABLE['MOVING_SUBJECT_OFF'][0], 'taste')

    def test_fog_over_the_frame_or_the_focus_is_said(self):
        """A machine on a factory floor in fog: built, the fog fills most of the frame."""
        scene = Scene(self, [element('press', 'subject', 'a hydraulic press on the factory floor', ['press'])], ['press'])
        scene.read([BASE, BASE])
        ft.apply(scene.p, 's', target='press')
        report = built([0, 59], f0={'key:press': key([0.4, 0.3, 0.6, 0.7])}, f59={'key:press': key([0.4, 0.3, 0.6, 0.7])})
        rows = [{'at': 0.0, 'frame': 0, 'built': {'atmosphere': {'frame_share': 0.72, 'focus_share': 0.1}}},
                {'at': 1.0, 'frame': 59, 'built': {'atmosphere': {'frame_share': 0.1, 'focus_share': 0.05}}}]
        lines = ft.gate_lines(scene.p, scene.shot(), report, {}, {'frames': rows})
        self.assertEqual([(line['code'], 't=0.0' in line['text']) for line in lines], [('ATMOSPHERE_COVERS', True)])
        self.assertEqual(SOFTENABLE['ATMOSPHERE_COVERS'][0], 'taste')
        self.assertEqual(SOFTENABLE['CAMERA_IN_ATMOSPHERE'][0], 'broken')


if __name__ == '__main__':
    unittest.main()
