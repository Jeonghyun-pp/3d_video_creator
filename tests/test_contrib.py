"""Contrib entries (studio/contrib.py): a good entry passes its contracts and is promoted only after a passing build used
it; NaN, an open mesh, bpy, a parameter the manifest does not declare, an unpinned reference and a deprecated version
are refused."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from studio import contrib
from studio.common import StudioError
from studio.project import init_project

ROOT = Path(__file__).resolve().parents[1]
DISC = ROOT / 'tests/fixtures/contrib/cycloid_disc'
BOX = '''def box(size):
    s = size / 2
    v = [(-s, -s, -s), (s, -s, -s), (s, s, -s), (-s, s, -s), (-s, -s, s), (s, -s, s), (s, s, s), (-s, s, s)]
    f = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return v, f
'''


class ContribTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(init_project('c', {'request': 'contrib', 'shots': [{'shot_id': 's', 'frame_count': 10}]}, self.tmp.name)['project_path'])
        self.library = Path(self.tmp.name) / 'library' / 'contrib'
        patcher = mock.patch.object(contrib, 'LIBRARY', self.library)
        patcher.start(); self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def entry(self, kind, name, impl, manifest):
        folder = self.project / 'contrib' / kind / name
        folder.mkdir(parents=True)
        (folder / 'impl.py').write_text(impl)
        (folder / 'manifest.json').write_text(json.dumps({'kind': kind, 'name': name, 'words': [name.replace('_', ' ')], **manifest}))
        return folder

    def test_a_good_profile_passes_and_resolves(self):
        shutil.copytree(DISC, self.project / 'contrib/profile/cycloid_disc')
        table = contrib.resolve(self.project, {'contrib:cycloid_disc@draft'})
        self.assertTrue(table['contrib:cycloid_disc@draft']['draft'])

    def test_a_good_mesh_passes(self):
        self.entry('mesh', 'cube', BOX, {'entry': 'box', 'params': {'size': 1.0}, 'lengths': ['size']})
        self.assertTrue(contrib.check(self.project / 'contrib/mesh/cube')['ok'])

    def test_refusals(self):
        cases = {
            'nan_box': (BOX.replace('s = size / 2', 's = float("nan")'), 'non-finite'),
            'open_box': (BOX.replace(', (3, 0, 4, 7)]', ']'), 'not closed'),
            'bpy_box': ('import bpy\n' + BOX, 'import'),
            'files_box': (BOX.replace('return v, f', 'open("/tmp/x", "w")\n    return v, f'), 'call:open'),
            'extra_box': (BOX.replace('def box(size)', 'def box(size, colour)'), 'manifest params'),
        }
        for name, (source, words) in cases.items():
            folder = self.entry('mesh', name, source, {'entry': 'box', 'params': {'size': 1.0}, 'lengths': ['size']})
            result = contrib.check(folder)
            self.assertFalse(result['ok'], name)
            self.assertTrue(any(words in p for p in result['problems']), (name, result['problems']))
        with self.assertRaises(ValueError):
            contrib.resolve(self.project, {'contrib:cube'})   # a reference must pin a version

    def test_promotion_needs_a_passing_build_and_pins_versions(self):
        shutil.copytree(DISC, self.project / 'contrib/profile/cycloid_disc')
        table = contrib.resolve(self.project, {'contrib:cycloid_disc@draft'})
        self.assertEqual(contrib.auto_promote(self.project, table, {'fidelity': {'passed': False}}), [])
        promoted = contrib.auto_promote(self.project, table, {'shot_id': 's', 'scene_version': 'v0001', 'fidelity': None})
        self.assertEqual(promoted, ['contrib:cycloid_disc@v001'])
        self.assertEqual(contrib.auto_promote(self.project, table, {'shot_id': 's', 'scene_version': 'v0002'}), ['contrib:cycloid_disc@v001'])  # same code
        self.assertEqual(contrib.words('contrib:cycloid_disc@v001'), ['cycloidal disc', 'lobed disc'])
        self.assertTrue(contrib.resolve(self.project, {'contrib:cycloid_disc@v001'}))
        contrib.deprecate('cycloid_disc', 'v001', 'pin radius off by one')
        with self.assertRaises(StudioError) as caught:
            contrib.resolve(self.project, {'contrib:cycloid_disc@v001'})
        self.assertEqual(caught.exception.code, 'CONTRIB_DEPRECATED')

    def test_changed_code_never_runs_and_couplings_solve(self):
        from studio.blender_ops import contrib_loader, kinematics_core
        folder = self.entry('coupling', 'half', 'def law(x, ratio):\n    return x * ratio\n', {'entry': 'law', 'params': {'ratio': 0.5}})
        table = contrib.resolve(self.project, {'contrib:half@draft'})
        with mock.patch.dict(contrib_loader.TABLE, table, clear=True):
            values = kinematics_core.solve([{'id': 'c', 'kind': 'contrib:half@draft', 'driver': 'a', 'driven': 'b', 'args': {'ratio': 0.25}}], {'a': 80.0})
            self.assertEqual(values['b'], 20.0)
            self.assertEqual(kinematics_core.check([{'id': 'a'}, {'id': 'b'}], [{'id': 'c', 'kind': 'contrib:half@draft', 'driver': 'a', 'driven': 'b'}]), [])
            (folder / 'impl.py').write_text('def law(x, ratio):\n    return 0\n')
            contrib_loader._CACHE.clear()
            with self.assertRaises(ValueError):
                contrib_loader.load('contrib:half@draft')


if __name__ == '__main__':
    unittest.main()
