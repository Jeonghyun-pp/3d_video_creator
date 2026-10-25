"""What the watchers ask (2026-10-25, plan M6): harsh is motion an eye cannot follow - no clear destination, a sudden
lunge, a shake or swing - and flying fast to a destination in view and stopping there is not harsh; `lost` asks whether
a viewer can tell where the camera is and what to look at. A lost cut is a finding like any other (reel answer takes it)."""
import unittest

from studio import reel_judge, storyboard


class JudgeQuestionsTest(unittest.TestCase):
    def test_harsh_is_what_an_eye_cannot_follow_and_a_visible_destination_is_not(self):
        for prompt in (reel_judge.PROMPT, storyboard.JUDGE_PROMPT):
            self.assertIn('destination', prompt)
            self.assertIn('not harsh', prompt)
            self.assertIn('lost', prompt)

    def test_lost_is_asked_and_is_a_finding(self):
        self.assertIn('lost', reel_judge.ITEMS)
        self.assertIn('lost', storyboard.JUDGE_ITEMS)
        self.assertIs(reel_judge.WANTING['lost'], True)
        row = {'shots': [{'shot_id': 'a', 'reads': True, 'lost': True}]}
        self.assertEqual(reel_judge.findings(row), {'a': ['lost']})
        self.assertIn('lost', reel_judge._schema(['a'])['properties']['shots']['items']['required'])


if __name__ == '__main__':
    unittest.main()
