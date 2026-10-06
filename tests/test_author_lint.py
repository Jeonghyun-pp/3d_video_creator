"""Author-script lint (studio/author_lint.py): one snippet per refusal, and the corpus of scripts the studio already
builds must pass untouched - the lint narrows what a script may do, never what the studio already makes."""
import ast
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.author_lint import lint, require_clean
from studio.common import StudioError

REFUSED = {
    'import os': 'import',
    'import subprocess': 'import',
    'from urllib import request': 'import',
    'import importlib': 'import',
    'mod = getattr(__builtins__, "imp" + "ort")': 'call:getattr',
    'exec("x = 1")': 'call:exec',
    'eval("1")': 'call:eval',
    'code = compile("1", "x", "eval")': 'call:compile',
    '__import__("os")': 'call:__import__',
    'g = globals()': 'call:globals',
    'x = (1).__class__': 'dunder',
    'import bpy\nbpy.app.handlers.frame_change_post.append(print)': 'bpy:app.handlers',
    'import bpy\nbpy.app.timers.register(print)': 'bpy:app.timers',
    'import bpy\nbpy.app.driver_namespace["f"] = print': 'bpy:app.driver_namespace',
    'import bpy\nbpy.ops.wm.save_as_mainfile(filepath="/tmp/x.blend")': 'bpy:wm.save_as_mainfile',
    'import bpy\nbpy.ops.preferences.addon_enable(module="x")': 'bpy:ops.preferences',
    'import bpy\nbpy.ops.export_scene.gltf(filepath="x")': 'bpy:ops.export_scene',
    'import bpy\nbpy.data.libraries.write("x.blend", set())': 'bpy:libraries.write',
    'import bpy\nbpy.data.texts["t"].as_module()': 'bpy:as_module',
    'import sys\nsys.modules["bpy"] = None': 'sys.modules',
    'from . import x': 'import',
}
ALLOWED = [
    'import bpy, bmesh, math, json, random, hashlib, bisect, copy\nfrom mathutils import Vector\nfrom pathlib import Path',
    'import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parent))',
    'import bpy\nwith bpy.data.libraries.load("assets/x.blend", link=True) as (src, dst):\n    dst.objects = src.objects',
    'import bpy\nbpy.ops.wm.append(filepath="assets/x.blend/Object/a", directory="assets/x.blend/Object/", filename="a")',
    'import bpy\nobj = bpy.context.object\nsetattr(obj, "location", (0, 0, 1))\nv = getattr(obj, "name")',
    'from scene_tools import inspect\nimport camera_moves_core, modeling',
    'import numpy as np\nx = np.zeros(3)',
]


class AuthorLintTest(unittest.TestCase):
    def check(self, source, profile='author', extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'author.py'
            script.write_text(textwrap.dedent(source))
            for name, text in (extra or {}).items():
                (Path(tmp) / name).write_text(text)
            return lint(script, profile)

    def test_each_refusal(self):
        for source, rule in REFUSED.items():
            rules = [e['rule'] for e in self.check(source)['errors']]
            self.assertIn(rule, rules, f'{source!r} -> {rules}')

    def test_what_authors_need_stays_allowed(self):
        for source in ALLOWED:
            self.assertEqual(self.check(source)['errors'], [], source)

    def test_companions_are_linted_with_the_same_rules(self):
        result = self.check('import mylib\nmylib.go()', extra={'mylib.py': 'import socket\ndef go():\n    pass\n'})
        self.assertEqual([(Path(e['file']).name, e['rule']) for e in result['errors']], [('mylib.py', 'import')])
        self.assertEqual(len(result['modules']), 2)

    def test_engine_entry_scripts_are_not_importable(self):
        self.assertTrue(self.check('import build_scene')['errors'])
        self.assertTrue(self.check('import render_frames')['errors'])

    def test_rig_and_pure_profiles_have_no_bpy(self):
        self.assertTrue(self.check('import bpy', profile='rig')['errors'])
        self.assertTrue(self.check('import math\nf = open("x")', profile='rig')['errors'])
        self.assertEqual(self.check('import math, bisect\ndef camera_state(t, info):\n    return {}', profile='rig')['errors'], [])
        self.assertTrue(self.check('from pathlib import Path', profile='pure')['errors'])

    def test_refusal_is_a_studio_error_with_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'a.py'; script.write_text('import os\n')
            with self.assertRaises(StudioError) as caught:
                require_clean(script)
            self.assertEqual(caught.exception.code, 'AUTHOR_SCRIPT_REFUSED')
            self.assertIn('a.py:1', caught.exception.message)

    def test_the_corpus_passes(self):
        corpus = [(ROOT / 'examples/samsung_cutaway/author_samsung.py', 'author', ()),
                  (ROOT / 'tests/fixtures/jet_canyon_rig/author_jet.py', 'author', (ROOT / 'tests/fixtures/jet_canyon_rig',)),
                  (ROOT / 'tests/fixtures/jet_canyon_rig/author_jet_spec.py', 'author', (ROOT / 'tests/fixtures/jet_canyon_rig',)),
                  (ROOT / 'tests/fixtures/jet_canyon_rig/camera_rigs/excerpt_state.py', 'rig', (ROOT / 'tests/fixtures/jet_canyon_rig',))]
        for path, profile, roots in corpus:
            self.assertEqual(lint(path, profile, roots)['errors'], [], path.name)
        from studio.workbench import PATCH
        self.assertEqual(self.check(PATCH.format(session_id='wb0', ops='[]'))['errors'], [])
        authors = []
        for smoke in sorted((ROOT / 'tests/studio').glob('*.py')):   # every AUTHOR string constant a smoke builds with
            for node in ast.parse(smoke.read_text()).body:
                if (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id.endswith('AUTHOR') for t in node.targets)
                        and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
                    authors.append(node.value.value.replace('\n%s\n', '\npass\n'))   # a %-template's slot
        self.assertGreater(len(authors), 5)
        for source in authors:
            self.assertEqual(self.check(source)['errors'], [], source[:200])


if __name__ == '__main__':
    unittest.main()
