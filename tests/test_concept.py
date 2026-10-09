"""Concept frames without Blender: provenance, the pick's authority (user words, or the agent's note only when delegated),
staleness, and the cut's hero frame critiqued against the concept in qa (2026-10-09)."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from studio import concept
from studio.common import StudioError
from studio.decisions import delegate
from studio.project import init_project
from studio.qa import _concept_critique


class ConceptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.p = Path(init_project('c', {'request': 'concept test', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, self.root)['project_path'])
        self.image = self.root / 'gen.png'
        Image.new('RGB', (90, 160), (30, 60, 120)).save(self.image)

    def tearDown(self):
        self.tmp.cleanup()

    def test_not_required_until_ladder_or_delegation(self):
        self.assertIsNone(concept.require(self.p, 's'))
        delegate(self.p, '이번 실행은 에이전트에게 맡긴다', [])
        with self.assertRaises(StudioError) as caught:
            concept.require(self.p, 's')
        self.assertEqual(caught.exception.code, 'CONCEPT_UNPICKED')

    def test_the_agent_picks_only_in_a_delegated_run(self):
        concept.add(self.p, 's', self.image, prompt='x')
        with self.assertRaises(StudioError):
            concept.pick(self.p, 's', 'c01', agent_note='looks right')          # not delegated: the user's words only
        picked = concept.pick(self.p, 's', 'c01', user_words='이 그림으로 가자')
        self.assertEqual(picked['decision']['by'], 'user')

    def test_qa_critiques_the_hero_frame_against_the_concept(self):
        delegate(self.p, '이번 실행은 에이전트에게 맡긴다', [])
        concept.add(self.p, 's', self.image)
        concept.pick(self.p, 's', 'c01', agent_note='closest to the brief')
        video = self.root / 'cut.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=0xC07020:s=90x160:r=30', '-frames:v', '30', str(video)], check=True)
        rows = _concept_critique(self.p, video, [{'shot_id': 's', 'start_frame': 0, 'frame_count': 30}], self.root / 'out')
        self.assertEqual((rows[0]['shot_id'], rows[0]['concept_id'], rows[0]['frame']), ('s', 'c01', 15))
        self.assertGreater(rows[0]['score'], 0)                                   # orange cut against a blue concept
        self.assertTrue(rows[0]['top'])


    def test_appearance_builds_are_counted_after_the_first(self):
        from studio import repair
        from studio.common import read_json, write_json
        project = read_json(self.p / 'project.json'); project['limits']['appearance_iterations_per_shot'] = 2
        write_json(self.p / 'project.json', project)
        self.assertEqual(repair.appearance(self.p, 's', 'v0001'), {'used': 0, 'budget': 2, 'remaining': 2})   # construction
        repair.appearance(self.p, 's', 'v0002')
        self.assertEqual(repair.appearance(self.p, 's', 'v0002')['used'], 1)                                # same version: once
        self.assertEqual(repair.appearance(self.p, 's', 'v0003'), {'used': 2, 'budget': 2, 'remaining': 0})

if __name__ == '__main__':
    unittest.main()
