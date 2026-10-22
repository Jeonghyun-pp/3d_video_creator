"""3D-first frame pictures (studio/picture3d.py, 2026-10-17): pictures painted over the shot's own 3D at its chosen pose,
the 3D's facts checked against what the lines claim, the review gate for generated clips (studio/video_review.py).
Scenes here are not the reel the pipeline came from (N+1): a drawbridge whose leaves open, a kettle on a hob, a
lighthouse. The image tool, the reader and Blender are mocked: nothing here paints, reads or renders."""
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest import mock

from PIL import Image

from studio import concept, picture3d, video_review
from studio import frame_targets as ft
from studio.blender_ops import part_state_core as ps
from studio.common import StudioError, read_json, write_json
from studio.project import load_shot, shot_path

from tests.test_frame_reading_checks import BASE, Scene, element


def bridge(test, claim_text='the two leaves swing up to let the ship through', about=('bridge/feat.leaves',)):
    """A drawbridge (two leaves) and a ship; the shot's one line speaks of the leaves."""
    scene = Scene(test, [element('bridge', 'subject', 'a drawbridge over a canal', ['bridge']),
                         element('ship', 'context', 'a cargo ship in the canal', ['ship'])], ['bridge', 'ship'])
    write_json(scene.p / 'subjects' / 'bridge' / 'spec.json', {
        'subject_id': 'bridge', 'identity': 'a drawbridge',
        'builders': [{'part_id': 'deck'}, {'part_id': 'leaf_l'}, {'part_id': 'leaf_r'}],
        'features': [{'id': 'feat.leaves', 'description': 'the two lifting leaves', 'part_ids': ['leaf_l', 'leaf_r']}]})
    shot = scene.shot()
    shot['subjects'] = [{'subject_id': 'bridge'}]
    write_json(shot_path(scene.p, 's'), shot)
    root = scene.p / 'decisions'
    write_json(root / 'facts.json', {'layer': 'facts', 'status': 'approved', 'body': {'claims': [
        {'claim_id': 'c', 'text': claim_text, 'about': list(about)}]}})
    write_json(root / 'script.json', {'layer': 'script', 'status': 'approved', 'body': {'lines': [
        {'line_id': 'l1', 'text': 'Then the bridge opens.', 'claim_ids': ['c']}]}})
    write_json(root / 'shotlist.json', {'layer': 'shotlist', 'status': 'approved', 'body': {'shots': [{'shot_id': 's', 'line_ids': ['l1']}]}})
    return scene


def reader(state='extended', count=2, calls=None):
    def read(images, prompt, schema, target):
        if calls is not None:
            calls.append(prompt)
        ids = schema['properties']['things']['items']['properties']['id']['enum']
        answer = {'things': [{'id': i, 'state': state, 'count': count} for i in ids]}
        write_json(target, {'answer': answer})
        return {'answer': answer}
    return read


def moment(scene, at, state, visible=('bridge/leaf_l', 'bridge/leaf_r'), thick=40):
    folder = picture3d._moment_dir(scene.p, 's', at)
    folder.mkdir(parents=True, exist_ok=True)
    parts = {p: {'extension': state, 'contact': 'unknown'} for p in ('bridge/leaf_l', 'bridge/leaf_r')}
    write_json(folder / 'state.json', {'frame': 0, 'things': {'bridge/feat.leaves': {'kind': 'feature', 'parts': parts, 'extension': state,
                                                                                    'contact': 'unknown'}}})
    write_json(folder / 'id.json', {'visible': {v: {'px': 4000, 'box': [0.4, 0.4, 0.6, 0.6], 'thick_px': thick} for v in visible},
                                    'row': {'shapes': {'subject': {'bbox': [0.3, 0.3, 0.7, 0.7]}}}})
    return folder


class PartStateTest(unittest.TestCase):
    """The 3D's own state of a part, from geometry: a leaf lying along its deck, a leaf swung up, a foot on a deck."""

    def deck(self):
        return [(x / 10.0, y / 10.0, 0.0) for x in range(0, 101, 5) for y in (-5, 0, 5)]   # 10 m long, 1 m wide, along x

    def test_a_part_lying_along_its_body_is_retracted_one_swung_out_is_extended(self):
        lying = [(x / 10.0, 0.6, 0.1) for x in range(20, 61)]                     # along the deck, at its edge
        swung = [(2.0, 0.6 + t / 10.0, t / 10.0 * 0.8) for t in range(0, 41)]       # up and out by most of its length
        self.assertEqual(ps.extension(lying, self.deck())[0], 'retracted')
        self.assertEqual(ps.extension(swung, self.deck())[0], 'extended')
        self.assertEqual(ps.extension([], self.deck())[0], 'unknown')

    def test_a_lowest_point_within_the_tolerance_touches_its_support(self):
        self.assertEqual(ps.contact(0.01, 0.0)[0], 'contact')
        self.assertEqual(ps.contact(-0.3, 0.0)[0], 'contact')                      # into it is touching too (SUBJECT_SINKS says the rest)
        self.assertEqual(ps.contact(3.7, 0.0), ('clear', 3.7))
        self.assertEqual(ps.contact(0.5, None)[0], 'unknown')

    def test_a_state_is_judged_only_against_a_measure_of_its_own_kind(self):
        self.assertTrue(ps.differs('contact', 'clear'))
        self.assertFalse(ps.differs('contact', 'retracted'))                     # how far it reaches says nothing of touching
        self.assertFalse(ps.differs('extended', 'extended'))
        self.assertFalse(ps.differs('open', 'unknown'))
        self.assertEqual([ps.kind(w) for w in ('deployed', 'landed', 'lit', 'closed', 'banana')], ['pose', 'contact', 'render', 'open', None])

    def test_words_for_states_map_into_one_vocabulary(self):
        self.assertEqual([ps.normal(w) for w in ('deployed', 'folded', 'firing', 'touching', 'banana')],
                         ['extended', 'retracted', 'on', 'contact', 'unknown'])
        self.assertEqual(ps.principal_axis(self.deck())[0] ** 2 > 0.9, True)


