"""The clay seen by a viewer who does not know the plan (studio/reel_see.py): each cut without words or captions, told what
it sees, what changes and what it learns, and the reel's one point; then a reader of words alone matches those accounts
against each cut's point and the reel's. Why (2026-10-26, the user): whether the reel worked is judged on the picture by
someone who does not know the plan, never on the text."""
from pathlib import Path
import subprocess
import tempfile
import unittest
import unittest.mock

from PIL import Image

from studio import plan_read, quality_gate, reel, reel_see
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, load_shot, shot_path
from studio.reel import CLAY


def clip(path, colour='gray'):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=c={colour}:s=90x160:r=30', '-frames:v', '30',
                    '-pix_fmt', 'yuv420p', str(path)], check=True)
    return path


class SeeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(init_project('see', {'request': 'x', 'shots': [{'shot_id': s, 'frame_count': 30} for s in ('s01', 's02')]},
                                   self.tmp.name)['project_path'])
        for sid, colour, point in (('s01', 'red', 'machines keep the box away from hands'), ('s02', 'blue', 'one speck kills a whole chip')):
            shot = load_shot(self.p, sid)
            shot.update({'scene_version': 'v0001', 'goal': f'the cut {sid}', 'narration': {**shot['narration'], 'text': f'대사 {sid} 입니다.'},
                         'direction': {'show_how': {'point': point, 'proof': ['frame'], 'way': {'id': 'glance', 'rejected': {
                             'id': 'follow', 'why': 'nothing to follow here'}}}}})
            write_json(shot_path(self.p, sid), shot)
            clip(self.p / f'{sid}.mp4', colour)
        self.made = reel.clay(self.p, clip_of=lambda p, s, v: f'{s}.mp4')
        write_json(self.p / 'decisions' / 'shotlist.json', {'body': {'point': {'says': 'a speck of dust kills a chip', 'proof': {
            'what': 'w', 'rows': ['s02/frame']}, 'opening': {'does': 'asks', 'why': 'w'}}, 'shots': []}})

    def tearDown(self):
        self.tmp.cleanup()

    def viewer(self, asked, learns=('a box is lifted away', 'a grey block')):
        def read(images, prompt, schema, target):
            asked.append(('blind', images, prompt))
            write_json(target, {'answer': {'cuts': [{'shot_id': s, 'seen': 'shapes', 'changes': 'something moves', 'learns': l}
                                                    for s, l in zip(('s01', 's02'), learns)], 'says': 'boxes move around a room'}})
        return read

    def matcher(self, asked, seen=('yes', 'no'), reel_seen='no'):
        def read(images, prompt, schema, target):
            asked.append(('match', images, prompt))
            write_json(target, {'answer': {'cuts': [{'shot_id': s, 'seen': v, 'why': 'w'} for s, v in zip(('s01', 's02'), seen)],
                                           'reel': {'seen': reel_seen, 'why': 'the viewer saw moving boxes, not a dead chip'}}})
        return read

    def test_the_clay_also_makes_each_cut_without_words(self):
        rows = {r['shot_id']: r for r in read_json(self.p / CLAY / 'clay.json')['shots']}
        self.assertEqual(rows['s01']['see_clip'], 'see/s01.mp4')
        self.assertTrue((self.p / CLAY / 'see' / 's01.mp4').is_file())
        # the captioned clip has its words drawn at the bottom; the one the blind viewer sees does not
        def bottom(path):
            out = Path(self.tmp.name) / f'{path.stem}_{path.parent.name}.png'
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(path), '-frames:v', '1', str(out)], check=True)
            with Image.open(out) as im:
                return im.convert('RGB').crop((0, 120, 90, 160)).getcolors(10000)
        self.assertNotEqual(bottom(self.p / CLAY / 's01.mp4'), bottom(self.p / CLAY / 'see' / 's01.mp4'))

    def test_the_viewer_knows_nothing_of_the_plan_and_the_matcher_sees_no_picture(self):
        asked = []
        got = reel_see.see(self.p, viewer=self.viewer(asked), matcher=self.matcher(asked))
        (_, images, blind), (_, none, match) = asked
        self.assertEqual(len(images), 2)
        for secret in ('machines keep the box away from hands', 'one speck kills', 'a speck of dust kills', '대사', 'the cut s01'):
            self.assertNotIn(secret, blind)
        self.assertEqual(none, [])
        for said in ('a box is lifted away', 'machines keep the box away from hands', 'a speck of dust kills a chip', 'boxes move around a room'):
            self.assertIn(said, match)
        self.assertEqual(got['findings'], {'s02': ['POINT_UNSEEN'], 'reel': ['REEL_POINT_UNSEEN']})
        again = reel_see.see(self.p, viewer=self.viewer(asked), matcher=self.matcher(asked))   # the same clay: not asked again
        self.assertEqual((len(asked), again['round']), (2, 1))

    def test_a_render_waits_until_the_cut_was_seen_as_it_is(self):
        with self.assertRaises(StudioError) as caught:
            reel_see.seen(self.p, 's01')
        self.assertEqual(caught.exception.code, 'REEL_UNSEEN')
        reel_see.see(self.p, viewer=self.viewer([]), matcher=self.matcher([]))
        reel_see.seen(self.p, 's01')
        shot = load_shot(self.p, 's01'); shot['scene_version'] = 'v0002'; write_json(shot_path(self.p, 's01'), shot)
        with self.assertRaises(StudioError):   # another version is another picture
            reel_see.seen(self.p, 's01')

    def test_an_unseen_point_sends_the_cut_back_to_its_plan_only_where_calibrated(self):
        from studio.storyboard import envelope
        self.assertEqual(set(reel_see.SEE), {'POINT_UNSEEN', 'REEL_POINT_UNSEEN'})
        write_json(self.p / 'decisions' / 'shotlist.json', {'body': {'shots': [{'shot_id': 's02', 'purpose': 'p', 'line_ids': [], 'duration_s': 1}]}})
        with unittest.mock.patch.dict(reel_see.SEE, {'POINT_UNSEEN': {**reel_see.SEE['POINT_UNSEEN'], 'floor': True}}):
            got = reel_see.see(self.p, viewer=self.viewer([]), matcher=self.matcher([]))
        self.assertEqual(envelope(self.p, 's02')['reopened']['level'], 'plan')
        self.assertIn('sent back to its plan', ' '.join(got['next']))
        self.assertIsNone(envelope(self.p, 's01'))
        with unittest.mock.patch.dict(reel_see.SEE, {'POINT_UNSEEN': {**reel_see.SEE['POINT_UNSEEN'], 'floor': False}}):
            got = reel_see.see(self.p, viewer=self.viewer([]), matcher=self.matcher([]), force=True)
        self.assertTrue(any('advice' in n for n in got['next']))


if __name__ == '__main__':
    unittest.main()
