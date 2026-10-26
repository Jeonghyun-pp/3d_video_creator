"""The v2 launcher instruction (studio2/launch.py, scripts/reel_agent.py --v2): goals, criteria and stop points only -
never a picture. The agent decides every picture; a brief that carries one is a hole in the process (the user,
2026-10-26: a user-suggested better choice is a process hole)."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LaunchInstructionTest(unittest.TestCase):
    def test_goals_criteria_and_stop_points(self):
        from studio2 import launch
        text = launch.instruction('a reel about how a cleanroom keeps dust off the wafers')
        for head in ('GOAL', 'CRITERIA', 'STOP'):
            self.assertIn(head, text)
        self.assertIn('.agents/skills/reel-production-v2/SKILL.md', text)
        self.assertIn('sheet build', text)                       # the first stop: the approval sheet
        self.assertIn('USER_WORDS_UNATTENDED', text)              # no words of the user's are written in the run
        self.assertTrue(text.rstrip().endswith('a reel about how a cleanroom keeps dust off the wafers'))

    def test_it_never_carries_a_picture(self):
        """No picture in words: none of the grammar's picture phrases (how an opening, a view is drawn) and no place,
        part or framing of any reel."""
        from studio2 import grammar, launch
        text = launch.instruction('x').lower()
        table = grammar.load()
        phrases = [row.get('prompt') for field in ('opens', 'view') for row in table['fields'][field]['values'].values() if row.get('prompt')]
        self.assertTrue(phrases)
        for phrase in phrases:
            self.assertNotIn(phrase.lower()[:40], text)
        for word in ('cleanroom', 'wafer', 'close-up of', 'seen from', 'frontal', 'from above'):
            self.assertNotIn(word, text)

    @unittest.skipUnless(shutil.which('codex'), 'codex CLI not installed')
    def test_the_launcher_sends_it_with_v2(self):
        out = subprocess.run([sys.executable, 'scripts/reel_agent.py', 'make a test', '--dry-run', '--v2'], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout
        prompt = json.loads(out)['command'][-1]
        self.assertIn('reel-production-v2/SKILL.md', prompt)
        self.assertNotIn('route plan', prompt)                      # v1's 3D instructions are not v2's
        self.assertTrue(prompt.rstrip().endswith('make a test'))


if __name__ == '__main__':
    unittest.main()