class LitPartTest(unittest.TestCase):
    """A part is lit by what is attached to it: the four lamps of a lamp post, two of them with a beam under them."""

    def lamps(self):
        return {f'post/lamp_{i}': ((i * 1.0, 0.0, 4.0), (i * 1.0 + 0.4, 0.4, 4.4)) for i in range(4)}

    def test_each_beam_goes_to_the_one_lamp_it_hangs_from(self):
        beams = {'beam_a': ((-0.3, -0.3, 0.0), (0.7, 0.7, 4.0)),       # under lamp 0, as wide as the gap to lamp 1
                 'beam_b': ((1.7, -0.3, 0.0), (2.7, 0.7, 4.0)),        # under lamp 2
                 'far_glow': ((9.0, 9.0, 9.0), (10.0, 10.0, 10.0)),    # touches nothing
                 'sky_glow': ((-200.0, -200.0, -1.0), (200.0, 200.0, 100.0))}   # touches every lamp, no one lamp's light
        got = ps.attach(self.lamps(), beams)
        self.assertEqual({k: v for k, v in got.items() if v}, {'post/lamp_0': ['beam_a'], 'post/lamp_2': ['beam_b']})

    def test_the_count_in_a_state_is_of_the_parts_in_it(self):
        parts = {'a': {'render': 'on'}, 'b': {'render': 'off'}, 'c': {'render': 'on'}, 'd': {'render': 'off'}}
        self.assertEqual(ps.in_state('lit', parts), 2)
        self.assertEqual(ps.in_state('off', parts), 2)
        self.assertIsNone(ps.in_state('extended', parts))                       # no part measured for reach
        self.assertIsNone(ps.in_state('banana', parts))


class PosesTest(unittest.TestCase):
    def sheet(self, scene):
        rows = [{'at': at, 'concept_id': cid, 'candidates': [
            {'n': 0, 'view': [10.0, 0.0], 'lens_mm': 35.0, 'eye': [0, -10, 2], 'aim': [0, 0, 1], 'roll_deg': 0.0, 'frame': f},
            {'n': 1, 'view': [10.0, 40.0], 'lens_mm': 24.0, 'eye': [6, -8, 2], 'aim': [0, 0, 1], 'roll_deg': 1.0, 'frame': f}]}
            for at, cid, f in ((0.0, 'c01', 0), (1.0, 'c02', 59))]
        write_json(scene.p / 'concepts' / 's' / 'candidates' / 'candidates.json', {'shot_id': 's', 'moments': rows})

    def test_the_users_pick_is_the_pose_else_the_engines_first(self):
        scene = bridge(self)
        scene.read([BASE, BASE])
        self.sheet(scene)
        store = ft._store(scene.p, 's')
        store['pictures']['c02']['chosen'] = {'view': [10.0, 40.0], 'lens_mm': 24.0, 'eye': [6, -8, 2], 'aim': [0, 0, 1], 'candidate': 1,
                                              'by': 'user', 'user_words': 'the other side'}
        write_json(ft._store_path(scene.p, 's'), store)
        first, last = picture3d.poses(scene.p, 's')
        self.assertEqual((first['candidate'], first['by'], first['frame']), (0, 'agent', 0))
        self.assertEqual((last['candidate'], last['by'], last['lens_mm'], last['roll_deg']), (1, 'user', 24.0, 1.0))

    def test_a_pick_made_after_a_run_lands_on_the_moments_current_picture_and_is_carried_on(self):
        from studio import frames_check
        scene = bridge(self)
        scene.read([BASE, BASE])
        self.sheet(scene)

        def paint_record(n):
            folder = moment(scene, 0.0, 'extended')
            Image.new('RGB', (72, 128)).save(folder / 'clay.png')
            Image.new('RGB', (72, 128)).save(folder / f'registered_{n}.png')
            write_json(folder / 'pose.json', {**picture3d.poses(scene.p, 's')[0]})
            return picture3d.record(scene.p, 's', 0.0, n)['concept_id']
        first = paint_record(1)                                                   # the run: c01 -> a painted picture
        picked = frames_check.pick(scene.p, 's', '0.0', 1, 'the other side, from the bank')
        self.assertEqual(picked['concept_id'], first)                             # on the picture the moment shows now
        pose = picture3d.poses(scene.p, 's')[0]
        self.assertEqual((pose['concept_id'], pose['candidate'], pose['by'], pose['roll_deg']), (first, 1, 'user', 1.0))
        second = paint_record(2)                                                  # painted again at the pick
        chosen = ft._store(scene.p, 's')['pictures'][second]['chosen']
        self.assertEqual((chosen['by'], chosen['user_words'], chosen['candidate']), ('user', 'the other side, from the bank', 1))
        third = paint_record(1)                                                   # and again: the pick, two pictures back, still holds
        self.assertEqual(ft.user_pick(scene.p, 's', third)['user_words'], 'the other side, from the bank')

    def test_a_pick_the_sheet_no_longer_holds_is_refused_not_posed_from_another_candidate(self):
        scene = bridge(self)
        scene.read([BASE, BASE])
        self.sheet(scene)
        store = ft._store(scene.p, 's')
        store['pictures']['c01']['chosen'] = {'view': [10.0, 90.0], 'lens_mm': 50.0, 'eye': [9, 0, 2], 'aim': [0, 0, 1], 'candidate': 4,
                                              'by': 'user', 'user_words': 'from the far side'}
        write_json(ft._store_path(scene.p, 's'), store)
        with self.assertRaises(StudioError) as caught:
            picture3d.poses(scene.p, 's')
        self.assertIn('not on the candidates sheet', caught.exception.message)

    def test_no_sheet_says_how_to_make_one(self):
        scene = bridge(self)
        with self.assertRaises(StudioError) as caught:
            picture3d.poses(scene.p, 's')
        self.assertIn('frames candidates', caught.exception.recovery)


