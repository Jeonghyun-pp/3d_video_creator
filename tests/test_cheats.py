"""Declared cheats (B, 2026-10-14; studio/cheat_core.py, blender_ops/cheat_table.py): the narration's facts stay true,
the pictures may cheat. Scenes here are not the reel the layer came from (N+1): a cafe whose diner sits behind a pillar,
a pallet of cans, a skateboarder drawn from a low angle."""
import unittest

from studio import cheat_core, frame_targets as ft
from studio.common import StudioError, read_json, write_json
from studio.gates import SOFTENABLE, severity_map
from studio.project import shot_path, validate_shot

from tests.test_frame_reading_checks import BASE, Scene, built, element, key


def say(scene, about, shot_id='s'):
    """Facts, a script line and the shot list entry: the shot's one line speaks of `about`."""
    root = scene.p / 'decisions'
    write_json(root / 'facts.json', {'layer': 'facts', 'status': 'approved', 'body': {'claims': [{'claim_id': 'c', 'text': 'x', 'about': about}]}})
    write_json(root / 'script.json', {'layer': 'script', 'status': 'approved', 'body': {'lines': [{'line_id': 'l1', 'text': 'x', 'claim_ids': ['c']}]}})
    write_json(root / 'shotlist.json', {'layer': 'shotlist', 'status': 'approved', 'body': {'shots': [{'shot_id': shot_id, 'line_ids': ['l1']}]}})


def cafe(test):
    return Scene(test, [element('diner', 'agent', 'a diner at a corner table', ['diner']),
                        element('pillar', 'set', 'a round pillar between the tables', ['pillar']),
                        element('room', 'context', 'a small corner cafe')], ['diner', 'pillar', 'room'])


def move_pillar(size=0.6):
    return {'kind': 'context_move', 'target': 'pillar', 'value': {'move_m': [0.5, 0.0, 0.0], 'turn_deg': 0.0, 'size_m': size}}


class TableTest(unittest.TestCase):
    def test_every_row_says_what_it_touches_its_limits_cost_and_mechanism(self):
        for kind, row in cheat_core.CHEATS.items():
            self.assertTrue({'cost', 'applies', 'targets', 'on_locked', 'limits', 'steps', 'touches'} <= set(row), kind)
        self.assertEqual(cheat_core.ordered()[:2], ['lens', 'view'])                 # the camera first, the scene after
        self.assertEqual(cheat_core.ordered()[-2:], ['context_scale', 'subject_turn'])

    def test_a_search_tries_every_kind_before_a_second_candidate_of_any(self):
        from studio.blender_ops.cheat_table import in_rounds
        offered = [{'kind': 'lens', 'value': 35.0}, {'kind': 'lens', 'value': 50.0}, {'kind': 'view', 'value': 1},
                   {'kind': 'see_neighbours', 'value': 'a'}, {'kind': 'see_neighbours', 'value': 'b'}, {'kind': 'context_move', 'value': 'c'}]
        self.assertEqual([(c['kind'], c['value']) for c in in_rounds(offered)],
                         [('lens', 35.0), ('view', 1), ('see_neighbours', 'a'), ('context_move', 'c'), ('lens', 50.0), ('see_neighbours', 'b')])
        self.assertEqual(cheat_core.CHEATS['view']['limits'], {'azimuth_deg': 36.0, 'elevation_deg': 20.0})   # VIEW_STEPS reused

    def test_values_stay_within_their_rows_limits(self):
        cv = cheat_core.check_value
        self.assertIsNone(cv('lens', 18.0)); self.assertIn('within', cv('lens', 85.0))
        self.assertIsNone(cv('view', {'azimuth_deg': -24.0})); self.assertIn('beyond', cv('view', {'elevation_deg': 30.0}))
        self.assertIsNone(cv('atmosphere_scale', 0.3)); self.assertIsNotNone(cv('atmosphere_scale', 0.2))
        self.assertIsNone(cv('context_scale', 2.0)); self.assertIsNotNone(cv('context_scale', 0.4))
        self.assertIsNone(cv('context_move', move_pillar()['value']))
        self.assertIn('more than', cv('context_move', move_pillar(size=0.3)['value']))
        self.assertIn('turns', cv('context_move', {'move_m': [0, 0, 0], 'turn_deg': 200.0, 'size_m': 1.0}))
        self.assertIsNone(cv('subject_turn', -45.0)); self.assertIsNotNone(cv('subject_turn', 60.0))
        self.assertIn('unknown', cv('state', 'legs out'))                         # no state cheat: a wrong state is redrawn


