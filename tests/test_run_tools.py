"""Run record tools: astra_run_metrics finds every rollout of a launcher run (main + subagents) from its thread id and
sums their tokens; run_status counts passing / failed versions with no shell globs, an empty folder being a 0."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rollout(path, meta, total):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [{'type': 'session_meta', 'timestamp': '2026-10-07T01:00:00Z', 'payload': meta},
             {'type': 'turn_context', 'timestamp': '2026-10-07T01:00:01Z', 'payload': {'model': 'gpt-6-astra', 'effort': 'medium'}},
             {'type': 'event_msg', 'timestamp': '2026-10-07T01:10:00Z',
              'payload': {'type': 'token_count', 'info': {'total_token_usage': {'input_tokens': total, 'total_tokens': total}}}}]
    path.write_text('\n'.join(json.dumps(line) for line in lines) + '\n')


class RunToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_metrics_group_a_launcher_run_with_its_subagents(self):
        metrics = load('astra_run_metrics')
        sessions = self.root / 'sessions' / '2026' / '10' / '07'
        rollout(sessions / 'rollout-a-main.jsonl', {'id': 'main', 'session_id': 'main'}, 1000)
        rollout(sessions / 'rollout-b-sub.jsonl', {'id': 'sub1', 'session_id': 'main', 'parent_thread_id': 'main'}, 300)
        rollout(sessions / 'rollout-c-other.jsonl', {'id': 'other', 'session_id': 'other'}, 999)
        log = self.root / 'log'
        log.mkdir()
        (log / 'events.jsonl').write_text(json.dumps({'type': 'thread.started', 'thread_id': 'main'}) + '\n')
        with mock.patch.object(metrics, 'SESSIONS', self.root / 'sessions'):
            self.assertEqual(metrics.launcher_thread(log), 'main')
            self.assertIsNone(metrics.launcher_thread(sessions / 'rollout-a-main.jsonl'))   # a rollout is not a launcher log
            group = metrics.run_group(log)
        self.assertEqual(group['agents'], 2)
        self.assertEqual(group['tokens_all_agents']['total_tokens'], 1300)   # the other run is not counted
        self.assertTrue(group['main']['rollout'].endswith('rollout-a-main.jsonl'))

    def test_status_counts_versions_without_globs(self):
        run_status = load('run_status')
        project = self.root / 'p'
        for name in ('v0001', 'v0002', 'failed_ab', '.building-x'):
            (project / 'shots' / 's01' / 'versions' / name).mkdir(parents=True)
        (project / 'shots' / 's02' / 'versions').mkdir(parents=True)   # empty: a 0, not an error
        (project / 'shots' / 's01' / 'renders' / 'r1').mkdir(parents=True)
        log = self.root / 'log'
        log.mkdir()
        (log / 'run.json').write_text(json.dumps({'started_at': 't0'}))
        now = run_status.status(project, log)
        self.assertEqual(now['shots']['s01'], {'passed': ['v0001', 'v0002'], 'failed': ['failed_ab'], 'renders': 1})
        self.assertEqual(now['shots']['s02'], {'passed': [], 'failed': [], 'renders': 0})
        self.assertFalse(now['run']['ended'])
        (log / 'run.json').write_text(json.dumps({'started_at': 't0', 'exit_code': 0, 'finished_at': 't1'}))
        self.assertTrue(run_status.status(project, log)['run']['ended'])


if __name__ == '__main__':
    unittest.main()