class ClaimsTest(unittest.TestCase):
    def test_the_lines_state_and_count_are_read_once_and_a_mark_corrects_them(self):
        scene = bridge(self)
        calls = []
        out = picture3d.claims(scene.p, 's', reader=reader(calls=calls))
        leaves = out['things']['bridge/feat.leaves']
        self.assertEqual((leaves['state'], leaves['count'], leaves['by'], leaves['owner']), ('extended', 2, 'reader', 'bridge'))
        self.assertEqual(leaves['parts'], ['bridge/leaf_l', 'bridge/leaf_r'])
        self.assertIn('swing up', calls[0])
        picture3d.claims(scene.p, 's', reader=reader(calls=calls))
        self.assertEqual(len(calls), 1)                                          # cached by the texts
        marked = picture3d.mark_claim(scene.p, 's', 'bridge/feat.leaves', state='retracted', note='read the line again by eye')
        self.assertEqual((marked['things']['bridge/feat.leaves']['state'], marked['things']['bridge/feat.leaves']['by']), ('retracted', 'mark'))
        with self.assertRaises(StudioError):
            picture3d.mark_claim(scene.p, 's', 'bridge/feat.leaves', state='open', note='')


class ClaimTextTest(unittest.TestCase):
    def test_only_claims_about_exactly_the_thing_and_their_lines_are_its_texts(self):
        scene = bridge(self, about=('bridge/feat.leaves_old',))                  # a near id is another thing
        root = scene.p / 'decisions'
        facts = read_json(root / 'facts.json')
        facts['body']['claims'].append({'claim_id': 'd', 'text': 'the leaves rise in a minute', 'about': ['bridge/feat.leaves']})
        write_json(root / 'facts.json', facts)
        script = read_json(root / 'script.json')
        script['body']['lines'] = [{'line_id': 'l1', 'text': 'Then the bridge opens.', 'claim_ids': ['c']},
                                   {'line_id': 'l2', 'text': 'In one minute it is up.', 'claim_ids': ['d']}]
        write_json(root / 'script.json', script)
        shotlist = read_json(root / 'shotlist.json')
        shotlist['body']['shots'][0]['line_ids'] = ['l1', 'l2']
        write_json(root / 'shotlist.json', shotlist)
        texts = picture3d._claim_texts(scene.p, load_shot(scene.p, 's'), 'bridge/feat.leaves')
        self.assertEqual(texts, ['the leaves rise in a minute', 'In one minute it is up.'])


class CheckTest(unittest.TestCase):
    def facts(self, scene, *moments, count=2):
        picture3d.claims(scene.p, 's', reader=reader(count=count))
        for at, state, *rest in moments:
            moment(scene, at, state, *rest)
        return picture3d.check(scene.p, 's')

    def test_a_claimed_state_no_moment_has_is_an_error(self):
        out = self.facts(bridge(self), (0.0, 'retracted'), (1.0, 'retracted'))
        self.assertEqual([f['code'] for f in out['gate_failures']], ['PICTURE3D_STATE_DIFFERS'])

    def test_a_state_the_shot_changes_is_met_by_one_moment(self):
        out = self.facts(bridge(self), (0.0, 'retracted'), (1.0, 'extended'))
        self.assertEqual(out['gate_failures'], [])

    def state(self, scene, at, row):
        folder = moment(scene, at, 'unknown')
        write_json(folder / 'state.json', {'frame': 0, 'things': {'bridge/feat.leaves': {'kind': 'feature', 'parts': {}, **row}}})

    def test_a_claimed_contact_is_judged_by_contact_alone(self):
        scene = bridge(self)
        picture3d.claims(scene.p, 's', reader=reader(state='contact'))
        self.state(scene, 0.0, {'extension': 'retracted', 'contact': 'unknown', 'render': 'on'})
        self.assertEqual(picture3d.check(scene.p, 's')['gate_failures'], [])     # contact not measured: never judged
        self.state(scene, 0.0, {'extension': 'retracted', 'contact': 'clear', 'render': 'on'})
        self.assertEqual([f['code'] for f in picture3d.check(scene.p, 's')['gate_failures']], ['PICTURE3D_STATE_DIFFERS'])

    def test_a_claimed_open_nothing_measures_is_never_judged(self):
        scene = bridge(self)
        picture3d.claims(scene.p, 's', reader=reader(state='open'))
        self.state(scene, 0.0, {'extension': 'retracted', 'contact': 'clear', 'render': 'on'})
        self.assertEqual(picture3d.check(scene.p, 's')['gate_failures'], [])

    def test_a_moment_the_shot_no_longer_pictures_is_not_checked(self):
        scene = bridge(self)
        scene.read([BASE, BASE])
        PosesTest().sheet(scene)                                                  # pictured: 0.00 and 1.00
        picture3d.claims(scene.p, 's', reader=reader())
        moment(scene, 0.0, 'extended')
        moment(scene, 0.5, 'retracted')                                           # an older moment's folder, left on disk
        out = picture3d.check(scene.p, 's')
        self.assertEqual(out['gate_failures'], [])
        self.assertEqual([m['at'] for m in out['things'][0]['moments']], ['0.00'])

    def lit(self, scene, at, on, in_frame=True):
        folder = moment(scene, at, 'unknown')
        parts = {'bridge/leaf_l': {'render': 'on' if on > 0 else 'off'}, 'bridge/leaf_r': {'render': 'on' if on > 1 else 'off'}}
        write_json(folder / 'state.json', {'frame': 0, 'things': {'bridge/feat.leaves': {
            'kind': 'feature', 'parts': parts, 'render': 'on' if on else 'off', 'in_frame': in_frame}}})

    def test_a_count_is_of_the_parts_in_the_claimed_state_not_of_all_built(self):
        scene = bridge(self)
        picture3d.claims(scene.p, 's', reader=reader(state='lit', count=1))     # "one leaf lit" of two built
        self.lit(scene, 0.0, 1)
        self.assertEqual(picture3d.check(scene.p, 's')['gate_failures'], [])
        self.lit(scene, 0.0, 2)
        self.assertEqual([f['code'] for f in picture3d.check(scene.p, 's')['gate_failures']], ['PICTURE3D_COUNT_DIFFERS'])
        self.lit(scene, 0.0, 0)                                                   # none lit: the state differs, said once
        self.assertEqual([f['code'] for f in picture3d.check(scene.p, 's')['gate_failures']], ['PICTURE3D_STATE_DIFFERS'])

    def test_a_lit_part_out_of_frame_is_lit_and_not_seen(self):
        scene = bridge(self)
        picture3d.claims(scene.p, 's', reader=reader(state='lit', count=1))
        folder = picture3d._moment_dir(scene.p, 's', 0.0)
        self.lit(scene, 0.0, 1, in_frame=False)
        write_json(folder / 'id.json', {'visible': {}, 'row': {'shapes': {}}})   # nothing of it on screen
        codes = [f['code'] for f in picture3d.check(scene.p, 's')['gate_failures']]
        self.assertNotIn('PICTURE3D_STATE_DIFFERS', codes)
        self.assertEqual(codes, ['PICTURE3D_CLAIM_UNSEEN'])

    def test_a_claimed_thing_seen_at_no_moment_and_another_number_are_errors(self):
        out = self.facts(bridge(self), (0.0, 'extended', (), 0), (1.0, 'extended', ('bridge/leaf_l',), 4), count=3)
        self.assertEqual(sorted(f['code'] for f in out['gate_failures']), ['PICTURE3D_CLAIM_UNSEEN', 'PICTURE3D_COUNT_DIFFERS'])