class LockedTest(unittest.TestCase):
    def test_a_pillar_no_line_speaks_of_may_move_one_a_line_speaks_of_may_not(self):
        scene = cafe(self)
        say(scene, ['diner'])
        lock = cheat_core.locked(scene.p, scene.shot())
        self.assertEqual(lock['ids'], ['diner'])
        self.assertIsNone(cheat_core.touches_locked(move_pillar(), lock))
        self.assertIn('lines speak of', cheat_core.touches_locked({**move_pillar(), 'target': 'diner'}, lock))
        self.assertIsNone(cheat_core.touches_locked({'kind': 'see_neighbours', 'target': ['diner'], 'value': 0.55}, lock))   # no fact changes
        say(scene, ['pillar'])                                                    # "the pillar holds up the old roof"
        lock = cheat_core.locked(scene.p, scene.shot())
        self.assertIn('lines speak of', cheat_core.touches_locked(move_pillar(), lock))
        self.assertIn('pillar', cheat_core.touches_locked({'kind': 'context_scale', 'target': 'pillar.02', 'value': 0.5}, lock))   # a copy of it too

    def test_a_built_subjects_parts_keep_their_places_and_its_pose_is_free_until_a_line_speaks_of_it(self):
        scene = Scene(self, [element('cans', 'subject', 'a pallet of nine cans', ['cans'])], ['cans'])
        write_json(scene.p / 'subjects' / 'cans' / 'spec.json', {'subject_id': 'cans', 'identity': 'nine cans on a pallet',
                                                                  'builders': [{'part_id': f'c{n}'} for n in range(9)], 'features': []})
        shot = scene.shot(); shot['subjects'] = [{'subject_id': 'cans'}]
        lock = cheat_core.locked(scene.p, shot)
        self.assertIn('spec fixes', cheat_core.touches_locked({'kind': 'context_move', 'target': 'cans/c4',
                                                               'value': {'move_m': [0.1, 0, 0], 'size_m': 1.0}}, lock))
        whole = {'kind': 'context_move', 'target': 'cans', 'value': {'move_m': [0.1, 0, 0], 'size_m': 1.0}}
        self.assertIsNone(cheat_core.touches_locked(whole, lock))                  # the whole pallet may move, unsaid
        self.assertIsNone(cheat_core.touches_locked({**whole, 'target': 'fill-cans.03'}, lock))   # a whole fill copy too
        self.assertIn('spec fixes', cheat_core.touches_locked({'kind': 'subject_turn', 'target': 'cans/c4', 'value': 10.0}, lock))
        turn = {'kind': 'subject_turn', 'target': 'cans', 'value': 22.5}
        self.assertIsNone(cheat_core.touches_locked(turn, lock))
        from studio.blender_ops.cheat_table import held_locked
        say(scene, ['cans/c4'])                                                   # moving the pallet carries the said can
        self.assertIn('cans/c4', held_locked('context_move', ['cans', 'cans/c4'], cheat_core.locked(scene.p, shot)))
        self.assertIsNone(held_locked('subject_turn', ['cans', 'cans/c4'], cheat_core.locked(scene.p, shot)))
        say(scene, ['other'])
        say(scene, ['cans'])
        self.assertIn('pose', cheat_core.touches_locked(turn, cheat_core.locked(scene.p, shot)))
        say(scene, ['cans/c4'])   # a line about one can's label: its state is said, the pallet's pose is not
        lock = cheat_core.locked(scene.p, shot)
        self.assertEqual(lock['state'], ['cans/c4'])
        self.assertIsNone(cheat_core.touches_locked(turn, lock))

    def test_frames_apply_carries_a_cheat_and_refuses_one_on_what_a_line_speaks_of(self):
        scene = cafe(self)
        scene.read([BASE, BASE])
        store = ft._store(scene.p, 's')
        store['pictures']['c02']['cheats'] = [cheat_core.entry('context_move', 'pillar', move_pillar()['value'], 'the diner behind it',
                                                               'agent', '2026-10-14')]
        write_json(scene.p / 'concepts' / 's' / 'frames.json', store)
        say(scene, ['diner'])
        move = ft.apply(scene.p, 's', target='diner')['move']
        self.assertEqual([c['kind'] for c in move['frames'][1]['cheats']], ['context_move'])
        validate_shot(scene.shot())                                               # the schema carries it
        self.assertEqual(cheat_core.touch_lines(scene.p, scene.shot()), [])
        say(scene, ['pillar'])
        with self.assertRaises(StudioError) as refused:
            ft.apply(scene.p, 's', target='diner')
        self.assertEqual(refused.exception.code, 'CHEAT_TOUCHES_LOCKED')
        self.assertNotIn('CHEAT_TOUCHES_LOCKED', SOFTENABLE)                    # never softened: the facts stay true
        with self.assertRaises(StudioError):
            severity_map({'policy': {'gates': {'CHEAT_TOUCHES_LOCKED': 'warn'}}})
        shot = scene.shot(); shot['camera']['move']['frames'][1]['cheats'][0]['value']['move_m'] = [5.0, 0, 0]
        self.assertEqual([c['code'] for c in cheat_core.touch_lines(scene.p, shot)], ['CHEAT_INVALID'])
        shot['camera']['move']['frames'][1]['cheats'] = [{'kind': 'context_scale', 'target': None, 'value': 0.5}]
        self.assertIn('names no target', cheat_core.touch_lines(scene.p, shot)[0]['text'])


