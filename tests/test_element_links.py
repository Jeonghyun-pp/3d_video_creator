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
