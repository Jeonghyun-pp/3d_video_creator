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


class OtherGatesClosedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.project = Path(init_project('closed2', {'request': 'x', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, self.tmp.name)['project_path'])

    def test_a_generated_clip_needs_a_persons_pass_on_any_project(self):
        from studio.video_review import require
        clip = self.project / 'clip.mp4'; clip.write_bytes(b'x')
        with self.assertRaises(StudioError) as caught:   # off the ladder: it was asked only when the ladder listed it
            require(self.project, {'shot_id': 's', 'narration': {'text': ''}}, clip)
        self.assertEqual(caught.exception.code, 'VIDEO_REVIEW_MISSING')

    def test_an_asset_with_no_manifest_is_unknown_and_a_real_spec_asks_a_turnaround(self):
        from studio import jobs
        from studio.common import read_json, write_json
        project = read_json(self.project / 'project.json')
        with self.assertRaises(StudioError) as caught:
            jobs._require_turnaround(self.project, project, {'shot_id': 's', 'asset_instances': [{'asset_id': 'nowhere_0001'}]}, 'v0001')
        self.assertEqual(caught.exception.code, 'ASSET_UNKNOWN')
        (self.project / 'subjects/boat').mkdir(parents=True)
        write_json(self.project / 'subjects/boat/spec.json', {'subject_mode': 'specific_real'})   # the brief stays schematic
        with self.assertRaises(StudioError) as caught:
            jobs._require_turnaround(self.project, project, {'shot_id': 's', 'subjects': [{'subject_id': 'boat'}]}, 'v0001')
        self.assertEqual(caught.exception.code, 'TURNAROUND_APPROVAL_REQUIRED')

    def test_a_pictured_moment_with_no_reading_is_said(self):
        from unittest import mock
        from studio import frame_targets
        shot = {'shot_id': 's', 'camera': {'move': {'frames': [{'at': 0.0, 'picture': {'concept_id': 'c01'}}]}}}
        with mock.patch.object(frame_targets, 'targets', return_value=[]), mock.patch.object(frame_targets, 'moments', return_value=[]):
            codes = [l['code'] for l in frame_targets.gate_lines(self.project, shot, {}, {})]
        self.assertEqual(codes, ['FRAME_PICTURE_UNREAD'])

    def test_a_flow_that_measured_nothing_is_refused(self):
        from studio.gates import SOFTENABLE
        self.assertEqual(SOFTENABLE['REEL_FLOW_UNMEASURED'][0], 'broken')
        self.assertEqual(SOFTENABLE['FRAME_PICTURE_UNREAD'][0], 'broken')


if __name__ == '__main__':
    unittest.main()