class MomentTest(unittest.TestCase):
    """One moment within PAINT_MAX paintings: moved too far -> painted again; read wrong -> painted again with the
    finding; nothing registers -> the clay stands in."""

    def steps(self, fits, findings):
        calls = []

        def paint(project, shot_id, at, attempt, finding):
            calls.append(('paint', attempt, finding))

        def register(project, shot_id, at, attempt):
            return fits[attempt - 1]

        def read(project, shot_id, at, attempt):
            return {'findings': findings[attempt - 1]}

        def record(project, shot_id, at, attempt, fallback=False):
            calls.append(('record', attempt, fallback))
            return {'concept_id': 'c03', 'source': 'clay_fallback' if fallback else 'paint_over_3d'}
        return calls, {'paint': paint, 'register': register, 'read': read, 'record': record}

    def test_a_painting_moved_too_far_is_painted_again(self):
        calls, steps = self.steps([{'over_limit': True, 'worst_share': 0.2}, {'over_limit': False, 'worst_share': 0.06}], [[], []])
        row = picture3d._moment('p', 's', 0.0, {}, steps)
        self.assertEqual(calls, [('paint', 1, None), ('paint', 2, None), ('record', 2, False)])
        self.assertEqual(row['source'], 'paint_over_3d')

    def test_a_painting_read_wrong_is_painted_again_with_what_it_got_wrong(self):
        wrong = [{'code': 'PICTURE_PAINT_STATE', 'text': 'leaves painted retracted, the 3D has them extended'}]
        calls, steps = self.steps([{'over_limit': False}, {'over_limit': False}], [wrong, []])
        picture3d._moment('p', 's', 0.0, {}, steps)
        self.assertEqual(calls[1], ('paint', 2, wrong[0]['text']))
        self.assertEqual(calls[-1], ('record', 2, False))

    def test_no_painting_that_registers_leaves_the_clay(self):
        calls, steps = self.steps([{'over_limit': True}, {'over_limit': True}], [[], []])
        row = picture3d._moment('p', 's', 0.0, {}, steps)
        self.assertEqual(calls[-1], ('record', picture3d.PAINT_MAX, True))
        self.assertEqual(row['findings'][0]['code'], 'PICTURE_PAINT_SHIFTED')


