"""Does a cut look like it belongs to the reference series (studio2/style.py): the reference's own cuts as strips with
their subtitles masked, a reader's verdict per cut kept for its pictures (a failure is redrawn from the plan, never
passed by a new clip of the same pictures), and a calibration that makes the verdict a floor only where it held."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from studio import decisions
from studio.common import read_json, write_json
from tests import studio2_fixture as fx


def video(target, seconds=2, colour='testsrc'):
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'{colour}=size=108x192:rate=30', '-t', str(seconds), '-pix_fmt', 'yuv420p',
                    str(target)], check=True)
    return target


def say(score_of):
    """A reader answering by what it is shown last (the candidate strip's file name): its score out of 10."""
    calls = []

    def reader(images, prompt, schema, target):
        calls.append(Path(images[-1]).name)
        score = score_of(Path(images[-1]).name)
        write_json(target, {'answer': {'score': score, 'belongs': 'yes' if score >= 7 else 'partly' if score >= 4 else 'no', 'closer': 'a test',
                                       'further': 'a test', 'opens': 0 if schema['properties']['opens'].get('type') == 'integer' else False}})
    reader.calls = calls
    return reader


class StyleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [mock.patch('studio2.style.POINTER', self.root / 'pointer.json'), mock.patch('studio2.style.TABLE', self.root / 'style_check.json')]
        for p in self.patches:
            p.start()
        self.make_refs()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def make_refs(self):
        from studio2 import style
        measured, refs = self.root / 'measured', self.root / 'refs'
        manifest = []
        for n in range(3):
            reel = f'reel{n}'
            video(refs / f'{reel}.mp4', 8)
            manifest.append({'reel': reel, 'file': f'{reel}.mp4'})
            shots = [{'shot': k, 'start_s': 2.0 * k, 'len_s': 2.0, 'labels': [{'run': 1, 'role': 'explain',
                                                                               'subtitle': {'present': True, 'center_y': 0.7, 'lines': 1}}]}
                     for k in range(4)]
            write_json(measured / reel / 'grammar.json', {'media': {'fps': 30}, 'shots': shots})
        write_json(refs / 'manifest.json', manifest)
        self.refs = style.build_refs(measured, refs / 'manifest.json', self.root / 'strips', per_reel=2, context=1, context_per_reel=2)

    def test_reference_strips_hold_out_the_reels_not_shown(self):
        from studio2 import style
        data = style.refs()
        self.assertEqual(data['context'], ['r00', 'r01'])                   # the first reel's cuts shown with every reading
        self.assertEqual(data['held_out'], ['r02', 'r03', 'r04', 'r05'])    # the other reels' cuts: never shown as context
        self.assertAlmostEqual(data['mask_y'], 0.7)
        self.assertTrue(Path(data['items'][0]['strip']).is_file())

    def made_cut(self, colour='testsrc'):
        """A v2 project whose cut c1 has a made clip (route composite) - its pictures as picked (none here)."""
        from studio.project import load_shot, shot_path
        from studio.common import file_hash
        if not hasattr(self, 'path'):
            self.path = fx.project(self.root / 'p')
            decisions.write(self.path, 'cuts', fx.body(), agent_note=fx.TEST_NOTE)
        shot = load_shot(self.path, 'c1')
        key = f'{abs(hash(colour)) % 16 ** 16:016x}'
        folder = shot_path(self.path, 'c1').parent / 'composite' / key
        clip = video(folder / 'clip.mp4', shot['duration_frames'] / 30, colour)
        write_json(folder / 'clip.json', {'status': 'complete', 'frame_count': shot['duration_frames'], 'clip_path': str(clip.relative_to(self.path)),
                                          'clip_sha256': file_hash(clip)})
        shot['route']['composite'] = {'key': key, 'cut_sha': '0' * 16}
        write_json(shot_path(self.path, 'c1'), shot)
        return key

    def test_a_failing_cut_keeps_its_verdict_for_its_pictures(self):
        from studio2 import style
        self.made_cut()
        got = style.judge_cut(self.path, 'c1', reader=say(lambda name: 2))
        self.assertFalse(got['passes'])
        self.assertTrue(got['next'][0].startswith('STYLE_OFF: c1'))
        self.assertIn('what it opens', got['next'][0])
        self.made_cut('smptebars')   # a new clip of the same pictures: the verdict comes back unread
        again = style.judge_cut(self.path, 'c1', reader=say(lambda name: self.fail('read again')))
        self.assertFalse(again['passes'])
        self.assertIn('redraw the cut from its plan', again['note'])

    def test_calibration_makes_a_floor_only_where_every_reading_held(self):
        from studio2 import style
        pictures = []
        from PIL import Image
        for n in range(2):
            pictures.append(self.root / f'photo{n}.png')
            Image.new('RGB', (90, 160), (40 * n, 90, 120)).save(pictures[-1])
        items = [{'name': 'held0', 'ref': 'r02', 'expect': True}, {'name': 'held1', 'ref': 'r04', 'expect': True},
                 {'name': 'photo0', 'picture': str(pictures[0]), 'expect': False}, {'name': 'photo1', 'picture': str(pictures[1]), 'expect': False}]
        write_json(self.root / 'set.json', {'items': items})
        got = style.calibrate(self.root / 'set.json', self.root / 'cal', reader=say(lambda n: 8 if n.startswith('r0') else 4), write=True)
        self.assertTrue(got['floor'])
        self.assertEqual(got['min_score'], 6)                       # the middle of the scores that separate (5..8): passes at 6 and over
        self.assertTrue(read_json(self.root / 'style_check.json')['floor'])
        self.assertTrue(style.passes({'score': 6}))
        self.assertFalse(style.passes({'score': 5}))
        got = style.calibrate(self.root / 'set.json', self.root / 'cal2', reader=say(lambda n: 6), write=True)
        self.assertFalse(got['floor'])                              # the plain photos scored as the reference did: advice only
        self.assertFalse(read_json(self.root / 'style_check.json')['floor'])

    def test_a_context_cut_cannot_be_calibrated_on(self):
        from studio.common import StudioError
        from studio2 import style
        write_json(self.root / 'set.json', {'items': [{'name': 'shown', 'ref': 'r00', 'expect': True}]})
        with self.assertRaises(StudioError):
            style.calibrate(self.root / 'set.json', self.root / 'cal', reader=say(lambda n: 8))


if __name__ == '__main__':
    unittest.main()
