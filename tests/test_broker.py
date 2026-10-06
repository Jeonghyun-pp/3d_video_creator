"""The studio broker (studio/broker.py): Blender runs outside the agent's sandbox, inside the studio's own - and only
studio commands, never the ones that record the lines themselves."""
import os
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
        self.assertIn('-m', cmd)

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