class RecordTest(unittest.TestCase):
    def test_a_painting_becomes_the_moments_picture_read_from_the_3d_with_the_pick_and_cheats_carried(self):
        scene = bridge(self)
        scene.read([BASE, BASE])
        store = ft._store(scene.p, 's')
        store['pictures']['c01']['chosen'] = {'view': [12.0, 30.0], 'lens_mm': 24.0, 'by': 'user', 'user_words': 'from the bank'}
        store['pictures']['c01']['cheats'] = [{'kind': 'lens', 'target': None, 'value': 24.0}]
        store['pictures']['c01']['departs'] = [{'item': 'angle'}]
        write_json(ft._store_path(scene.p, 's'), store)
        folder = moment(scene, 0.0, 'extended')
        Image.new('RGB', (72, 128), (128, 128, 128)).save(folder / 'clay.png')
        Image.new('RGB', (72, 128), (90, 110, 140)).save(folder / 'registered_1.png')
        write_json(folder / 'pose.json', {'at': 0.0, 'frame': 0, 'eye': [0, -9, 2], 'aim': [0, 0, 1], 'lens_mm': 24.0, 'view': [12.0, 30.0],
                                          'roll_deg': 0.0, 'candidate': 1, 'by': 'user'})
        made = picture3d.record(scene.p, 's', 0.0, 1)
        record = read_json(scene.p / 'concepts' / 's' / f"{made['concept_id']}.json")
        self.assertEqual((record['source'], record['proposal']['by'], record['derived_from']['supersedes']), ('paint_over_3d', 'agent', 'c01'))
        row = ft._store(scene.p, 's')['pictures'][made['concept_id']]
        self.assertEqual(row['provenance']['kind'], 'derived_passes')
        self.assertEqual((row['chosen']['by'], row['cheats'][0]['kind']), ('user', 'lens'))   # the user's pick and the cheats carried
        self.assertNotIn('departs', row)                                          # it matches the 3D: no departure left
        self.assertFalse((scene.p / 'decisions' / 'concept').exists())           # nothing picked
        start = [t for t in ft.targets(scene.p, 's') if t['at'] == 0.0][0]
        self.assertEqual(start['concept_id'], made['concept_id'])

    def test_without_a_users_pick_the_agent_pins_the_pose_it_painted_over(self):
        scene = bridge(self)
        scene.read([BASE, BASE])
        folder = moment(scene, 1.0, 'extended')
        Image.new('RGB', (72, 128)).save(folder / 'clay.png')
        Image.new('RGB', (72, 128)).save(folder / 'registered_2.png')
        write_json(folder / 'pose.json', {'at': 1.0, 'frame': 59, 'eye': [0, -9, 2], 'aim': [0, 0, 1], 'lens_mm': 35.0, 'view': [8.0, 0.0],
                                          'roll_deg': 0.0, 'candidate': 0, 'by': 'agent'})
        made = picture3d.record(scene.p, 's', 1.0, 2)
        chosen = ft._store(scene.p, 's')['pictures'][made['concept_id']]['chosen']
        self.assertEqual((chosen['by'], chosen['candidate'], chosen['lens_mm']), ('agent', 0, 35.0))
        self.assertIn('painted over', chosen['agent_note'])


    def test_the_painting_keeps_the_lean_the_3d_had_and_the_pose_where_its_subject_stood(self):
        # an author may pose the subject by its pictures' lean: the moment's lean the passes were made with stays the
        # picture's, and the pin keeps where the subject stood and how it was placed
        scene = bridge(self)
        scene.read([BASE, BASE])
        shot = load_shot(scene.p, 's')
        shot['camera'].update(movement='rig', keys=[])
        shot['camera']['move'] = {'type': 'frame_to_frame', 'params': {'target': 'bridge'},
                                  'frames': [{'at': 0.0, 'height_share': 0.5, 'centre': [0.5, 0.5], 'view': {'elevation_deg': 8.0},
                                              'lens_mm': 35.0, 'tilt_deg': -20.0},
                                             {'at': 1.0, 'height_share': 0.5, 'centre': [0.5, 0.5], 'view': {'elevation_deg': 8.0},
                                              'lens_mm': 35.0}]}
        write_json(shot_path(scene.p, 's'), shot)
        folder = moment(scene, 0.0, 'extended')
        Image.new('RGB', (72, 128)).save(folder / 'clay.png')
        Image.new('RGB', (72, 128)).save(folder / 'registered_1.png')
        write_json(folder / 'pose.json', {'at': 0.0, 'frame': 0, 'eye': [0, -9, 2], 'aim': [0, 0, 1], 'lens_mm': 35.0, 'view': [8.0, 0.0],
                                          'roll_deg': 0.0, 'candidate': 0, 'by': 'agent', 'subject_at': [0.0, 0.0, 1.0],
                                          'placement': {'aim': 'parts', 'closer': 1.0}, 'scene': 'shots/s/versions/a/authored.blend'})
        made = picture3d.record(scene.p, 's', 0.0, 1)
        row = ft._store(scene.p, 's')['pictures'][made['concept_id']]
        self.assertEqual(row['read']['subject_tilt_deg'], -20.0)
        self.assertEqual((row['chosen']['subject_at'], row['chosen']['placement']['aim'], row['chosen']['scene']),
                         ([0.0, 0.0, 1.0], 'parts', 'shots/s/versions/a/authored.blend'))

    def test_a_moment_the_shot_list_opens_is_read_as_opened_that_way(self):
        # the passes are rendered with the moment's opening applied, so its painting shows the subject that way
        scene = bridge(self)
        scene.read([BASE, BASE])
        shot = load_shot(scene.p, 's')
        shot['show'] = [{'at': 'end', 'parts': ['bridge/leaf_l'], 'how': 'cutaway', 'why': 'the counterweight inside'}]
        write_json(shot_path(scene.p, 's'), shot)
        folder = moment(scene, 1.0, 'extended')
        Image.new('RGB', (72, 128)).save(folder / 'clay.png')
        Image.new('RGB', (72, 128)).save(folder / 'registered_1.png')
        write_json(folder / 'pose.json', {'at': 1.0, 'frame': 59, 'eye': [0, -9, 2], 'aim': [0, 0, 1], 'lens_mm': 35.0, 'view': [8.0, 0.0],
                                          'roll_deg': 0.0, 'candidate': 0, 'by': 'agent'})
        made = picture3d.record(scene.p, 's', 1.0, 1)
        read = ft._store(scene.p, 's')['pictures'][made['concept_id']]['read']
        self.assertEqual((read['how'], read['opened']), ('cutaway', ['bridge/leaf_l']))

    def test_the_opening_the_passes_applied_is_how_the_picture_shows_it(self):
        # a moment the shot list names no row for, inside an opening still held from the start: the passes applied it
        scene = bridge(self)
        scene.read([BASE, BASE])
        folder = moment(scene, 1.0, 'extended')
        info = read_json(folder / 'id.json')
        write_json(folder / 'id.json', {**info, 'opening': {'how': 'section', 'parts': ['bridge/leaf_r'], 'opened': ['bridge/leaf_r']}})
        Image.new('RGB', (72, 128)).save(folder / 'clay.png')
        Image.new('RGB', (72, 128)).save(folder / 'registered_1.png')
        write_json(folder / 'pose.json', {'at': 1.0, 'frame': 59, 'eye': [0, -9, 2], 'aim': [0, 0, 1], 'lens_mm': 35.0, 'view': [8.0, 0.0],
                                          'roll_deg': 0.0, 'candidate': 0, 'by': 'agent'})
        made = picture3d.record(scene.p, 's', 1.0, 1)
        read = ft._store(scene.p, 's')['pictures'][made['concept_id']]['read']
        self.assertEqual((read['how'], read['opened']), ('section', ['bridge/leaf_r']))   # not the shot list's (none here)

    def test_the_clay_standing_in_carries_no_prompt_or_fit_of_the_painting_that_failed(self):
        scene = bridge(self)
        scene.read([BASE, BASE])
        folder = moment(scene, 0.0, 'extended')
        Image.new('RGB', (72, 128)).save(folder / 'clay.png')
        write_json(folder / 'paint.json', {'2': {'prompt': 'the failed painting'}})
        write_json(folder / 'register.json', {'paint_2.png': {'scale': 1.3, 'shift_share': [0.2, 0], 'worst_share': 0.3, 'subject_f1': 0.2}})
        write_json(folder / 'pose.json', {'at': 0.0, 'frame': 0, 'eye': [0, -9, 2], 'aim': [0, 0, 1], 'lens_mm': 35.0, 'view': [8.0, 0.0],
                                          'roll_deg': 0.0, 'candidate': 0, 'by': 'agent'})
        made = picture3d.record(scene.p, 's', 0.0, picture3d.PAINT_MAX, fallback=True)
        record = read_json(scene.p / 'concepts' / 's' / f"{made['concept_id']}.json")
        self.assertEqual((record['source'], record['prompt']), ('clay_fallback', None))
        self.assertNotIn('register', record)


