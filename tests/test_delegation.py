"""Delegated runs (studio/decisions.py): the user's words hand the run's decisions to the agent - recorded verbatim,
claimed once by the project the launcher's run creates - and the agent's own choices go to a log no gate reads."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest import mock

from studio import decisions
from studio.common import StudioError
from studio.project import init_project, load_project, validate_schema

BRIEF = {'request': 'engine cutaway', 'shots': [{'shot_id': 's01', 'frame_count': 30}]}


class DelegationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pending = self.root / '.studio' / 'delegation.json'
        patcher = mock.patch.object(decisions, 'PENDING_DELEGATION', self.pending)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def new(self, name='p'):
        return init_project(name, BRIEF, self.root)

    def test_delegation_is_the_users_words_and_changes_no_gate(self):
        project = Path(self.new()['project_path'])
        recorded = decisions.delegate(project, '알아서 다 정해서 진행해')['delegation']
        self.assertEqual(load_project(project)['delegation']['user_words'], '알아서 다 정해서 진행해')
        self.assertTrue(recorded['recorded_at'])
        decisions.require(project, 'build')   # the ladder stays opt-in: nothing newly gated
        self.assertFalse(decisions.adopted(project))
        with self.assertRaises(StudioError):
            decisions.delegate(project, 'User (paraphrased): go ahead')   # an agent's wrapper is refused
        with self.assertRaises(StudioError):
            decisions.delegate(project, 'ok')

    def test_the_launchers_words_go_to_the_first_project_created(self):
        self.pending.parent.mkdir(parents=True)
        self.pending.write_text(json.dumps({'user_words': '전부 맡길게, 끝까지 해'}))
        first = self.new('first')
        self.assertEqual(first['delegation']['user_words'], '전부 맡길게, 끝까지 해')
        self.assertFalse(self.pending.exists())
        self.assertNotIn('delegation', self.new('second'))   # one launch, one project

    def test_agent_notes_are_numbered_records_not_approvals(self):
        project = Path(self.new()['project_path'])
        a = decisions.note(project, 'runners', 'sweep + catmull_rom', 'smooth bends like the photo')
        b = decisions.note(project, 'head', 'casting with ribs and bosses', 'cast look; details over 24 px')
        self.assertEqual([a['n'], b['n']], [1, 2])
        self.assertEqual([n['topic'] for n in decisions.notes(project)], ['runners', 'head'])
        with self.assertRaises(StudioError):
            decisions.note(project, 'x', '', 'why')
        with self.assertRaises(StudioError):
            decisions.note(project, 'head', 'casting', 'because', evidence='no/such/file.png')
        self.assertFalse(decisions.adopted(project))

    def test_schema_refuses_unknown_delegation_keys(self):
        data = load_project(Path(self.new()['project_path']))
        data['delegation'] = {'user_words': 'go ahead please', 'recorded_at': 'now', 'approved_by': 'agent'}
        with self.assertRaises(Exception):
            validate_schema(data, 'project')


if __name__ == '__main__':
    unittest.main()