class ViewpointPolicyTest(unittest.TestCase):
    def test_the_users_delegation_lets_the_angle_go_without_words_and_still_counts(self):
        scene = cafe(self)
        scene.read([BASE, BASE])
        with self.assertRaises(StudioError):
            ft.depart(scene.p, 's', 'c01', 'angle', 'the diner reads better from the side')
        project = read_json(scene.p / 'project.json')
        project['policy'] = {'viewpoint_cheat': {'by': 'agent', 'user_words': 'the agent chooses the angle'}}
        write_json(scene.p / 'project.json', project)
        departs = ft.depart(scene.p, 's', 'c01', 'angle', 'the diner reads better from the side')['departs']
        self.assertEqual(departs[0]['by_policy'], 'viewpoint_cheat')
        policy = cheat_core.viewpoint_policy(scene.p)
        self.assertFalse(ft.abandoned(departs, policy))
        self.assertTrue(ft.abandoned(departs, None))                               # the policy taken back: no longer allowed
        ft.depart(scene.p, 's', 'c01', 'size', 'the diner a little smaller than drawn')
        with self.assertRaises(StudioError):                                      # counted: a third is past the limit
            ft.depart(scene.p, 's', 'c01', 'place', 'the diner left of where drawn')


class PolicyCommandTest(unittest.TestCase):
    def test_only_the_users_own_words_set_the_viewpoint_policy_and_it_says_when(self):
        scene = cafe(self)
        for words in (None, 'ok', 'User (paraphrased): do what you like'):
            with self.assertRaises(StudioError):
                cheat_core.set_viewpoint_policy(scene.p, words)
        self.assertIsNone(cheat_core.viewpoint_policy(scene.p))
        cheat_core.set_viewpoint_policy(scene.p, 'pick the angle yourself')
        policy = cheat_core.viewpoint_policy(scene.p)
        self.assertEqual((policy['by'], policy['user_words']), ('agent', 'pick the angle yourself'))
        self.assertTrue(policy['at'])


class UncheatTest(unittest.TestCase):
    def test_a_recorded_cheat_comes_off_its_picture_by_kind_or_all(self):
        from studio import frames_check
        scene = cafe(self)
        scene.read([BASE, BASE])
        store = ft._store(scene.p, 's')
        store['pictures']['c02']['cheats'] = [cheat_core.entry('lens', None, 24.0, 'x', 'agent', 'now'),
                                              cheat_core.entry('context_move', 'pillar', move_pillar()['value'], 'x', 'agent', 'now')]
        write_json(ft._store_path(scene.p, 's'), store)
        out = frames_check.uncheat(scene.p, 's', 'c02', ['lens'])
        self.assertEqual(([c['kind'] for c in out['removed']], [c['kind'] for c in out['cheats']]), (['lens'], ['context_move']))
        frames_check.uncheat(scene.p, 's', 'c02')
        self.assertEqual(ft._store(scene.p, 's')['pictures']['c02']['cheats'], [])
        with self.assertRaises(StudioError):
            frames_check.uncheat(scene.p, 's', 'c09')