class PaintTest(unittest.TestCase):
    """One image request: the painting its --json events name, a failed or timed-out call none, no codex said."""

    def folder(self):
        scene = bridge(self)
        folder = picture3d._moment_dir(scene.p, 's', 0.0)
        folder.mkdir(parents=True)
        for name in ('clay.png', 'depth.png', 'lines.png'):
            Image.new('RGB', (72, 128)).save(folder / name)
        return scene, folder

    def run_paint(self, scene, runner):
        with mock.patch.object(picture3d, '_style', lambda *a: None), mock.patch.object(picture3d, '_newest', lambda after: None):
            return picture3d.paint(scene.p, 's', 0.0, runner=runner)

    def test_the_painting_is_the_one_the_events_name(self):
        scene, folder = self.folder()
        made = scene.p / 'made.png'

        def runner(cmd, cwd, timeout):
            self.assertEqual((cmd[1:3], cmd[cmd.index('-C') + 1]), (['exec', '--json'], cwd))
            Image.new('RGB', (64, 96), (200, 10, 10)).save(made)
            return {'returncode': 0, 'stdout': '{"type":"item.completed","item":{"type":"image","path":"%s"}}\n' % made,
                    'stderr': '', 'timed_out': False}
        out = self.run_paint(scene, runner)
        with Image.open(out['painting']) as painted:
            self.assertEqual(painted.size, (64, 96))
        self.assertEqual(read_json(folder / 'paint.json')['1']['found_by'], 'event')

    def test_a_claimed_count_is_told_to_the_painter(self):
        # a drawbridge whose lines raise exactly 2 of its 4 lamps: "lamps: on" alone lets the painter light all four
        scene, folder = self.folder()
        claims = picture3d._folder(scene.p, 's') / 'claims.json'
        write_json(claims, {'things': {'bridge/feat.lamps': {'state': 'on', 'count': 2}, 'bridge/deck': {'state': 'unknown'}}})
        seen = {}
        made = scene.p / 'made.png'

        def runner(cmd, cwd, timeout):
            seen['prompt'] = cmd[-1]
            Image.new('RGB', (64, 96)).save(made)
            return {'returncode': 0, 'stdout': '{"type":"item.completed","item":{"type":"image","path":"%s"}}\n' % made,
                    'stderr': '', 'timed_out': False}
        self.run_paint(scene, runner)
        prompt = read_json(folder / 'paint.json')['1']['prompt']
        self.assertIn('bridge/feat.lamps: on (exactly 2 of them, no more)', prompt)
        self.assertNotIn('bridge/deck', prompt)

    def test_a_failed_or_timed_out_call_makes_no_painting_even_if_an_image_appeared(self):
        scene, folder = self.folder()
        made = scene.p / 'made.png'
        for result in ({'returncode': 1, 'timed_out': False}, {'returncode': -9, 'timed_out': True}):
            def runner(cmd, cwd, timeout, result=result):
                Image.new('RGB', (64, 96)).save(made)
                return {**result, 'stdout': '{"path":"%s"}' % made, 'stderr': 'boom'}
            with self.assertRaises(StudioError) as caught:
                self.run_paint(scene, runner)
            self.assertEqual(caught.exception.code, 'PAINT_FAILED')
        self.assertFalse((folder / 'paint_1.png').exists())

    def test_no_codex_is_said_as_a_studio_error(self):
        scene, _folder = self.folder()

        def runner(cmd, cwd, timeout):
            raise FileNotFoundError(cmd[0])
        with self.assertRaises(StudioError) as caught:
            self.run_paint(scene, runner)
        self.assertIn('not installed', caught.exception.message)

    def test_a_timed_out_call_leaves_no_child_running(self):
        import time
        started = time.monotonic()
        out = picture3d._codex_image(['sh', '-c', 'sleep 30 & sleep 30; wait'], None, 0.5)
        self.assertTrue(out['timed_out'])
        self.assertLess(time.monotonic() - started, 10)                          # the whole group killed and waited for


