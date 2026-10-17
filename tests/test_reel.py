"""The reel as a whole (studio/reel.py): the clay cut captioned on one page, and one shot redone as options."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest import mock

from PIL import Image

from studio import patterns, reel, taste_picks
from studio.common import StudioError, read_json, write_json
from studio.concept import concepts, frames
from studio.project import init_project, load_shot, shot_path


def clip(path, frames=12):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=gray:s=90x160:r=30', '-frames:v', str(frames),
                    '-pix_fmt', 'yuv420p', str(path)], check=True)
    return path


class ReelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.p = Path(init_project('reel', {'request': 'x', 'shots': [{'shot_id': 's01', 'frame_count': 12}, {'shot_id': 's02', 'frame_count': 12}]},
                                   self.root)['project_path'])
        for module in (patterns, taste_picks):
            patcher = mock.patch.object(module, 'TASTE', self.root / 'taste.json')
            patcher.start(); self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_clay_cut_is_captioned_and_put_on_one_page(self):
        shot = load_shot(self.p, 's01'); shot['scene_version'] = 'v0001'; shot['narration']['text'] = '부스터가 바다 위 배로 돌아옵니다.'
        write_json(shot_path(self.p, 's01'), shot)
        made = clip(self.p / 's01.mp4')
        result = reel.clay(self.p, clip_of=lambda p, s, v: made.name)
        self.assertEqual((result['shots'], result['skipped']), (['s01'], ['s02']))
        self.assertTrue(Path(result['reel']).is_file())
        page = Path(result['page']).read_text()
        self.assertIn('다시 할 샷 번호와 한마디', page)
        self.assertIn('부스터가 바다 위 배로 돌아옵니다.', page)
        probe = subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-show_entries', 'stream=nb_read_frames', '-of', 'csv=p=0',
                                result['reel']], capture_output=True, text=True)
        self.assertEqual(probe.stdout.strip(), '12')

    def test_a_shot_redone_as_options_is_built_side_by_side_and_the_pick_becomes_the_shot(self):
        with self.assertRaises(StudioError):
            reel.start(self.p, 's01', 'ok')
        plan = reel.start(self.p, 's01', '더 멀리서, 바다가 보이게', count=2)
        self.assertTrue(all('The user asked for this shot: 더 멀리서' in m['prompt'] for o in plan['options'] for m in o['moments']))
        image = self.root / 'pic.png'
        Image.new('RGB', (90, 160), (90, 90, 90)).save(image)
        reel.add(self.p, 's01', 1, 'start', image)
        with self.assertRaises(StudioError):   # one option drawn: nothing to compare yet
            reel.build(self.p, 's01', measure_fn=lambda p, s: None, apply_fn=lambda p, s: None, variants_fn=None)
        for option, frame in ((1, 'end'), (2, 'start'), (2, 'end')):
            reel.add(self.p, 's01', option, frame, image)
        before = shot_path(self.p, 's01').read_text()
        seen = []

        def apply(path, shot_id):   # the solve sees exactly the option's pictures, and writes a camera
            seen.append([c['concept_id'] for c in frames(path, shot_id)])
            shot = load_shot(path, shot_id)
            shot['camera'] = {**shot['camera'], 'target_anchor': f'anchor{len(seen)}'}
            write_json(shot_path(path, shot_id), shot)
        built = reel.build(self.p, 's01', measure_fn=lambda p, s: None, apply_fn=apply,
                           variants_fn=lambda p, s, takes, times: {'rev': 'v01', 'takes': takes})
        self.assertEqual(seen, [['c01', 'c02'], ['c03', 'c04']])
        self.assertEqual([t['change']['camera']['target_anchor'] for t in built['takes']], ['anchor1', 'anchor2'])
        self.assertEqual(shot_path(self.p, 's01').read_text(), before)          # the shot is put back
        self.assertEqual(frames(self.p, 's01'), [])                              # and no option is chosen yet
        result = reel.pick(self.p, 's01', 2, '두 번째, 바다가 넓게 보여서 좋아요', pick_fn=lambda *a: {'picked': a[3]})
        self.assertEqual(result['storyboard'], {'picked': 'o2'})
        self.assertEqual([c['concept_id'] for c in frames(self.p, 's01')], ['c03', 'c04'])
        row = taste_picks.picks('redo')[0]
        self.assertEqual((row['chosen'], len(row['rejected'])), (plan['options'][1]['axes'], 1))
        with self.assertRaises(StudioError):
            reel.pick(self.p, 's01', 1, '역시 첫 번째로 할게요', pick_fn=lambda *a: None)


if __name__ == '__main__':
    unittest.main()
