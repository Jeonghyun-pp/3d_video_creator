"""The sandbox policy (blender_ops/sandbox.judge), pure: what an author step may do while it runs."""
import os
import tempfile
import unittest
from pathlib import Path

from studio.blender_ops.sandbox import caller_frame, judge


class SandboxPolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.job = self.root / 'author_job.json'
        self.rules = {'write_roots': [self.root], 'protected': {self.job}}

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_stay_in_the_build_folder(self):
        self.assertIsNone(judge('open', (str(self.root / 'note.json'), 'w', 0), **self.rules))
        self.assertIsNone(judge('open', ('/etc/hosts', 'r', 0), **self.rules))                       # reading is free
        self.assertIn('build output', judge('open', ('/tmp/../etc/x', 'w', 0), **self.rules))
        self.assertIn('read-only', judge('open', (str(self.job), 'w', 0), **self.rules))
        self.assertIn('read-only', judge('open', (str(self.job), None, os.O_WRONLY | os.O_TRUNC), **self.rules))
        self.assertIn('build output', judge('os.rename', (str(self.root / 'a'), '/elsewhere/b', None, None), **self.rules))
        self.assertIn('build output', judge('shutil.rmtree', ('/Users', None, None), **self.rules))

    def test_processes_network_native_code_and_strings_are_refused(self):
        for event, args in (('subprocess.Popen', ('ls', [], None, None)), ('os.system', ('ls',)), ('socket.connect', (None, ('1.1.1.1', 80))),
                            ('ctypes.dlopen', ('libc',)), ('sys.addaudithook', ()), ('os.putenv', ('A', 'B')), ('webbrowser.open', ('x',))):
            self.assertIsNotNone(judge(event, args, **self.rules), event)
        self.assertIsNotNone(judge('compile', (b'1', '<string>'), **self.rules))
        self.assertIsNone(judge('compile', (b'1', __file__), **self.rules))

    def test_imports_are_judged_only_for_author_frames(self):
        allowed = {'bpy', 'math'}
        self.assertIsNotNone(judge('import', ('subprocess', None, [], [], []), **self.rules, allowed_imports=allowed, importer_is_author=True))
        self.assertIsNone(judge('import', ('math', None, [], [], []), **self.rules, allowed_imports=allowed, importer_is_author=True))
        self.assertIsNone(judge('import', ('subprocess', None, [], [], []), **self.rules, allowed_imports=allowed, importer_is_author=False))


class CallerFrameTest(unittest.TestCase):
    def test_a_shallow_stack_has_no_caller_frame(self):
        def inner():
            return caller_frame(1)
        self.assertIs(inner().f_code, self.test_a_shallow_stack_has_no_caller_frame.__func__.__code__)
        self.assertIsNone(caller_frame(10_000))   # deeper than any real stack: no frame, no exception


if __name__ == '__main__':
    unittest.main()
