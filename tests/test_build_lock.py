"""A build holds the project lock to number its version and to commit it, not while Blender runs (studio/blender.py):
other shots build at the same time (studio/pool.py), and a number taken stays taken until its build ends."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from studio import blender
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, shot_path
from tests.decisions_fixture import settle

SCENE = {'world': {'kind': 'blockout', 'color': [0.2, 0.24, 0.4], 'strength': 0.5, 'samples': 16},
         'materials': {'body': {'color': [0.6, 0.6, 0.6]}},
         'primitives': [{'id': 'machine', 'shape': 'box', 'size': [2, 2, 2], 'at': [0, 0, 1], 'material': 'body'}]}

FREE = ('import fcntl,sys; s=open(sys.argv[1],"a")\n'
        'try:\n fcntl.flock(s, fcntl.LOCK_EX|fcntl.LOCK_NB); print("free")\nexcept BlockingIOError:\n print("held")')


class BuildLockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(init_project('bl', {'request': 'x', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, self.tmp.name)['project_path'])
        settle(self.p)
        shot = read_json(shot_path(self.p, 's'))
        shot['scene'] = SCENE
        write_json(shot_path(self.p, 's'), shot)

    def tearDown(self):
        self.tmp.cleanup()

    def test_blender_runs_with_the_project_lock_free_and_its_number_reserved(self):
        seen = {}

        def fake(command, log=None, timeout=3600, env=None):
            seen['lock'] = subprocess.run([sys.executable, '-c', FREE, str(self.p / '.project.lock')], capture_output=True,
                                          text=True).stdout.strip()
            versions = shot_path(self.p, 's').parent / 'versions'
            seen['reserved'] = sorted(p.name for p in versions.glob('.v*.reserved'))
            seen['next'] = blender._reserve_version(versions)[0]   # another build of the shot meanwhile takes the next number
            raise StudioError('COMMAND_FAILED', 'stopped here')
        with patch('studio.blender.run_command', side_effect=fake):
            with self.assertRaises(StudioError):
                blender._build_shot(self.p, 's', None)
        self.assertEqual(seen['lock'], 'free')
        self.assertEqual(seen['reserved'], ['.v0001.reserved'])
        self.assertEqual(seen['next'], 'v0002')
        versions = shot_path(self.p, 's').parent / 'versions'
        self.assertEqual(sorted(p.name for p in versions.glob('.v0001.reserved')), [])   # its build ended: given back
        self.assertTrue(list(versions.glob('failed_*')))

    def test_numbers_count_built_and_reserved_versions(self):
        versions = shot_path(self.p, 's').parent / 'versions'
        (versions / 'v0003').mkdir(parents=True)
        first, mark = blender._reserve_version(versions)
        second, _ = blender._reserve_version(versions)
        self.assertEqual((first, second), ('v0004', 'v0005'))
        self.assertTrue(mark.is_file())


if __name__ == '__main__':
    unittest.main()
