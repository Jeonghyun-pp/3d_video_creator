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
    # 2026-10-12 audit: reaching what an import would refuse through what it allows
    'b = __builtins__': 'dunder',
    'import random\nrandom._os.system("echo x")': 'private',
    'import typing\ntyping.sys.modules["os"]': 'module',
    'import typing as t\nt.sys.modules["os"]': 'module',
    'import typing\nt = typing\nm = t.sys': 'module',
    'thing = object()\nthing.exec("x = 1")': 'call:exec',
    'thing = object()\nf = thing.getattr(thing, "x" + "y")': 'call:getattr',
    # 2026-10-12 re-audit: the same reach through a from-import, a submodule, a constant getattr, other bindings
    'from random import _os\n_os.system("x")': 'private',
    'from typing import sys as s\ns.modules["os"].system("x")': 'module',
    'import collections\ncollections.abc.sys.modules["os"]': 'module',
    'import typing\ns = getattr(typing, "sys")': 'module',
    'import operator, typing\noperator.attrgetter("sys")(typing)': 'module',
    'import collections\nm = getattr(collections.abc, "sys")': 'module',
    'import typing\nt, u = typing, 1': 'module',
    'import typing\ndef f(t=typing):\n    return t.sys': 'module',
    'import typing\nfor m in [typing]:\n    pass': 'module',
    'import typing\nx = (t := typing)': 'module',
    'import operator\ng = operator.attrgetter("__class__")': 'dunder',
}
ALLOWED = [
    'import bpy, bmesh, math, json, random, hashlib, bisect, copy\nfrom mathutils import Vector\nfrom pathlib import Path',
    'import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parent))',
    'import bpy\nwith bpy.data.libraries.load("assets/x.blend", link=True) as (src, dst):\n    dst.objects = src.objects',
    'import bpy\nbpy.ops.wm.append(filepath="assets/x.blend/Object/a", directory="assets/x.blend/Object/", filename="a")',
    'import bpy\nobj = bpy.context.object\nsetattr(obj, "location", (0, 0, 1))\nv = getattr(obj, "name")',
    'from scene_tools import inspect\nimport camera_moves_core, modeling',
    'import numpy as np\nx = np.zeros(3)',
    'import re, collections.abc\npattern = re.compile("a+")\nok = isinstance({}, collections.abc.Mapping)',
    '__all__ = ["x"]\nclass A:\n    __slots__ = ("x",)',
    'from collections import abc, namedtuple\nfrom math import sqrt\nok = isinstance({}, abc.Mapping)\nr = list(map(sqrt, [4.0]))',
    'import operator\nkey = operator.attrgetter("name")',
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

    def test_pure_code_touches_no_private_attribute(self):
        source = 'import math\nclass A:\n    def __init__(self):\n        self._x = 1\n    def get(self):\n        return self._x\na = A()\ny = a._x\n'
        self.assertEqual([e['rule'] for e in self.check(source, profile='pure')['errors']], ['private'])
        self.assertEqual(self.check(source)['errors'], [])   # an author script may (the runtime hook guards it)

    def test_pure_refusals_hold_in_the_pure_profile(self):
        for source, rule in (('from random import _os\n_os.system("x")', 'private'), ('import typing\ns = getattr(typing, "sys")', 'module'),
                             ('import collections\ncollections.abc.sys.modules["os"]', 'module')):
            self.assertIn(rule, [e['rule'] for e in self.check(source, profile='pure')['errors']], source)

    def test_named_tuple_api_and_one_report_per_name(self):
        source = 'from collections import namedtuple\nP = namedtuple("P", "x y")\nq = P(1, 2)._replace(x=3)\nd = q._asdict()\n'
        self.assertEqual(self.check(source, profile='pure')['errors'], [])
        self.assertEqual([e['rule'] for e in self.check('import struct\nx = struct._clearcache', profile='pure')['errors']], ['private'])

    def test_the_contrib_library_passes_pure(self):
        entries = sorted((ROOT / 'library/contrib').glob('*/*/v*/impl.py')) + sorted((ROOT / 'tests/fixtures/contrib').glob('*/impl.py'))
        self.assertGreater(len(entries), 5)
        for impl in entries:
            self.assertEqual(lint(impl, 'pure')['errors'], [], impl)

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
