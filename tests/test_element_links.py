"""A shot names world elements (its show rows, details, keeps, the move's must-see); once an element is built as parts
of a subject spec, every reader of a declared id follows the element's world link (blender_ops/ids_core.linked).
Why (2026-10-22, submarine): the boat's tanks were linked to parts of the submarine spec and every reader looked for the
element's own id and found nothing."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'studio' / 'blender_ops'))
import ids_core  # noqa: E402


class LinkedTest(unittest.TestCase):
    def tearDown(self):
        ids_core.LINKS = {}

    def test_an_element_names_what_its_links_build(self):
        ids_core.LINKS = {'main_ballast_tanks': ['submarine_hull/mbt_fore', 'submarine_hull/mbt_aft'], 'hull': ['submarine_hull']}
        self.assertEqual(ids_core.linked('main_ballast_tanks'), ['submarine_hull/mbt_fore', 'submarine_hull/mbt_aft'])
        self.assertEqual(ids_core.linked('hull/sail'), ['submarine_hull/sail'])   # a part under a linked element
        self.assertEqual(ids_core.linked('stone'), [])                            # not linked: nothing to follow

    def test_the_build_job_carries_the_links(self):
        import inspect
        from studio import blender
        self.assertIn("'element_links'", inspect.getsource(blender.generator_inputs))
        root = Path(__file__).resolve().parents[1] / 'studio' / 'blender_ops'
        for entry in ('build_scene.py', 'build_author.py', 'frames_check.py', 'workbench_tools.py'):   # every Blender entry
            self.assertIn('ids_core.from_job(', (root / entry).read_text(), entry)


class LinkCycleTest(unittest.TestCase):
    """2026-10-26 (semiconductor_fab): the element fab_building is linked to fab_building (the hall kit builds it under
    that name); a cut that had not built it sent resolve_group round the self-link until Python's recursion limit. Every
    id is followed once: a self-link or a cycle names nothing, and the caller says what is missing."""
    def tearDown(self):
        ids_core.LINKS = {}

    def _scene_index(self):
        import importlib
        import types
        from unittest import mock
        empty = types.SimpleNamespace(objects=[])
        bpy = types.SimpleNamespace(data=empty, context=types.SimpleNamespace(scene=empty))
        mathutils = types.SimpleNamespace(Vector=lambda *a: a)
        patch = mock.patch.dict(sys.modules, {'bpy': bpy, 'mathutils': mathutils})
        patch.start()
        self.addCleanup(patch.stop)
        sys.modules.pop('scene_index', None)
        self.addCleanup(sys.modules.pop, 'scene_index', None)
        return importlib.import_module('scene_index')

    def test_an_element_linked_to_its_own_id_that_nothing_built_names_nothing(self):
        scene_index = self._scene_index()
        ids_core.LINKS = {'fab_building': ['fab_building']}
        self.assertEqual(scene_index.resolve_group('fab_building'), [])
        self.assertEqual(scene_index.resolve_group('fab_building/roof'), [])
        self.assertIsNone(scene_index.resolve_ref('fab_building'))

    def test_a_cycle_of_links_names_nothing_and_a_chain_is_followed(self):
        ids_core.LINKS = {'a': ['b'], 'b': ['a', 'c']}
        built = {'c': ['root_c']}
        self.assertEqual(ids_core.through_links('a', lambda i: built.get(i, [])), ['root_c'])
        self.assertEqual(ids_core.through_links('b', lambda i: []), [])


class ContextKeysTest(unittest.TestCase):
    def test_a_linked_id_a_cut_does_not_build_is_not_an_unknown_key_part(self):
        """2026-10-25 (semiconductor_fab): the element 'wafer' links the box's wafers and the wafer on the tool's chuck;
        the chip close-up builds only the second and stopped with KEY_PART_UNKNOWN foup/wafers. The picture's things are
        optional key parts; the probe skips an optional one nothing built."""
        import inspect
        from unittest import mock
        from studio import blender
        shot = {'shot_id': 's11', 'duration_frames': 30, 'camera': {'move': {'type': 'frame_to_frame', 'params': {'target': 'grid'},
                'frames': [{'at': 0.0}]}}, 'screen': {'elements': ['wafer']}}
        with mock.patch('studio.frame_targets.context_elements', return_value={}), \
                mock.patch('studio.frame_targets.layout_elements', return_value={'wafer': {'words': 'w', 'ids': ['foup/wafers', 'chuck/wafer']}}), \
                mock.patch('studio.storyboard.envelope', return_value=None):
            keys = {k['id']: k for k in blender.probe_inputs(Path('.'), shot, 30)['key_parts']}
        self.assertTrue(keys['foup/wafers']['optional'] and keys['chuck/wafer']['optional'])
        source = (Path(__file__).resolve().parents[1] / 'studio' / 'blender_ops' / 'frame_probe.py').read_text()
        self.assertIn("if not part.get('optional')", source)


class EntryScriptsTest(unittest.TestCase):
    def test_every_blender_entry_script_that_resolves_ids_loads_the_links(self):
        """2026-10-22: picture passes, candidates and the keep masks read declared ids without the world's links (a
        shot naming a linked part met SHOW_PART_MISSING): every script Blender runs that resolves ids sets them."""
        from pathlib import Path
        ops = Path(__file__).resolve().parents[1] / 'studio' / 'blender_ops'
        resolves = ('resolve_ref', 'resolve_group', 'scene_index', 'show_scene', 'camera_moves')
        missing = [f.name for f in sorted(ops.glob('*.py')) if "sys.argv.index('--')" in (text := f.read_text())
                   and any(word in text for word in resolves) and 'from_job(' not in text]
        self.assertEqual(missing, [])


if __name__ == '__main__':
    unittest.main()
