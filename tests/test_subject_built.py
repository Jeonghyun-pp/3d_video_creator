"""A clay box stands only where a model paints over it; what a shot shows that a box may not stand for is in the build
(studio/world.py stand_in_allowed / unbuilt_held, studio/layout.py _stand_ins). Why (submarine, 2026-10-21): the boat,
its tanks and the stone stood in as 8-vertex boxes in every cut, every check passed and the clay reel was grey bars."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from studio import world
from studio.common import StudioError
from studio.gates import SOFTENABLE, severity_map


def element(ident, function='set', requirements=(), look='generative', shape='blender', **extra):
    return {'id': ident, 'function': function, 'description': ident, 'why': 'test', 'requirements': list(requirements),
            'owner': {'shape': shape, 'look': look}, **extra}


BOX = {'kind': 'box', 'size_m': [1, 1, 1]}


class StandInAllowedTest(unittest.TestCase):
    def test_only_what_a_model_paints_and_nothing_holds(self):
        self.assertTrue(world.stand_in_allowed(element('crate')))
        self.assertFalse(world.stand_in_allowed(element('boat', 'subject')))
        self.assertFalse(world.stand_in_allowed(element('tank', look='blender')))
        for req in world.HELD_REQUIREMENTS:
            self.assertFalse(world.stand_in_allowed(element('stone', requirements=[req])), req)

    def test_a_new_requirement_that_forces_a_blender_look_needs_no_new_line(self):
        rules = {**world.OWNER_RULES, 'measured_glow': {'look': {'blender'}}}   # N+1: a requirement not written yet
        lamp = element('lamp', requirements=['measured_glow'])
        with mock.patch.object(world, 'OWNER_RULES', rules):
            lamp['owner'] = world.suggested_owner(lamp, 'continuous')
        self.assertEqual(lamp['owner']['look'], 'blender')
        self.assertFalse(world.stand_in_allowed(lamp))

    def test_a_blockout_builds_only_what_may_stand_in(self):
        self.assertEqual(world.element_ids(element('crate', blockout=BOX)), ['crate'])
        self.assertEqual(world.element_ids(element('boat', 'subject', blockout=BOX)), [])     # a size, not a build
        self.assertEqual(world.element_ids(element('boat', 'subject', blockout=BOX, ids=['hull'])), ['hull'])

    def test_world_lint_says_a_blockout_is_only_a_start(self):
        body = {'kind': 'none', 'place': None, 'beats': [], 'elements': [element('boat', 'subject', look='blender', blockout=BOX)]}
        _errors, warnings = world.lint('.', body, None)
        self.assertTrue(any(w.startswith('ELEMENT_NEEDS_BUILD: boat') for w in warnings), warnings)


class LayoutRefusesTest(unittest.TestCase):
    def test_the_subject_cannot_stand_in(self):
        from studio.layout import _stand_ins
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'project.json').write_text('{}')
            elements = [element('boat', 'subject', ['exact'], look='blender', blockout=BOX), element('crate', blockout=BOX)]
            (Path(root) / 'world.json').write_text(json.dumps({'kind': 'none', 'elements': elements}))
            self.assertEqual([r['id'] for r in _stand_ins(root, [{'element': 'crate', 'at': [0, 0, 0]}])], ['crate'])
            with self.assertRaises(StudioError) as caught:
                _stand_ins(root, [{'element': 'boat', 'at': [0, 0, 0]}])
        self.assertEqual(caught.exception.code, 'STAND_IN_NOT_ALLOWED')


class UnbuiltHeldTest(unittest.TestCase):
    WORLD = {'elements': [element('boat', 'subject', ['exact'], look='blender'), element('tank', look='blender'),
                          element('pad', look='blender', ids=['pad_mesh']), element('crate'), element('sky', shape='generative')]}

    def judge(self, shown, made):
        shot = {'shot_id': 's01', 'screen': {'elements': shown}}
        return [(line['code'], line['text'].split(' shows ')[1].split(' ')[0])
                for line in world.unbuilt_held(shot, self.WORLD, {'objects': [{'studio_id': m} for m in made]})]

    def test_judged_on_what_the_build_made(self):
        self.assertEqual(self.judge(['boat', 'tank', 'crate', 'sky'], []), [('SUBJECT_UNBUILT', 'boat'), ('ELEMENT_UNBUILT', 'tank')])
        self.assertEqual(self.judge(['boat', 'tank', 'pad'], ['boat.1', 'tank/valve', 'pad_mesh']), [])   # copies, parts, links

    def test_the_subject_missing_is_never_softened(self):
        self.assertIn('ELEMENT_UNBUILT', SOFTENABLE)
        self.assertNotIn('SUBJECT_UNBUILT', SOFTENABLE)
        self.assertNotIn('SUBJECT_UNBUILT', severity_map({'policy': {'strictness': 'look-first'}}))   # absent = error


class ClayPageSaysTest(unittest.TestCase):
    def test_a_box_is_named_on_its_card(self):
        from studio.reel import _page
        rows = [{'shot_id': 's01', 'version': 'v1', 'seconds': 2, 'text': 'x', 'clip': 's01.mp4', 'stand_ins': ['crate']},
                {'shot_id': 's02', 'version': 'v1', 'seconds': 2, 'text': 'y', 'clip': 's02.mp4', 'stand_ins': []}]
        self.assertEqual(_page('t', rows, [], [], '/p').count('점토 상자(모델이 칠할 자리): crate'), 1)


if __name__ == '__main__':
    unittest.main()
