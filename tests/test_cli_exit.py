"""CLI envelope: the exit code follows the error's own retryable flag, tool bugs are told apart from bad input,
and the status refresh after a command never turns a completed command into a failure."""
import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch

from studio import __main__ as cli
from studio.common import StudioError
from studio.project import init_project


def run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(argv)
    return code, json.loads(out.getvalue())


class CliExitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = init_project('cli_test', {'request': 'cli', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, self.tmp.name)['project_path']
        self.argv = ['route', 'check', '--project', self.project, '--shot', 's', '--operation', 'build']

    def tearDown(self):
        self.tmp.cleanup()

    def test_exit_code_follows_retryable(self):
        for retryable, expected in ((True, 1), (False, 2)):
            with patch('studio.routing.check', side_effect=StudioError('ANY_CODE', 'x', retryable=retryable)):
                code, body = run(self.argv)
            self.assertEqual((code, body['error']['code']), (expected, 'ANY_CODE'))

    def test_tool_bug_is_internal_error_not_input_invalid(self):
        with patch('studio.routing.check', side_effect=TypeError('NoneType is not subscriptable')):
            code, body = run(self.argv)
        self.assertEqual((code, body['error']['code']), (3, 'INTERNAL_ERROR'))
        self.assertIn('TypeError', body['error']['message'])

    def test_status_refresh_failure_is_a_warning(self):
        with patch('studio.project.status_project', side_effect=KeyError('output_path')):
            code, body = run(self.argv)
        self.assertEqual((code, body['ok']), (0, True))
        self.assertTrue(any(w.startswith('STATUS_REFRESH_FAILED') for w in body['warnings']))


if __name__ == '__main__':
    unittest.main()
