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
from tests.decisions_fixture import allowed


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

    def judge(self, shown, made, measured=()):
        shot = {'shot_id': 's01', 'screen': {'elements': shown}}
        fidelity = {'subjects': [{'subject_id': m} if isinstance(m, str) else m for m in measured]}
        return [(line['code'], line['text'].split(' shows ')[1].split(' ')[0])
                for line in world.unbuilt_held(shot, self.WORLD, {'objects': [{'studio_id': m} for m in made]}, fidelity)]

    def test_judged_on_what_the_build_made(self):
        self.assertEqual(self.judge(['boat', 'tank', 'crate', 'sky'], []), [('SUBJECT_UNMEASURED', 'boat'), ('ELEMENT_UNBUILT', 'tank')])
        self.assertEqual(self.judge(['boat', 'tank', 'pad'], ['boat', 'tank/valve', 'pad_mesh'], ['boat']), [])   # parts, links

    def test_an_object_carrying_the_subjects_name_is_not_the_subject(self):
        # a box primitive or an author object named like the subject: in the inventory, but no spec of it was measured
        self.assertEqual(self.judge(['boat'], ['boat']), [('SUBJECT_UNMEASURED', 'boat')])
        self.assertEqual(self.judge(['boat'], ['boat_1'], [{'subject_id': 'boat_1', 'placed_from': 'boat'}]), [])   # placed by scene.instances

    def test_a_subject_linked_to_a_spec_or_its_parts_is_measured_by_that_spec(self):
        linked = {'elements': [element('hull', 'subject', ['exact'], look='blender', ids=['sub']),
                               element('tank', 'subject', look='blender', ids=['sub/tank'])]}
        shot = {'shot_id': 's01', 'screen': {'elements': ['hull', 'tank']}}
        self.assertEqual(world.unbuilt_held(shot, linked, {'objects': []}, {'subjects': [{'subject_id': 'sub'}]}), [])
        self.assertEqual([l['code'] for l in world.unbuilt_held(shot, linked, {'objects': []}, {'subjects': []})],
                         ['SUBJECT_UNMEASURED', 'SUBJECT_UNMEASURED'])
        self.assertEqual(world.subject_roots(linked), {'hull', 'tank', 'sub'})   # the photo rule asks the linked spec too

    def test_the_subject_missing_is_never_softened(self):
        self.assertIn('ELEMENT_UNBUILT', SOFTENABLE)
        self.assertEqual(SOFTENABLE['ELEMENT_UNBUILT'][0], 'broken')
        self.assertNotIn('SUBJECT_UNMEASURED', SOFTENABLE)
        self.assertNotIn('SUBJECT_UNMEASURED', severity_map({'policy': allowed({'strictness': 'look-first'})}))   # absent = error


class PhotosFirstTest(unittest.TestCase):
    def test_a_subject_is_built_from_photos_of_its_kind_or_the_users_words(self):
        from studio.common import write_json
        from studio.subjects import photo_gap, record_no_photo
        from studio.project import init_project
        with tempfile.TemporaryDirectory() as tmp:
            root = init_project('photos', {'request': 'x', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, tmp)['project_path']
            (Path(root) / 'subjects/boat').mkdir(parents=True)
            self.assertIn('no photo', photo_gap(root, 'boat', {}))
            self.assertIsNone(photo_gap(root, 'boat', {'photo_views': [{'id': 'side'}]}))
            (Path(root) / 'references/photos').mkdir(parents=True)
            (Path(root) / 'references/photos/c_1.jpg').write_bytes(b'x')
            write_json(Path(root) / 'references/photos/index.json', {'photos': [{'file': 'references/photos/c_1.jpg', 'subject_id': 'other'}]})
            self.assertIn('no photo', photo_gap(root, 'boat', {}))      # a photo of something else is not one of it
            write_json(Path(root) / 'references/photos/index.json', {'photos': [{'file': 'references/photos/c_1.jpg', 'subject_id': 'boat'}]})
            self.assertIsNone(photo_gap(root, 'boat', {}))
            with self.assertRaises(StudioError):
                record_no_photo(root, 'ship', 'User: approved')             # an agent's wrapper
            (Path(root) / 'subjects/ship').mkdir(parents=True)
            record_no_photo(root, 'ship', '가상의 배라서 사진 필요 없어')
            self.assertIsNone(photo_gap(root, 'ship', {}))


class ClayPageSaysTest(unittest.TestCase):
    def test_a_box_is_named_on_its_card(self):
        from studio.reel import _page
        rows = [{'shot_id': 's01', 'version': 'v1', 'seconds': 2, 'text': 'x', 'clip': 's01.mp4', 'stand_ins': ['crate']},
                {'shot_id': 's02', 'version': 'v1', 'seconds': 2, 'text': 'y', 'clip': 's02.mp4', 'stand_ins': []}]
        self.assertEqual(_page('t', rows, [], [], '/p').count('점토 상자(모델이 칠할 자리): crate'), 1)


if __name__ == '__main__':
    unittest.main()
