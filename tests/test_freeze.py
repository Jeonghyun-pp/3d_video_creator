"""Frozen code is checked when a build or render starts: a changed file refuses, a baseline is recorded only with the
user's words, and the baseline lives outside the workspace an agent can write."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio import freeze
from studio.common import StudioError


class FreezeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / 'frozen.json'

    def tearDown(self):
        self.dir.cleanup()

    def test_missing_baseline_is_a_warning(self):
        self.assertTrue(freeze.require_code_frozen(self.path)[0].startswith('FROZEN_BASELINE_MISSING'))

    def test_record_needs_the_users_words(self):
        with self.assertRaises(StudioError):
            freeze.record('', self.path)
        with self.assertRaises(StudioError):
            freeze.record('User (paraphrased): ok', self.path)
        self.assertFalse(self.path.exists())

    def test_recorded_then_unchanged_passes_and_a_changed_hash_refuses(self):
        freeze.record('수정 금지 기준선 기록해', self.path)
        self.assertEqual(freeze.require_code_frozen(self.path), [])
        data = json.loads(self.path.read_text())
        name = next(iter(data['groups']['render_fingerprint']))
        data['groups']['render_fingerprint'][name] = '0' * 64
        self.path.write_text(json.dumps(data))
        with self.assertRaises(StudioError) as caught:
            freeze.require_code_frozen(self.path)
        self.assertEqual(caught.exception.code, 'FROZEN_CODE_CHANGED')
        self.assertIn(name, caught.exception.message)

    def test_every_code_group_is_watched_and_the_check_watches_itself(self):
        groups = freeze.code_groups()
        self.assertEqual(set(groups), set(freeze.CODE_GROUPS))
        self.assertIn('studio/freeze.py', groups['guard'])
        self.assertIn('studio/blender_ops/look.py', groups['look_inputs'])
        self.assertIn('control_pass.py', groups['control'])

    def test_render_group_is_the_fingerprinted_code(self):
        from studio.jobs import RENDER_CODE
        self.assertEqual(sorted(freeze.code_groups()['render_fingerprint']), sorted(RENDER_CODE))
        self.assertIn('studio/blender_ops/scene_roles.py', RENDER_CODE)   # an import, not a list entry

    def test_network_group_is_the_gate_plus_network_capable_code_the_entries_reach(self):
        network = freeze.code_groups()['network_worker']
        for name in ('studio/broker.py', 'studio/remote_render.py', 'studio/remote_gpu.py', 'studio/generative/fal_client.py'):
            self.assertIn(name, network)
        self.assertNotIn('studio/jobs.py', network)   # reached, but opens no connection
        with tempfile.TemporaryDirectory() as tmp:   # N+1: a module that starts shelling out to curl joins the group
            module = Path(tmp) / 'm.py'
            module.write_text("import subprocess\nsubprocess.run(['curl', 'https://example.org'])\n")
            self.assertTrue(freeze._network_capable(module))
            module.write_text("from urllib.request import urlopen\n")
            self.assertTrue(freeze._network_capable(module))
            module.write_text("NAMES = ('ssh', 'rsync')\nimport json\n")
            self.assertFalse(freeze._network_capable(module))

    def test_a_group_added_later_warns_until_recorded(self):
        freeze.record('기준선 기록', self.path)
        data = json.loads(self.path.read_text()); del data['groups']['contrib_gate']; self.path.write_text(json.dumps(data))
        warnings = freeze.require_code_frozen(self.path)
        self.assertTrue(warnings and warnings[0].startswith('FROZEN_GROUP_UNRECORDED'))

    def test_default_baseline_is_outside_the_repository(self):
        self.assertFalse(freeze.baseline_path().resolve().is_relative_to(ROOT))


class BlenderEnvTest(unittest.TestCase):
    def test_keys_never_reach_blender(self):
        import os
        from unittest import mock
        from studio.common import blender_env
        with mock.patch.dict(os.environ, {'FAL_KEY': 'x', 'OPENAI_API_KEY': 'y', 'AWS_SECRET_ACCESS_KEY': 'z', 'GITHUB_TOKEN': 't',
                                          'LC_ALL': 'C', 'BLENDER_USER_SCRIPTS': '/s'}):
            env = blender_env()
        self.assertFalse({'FAL_KEY', 'OPENAI_API_KEY', 'AWS_SECRET_ACCESS_KEY', 'GITHUB_TOKEN'} & set(env))
        self.assertEqual((env['LC_ALL'], env['BLENDER_USER_SCRIPTS'], env['PYTHONPATH']), ('C', '/s', ''))
        self.assertIn('PATH', env)


if __name__ == '__main__':
    unittest.main()
