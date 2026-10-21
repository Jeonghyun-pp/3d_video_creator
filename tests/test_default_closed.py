"""Gates are closed until their inputs exist (2026-10-21): a missing layer, parent or measurement is a refusal or a
said gap, never a check left out. Why: the submarine run skipped the subject spec and its photos, so the photo check
never ran; with no decisions/ladder.json every gate was open; with no script the shot-list lint checked no line."""
from pathlib import Path
import tempfile
import unittest

from studio import decisions
from studio.common import StudioError
from studio.project import init_project
from tests.decisions_fixture import settle


class DecisionsClosedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.project = Path(init_project('closed', {'request': 'x', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, self.tmp.name)['project_path'])

    def test_every_operation_is_refused_until_its_layers_exist(self):
        for operation in decisions.GATES:   # N+1: an operation added to GATES is closed with no line of its own
            with self.assertRaises(StudioError) as caught:
                decisions.require(self.project, operation)
            self.assertEqual(caught.exception.code, 'DECISION_MISSING', operation)
        settle(self.project)
        for operation in decisions.GATES:
            decisions.require(self.project, operation)

    def test_a_layer_is_linted_against_parents_that_exist(self):
        errors = decisions.lint(self.project, 'shotlist', {'shots': []})['errors']
        self.assertTrue(any(e.startswith('PARENT_MISSING: shotlist stands on script') for e in errors), errors)
        self.assertTrue(any(e.startswith('PARENT_MISSING: shotlist stands on world') for e in errors), errors)

    def test_a_decision_with_a_missing_parent_stays_a_draft(self):
        decisions.delegate(self.project, '알아서 끝까지 진행해')
        from tests.test_story import StoryLayerTest
        out = decisions.write(self.project, 'script', StoryLayerTest.SCRIPT, agent_note='no facts or story yet')
        self.assertEqual(out['state']['state'], 'proposed', out)


if __name__ == '__main__':
    unittest.main()