class CheckpointTest(unittest.TestCase):
    def test_a_checkpoint_stands_until_what_made_it_changes(self):
        from studio import frames_check
        from studio.world import lit, load as load_world
        scene = cafe(self)
        author = scene.p / 'author' / 'cafe_author.py'
        author.parent.mkdir(parents=True, exist_ok=True)
        author.write_text('print("tables")\n')
        shot = scene.shot()
        shot['author'] = 'author/cafe_author.py'
        write_json(shot_path(scene.p, 's'), shot)
        folder = shot_path(scene.p, 's').parent / 'versions' / 'v0001'
        folder.mkdir(parents=True)
        (folder / 'authored.blend').write_bytes(b'blend')
        (folder / 'author.py').write_text(author.read_text())
        write_json(folder / 'author_job.json', {'shot': {**lit(shot, load_world(scene.p)), 'scene_version': 'v0001', 'revision': 9}})
        blend = frames_check.latest_authored(scene.p, 's')
        self.assertEqual(frames_check.checkpoint_stale(scene.p, scene.shot(), blend), [])   # written back by a build: not a change
        author.write_text('print("tables and a pillar")\n')
        self.assertEqual(frames_check.checkpoint_stale(scene.p, scene.shot(), blend), ['the author script author/cafe_author.py changed'])
        (folder / 'author.py').write_text(author.read_text())
        shot['duration_frames'] = shot['duration_frames'] + 12
        write_json(shot_path(scene.p, 's'), shot)
        self.assertIn('duration_frames', frames_check.checkpoint_stale(scene.p, scene.shot(), blend)[0])
        # a first build that failed never wrote its author back to the shot: the script its job ran is compared
        del shot['author']
        write_json(shot_path(scene.p, 's'), shot)
        write_json(folder / 'author_job.json', {'shot': {**lit(shot, load_world(scene.p)), 'author': 'author/cafe_author.py'}})
        self.assertEqual(frames_check.checkpoint_stale(scene.p, scene.shot(), blend), [])
        author.write_text('print("tables, a pillar and a door")\n')
        self.assertEqual(frames_check.checkpoint_stale(scene.p, scene.shot(), blend), ['the author script author/cafe_author.py changed'])
        (folder / 'author.py').write_text(author.read_text())
        # the camera's move is not the author's: frames apply rewriting it leaves the checkpoint standing
        scene.read([BASE, BASE])
        ft.apply(scene.p, 's', target='diner')
        shot = scene.shot()
        self.assertEqual(shot['camera']['move']['type'], 'frame_to_frame')
        self.assertEqual(frames_check.checkpoint_stale(scene.p, shot, blend), [])
        # project data an author reads beside the shot: recorded by the build, a change makes the checkpoint stale
        made = read_json(folder / 'author_job.json')
        made['project_sidecars'] = {}
        write_json(folder / 'author_job.json', made)
        write_json(scene.p / 'places.json', {'pillar': [1, 2]})
        self.assertIn('places.json', frames_check.checkpoint_stale(scene.p, scene.shot(), blend)[0])
        # a revision checkpoint is a patch on its base, which a fresh build does not run
        write_json(folder / 'author_job.json', {**made, 'base_version': 'v0000'})
        self.assertIn('revision', frames_check.checkpoint_stale(scene.p, scene.shot(), blend)[0])


class RecordTest(unittest.TestCase):
    def test_check_apply_records_verified_cheats_and_counts_the_angle_under_the_policy(self):
        from studio import frames_check
        scene = cafe(self)
        scene.read([BASE, BASE])
        row = lambda cheats, verified=True, cid='c02': {'concept_id': cid, 'status': 'cheat', 'verified': verified,  # noqa: E731
                                                        'as_is': {'code': 'FRAME_UNSOLVED', 'message': 'blocked'},
                                                        'solved_by': {'cheats': cheats, 'row': {'lens_mm': 35.0, 'view': [0.0, 12.0]}}}
        view = {'kind': 'view', 'target': None, 'value': {'azimuth_deg': 12.0}}
        skipped = frames_check._record(scene.p, 's', [row([view])], None)
        self.assertIn('viewpoint_cheat', skipped[0]['skipped'])                  # the camera is the user's to give
        project = read_json(scene.p / 'project.json')
        project['policy'] = {'viewpoint_cheat': {'by': 'agent', 'user_words': 'the agent chooses the angle'}}
        write_json(scene.p / 'project.json', project)
        policy = cheat_core.viewpoint_policy(scene.p)
        self.assertEqual(frames_check._record(scene.p, 's', [row([move_pillar()], verified=False)], policy), [])   # unverified: kept out
        frames_check._record(scene.p, 's', [row([view, move_pillar()])], policy)
        picture = ft._store(scene.p, 's')['pictures']['c02']
        self.assertEqual([(c['kind'], c['by'], c['under']) for c in picture['cheats']],
                         [('view', 'agent', 'policy.viewpoint_cheat'), ('context_move', 'agent', 'cheat_table')])
        self.assertEqual([(d['item'], d['by_policy']) for d in picture['departs']], [('angle', 'viewpoint_cheat')])
        # a moved moment (the solve's own view step) is an angle departure under the policy; the user's own words stay
        ft.depart(scene.p, 's', 'c01', 'angle', 'the diner reads better from the side', user_words='keep it from the side')
        moved = {'concept_id': 'c01', 'status': 'moved', 'moved': ['the solve turned 24 deg'], 'as_is': {'ok': True},
                 'row': {'offset_from_picture': {'azimuth_deg': 24.0, 'elevation_deg': 0.0}}}
        frames_check._record(scene.p, 's', [moved], policy)
        self.assertEqual(ft._store(scene.p, 's')['pictures']['c01']['departs'][0]['user_words'], 'keep it from the side')