class RunTest(unittest.TestCase):
    def test_a_failing_shot_does_not_stop_the_next_and_a_done_moment_is_not_painted_again(self):
        scene = bridge(self)
        painted = []
        poses = [{'at': 0.0, 'frame': 0, 'candidate': 0, 'by': 'agent', 'eye': [0, -9, 2], 'aim': [0, 0, 1], 'lens_mm': 35.0, 'view': [8, 0]}]

        def fake_moment(project, shot_id, at, ledger, steps):
            painted.append((shot_id, at))
            return {'concept_id': 'c03', 'source': 'paint_over_3d', 'findings': []}
        blend = scene.p / 'authored.blend'
        blend.write_bytes(b'one')
        from studio import frames_check
        with mock.patch.object(picture3d, 'poses', lambda path, shot_id: poses if shot_id == 's' else (_ for _ in ()).throw(StudioError('INPUT_INVALID', 'no shot'))), \
                mock.patch.object(picture3d, '_moment', fake_moment), \
                mock.patch.object(frames_check, 'authored_checkpoint', lambda path, shot_id: (blend, False, False)), \
                mock.patch.object(picture3d, 'check', lambda project, shot_id: {'gate_failures': [], 'warnings': []}):
            kw = dict(candidates_fn=lambda *a: None, passes_fn=lambda *a: None, claims_fn=lambda *a: None, apply_fn=lambda *a: None)
            (scene.p / 'concepts' / 's' / 'candidates').mkdir(parents=True, exist_ok=True)
            write_json(scene.p / 'concepts' / 's' / 'candidates' / 'candidates.json', {'moments': []})
            out = picture3d.run(scene.p, ['zz', 's'], **kw)
            self.assertIn('error', out['shots']['zz'])
            self.assertEqual(out['shots']['s']['moments']['0.00']['concept_id'], 'c03')
            picture3d.run(scene.p, ['s'], **kw)
            self.assertEqual(painted, [('s', 0.0)])                               # resumed: the same pose is not painted again
            poses[0]['concept_id'] = 'c03'                                        # the moment's picture is now the painted one
            picture3d.run(scene.p, ['s'], **kw)
            self.assertEqual(painted, [('s', 0.0)])                               # still the same pose: not painted again
            blend.write_bytes(b'two')                                             # the 3D changed since
            picture3d.run(scene.p, ['s'], **kw)
        self.assertEqual(painted, [('s', 0.0), ('s', 0.0)])
        self.assertTrue((scene.p / 'concepts' / 'picture3d_sheet.html').is_file())


class ChosenMotionTest(unittest.TestCase):
    """2026-10-22: a shot whose camera is a motion chosen as a motion (a recipe's move, not frame_to_frame) is pictured the
    other way round - its start, key moment and end posed where its built camera stands, no candidates, no apply - and a
    picture painted for another camera is said at build (FRAME_PICTURE_OFF_MOTION)."""

    def moving(self, scene):
        shot = load_shot(scene.p, 's')
        shot['camera'] = {**shot['camera'], 'movement': 'rig', 'move': {'type': 'pull_back', 'params': {'target': 'bridge'}}}
        shot['direction'] = {'move': {'recipe': 'look_up', 'reveals': 'the leaves overhead', 'key_at': 0.4, 'why': 'the bridge feels tall'}}
        shot['scene_version'] = 'v0003'
        write_json(shot_path(scene.p, 's'), shot)
        return shot

    def test_its_moments_are_posed_where_the_built_camera_stands(self):
        scene = bridge(self)
        shot = self.moving(scene)
        asked = []

        def measure(path, shot_id, version, frames, focus, out_dir, render=True):
            asked.append((version, list(frames), render))
            return {'frames': [{'frame': f, 'eye': [0.0, -10.0, float(f)], 'forward': [0.0, 1.0, 0.0], 'lens_mm': 24.0,
                                'focus_distance_m': 10.0} for f in frames]}
        rows = picture3d.built_poses(scene.p, 's', measure=measure)
        last = shot['duration_frames'] - 1
        self.assertEqual(asked, [('v0003', [0, round(0.4 * last), last], False)])   # read, not rendered
        self.assertEqual([r['at'] for r in rows], [0.0, 0.4, 1.0])                  # start, key, end
        self.assertEqual(rows[1]['aim'], [0.0, 0.0, float(round(0.4 * last))])
        self.assertEqual((rows[0]['by'], rows[0]['version']), ('build', 'v0003'))
        with mock.patch.object(picture3d, 'built_poses', lambda path, shot_id: rows):
            self.assertEqual(picture3d.poses(scene.p, 's'), rows)                   # no candidates sheet asked for

    def test_a_key_moment_gets_its_first_picture_and_an_older_camera_is_said(self):
        scene = bridge(self)
        scene.read([BASE, BASE])
        shot = self.moving(scene)
        self.assertTrue(ft.motion_lines(scene.p, shot)[0].startswith('FRAME_PICTURE_OFF_MOTION: s'))   # drawn for no motion
        folder = moment(scene, 0.4, 'extended')
        Image.new('RGB', (72, 128)).save(folder / 'clay.png')
        Image.new('RGB', (72, 128)).save(folder / 'registered_1.png')
        write_json(folder / 'pose.json', {'at': 0.4, 'frame': 24, 'eye': [0, -9, 2], 'aim': [0, 0, 1], 'lens_mm': 24.0, 'view': [12.0, 0.0],
                                          'roll_deg': 0.0, 'candidate': None, 'by': 'build', 'version': 'v0003'})
        made = picture3d.record(scene.p, 's', 0.4, 1)
        record = read_json(scene.p / 'concepts' / 's' / f"{made['concept_id']}.json")
        self.assertEqual((record['frame']['role'], record['motion']['move_sha256']), ('key', picture3d.motion_sha(shot)))
        off = ft.motion_lines(scene.p, shot)[0]
        self.assertNotIn(made['concept_id'], off.split('show another')[0])   # the one painted over this motion is not off
        self.assertIn('c01', off)


