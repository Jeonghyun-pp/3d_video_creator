"""A run started without its user (2026-10-18): no command takes the user's words there - an unattended run recorded
"do as recommended", quoted from its request, as the pick of a shot whose takes the user had turned down. The
launcher's own delegation words still reach the project the run creates."""
import ast
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from studio import assets, decisions, workbench_mcp
from studio.common import CODE, UNATTENDED, StudioError, blender_env
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

    def test_a_command_the_broker_runs_for_the_run_is_unattended_too(self):
        # 2026-10-24 audit: the broker built its child's environment from an allow-list without the flag, so
        # `studio_run ["review", "record", ...]` with user_words in its JSON passed as the user's approval
        self.assertEqual(blender_env().get(UNATTENDED), '1')

    def test_an_asset_turnaround_is_never_approved_by_the_run(self):
        with self.assertRaises(StudioError) as refused:
            assets.approve_asset('/nowhere/manifest.json', 'approved', 'me', '좋아요 이대로 승인합니다')
        self.assertEqual(refused.exception.code, 'USER_WORDS_UNATTENDED')

    def test_every_command_that_takes_the_users_words_checks_them(self):
        """A function given user_words checks them (check_user_words, or a weakening) or hands them on to a call that
        does; a length test of its own lets a run with no user write them (2026-10-24: asset approval, frames depart,
        a beat's answer, the taste profile)."""
        checks = ('check_user_words', 'refuse_unattended', 'weakening', 'require_weakening')
        plain = ('len', 'bool', 'str', 'print', 'isinstance')
        # writers handed words their caller already checked - the command that calls each one is scanned too
        recorders = {'camera_taste.build_profile': 'camera_taste.learn', 'generative/review.record_approval': 'routing.approve',
                     'seal.seal': 'freeze.record', 'story.materialize': 'story.pick / decisions'}
        # frozen (network_worker, contrib_gate): fixed only on the user's freeze words - listed until then
        frozen = {'generative/fal_client.reconcile', 'contrib.promote_by_user'}
        name = lambda f: f.attr if isinstance(f, ast.Attribute) else getattr(f, 'id', '')  # noqa: E731
        unchecked = []
        for path in sorted((CODE / 'studio').rglob('*.py')):
            for fn in ast.walk(ast.parse(path.read_text())):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or \
                        'user_words' not in [a.arg for a in fn.args.args + fn.args.kwonlyargs]:
                    continue
                calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)]
                passed = any(name(c.func) in checks or (name(c.func) not in plain and any(
                    isinstance(a, ast.Name) and a.id == 'user_words' for a in c.args + [k.value for k in c.keywords])) for c in calls)
                key = f"{path.relative_to(CODE / 'studio').with_suffix('')}.{fn.name}"
                if not passed and key not in recorders and key not in frozen:
                    unchecked.append(key)
        self.assertEqual(unchecked, [])

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
