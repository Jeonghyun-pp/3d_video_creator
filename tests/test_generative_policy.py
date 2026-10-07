"""Shot role decides what a generated take may be used for (studio/generative/policy.py), for every caller."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from studio.common import StudioError, file_hash, write_json
from studio.edit import latest_generated
from studio.generative.policy import judge, policy_for, role_of
from studio.project import default_shot, validate_shot

PASSED = {'passed': True, 'reasons': []}
FAILED = {'passed': False, 'reasons': ['median edge IoU 0.23 < 0.5']}


def shot(mode='hybrid', role=None, labels=()):
    s = default_shot('s01', 60, {'request': 'policy test'})
    route = {'mode': mode, 'status': 'proposed', 'decided_by': 'agent',
             'generative': {'provider': 'fal', 'model': 'wan-2.2-vace', 'operation': 'video_to_video', 'prompt_ref': 'prompts/s01.txt',
                            'duration_seconds': 2.0, 'usd_per_second': None, 'max_attempts': 1, 'text_in_frame': False, 'ai_disclosure': True}}
    if role:
        route['role'] = role
    s['route'] = route
    s['labels'] = list(labels)
    return s


class PolicyTest(unittest.TestCase):
    def test_a_lost_part_blocks_explain_unless_kept_and_light_turn_is_said(self):
        lost = {'structure_qa': PASSED, 'qa': {'structure': PASSED, 'parts': {'lost': ['valve']}, 'light': {'angle_deg': 70.0}}}
        verdict = judge(lost, policy_for(shot()))
        self.assertFalse(verdict['usable']); self.assertIn('valve', verdict['reasons'][0])
        self.assertTrue(any('light turned 70.0' in w for w in verdict['warnings']))
        self.assertTrue(judge({**lost, 'kept': {'parts': ['valve']}}, policy_for(shot()))['usable'])   # put back from Blender
        mood = judge(lost, policy_for(shot(role='mood')))
        self.assertTrue(mood['usable']); self.assertTrue(any('valve' in w for w in mood['warnings']))

    def test_default_roles(self):
        self.assertEqual(role_of({'mode': 'generative'}), 'mood')    # never had a structure input
        self.assertEqual(role_of({'mode': 'hybrid'}), 'explain')
        self.assertEqual(role_of({'mode': 'hybrid', 'role': 'mood'}), 'mood')

    def test_user_pick_carries_a_structure_miss_only_without_overlays_and_when_allowed(self):
        allowed = {'explain_generated': 'pick_without_overlays'}
        self.assertFalse(judge({'structure_qa': FAILED}, policy_for(shot()))['pickable'])            # default policy: gate
        bare = policy_for(shot(), allowed)
        before = judge({'structure_qa': FAILED}, bare)
        self.assertEqual((before['usable'], before['pickable']), (False, True))
        picked = judge({'structure_qa': FAILED}, bare, picked=True)
        self.assertTrue(picked['usable']); self.assertIn("user's pick", picked['warnings'][-1])
        label = {'label_id': 'l', 'text': 't', 'anchor': 'a/b', 'start_frame': 0, 'end_frame': 10, 'slot': 'upper_left', 'occlusion_policy': 'hide'}
        labelled = policy_for(shot(labels=[label]), allowed)                                          # labels need anchors_2d
        self.assertFalse(judge({'structure_qa': FAILED}, labelled, picked=True)['usable'])
        self.assertFalse(judge({'structure_qa': FAILED}, labelled)['pickable'])

    def test_explain_needs_a_passed_hybrid_structure_gate(self):
        explain = policy_for(shot())
        self.assertTrue(judge({'structure_qa': PASSED}, explain)['usable'])
        verdict = judge({'structure_qa': FAILED}, explain)
        self.assertFalse(verdict['usable'])
        self.assertIn('structure gate failed', verdict['reasons'][0])
        self.assertFalse(judge({}, policy_for(shot(mode='generative', role='explain')))['usable'])

    def test_mood_is_usable_with_structure_and_check_warnings(self):
        manifest = {'qa': {'structure': FAILED, 'morph': {'warnings': ['morph: structure jump at frame 40']}}}
        verdict = judge(manifest, policy_for(shot(role='mood')))
        self.assertTrue(verdict['usable'])
        self.assertEqual(len(verdict['warnings']), 2)

    def test_mood_shot_refuses_labels(self):
        label = {'label_id': 'l1', 'text': '기둥', 'anchor': 'col', 'start_frame': 0, 'end_frame': 30, 'slot': 'upper_left', 'occlusion_policy': 'hide'}
        validate_shot(shot(role='explain', labels=[label]))
        with self.assertRaises(StudioError) as caught:
            validate_shot(shot(role='mood', labels=[label]))
        self.assertEqual(caught.exception.code, 'ROUTE_ROLE_CONFLICT')

    def test_edit_never_picks_an_unusable_take(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root)

            def take(key, structure):
                directory = project / 'shots/s01/generated' / key
                directory.mkdir(parents=True)
                clip = directory / 'clip.mp4'
                clip.write_bytes(key.encode())
                write_json(directory / 'clip.json', {'status': 'complete', 'frame_count': 60, 'clip_path': str(clip),
                                                     'clip_sha256': file_hash(clip), 'structure_qa': structure})
            take('a_failed', FAILED)
            explain = shot()
            self.assertEqual(latest_generated(project, explain, 60), {'rejected': ['a_failed: structure gate failed: median edge IoU 0.23 < 0.5']})
            take('b_passed', PASSED)
            self.assertEqual(latest_generated(project, explain, 60)['manifest_path'].parent.name, 'b_passed')   # the only usable take
            mood = deepcopy(explain); mood['route']['role'] = 'mood'
            with self.assertRaises(StudioError) as caught:                                                    # both usable now
                latest_generated(project, mood, 60)
            self.assertEqual(caught.exception.code, 'GENERATION_SELECTION_REQUIRED')


if __name__ == '__main__':
    unittest.main()
