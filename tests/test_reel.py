"""The reel as a whole (studio/reel.py): the clay cut captioned on one page, and one shot redone as options."""
from pathlib import Path
import json
import shutil
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

    def test_a_failed_clip_is_said_and_the_rest_cut_together_at_one_size(self):
        for sid in ('s01', 's02'):
            shot = load_shot(self.p, sid); shot['scene_version'] = 'v0001'; shot['narration']['text'] = '말 ' * 80
            write_json(shot_path(self.p, sid), shot)
        made = clip(self.p / 's01.mp4')
        wide = self.p / 'wide.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=gray:s=160x90:r=24', '-frames:v', '6',
                        '-pix_fmt', 'yuv420p', str(wide)], check=True)

        def clip_of(path, shot_id, version):
            if shot_id == 's02':
                raise StudioError('BUSY', 'held')
            return made.name
        result = reel.clay(self.p, clip_of=clip_of)
        self.assertEqual((result['shots'], [f['shot_id'] for f in result['failed']]), (['s01'], ['s02']))
        self.assertIn('클립을 만들지 못함', Path(result['page']).read_text())
        joined = reel._join([made, wide], self.root / 'joined.mp4', 30)
        size = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=p=0',
                               str(joined)], capture_output=True, text=True).stdout.strip()
        self.assertEqual(size, '90,160')   # every clip into the first one's frame

    def test_a_cut_a_model_makes_is_in_the_clay_as_its_picture_until_it_is_made(self):
        """2026-10-20 (studio/maker.py): a generated cut has no Blender version; the clay held it out and the reel had
        holes. Its start picture is held for the cut's length, said so."""
        from studio import concept
        from studio.world import planned_route
        picture = self.root / 'start.png'
        Image.new('RGB', (90, 160), (30, 90, 160)).save(picture)
        concept.add(self.p, 's02', picture, 'the lake at dawn', frame='start')
        shot = load_shot(self.p, 's02')
        shot['route'] = planned_route(shot, {'mode': 'generative', 'role': 'mood', 'generate_only': []}, (), 30)
        write_json(shot_path(self.p, 's02'), shot)
        made = clip(self.p / 's01.mp4')
        shot = load_shot(self.p, 's01'); shot['scene_version'] = 'v0001'; write_json(shot_path(self.p, 's01'), shot)
        result = reel.clay(self.p, clip_of=lambda p, s, v: made.name)
        self.assertEqual((result['shots'], result['skipped']), (['s01', 's02'], []))
        rows = read_json(Path(result['page']).parent / 'clay.json')['shots']
        self.assertEqual(rows[1]['version'], 'not generated yet (picture)')
        probe = subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-show_entries', 'stream=nb_read_frames', '-of', 'csv=p=0',
                                result['reel']], capture_output=True, text=True)
        self.assertEqual(probe.stdout.strip(), '24')

    def test_the_clay_shows_the_shots_graphics_under_its_words(self):
        """2026-10-20 (canal): a flow drawn along the culverts explains the shot, and the clay left graphics out."""
        made = clip(self.p / 'g.mp4', frames=3)
        layer = self.root / 'layer'
        layer.mkdir()
        for n in range(3):   # a red mark in the top right from the second frame on
            img = Image.new('RGBA', (90, 160), (0, 0, 0, 0))
            if n:
                img.paste((255, 0, 0, 255), (60, 40, 85, 70))
            img.save(layer / f'frame_{n:06d}.png')
        out = self.root / 'g_cap.mp4'
        reel.caption(made, out, 's01 v0001', '', 30, graphics=layer)
        stills = self.root / 'still_%d.png'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(out), str(stills)], check=True)
        first, second = (Image.open(self.root / f'still_{n}.png').convert('RGB').getpixel((72, 55)) for n in (1, 2))
        self.assertLess(first[0], 160)                          # nothing drawn yet
        self.assertTrue(second[0] > 200 and second[1] < 80)     # the layer's mark, in its place

    def test_a_long_caption_is_cut_to_its_rows(self):
        from PIL import ImageDraw, ImageFont
        from studio.common import REPO, font_file
        draw = ImageDraw.Draw(Image.new('RGB', (360, 640)))
        font = ImageFont.truetype(str(font_file({}, REPO)), 27)
        rows = reel._fit(draw, '아주 긴 내레이션 문장이 계속 이어집니다 ' * 12, font, 320, 640)
        self.assertEqual(len(rows), reel.CAPTION_ROWS)
        self.assertTrue(rows[-1].endswith('…'))
        self.assertEqual(reel._fit(draw, '짧은 줄', font, 320, 640), ['짧은 줄'])

    def test_a_shot_redone_as_options_is_built_side_by_side_and_the_pick_becomes_the_shot(self):
        with self.assertRaises(StudioError):
            reel.start(self.p, 's01', 'ok')
        plan = reel.start(self.p, 's01', '더 멀리서, 바다가 보이게', count=2)
        self.assertTrue(all('The user asked for this shot: 더 멀리서' in m['prompt'] for o in plan['options'] for m in o['moments']))
        held = reel.start(self.p, 's01', '레버 클로즈업으로', count=4, fixed='size=close')   # what the words settle, every option holds
        self.assertEqual(len({tuple(sorted(o['axes'].items())) for o in held['options']}), 4)
        self.assertTrue(all(o['axes']['size'] == 'close' and 'a close shot' in o['moments'][0]['prompt'] for o in held['options']))
        with self.assertRaises(StudioError):   # one option left on the axes: nothing to choose between
            reel.start(self.p, 's01', '레버 클로즈업으로', fixed='size=close,view=low,place=isolated')
        shutil.rmtree(self.p / reel.REDO / 's01' / held['round'])
        nearer = reel.start(self.p, 's01', '끝을 더 가깝게', count=4, fixed='size=medium|close')   # not wide, either of the rest
        self.assertTrue(all(o['axes']['size'] in ('medium', 'close') for o in nearer['options']))
        self.assertEqual(nearer['fixed'], {'size': ['medium', 'close']})
        with self.assertRaises(StudioError):
            reel.start(self.p, 's01', '끝을 더 가깝게', fixed='size=closer')
        shutil.rmtree(self.p / reel.REDO / 's01' / nearer['round'])
        image = self.root / 'pic.png'
        Image.new('RGB', (90, 160), (90, 90, 90)).save(image)
        reel.add(self.p, 's01', 1, 'start', image)
        with self.assertRaises(StudioError):   # one option drawn: nothing to compare yet
            reel.build(self.p, 's01', measure_fn=lambda p, s, w: None, apply_fn=lambda p, s: None, variants_fn=None)
        for option, frame in ((1, 'end'), (2, 'start'), (2, 'end')):
            reel.add(self.p, 's01', option, frame, image)
        before = shot_path(self.p, 's01').read_text()
        seen = []

        def apply(path, shot_id):   # the solve sees exactly the option's pictures, and writes a camera
            seen.append([c['concept_id'] for c in frames(path, shot_id)])
            write_json(path / 'concepts' / shot_id / 'frames.json', {'pictures': {}})   # what frames measure would leave
            shot = load_shot(path, shot_id)
            shot['camera'] = {**shot['camera'], 'target_anchor': f'anchor{len(seen)}'}
            write_json(shot_path(path, shot_id), shot)
        asked = []
        built = reel.build(self.p, 's01', measure_fn=lambda p, s, w: asked.append(w), apply_fn=apply, subject='the shift lever',
                           variants_fn=lambda p, s, takes, times: {'rev': 'v01', 'takes': takes})
        self.assertEqual(seen, [['c01', 'c02'], ['c03', 'c04']])
        self.assertEqual(asked, ['the shift lever'] * 2)                       # the words reach every reading, kept with the round
        self.assertEqual(read_json(self.p / reel.REDO / 's01' / built['round'] / 'plan.json')['subject'], 'the shift lever')
        self.assertEqual([t['change']['camera']['target_anchor'] for t in built['takes']], ['anchor1', 'anchor2'])
        self.assertEqual(shot_path(self.p, 's01').read_text(), before)          # the shot is put back
        self.assertFalse((self.p / 'concepts' / 's01' / 'frames.json').exists())   # and no reading the solve left
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
