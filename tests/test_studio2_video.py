"""The paid video path (studio2/video.py), tested with a stubbed fal client and a recorded fixture video - nothing is
spent: image-to-video through v1's generative/clip.py (its adapter, paid_call and retime) only for a cut the user
approved on the sheet, its first frame the approved first picture; a check of the video against the approved pictures
and the line; our marks located again on its frames and drawn over it; the clip entering the edit as the cut only after
a person passed its motion (2026-10-16: generated motion is checked by a person)."""
import base64
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from studio.common import StudioError, file_hash, read_json, write_json
from studio.project import load_shot
from tests.test_studio2_pictures import marks_ok, place_reader
from tests.test_studio2_sheet import made_reel


def fixture_video(target, seconds=4, fps=24):
    """What fal returns for veo-3.1 image-to-video, recorded: a 9:16 clip of the asked length at its own frame rate."""
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc=size=216x384:rate={fps}', '-t', str(seconds), '-pix_fmt', 'yuv420p',
                    str(target)], check=True)
    return target


class FakeFal:
    """Stands in for fal_client.paid_call: records what was sent, answers with the recorded video."""

    def __init__(self, root):
        self.root, self.calls = Path(root), []

    def __call__(self, endpoint, arguments, dest, **kw):
        self.calls.append({'endpoint': endpoint, 'arguments': arguments, **kw})
        video = fixture_video(Path(dest) / 'files' / 'video__out.mp4', seconds=int(arguments['duration'].rstrip('s')))
        return {'endpoint': endpoint, 'request_id': f'req-{len(self.calls)}', 'estimated_usd': kw['max_usd'],
                'files': [{'trail': 'video', 'path': str(video), 'sha256': file_hash(video), 'bytes': video.stat().st_size}]}


def video_reader(ok=True):
    def reader(images, prompt, schema, target):
        ids = schema['properties']['answers']['items']['properties']['id']['enum']
        write_json(target, {'answer': {'answers': [{'id': i, 'ok': ok, 'why': 'a test reading'} for i in ids], 'note': ''}})
    return reader


class VideoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {'STUDIO_UNATTENDED': ''})
        self.env.start()
        made_reel(self)
        from studio2 import sheet
        sheet.build(self.path)
        self.fal = FakeFal(self.root)
        self.patch = mock.patch('studio.generative.clip.paid_call', side_effect=self.fal)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_nothing_is_paid_for_without_the_users_approval(self):
        from studio2 import video
        with self.assertRaises(StudioError) as caught:
            video.make(self.path, 'c1')
        self.assertEqual(caught.exception.code, 'PAID_UNAPPROVED')
        self.assertEqual(self.fal.calls, [])

    def test_the_approved_first_picture_starts_the_paid_video(self):
        from studio2 import frames, sheet, video
        sheet.approve(self.path, '좋아요 다 진행해 주세요')
        got = video.make(self.path, 'c1')
        [call] = self.fal.calls
        self.assertEqual(call['endpoint'], 'fal-ai/veo3.1/image-to-video')
        self.assertEqual(call['arguments']['duration'], '4s')
        self.assertEqual(call['arguments']['aspect_ratio'], '9:16')
        first = frames.picked(self.path, self.body['cuts'][0], 'first')
        self.assertEqual(base64.b64decode(call['arguments']['image_url'].split(',', 1)[1]), first.read_bytes())
        self.assertTrue(call['allow_paid'])
        self.assertEqual(call['max_usd'], 0.8)                       # the approved price, never more
        shot = load_shot(self.path, 'c1')
        self.assertEqual(got['frame_count'], shot['duration_frames'])
        self.assertTrue(Path(got['clip']).is_file())
        self.assertFalse(video.make(self.path, 'c1')['cached'] is False)   # made once: never paid twice
        self.assertEqual(len(self.fal.calls), 1)

    def test_a_request_changed_after_the_words_is_not_paid(self):
        from studio2 import frames, sheet, video
        sheet.approve(self.path, '좋아요 다 진행해 주세요')
        (frames.folder(self.path, self.body['cuts'][0]) / 'first_1.png').write_bytes(b'another picture')
        with self.assertRaises(StudioError) as caught:
            video.make(self.path, 'c1')
        self.assertEqual(caught.exception.code, 'PAID_UNAPPROVED')

    def test_the_video_is_checked_against_the_approved_pictures_and_the_line(self):
        from studio2 import sheet, video
        sheet.approve(self.path, '좋아요 다 진행해 주세요')
        video.make(self.path, 'c2')
        seen = {}

        def reader(images, prompt, schema, target):
            seen['images'], seen['prompt'] = images, prompt
            video_reader(ok=False)(images, prompt, schema, target)
        got = video.check(self.path, 'c2', reader=reader)
        ids = set(got['answers'])
        self.assertEqual(ids, {'motion_matches_line', 'middle_near', 'no_morphing', 'ends_near_last'})
        self.assertIn('먼지 한 톨이 칩 하나를 죽입니다', seen['prompt'])        # the line
        self.assertEqual(len(seen['images']), 6)                              # three of the video, three approved pictures
        self.assertEqual(got['failures'], [])                                 # not calibrated: advice for the person
        self.assertEqual(sorted(got['warnings']), sorted(ids))

    def test_marks_are_drawn_over_the_video_and_it_enters_the_edit_only_after_a_person(self):
        from studio.edit import latest_composite
        from studio.reel import medium_of
        from studio2 import sheet, video
        sheet.approve(self.path, '좋아요 다 진행해 주세요')
        video.make(self.path, 'c2')
        before = load_shot(self.path, 'c2')['route']['composite']
        got = video.composite(self.path, 'c2', reader=place_reader, checker=marks_ok)
        self.assertGreaterEqual(len(got['located_at']), 3)                  # located again on the video's own frames
        self.assertEqual(load_shot(self.path, 'c2')['route']['composite'], before)   # not the cut yet: no person has looked
        with mock.patch.dict(os.environ, {'STUDIO_UNATTENDED': '1'}), self.assertRaises(StudioError):
            video.review(self.path, 'c2', '움직임 좋아요')
        video.review(self.path, 'c2', '움직임 좋아요')
        shot = load_shot(self.path, 'c2')
        self.assertEqual(shot['route']['composite']['key'], got['key'])
        made = latest_composite(self.path, shot, shot['duration_frames'])
        self.assertTrue(made['manifest']['generated_video'])
        self.assertEqual(medium_of(shot, made), 'generated')

    def test_a_part_left_unlocated_on_a_video_frame_is_off(self):
        from studio2 import sheet, video
        sheet.approve(self.path, '좋아요 다 진행해 주세요')
        video.make(self.path, 'c2')

        def twice(images, prompt, schema, target):
            place_reader(images, prompt, schema, target)
            data = read_json(target)
            data['answer']['parts'] = [dict(data['answer']['parts'][0]) for _ in data['answer']['parts']]
            write_json(target, data)
        with self.assertRaises(StudioError) as caught:
            video.composite(self.path, 'c2', reader=twice, checker=marks_ok)
        self.assertEqual(caught.exception.code, 'PLACES_OFF')

    def test_a_person_turning_the_motion_down_keeps_the_free_clip(self):
        from studio2 import sheet, video
        sheet.approve(self.path, '좋아요 다 진행해 주세요')
        video.make(self.path, 'c2')
        before = load_shot(self.path, 'c2')['route']['composite']
        video.composite(self.path, 'c2', reader=place_reader, checker=marks_ok)
        got = video.review(self.path, 'c2', '칩이 녹아내려요, 다시', accept=False)
        self.assertFalse(got['accepted'])
        self.assertEqual(load_shot(self.path, 'c2')['route']['composite'], before)


if __name__ == '__main__':
    unittest.main()
