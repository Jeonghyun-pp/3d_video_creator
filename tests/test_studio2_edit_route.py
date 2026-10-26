"""A composite shot (route mode composite: a clip made locally from pictures, its marks in its pixels - v2) is cut by v1's
edit and shown by v1's reel clay as it was made: the clip route.composite names, at the timeline's length, its bytes
unchanged; a shot not made yet is refused (edit) or skipped (clay), never stood in for."""
from pathlib import Path
import tempfile
import unittest

from studio import decisions
from studio.audio import build_audio, run_media
from studio.common import StudioError, file_hash, read_json, write_json
from studio.edit import build_edit, latest_composite
from studio.project import load_shot, shot_path
from tests import studio2_fixture as fx


def make_composite(path, shot_id, frames, key='0123456789abcdef', cut_sha='fedcba9876543210'):
    folder = shot_path(path, shot_id).parent / 'composite' / key
    folder.mkdir(parents=True, exist_ok=True)
    clip = folder / 'clip.mp4'
    run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc2=s=180x320:r=30:d={frames / 30}',
               '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(clip)])
    write_json(folder / 'clip.json', {'schema_version': 1, 'status': 'complete', 'frame_count': frames, 'fps': 30,
                                      'clip_path': str(clip.relative_to(path)), 'clip_sha256': file_hash(clip), 'generated_pictures': True})
    shot = read_json(shot_path(path, shot_id))
    shot['route']['composite'] = {'key': key, 'cut_sha': cut_sha}
    write_json(shot_path(path, shot_id), shot)
    return clip


class CompositeRouteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = fx.project(Path(self.tmp.name))
        decisions.write(self.path, 'cuts', fx.body(), agent_note=fx.TEST_NOTE)
        self.wav = Path(self.tmp.name) / 'voice.wav'
        run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=300:sample_rate=48000:duration=2',
                   str(self.wav)])

    def tearDown(self):
        self.tmp.cleanup()

    def test_edit_cuts_the_composite_clip(self):
        clips = [make_composite(self.path, s, 120, key) for s, key in (('c1', 'aaaaaaaaaaaaaaaa'), ('c2', 'bbbbbbbbbbbbbbbb'))]
        for s in ('c1', 'c2'):
            build_audio(self.path, s, input_wav=self.wav)
        got = build_edit(self.path, 'rough')
        self.assertTrue((self.path / got['output_path']).is_file())
        self.assertEqual(got['ai_generated_shots'], ['c1', 'c2'])   # its pictures were drawn by a model
        snapshot = read_json(self.path / 'final' / got['candidate_id'] / 'edit.snapshot.json')
        self.assertEqual([r['clip_sha256'] for r in snapshot['shots']], [file_hash(c) for c in clips])

    def test_a_shot_not_made_is_refused(self):
        make_composite(self.path, 'c1', 120)
        build_audio(self.path, 'c1', input_wav=self.wav)
        with self.assertRaises(StudioError) as caught:
            build_edit(self.path, 'rough')
        self.assertEqual(caught.exception.code, 'RENDER_REQUIRED')

    def test_a_clip_of_another_length_is_not_taken(self):
        make_composite(self.path, 'c1', 90)
        self.assertIsNone(latest_composite(self.path, load_shot(self.path, 'c1'), 120))

    def test_a_changed_clip_is_refused(self):
        clip = make_composite(self.path, 'c1', 120)
        clip.write_bytes(clip.read_bytes() + b'x')
        with self.assertRaises(StudioError) as caught:
            latest_composite(self.path, load_shot(self.path, 'c1'), 120)
        self.assertEqual(caught.exception.code, 'CACHE_CORRUPT')

    def test_clay_shows_it_as_made(self):
        from studio.reel import CLAY, clay
        make_composite(self.path, 'c1', 120, 'aaaaaaaaaaaaaaaa')
        got = clay(self.path)
        self.assertEqual(got['shots'], ['c1'])
        self.assertEqual(got['skipped'], ['c2'])
        row = read_json(self.path / CLAY / 'clay.json')['shots'][0]
        self.assertEqual(row['version'], 'aaaaaaaaaaaaaaaa')
        self.assertTrue((self.path / CLAY / row['see_clip']).is_file())

    def test_a_cut_changed_after_its_clip_drops_it(self):
        from studio2.cuts import cut_sha
        make_composite(self.path, 'c1', 120, cut_sha=cut_sha(fx.body()['cuts'][0]))
        decisions.write(self.path, 'cuts', fx.body(), agent_note=fx.TEST_NOTE)   # the same cut: its clip stands
        self.assertIn('composite', load_shot(self.path, 'c1')['route'])
        body = fx.body()
        body['cuts'][0]['purpose'] = 'a test cut shows the air falling, said again'
        decisions.write(self.path, 'cuts', body, agent_note=fx.TEST_NOTE)
        self.assertNotIn('composite', load_shot(self.path, 'c1')['route'])


if __name__ == '__main__':
    unittest.main()
