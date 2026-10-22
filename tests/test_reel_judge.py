"""The clay reel watched whole (studio/reel_judge.py): every cut in order to one watcher, a few cuts to make again, and
a bounded loop - two rounds, then the user sees the reel."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from studio import reel_judge
from studio.common import StudioError, write_json
from studio.project import init_project
from studio.reel import CLAY


def clip(path, colour):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=c={colour}:s=90x160:r=30', '-frames:v', '30',
                    '-pix_fmt', 'yuv420p', str(path)], check=True)


class ReelJudgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(init_project('rj', {'request': 'x', 'shots': [{'shot_id': s, 'frame_count': 30} for s in ('a', 'b', 'c')]},
                                   self.tmp.name)['project_path'])
        (self.p / CLAY).mkdir(parents=True)
        self.colours = ['red', 'green', 'blue']
        self.paint()

    def paint(self):
        for sid, colour in zip(('a', 'b', 'c'), self.colours):
            clip(self.p / CLAY / f'{sid}.mp4', colour)

    def tearDown(self):
        self.tmp.cleanup()

    def reader(self, asked):
        def read(images, prompt, schema, target):
            asked.append((len(images), prompt))
            write_json(target, {'answer': {'shots': [{'shot_id': s, 'reads': True, 'dull': s == 'b', 'repeats_before': s == 'b',
                                                      'harsh': False, 'note': 'n'} for s in ('a', 'b', 'c')],
                                           'hook_holds': True, 'flow': 'b repeats a', 'redo': [{'shot_id': 'b', 'why': 'same as a', 'try': 'from below'}]}})
        return read

    def test_every_cut_is_watched_in_order_and_a_round_says_what_to_make_again(self):
        asked = []
        got = reel_judge.judge(self.p, reader=self.reader(asked))
        self.assertEqual(asked[0][0], 3)
        self.assertIn('shot 1 = b', asked[0][1])
        self.assertEqual((got['round'], got['redo'][0]['shot_id']), (1, 'b'))
        self.assertTrue(got['next'][0].startswith('storyboard takes --shot b'))
        again = reel_judge.judge(self.p, reader=self.reader(asked))      # the same clay: the same verdict, not asked again
        self.assertEqual((len(asked), again['round']), (1, 1))

    def test_the_loop_stops_after_its_rounds_until_the_user_opens_another(self):
        asked = []
        for colour in ('white', 'black'):   # two rounds, each on a remade clay
            reel_judge.judge(self.p, reader=self.reader(asked))
            self.colours[1] = colour
            self.paint()
        with self.assertRaises(StudioError) as caught:
            reel_judge.judge(self.p, reader=self.reader(asked))
        self.assertEqual(caught.exception.code, 'REEL_JUDGE_ROUNDS')
        got = reel_judge.judge(self.p, reader=self.reader(asked), user_words='한 번 더 다듬어봐요')
        self.assertEqual(got['round'], 3)


if __name__ == '__main__':
    unittest.main()
