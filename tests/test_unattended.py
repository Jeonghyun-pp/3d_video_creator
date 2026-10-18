"""A run started without its user (2026-10-18): no command takes the user's words there - an unattended run recorded
"do as recommended", quoted from its request, as the pick of a shot whose takes the user had turned down. The
launcher's own delegation words still reach the project the run creates."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from studio import decisions, workbench_mcp
from studio.common import UNATTENDED, StudioError
from studio.generative.review import check_user_words
from studio.project import init_project


class UnattendedTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {UNATTENDED: '1'})
        patcher.start(); self.addCleanup(patcher.stop)

    def test_the_users_words_are_refused_everywhere_in_the_run(self):
        with self.assertRaises(StudioError) as refused:
            check_user_words('추천대로 하면 될 것 같아요', 'redo pick')
        self.assertEqual(refused.exception.code, 'USER_WORDS_UNATTENDED')
        self.assertIn('REPORT.md', refused.exception.recovery)
        with mock.patch.dict(os.environ, {UNATTENDED: ''}):
            self.assertEqual(check_user_words('추천대로 하면 될 것 같아요'), '추천대로 하면 될 것 같아요')   # the user at hand

    def test_a_brokered_command_is_refused_before_it_leaves_the_server(self):
        with mock.patch('studio.broker.run') as broker:
            with self.assertRaises(StudioError) as refused:
                workbench_mcp.run_tool('studio_run', {'args': ['reel', 'redo', 'pick', '--option', '3', '--user-words', '추천대로 해줘']})
        self.assertEqual(refused.exception.code, 'USER_WORDS_UNATTENDED')
        broker.assert_not_called()

    def test_the_launchers_delegation_still_reaches_the_new_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(init_project('u', {'request': 'x', 'shots': [{'shot_id': 's', 'frame_count': 12}]}, tmp)['project_path'])
            pending = Path(tmp) / 'delegation.json'
            pending.write_text('{"user_words": "알아서 끝까지 해줘"}')
            with mock.patch.object(decisions, 'PENDING_DELEGATION', pending):
                self.assertEqual(decisions.claim_pending_delegation(p)['user_words'], '알아서 끝까지 해줘')
            with self.assertRaises(StudioError):   # the run itself delegating in "the user's" words
                decisions.delegate(p, '알아서 끝까지 해줘')


if __name__ == '__main__':
    unittest.main()
