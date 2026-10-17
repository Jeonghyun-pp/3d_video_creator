"""Picks as taste (studio/taste_picks.py): what was chosen over what, in the user's words, read back as a few lines."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from studio import patterns, taste_picks
from studio.common import StudioError


class TastePicksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        file = Path(self.tmp.name) / 'taste.json'
        for module in (patterns, taste_picks):
            patcher = mock.patch.object(module, 'TASTE', file)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_pick_is_the_users_words_and_its_options_axes(self):
        with self.assertRaises(StudioError):
            taste_picks.record('story', {'hook': 'failure'}, [], 'User: chose 1')     # an agent's wrapper
        with self.assertRaises(StudioError):
            taste_picks.record('poster', {'hook': 'failure'}, [], '이게 좋아요 진짜')  # no such kind
        taste_picks.record('story', {'hook': 'failure', 'tone': 'tense'}, [{'hook': 'question', 'tone': 'calm'}, {'hook': 'myth', 'tone': 'tense'}],
                           '실패로 시작하는 게 제일 재밌네요')
        pref = taste_picks.preference('story')
        self.assertEqual(pref['hook'], {'failure': 1.0, 'question': -0.5, 'myth': -0.5})
        self.assertEqual(pref['tone'], {'tense': 1.0, 'calm': -0.5})   # a passed-over option sharing the pick's value costs it nothing

    def test_the_summary_says_leanings_and_words_and_keeps_pattern_entries(self):
        self.assertEqual(taste_picks.prompt_block(), '')
        patterns.taste_add('hook', 'subject_travel', 'higher', 'the key moment close and moving', '결정적 순간은 가깝고 역동적으로')
        for _ in range(2):
            taste_picks.record('story', {'hook': 'failure'}, [{'hook': 'question'}], '실패로 시작하는 게 좋아요')
        taste_picks.record('take', {'recipe': 'dig_in'}, [{'recipe': 'pass_by'}], '파고드는 게 낫네요')
        text = taste_picks.summary()['text']
        self.assertIn('story 2', text)
        self.assertIn('hook failure', text.split('Leans to:')[1].split('\n')[0])
        self.assertIn('hook question', text.split('Passes over:')[1].split('\n')[0])
        self.assertIn('"파고드는 게 낫네요"', text)
        self.assertIn('subject_travel', text)
        self.assertLessEqual(len(text.splitlines()), 5)
        patterns.taste_remove('t01')                 # removing a pattern entry keeps the picks
        self.assertEqual(len(taste_picks.picks()), 3)
        self.assertEqual(taste_picks.summary(['take'])['picks'], 1)


if __name__ == '__main__':
    unittest.main()