class VerifyTest(unittest.TestCase):
    def test_the_cheats_together_must_keep_every_solved_moment_and_ask_nothing_of_a_failed_one(self):
        from studio.frames_check import _verified
        moments = [{'at': 0.0, 'status': 'cheat'}, {'at': 1.0, 'status': 'redraw'}]
        self.assertTrue(_verified({'moments': moments, 'verify': {'0.0': {'ok': True}, '1.0': {'ok': False}}}))
        moments[1]['status'] = 'as_intended'                                     # the cheat breaks a moment that solved
        self.assertFalse(_verified({'moments': moments, 'verify': {'0.0': {'ok': True}, '1.0': {'ok': False}}}))
        self.assertIsNone(_verified({'moments': moments, 'verify': None}))


class FlattenedTest(unittest.TestCase):
    def test_a_low_angle_solved_at_eye_level_is_said(self):
        """A skateboarder at the top of a jump, drawn from a low wide angle (30 deg up, the board leaning 25 deg); built
        level, upright and on a long lens."""
        scene = Scene(self, [element('skater', 'subject', 'a skateboarder in the air over a ramp', ['skater'])], ['skater'])
        low = {**BASE, 'view_height': 'up', 'view_deg': 30, 'lens': 'wide', 'subject_tilt_deg': 25.0}
        scene.read([BASE, low])
        ft.apply(scene.p, 's', target='skater')
        report = built([0, 59], f0={'key:skater': key([0.4, 0.3, 0.6, 0.7])}, f59={'key:skater': key([0.45, 0.4, 0.55, 0.6])})
        rows = [{'at': 0.0, 'frame': 0, 'view': [0.0, 0.0], 'lens_mm': 35.0, 'built': {'tilt_deg': 0.0, 'sight_deg': 80.0}},
                {'at': 1.0, 'frame': 59, 'view': [0.0, 0.0], 'lens_mm': 50.0, 'built': {'tilt_deg': 4.0, 'sight_deg': 80.0}}]
        lines = [line for line in ft.gate_lines(scene.p, scene.shot(), report, {}, {'frames': rows}) if line['code'] == 'FRAME_FLATTENED']
        self.assertEqual(len(lines), 1)
        for words in ('the subject 0.20 of the frame tall, the picture 0.40', 'the camera 0 deg', 'leans +4 deg', '50 mm lens'):
            self.assertIn(words, lines[0]['text'])
        self.assertIn('t=1.0', lines[0]['text'])
        self.assertEqual(SOFTENABLE['FRAME_FLATTENED'][0], 'taste')
        kept = [rows[0], {**rows[1], 'view': [-28.0, 0.0], 'lens_mm': 24.0, 'built': {'tilt_deg': 22.0, 'sight_deg': 80.0}}]
        punchy = built([0, 59], f0={'key:skater': key([0.4, 0.3, 0.6, 0.7])}, f59={'key:skater': key([0.4, 0.3, 0.6, 0.7])})
        self.assertEqual([line for line in ft.gate_lines(scene.p, scene.shot(), punchy, {}, {'frames': kept})
                          if line['code'] == 'FRAME_FLATTENED'], [])


class SchemaTest(unittest.TestCase):
    def test_the_project_schema_takes_the_viewpoint_policy(self):
        from jsonschema import Draft202012Validator
        from studio.common import REPO
        policy = read_json(REPO / 'schemas/studio-v1/project.schema.json')['properties']['policy']
        ok = {'viewpoint_cheat': {'by': 'agent', 'user_words': 'the agent chooses the angle'}}
        self.assertEqual(list(Draft202012Validator(policy).iter_errors(ok)), [])
        self.assertEqual(list(Draft202012Validator(policy).iter_errors({'viewpoint_cheat': {**ok['viewpoint_cheat'], 'at': 'now'}})), [])
        self.assertTrue(list(Draft202012Validator(policy).iter_errors({'viewpoint_cheat': {'by': 'user'}})))


if __name__ == '__main__':
    unittest.main()
