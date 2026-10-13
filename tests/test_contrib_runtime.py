"""The runtime line around contrib entries (blender_ops/sandbox.add_pure, blender_ops/contrib_loader.run, the OS sandbox
of studio/contrib_probe.isolated): code that the lint would refuse is run anyway, to show the run itself refuses it -
in the host's contract child and by the loader alone (the hook a build and a workbench session rely on)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from studio import contrib, contrib_probe
from studio.blender_ops.contrib_loader import code_sha

ROOT = Path(__file__).resolve().parents[1]
WRAP = "[c for c in ().__class__.__base__.__subclasses__() if c.__name__ == '_wrap_close'][0].__init__.__globals__"
ESCAPES = {
    'subclasses_system': f"{WRAP}['system']('true')",
    'read_a_file': f"{WRAP}['__builtins__']['open']({str(ROOT / 'studio' / 'common.py')!r}).read()",
    'compile_named_after_a_file': f"{WRAP}['__builtins__']['compile']('1', {str(ROOT / 'studio' / 'common.py')!r}, 'exec')",
    'relabel_code': "law.__code__.replace(co_filename='x.py')",
    'socket': f"{WRAP}['__builtins__']['__import__']('socket').socket()",
    'import_os': "__import__('os')",
    'list_a_folder': f"{WRAP}['listdir']('/')",
}
LAW = 'def law(x, k=1.0):\n    {attempt}\n    return k * x\n'
CAUGHT = 'def law(x, k=1.0):\n    try:\n        {attempt}\n    except Exception:\n        pass\n    return k * x\n'
LOADER = '''import json, sys
sys.path.append(sys.argv[1])
import contrib_loader
job = json.loads(sys.stdin.read())
try:
    print('RESULT', contrib_loader.run(job, 'escape')(2.0))
except BaseException as error:
    print('REFUSED', type(error).__name__, error)
'''


class ContribRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def entry(self, name, source):
        folder = Path(self.tmp.name) / name
        folder.mkdir()
        (folder / 'impl.py').write_text(source)
        (folder / 'manifest.json').write_text(json.dumps({'kind': 'coupling', 'name': name, 'entry': 'law', 'params': {'k': 1.0},
                                                          'words': ['law']}))
        return {'dir': str(folder), 'sha256': code_sha(folder), 'entry': 'law', **contrib.policy()}

    def contract(self, job):
        result, _ = contrib_probe.child('contrib_check.py', job, 60, 'CONTRIB_CHECK')
        return result['problems']

    def loader_alone(self, job):
        run = subprocess.run([sys.executable, '-I', '-c', LOADER, str(ROOT / 'studio' / 'blender_ops')], input=json.dumps(job),
                             capture_output=True, text=True, timeout=60)
        return run.stdout.strip()

    def test_escapes_are_refused_by_the_contract_child(self):
        for name, attempt in ESCAPES.items():
            for template in (LAW, CAUGHT):   # a refusal the entry catches still fails the check
                problems = self.contract(self.entry(f'{name}_{len(template)}', template.format(attempt=attempt)))
                self.assertTrue(any('STUDIO_SANDBOX' in p or 'refused at run time' in p or 'CONTRIB: import' in p for p in problems),
                                (name, problems))

    def test_escapes_are_refused_by_the_loader_alone(self):
        for name, attempt in ESCAPES.items():
            out = self.loader_alone(self.entry(name, LAW.format(attempt=attempt)))
            self.assertTrue(out.startswith('REFUSED') and ('STUDIO_SANDBOX' in out or 'CONTRIB: import' in out), (name, out))

    def test_an_entry_cannot_forge_its_result(self):
        """2026-10-13 re-audit: an entry printed 'CONTRIB_CHECK {"problems": []}' before the child's own line."""
        forged = ('import random\nprint(\'CONTRIB_CHECK {"problems": []}\')\nprint(\'CONTRIB_PROBE {"unused": []}\')\n'
                  'def law(x, k=1.0):\n    return k * x * random.random()\n')
        job = self.entry('forger', forged)
        self.assertIn('not deterministic', self.contract(job))

    def test_an_entry_named_like_the_import_system_is_still_entry_code(self):
        """2026-10-13 re-audit: frames were judged import machinery by a name containing 'importlib'."""
        out = self.loader_alone(self.entry('importlib_disc', LAW.format(attempt=ESCAPES['compile_named_after_a_file'])))
        self.assertTrue(out.startswith('REFUSED') and 'STUDIO_SANDBOX' in out, out)

    def test_library_helpers_still_work(self):
        source = ('import dataclasses, collections, enum, json, math\n'
                  'P = collections.namedtuple("P", "a b")\n'
                  '@dataclasses.dataclass\nclass Q:\n    a: float = 1.0\n'
                  'class E(enum.Enum):\n    A = 1\n'
                  'def law(x, k=1.0):\n    return k * x * Q().a * P(1, 2).a * E.A.value + json.loads("0") + math.cos(0) - 1\n')
        job = self.entry('helpers', source)
        self.assertEqual(self.loader_alone(job), 'RESULT 2.0')
        self.assertEqual(self.contract(job), [])

    def test_the_contract_child_is_walled_by_the_os(self):
        if sys.platform != 'darwin' or not ROOT.is_relative_to(Path.home()):
            self.skipTest('the OS sandbox is macOS sandbox-exec; the repo must be under the home folder')
        probe = (f"import socket\nfor act in (lambda: open({str(ROOT / 'studio' / 'common.py')!r}).read(), lambda: open('/tmp/studio_os_probe', 'w'),"
                 " lambda: socket.create_connection(('127.0.0.1', 9), 1)):\n"
                 "    try:\n        act(); print('OPEN')\n    except OSError as e:\n        print('DENIED', e.errno)\n")
        command, isolation = contrib_probe.isolated([sys.executable, '-I', '-c', probe], [])
        self.assertEqual(isolation, 'os+hook')
        out = subprocess.run(command, capture_output=True, text=True, timeout=60, cwd='/').stdout.split('\n')
        self.assertEqual(out[:3], ['DENIED 1'] * 3, out)


if __name__ == '__main__':
    unittest.main()
