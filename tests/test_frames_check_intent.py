"""frames check on the intent probe (blender_ops/frames_check.py search_intent / _moment_intent) on stubs: what a cheat
is probed at, what a stopped run says, what counts as complete. A moment of a statue among hedges, not the reel the
rules came from (N+1)."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

OPS = Path(__file__).resolve().parents[1] / 'studio/blender_ops'
sys.path.insert(0, str(OPS))


def module(name, **members):
    result = ModuleType(name)
    result.__dict__.update(members)
    return result


class IntentCheckTest(unittest.TestCase):
    def setUp(self):
        stubs = {
            'bpy': module('bpy', context=SimpleNamespace(scene=SimpleNamespace(frame_set=lambda f: None)), data=SimpleNamespace(filepath='x')),
            'mathutils': module('mathutils', Vector=None),
            'camera_frame': module('camera_frame'),
            'camera_moves': module('camera_moves', _code=lambda message, default: message.split(':')[0] if ':' in message else default),
            'cheat_scene': module('cheat_scene', MOVES=('scale', 'move', 'turn')),
            'scene_index': module('scene_index', resolve_ref=lambda ref, index=None: None),
            'scene_roles': module('scene_roles', counts=None),
        }
        with patch.dict(sys.modules, stubs):
            spec = importlib.util.spec_from_file_location('frames_check_under_test', OPS / 'frames_check.py')
            self.fc = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.fc)
        self.views = []
        fc = self.fc
        fc._probe_attempt = self.probe
        fc._attempt = lambda job, move, fr, ctx, cheats, enforce=True, seeds=None: {'cheats': cheats, 'ok': False, 'row': None,
                                                                                    'code': 'FRAME_UNSOLVED', 'message': 'x', 'seconds': 0.0}

    def probe(self, job, move, fr, ctx, cheats, enforce=True, views=None):
        self.views.append((cheats[0]['kind'], views))
        return {'cheats': cheats, 'ok': False, 'score': None, 'best': None, 'passed': 0, 'passing': [], 'code': 'FRAME_UNSOLVED',
                'seconds': 0.0}

    def see_through(self):
        return [{'kind': 'see_neighbours', 'target': ['statue/arm'], 'value': 0.55}]

    def test_a_cheat_on_a_thing_is_probed_where_it_stood_and_everywhere_when_that_is_unknown(self):
        fc = self.fc
        fc._split = lambda *a, **k: (self.see_through(), [])
        fr, move = {'at': 1.0, 'lens_mm': 35.0}, {}
        known = {'report': {'rejected': [{'view': [0.0, 30.0], 'owners': ['statue/arm']}, {'view': [0.0, 60.0], 'owners': ['hedge']}]}}
        fc.search_intent({}, move, fr, {}, known, abort_at=1e18)
        self.assertEqual(self.views[-1], ('see_neighbours', {(0.0, 30.0)}))
        hidden = {'report': {'code': 'SHOW_NEEDS_OPENING', 'diagnosis': {}}}   # a hidden part's report: no views recorded
        fc.search_intent({}, move, fr, {}, hidden, abort_at=1e18)
        self.assertEqual(self.views[-1], ('see_neighbours', None))
        fc.search_intent({}, move, fr, {}, None, abort_at=1e18, solved={'view': [0.0, 0.0]})   # moved, no owners known
        self.assertEqual(self.views[-1], ('see_neighbours', None))

    def test_a_stop_in_the_locked_search_keeps_the_redraw(self):
        fc = self.fc
        fc._split = lambda *a, **k: ([], [{'kind': 'context_move', 'target': 'statue', 'value': {}, 'why_locked': 'said'}])
        real = fc._probe_attempt

        def stopping(*a, **k):
            raise fc.Stopped()
        fc._probe_attempt = stopping
        found = fc.search_intent({}, {}, {'at': 1.0, 'lens_mm': 35.0}, {}, None, abort_at=1e18)
        self.assertEqual((found['status'], found.get('locked_unfinished')), ('redraw', True))
        fc._probe_attempt = real

    def test_a_moved_moment_stopped_stays_moved_and_the_run_is_not_complete(self):
        fc = self.fc
        fc._solve = lambda job, move, fr, ctx: ({'view': [0.0, 60.0], 'departed': ['turned 60 deg round the subject'], 'warnings': []}, None)

        def stopped(*a, **k):
            raise fc.Stopped()
        fc.search_intent = stopped
        out = fc._moment_intent({}, {'params': {'solve': 'intent'}}, {'at': 1.0}, {}, shot_deadline=1e18, check={'moment_s': 600})
        self.assertEqual((out['status'], out['search']), ('moved', {'stopped': True}))
        self.assertEqual(out['moved'], ['turned 60 deg round the subject'])
        self.assertFalse(fc._complete([{'status': 'redraw'}, out]))
        self.assertFalse(fc._complete([{'status': 'stopped'}]))
        self.assertTrue(fc._complete([{'status': 'redraw', 'locked_unfinished': True}, {'status': 'moved', 'search': {'complete': True}}]))

    @staticmethod
    def turn(deg):
        return {'kind': 'turn', 'key': None, 'value': deg, 'text': f'turned {deg} deg round the subject'}

    @staticmethod
    def lens(rank, mm):
        return {'kind': 'lens', 'key': None, 'value': rank, 'text': f'lens ({mm} mm)'}

    def verify_with(self, own, joint, status='cheat', cheat_row=True):
        """verify() of one moment that gave up `own` on its own (by its cheat, or as it stood / moved) and `joint` with
        every cheat applied (structured departures, as camera_moves_core.intent_losses writes them)."""
        fc = self.fc
        fc._allowance = lambda job, cheats: {}
        fc._sunk = lambda job, cheats: None
        fc.cheat_scene.apply = lambda job: None
        fc._solve = lambda job, move, fr, ctx: ({'departures': list(joint), 'warnings': []}, None)
        own_row = {'departures': list(own), 'warnings': []}
        moment = {'at': 1.0, 'status': status, 'row': own_row if (status != 'cheat' or not cheat_row) else None,
                  'solved_by': {'cheats': [{'kind': 'lens', 'target': None, 'value': 50.0}], **({'row': own_row} if cheat_row else {})}}
        other = {'at': 0.0, 'status': 'cheat', 'solved_by': {'cheats': [{'kind': 'lens', 'target': None, 'value': 24.0}], 'row': own_row}}
        job = {'shot': {'camera': {'move': {'params': {'solve': 'intent'}, 'frames': [{'at': 0.0}, {'at': 1.0}]}}}}
        out = fc.verify(job, job['shot']['camera']['move'], {}, [other, moment], shot_deadline=1e18)   # another moment's cheat
        return out['1.0']['ok']

    def test_a_cheat_that_departed_on_its_own_verifies_when_it_loses_nothing_more_together(self):
        # a failed moment solved by a cheat that turns the camera round: applied with every other cheat it still turns
        # (a few degrees more or less) and loses nothing else - verified, never undecided
        self.assertTrue(self.verify_with([self.turn(60)], [self.turn(62)]))
        self.assertTrue(self.verify_with([], []))
        self.assertFalse(self.verify_with([self.turn(60)], [self.turn(60), self.lens(2, 24)]))   # loses a lens class it had kept
        self.assertFalse(self.verify_with([], [self.turn(60)], status='as_intended'))

    def test_how_far_it_gives_up_counts_not_only_what(self):
        self.assertTrue(self.verify_with([self.turn(50)], [self.turn(55)]))
        self.assertFalse(self.verify_with([self.turn(50)], [self.turn(175)]))   # a swing to the far side is new
        self.assertFalse(self.verify_with([self.lens(1, 35)], [self.lens(2, 24)]))   # a wider class than its own
        self.assertTrue(self.verify_with([self.lens(2, 24)], [self.lens(1, 35)]))
        size = lambda n: {'kind': 'size', 'key': None, 'value': n, 'text': f'{n} classes off'}  # noqa: E731
        self.assertFalse(self.verify_with([size(1)], [size(2)]))

    def test_a_moved_moment_and_a_cheat_with_no_row_of_its_own_are_judged_against_their_own_rows(self):
        self.assertTrue(self.verify_with([self.turn(60)], [self.turn(61)], status='moved'))
        self.assertFalse(self.verify_with([self.turn(60)], [self.turn(61), self.lens(2, 24)], status='moved'))
        # a cheat whose search kept no row of its own falls back to the moment's own row
        self.assertTrue(self.verify_with([self.turn(60)], [self.turn(61)], status='cheat', cheat_row=False))

    def test_things_with_digits_in_their_names_stay_apart(self):
        flip = lambda name: {'kind': 'layout_flip', 'key': [name, 'x'], 'value': 1, 'text': f'{name}: left, right here'}  # noqa: E731
        self.assertTrue(self.verify_with([flip('crane 2')], [flip('crane 2')]))
        self.assertFalse(self.verify_with([flip('crane 2')], [flip('crane 3')]))

    def test_an_intent_conflict_is_a_cheat_said_not_verified_never_undecided(self):
        sys.path.insert(0, str(OPS.parents[1]))
        from studio.frames_check import classify
        self.assertEqual(classify({'status': 'cheat'}, [], [], None, False, solve='intent'), 'cheat')
        self.assertEqual(classify({'status': 'cheat'}, [], [], None, False, solve='exact'), 'undecided')


if __name__ == '__main__':
    unittest.main()