class GenerateOnlyTest(unittest.TestCase):
    def test_a_shot_whose_lines_claim_something_is_never_drawn_from_text_alone(self):
        scene = bridge(self)
        with self.assertRaises(StudioError) as caught:
            picture3d.generate_only(scene.p, 's', 'mood shot')
        self.assertEqual(caught.exception.code, 'PICTURE_GENERATED_CLAIMS')

    def test_a_shot_without_claims_may_within_the_reels_limit(self):
        scene = Scene(self, [element('sea', 'context', 'open sea at dusk', ['sea'])], ['sea'])
        out = picture3d.generate_only(scene.p, 's', 'an opening mood shot over the sea')
        self.assertTrue(out['generate_only'])
        for n in range(picture3d.GENERATE_ONLY_MAX):
            write_json(scene.p / 'concepts' / f'x{n}' / 'picture3d' / 'text_only.json', {'shot_id': f'x{n}'})
        with self.assertRaises(StudioError) as caught:
            picture3d.generate_only(scene.p, 's', 'again')
        self.assertEqual(caught.exception.code, 'PICTURE_GENERATED_LIMIT')


    def test_a_limit_of_zero_allows_no_shot_drawn_from_text(self):
        scene = Scene(self, [element('sea', 'context', 'open sea at dusk', ['sea'])], ['sea'])
        project = read_json(scene.p / 'project.json')
        project.setdefault('policy', {})['generate_only_max'] = 0
        write_json(scene.p / 'project.json', project)
        self.assertEqual(picture3d.generate_only_max(scene.p), 0)
        with self.assertRaises(StudioError) as caught:
            picture3d.generate_only(scene.p, 's', 'an opening mood shot over the sea')
        self.assertEqual(caught.exception.code, 'PICTURE_GENERATED_LIMIT')


class VideoReviewTest(unittest.TestCase):
    def clip(self, scene, name='take.mp4', colour='blue'):
        path = scene.p / name
        if shutil.which('ffmpeg'):
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=c={colour}:s=90x160:d=1', str(path)], check=True, timeout=60)
        else:
            path.write_bytes(colour.encode())
        return path

    def test_a_pass_is_the_users_words_bound_to_the_clip_and_the_narration(self):
        scene = bridge(self)
        write_json(scene.p / 'decisions' / 'ladder.json', {'requires': ['video_review']})
        clip = self.clip(scene)
        shot = load_shot(scene.p, 's')
        with self.assertRaises(StudioError) as caught:
            video_review.require(scene.p, shot, clip)
        self.assertEqual(caught.exception.code, 'VIDEO_REVIEW_MISSING')
        with self.assertRaises(StudioError):
            video_review.pass_(scene.p, 's', clip, agent_note='looks fine')        # not delegated
        video_review.pass_(scene.p, 's', clip, user_words='움직임 괜찮다 넘어가자')
        self.assertEqual(video_review.require(scene.p, shot, clip)['decision']['by'], 'user')
        other = self.clip(scene, 'take2.mp4', 'red')
        with self.assertRaises(StudioError):
            video_review.require(scene.p, shot, other)                              # another clip
        script = read_json(scene.p / 'decisions' / 'script.json')
        script['body']['lines'][0]['text'] = 'Then the bridge closes.'
        write_json(scene.p / 'decisions' / 'script.json', script)
        with self.assertRaises(StudioError):
            video_review.require(scene.p, shot, clip)                               # the narration changed

    def test_required_on_every_project_and_a_delegated_agent_may_pass(self):
        scene = bridge(self)
        clip = self.clip(scene)
        with self.assertRaises(StudioError) as caught:   # 2026-10-21: no longer only when the ladder lists it
            video_review.require(scene.p, load_shot(scene.p, 's'), clip)
        self.assertEqual(caught.exception.code, 'VIDEO_REVIEW_MISSING')
        project = read_json(scene.p / 'project.json')
        project['delegation'] = {'user_words': '알아서 해줘', 'scope': ['video_review'], 'recorded_at': '2026-10-17T00:00:00'}
        write_json(scene.p / 'project.json', project)
        self.assertEqual(video_review.pass_(scene.p, 's', clip, agent_note='motion matches the line')['decision']['by'], 'agent')
        self.assertIsNotNone(video_review.require(scene.p, load_shot(scene.p, 's'), clip))

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'needs ffmpeg')
    def test_the_sheet_puts_a_frame_at_each_line(self):
        scene = bridge(self)
        out = video_review.sheet(scene.p, 's', self.clip(scene))
        review = read_json(Path(out['artifacts'][1]))
        self.assertEqual([f['label'] for f in review['frames']], ['시작', '문장 1', '끝'])
        self.assertTrue(all(f['image'] for f in review['frames']))


if __name__ == '__main__':
    unittest.main()
