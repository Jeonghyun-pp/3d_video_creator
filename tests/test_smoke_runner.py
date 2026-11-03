"""Optional-tool smoke skips are reported separately from executed successes."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


class SmokeRunnerTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('smoke_runner', Path(__file__).with_name('run_smokes.py'))
        self.runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.runner)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'fixture.py'
        self.path.write_text('pass\n')

    def run_fixture(self, output, code=0):
        with mock.patch.object(self.runner.subprocess, 'run', return_value=subprocess.CompletedProcess([], code, output, '')):
            return self.runner.run(self.path)

    def test_optional_skips_are_not_passes(self):
        for prefix in ('', 'STUDIO_SMOKE '):
            row = self.run_fixture(prefix + '{"ok": true, "skipped": "no optional tool"}\n')
            self.assertFalse(row['passed'])
            self.assertEqual(row['skipped'], 'no optional tool')

    def test_a_failure_cannot_be_hidden_by_a_skip_message(self):
        row = self.run_fixture('{"skipped": "no optional tool"}\n', code=1)
        self.assertFalse(row['passed'])
        self.assertIsNone(row['skipped'])

    def test_expected_negative_measurements_do_not_skip_a_smoke(self):
        row = self.run_fixture('{"ok": true, "planted_defect": {"ok": false}}\n')
        self.assertTrue(row['passed'])
        self.assertIsNone(row['skipped'])

    def test_summary_separates_skips_without_reporting_a_failure(self):
        with mock.patch.object(self.runner, 'SMOKES', self.path.parent), \
                mock.patch.object(self.runner.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '{"skipped":"no CAD"}', '')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = self.runner.main([])
        summary = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(code, 0)
        self.assertEqual((summary['total'], summary['passed'], summary['skipped'], summary['unexpected_failures']), (1, 0, 1, []))
