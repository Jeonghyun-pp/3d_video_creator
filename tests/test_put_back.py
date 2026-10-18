"""common.put_back (2026-10-18): files changed in place for a while are put back when the block ends, and - when the
process is killed inside, as a broker timeout kills it - by the next taker of the project lock."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.common import RESTORE_JOURNAL, StudioError, lock, put_back   # noqa: E402

KILLED = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from studio.common import lock, put_back
root = Path(sys.argv[1])
with lock(root / '.project.lock', reentrant=True), put_back(root, [root / 'shot.json', root / 'new.json']):
    (root / 'shot.json').write_text('candidate')
    (root / 'new.json').write_text('left over')
    os.kill(os.getpid(), 9)   # killed inside: no finally runs
"""


class PutBackTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / 'shot.json').write_text('original')

    def test_the_block_ending_puts_the_files_back(self):
        with self.assertRaises(ValueError):
            with lock(self.root / '.project.lock', reentrant=True), put_back(self.root, [self.root / 'shot.json', self.root / 'new.json']):
                (self.root / 'shot.json').write_text('candidate')
                (self.root / 'new.json').write_text('made inside')
                raise ValueError('the solve failed')
        self.assertEqual((self.root / 'shot.json').read_text(), 'original')
        self.assertFalse((self.root / 'new.json').exists())          # a file that was not there is not there after
        self.assertFalse((self.root / RESTORE_JOURNAL).exists())

    def test_a_process_killed_inside_is_put_back_by_the_next_taker_of_the_lock(self):
        done = subprocess.run([sys.executable, '-c', KILLED, str(self.root), str(ROOT)], capture_output=True, text=True)
        self.assertEqual(done.returncode, -9, done.stderr)
        self.assertEqual((self.root / 'shot.json').read_text(), 'candidate')   # left as the killed process wrote it
        self.assertTrue((self.root / RESTORE_JOURNAL).is_file())
        with lock(self.root / '.project.lock'):                                 # any command taking the project lock
            self.assertEqual((self.root / 'shot.json').read_text(), 'original')
            self.assertFalse((self.root / 'new.json').exists())
        self.assertFalse((self.root / RESTORE_JOURNAL).exists())

    def test_a_second_put_back_while_one_waits_is_refused(self):
        (self.root / RESTORE_JOURNAL).write_text('{"files": {}}')
        with self.assertRaises(StudioError):
            with put_back(self.root, [self.root / 'shot.json']):
                pass


if __name__ == '__main__':
    unittest.main()
