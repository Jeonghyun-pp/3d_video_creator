"""The studio broker (studio/broker.py): Blender runs outside the agent's sandbox, inside the studio's own - and only
studio commands, never the ones that record the lines themselves."""
import os
import sys
import unittest
from unittest import mock

from studio import broker
from studio.common import StudioError, blender_binary


class BrokerTest(unittest.TestCase):
    def test_only_studio_commands(self):
        for bad in ([], 'shot build', [1], ['--help'], ['freeze', 'record', '--user-words', 'x'], ['contrib', 'list'], ['_worker', 'a', 'b']):
            with self.assertRaises(StudioError, msg=bad):
                broker.command(bad)
        cmd = broker.command(['shot', 'build', '--project', 'p', '--shot', 's'])
        self.assertEqual(cmd[-6:], ['studio', 'shot', 'build', '--project', 'p', '--shot', 's'][-6:])
        self.assertIn('-I', cmd)   # started isolated from the checkout (studio/_launch.py; studio/seal.py)
        self.assertTrue(cmd[cmd.index('-I') + 4].endswith('studio/_launch.py'))

    def test_of_contrib_only_a_check_on_a_project_here_runs_and_in_the_brokers_own_process(self):
        import tempfile
        from pathlib import Path
        from studio.common import REPO
        root = Path(REPO) / 'projects' / 'harness_validation'
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            (Path(tmp) / 'project.json').write_text('{}')
            ok = ['contrib', 'check', '--project', tmp, '--name', 'bicycle_part']
            self.assertEqual(broker.unwrapped(ok), ['contrib', 'check', '--project', str(Path(tmp).resolve()), '--name', 'bicycle_part'])
            self.assertIsNotNone(broker.unwrapped(ok + ['--version', 'v001']))
            for bad in (['contrib', 'promote', '--project', tmp, '--name', 'x'], ['contrib', 'check', '--project', tmp, '--name', '../x'],
                        ['contrib', 'check', '--project', tempfile.gettempdir(), '--name', 'x'], ok + ['--version', '1'],
                        ['contrib', 'check', '--project', tmp, '--name', 'x', '--extra', 'y'], ['contrib', 'check', '--name', 'x', '--project', '/']):
                self.assertIsNone(broker.unwrapped(bad), bad)
                with self.assertRaises(StudioError, msg=bad):
                    broker.command(bad)   # never a new interpreter for contrib
            with mock.patch.object(broker.subprocess, 'run', side_effect=AssertionError('no new interpreter')):
                from studio import contrib
                with mock.patch.object(contrib, 'check_entry', return_value={'ok': True, 'problems': []}) as checked:
                    got = broker.run(ok)
                self.assertEqual((got['ok'], got['exit_code'], got['checked_by']), (True, 0, 'broker'))
                checked.assert_called_once_with(str(Path(tmp).resolve()), 'bicycle_part', None)

    def test_profile_denies_network_and_limits_writes(self):
        text = broker.profile()
        self.assertIn('(deny network*)', text)
        self.assertIn('(deny file-write*)', text)
        self.assertNotIn('(subpath "/Users")', text)

    def test_blender_refused_inside_the_agent_sandbox_with_the_way_out(self):
        with mock.patch.dict(os.environ, {'CODEX_SANDBOX': 'seatbelt'}):
            os.environ.pop('STUDIO_BROKERED', None)
            with self.assertRaises(StudioError) as caught:
                blender_binary()
            self.assertEqual(caught.exception.code, 'BLENDER_NEEDS_BROKER')
            self.assertTrue(broker.in_agent_sandbox())
        with mock.patch.dict(os.environ, {'CODEX_SANDBOX': 'seatbelt', 'STUDIO_BROKERED': '1'}):
            self.assertFalse(broker.in_agent_sandbox())

    def test_the_mcp_server_lists_studio_run(self):
        from studio import workbench_mcp
        self.assertIn('studio_run', {t['name'] for t in workbench_mcp.TOOLS})


if __name__ == '__main__':
    unittest.main()
