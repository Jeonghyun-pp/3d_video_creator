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


if __name__ == '__main__':
    unittest.main()
