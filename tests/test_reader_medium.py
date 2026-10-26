"""The readers (reel see, reel judge, excuses) are told what the cuts are made of, from what reel clay recorded per cut
(reel.MEDIA: a new medium is a row): clay cuts are read with exactly the words they were calibrated with, a cut made
from pictures is not called grey clay, and a reel mixing them says which cut is which."""
import unittest

from studio import reel, reel_judge, reel_see, excuses


OLD_BLIND = ('It is grey clay blocking: the shapes are stand-ins for real things - judge what the\nshapes, their places and their '
             'motion show, never surfaces or colour.')


class MediumTest(unittest.TestCase):
    def test_clay_reads_as_it_was_calibrated(self):
        prompt = reel_see.blind_prompt([('s01', 2.0), ('s02', 3.0)], media=['clay', 'clay'])
        self.assertIn(OLD_BLIND, prompt)
        self.assertEqual(prompt, reel_see.blind_prompt([('s01', 2.0), ('s02', 3.0)]))   # no medium recorded: clay, as before
        self.assertIn('You watch the grey clay blocking of a short vertical explainer reel', reel_judge.prompt_for(['clay'], 'x', 4))
        self.assertIn('are the grey clay blocking', excuses.prompt_for(['clay'], 'x'))

    def test_pictures_are_not_called_clay(self):
        for prompt, phrase in ((reel_see.blind_prompt([('c1', 2.0)], media=['pictures']), 'is'), (reel_judge.prompt_for(['pictures'], 'x', 4), 'head'),
                               (excuses.prompt_for(['pictures'], 'x'), 'are')):
            self.assertNotIn('clay', prompt)
            self.assertIn(reel.MEDIA['pictures'][phrase], prompt)

    def test_a_mixed_reel_says_which_cut_is_which(self):
        prompt = reel_see.blind_prompt([('c1', 2.0), ('s02', 3.0)], media=['pictures', 'clay'])
        self.assertIn('image 0', prompt.split('Cuts')[1] if 'Cuts' in prompt else '')
        self.assertIn('grey clay', prompt)

    def test_every_medium_says_every_phrase(self):
        for name, row in reel.MEDIA.items():
            self.assertEqual(set(row), set(reel.MEDIA['clay']), name)


if __name__ == '__main__':
    unittest.main()
