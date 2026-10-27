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


def advice_only():
    """library/video_checks.json with every floor off: for tests of the path a passed clip takes, not of the floors (the
    fixture video is a still test pattern, so a calibrated camera floor rightly refuses it against a planned push)."""
    from studio2 import video
    base = video.table()
    return mock.patch.object(video, 'table', return_value={**base, 'checks': {k: {**c, 'floor': False} for k, c in base['checks'].items()}})


def video_reader(ok=True, counts=None):
    """Answers the video's questions: ok to each asked check, and the count reader's counts (one per tile, `counts` or 3)."""
    def reader(images, prompt, schema, target):
        if 'parts' in schema['properties']:      # the count reader: one strip a part, a count a tile
            ids = schema['properties']['parts']['items']['properties']['id']['enum']
            write_json(target, {'answer': {'parts': [{'id': i, 'counts': list(counts or [3] * tiles(prompt, i))} for i in ids]}})
            return
        ids = schema['properties']['answers']['items']['properties']['id']['enum']
        write_json(target, {'answer': {'answers': [{'id': i, 'ok': ok, 'why': 'a test reading'} for i in ids], 'note': ''}})
    return reader


def tiles(prompt, part):
    import re
    return int(re.search(rf'"{re.escape(part)}"[^\n]*?(\d+) tiles', prompt).group(1))


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
        self.assertIs(call['arguments']['auto_fix'], False)          # the provider never rewrites the prompt the user approved
        self.assertIn('camera cut', call['arguments']['negative_prompt'])   # what to avoid, as nouns in its own field
        self.assertEqual(call['arguments']['prompt'], sheet.latest(self.path)['cuts'][0]['prompt'])
        self.assertEqual(call['arguments']['negative_prompt'], sheet.latest(self.path)['cuts'][0]['negative_prompt'])
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
            seen.setdefault('calls', []).append((images, prompt))
            video_reader(ok=False)(images, prompt, schema, target)
        got = video.check(self.path, 'c2', reader=reader, placer=place_reader)
        seen['images'], seen['prompt'] = seen['calls'][0]
        ids = {'motion_matches_line', 'middle_near', 'no_morphing', 'ends_near_last'}
        self.assertEqual(set(got['answers']) - {'shape_kept', 'contact_kept'}, ids)
        self.assertIn('먼지 한 톨이 칩 하나를 죽입니다', seen['prompt'])        # the line
        self.assertEqual(len(seen['images']), 6)                              # three of the video, three approved pictures
        self.assertFalse(ids & set(got['failures']))                         # these questions are not calibrated: advice
        # the approved pictures may be preview frames carrying our labels and flows; the video never has them, so the
        # reader is told they are drawn afterwards and which pictures are preview frames (fab c03, 2026-10-27: "lacks the labels")
        self.assertIn('drawn over the video afterwards', ' '.join(seen['prompt'].split()))
        self.assertIn('Image 4 is a frame of the free preview', seen['prompt'])   # c2 has no middle picture
        self.assertTrue(ids <= set(got['warnings']))

    def test_marks_are_drawn_over_the_video_and_it_enters_the_edit_only_after_a_person(self):
        from studio.edit import latest_composite
        from studio.reel import medium_of
        from studio2 import sheet, video
        sheet.approve(self.path, '좋아요 다 진행해 주세요')
        video.make(self.path, 'c2')
        before = load_shot(self.path, 'c2')['route']['composite']
        got = video.composite(self.path, 'c2', reader=place_reader, checker=marks_ok)
        self.assertGreaterEqual(len(got['located_at']), 3)
        with self.assertRaises(StudioError) as caught:          # a person's pass needs the clip checked first
            video.review(self.path, 'c2', '움직임 좋아요')
        self.assertEqual(caught.exception.code, 'VIDEO_UNCHECKED')
        with advice_only():
            video.check(self.path, 'c2', reader=video_reader(ok=True), placer=place_reader)                  # located again on the video's own frames
        self.assertEqual(load_shot(self.path, 'c2')['route']['composite'], before)   # not the cut yet: no person has looked
        with mock.patch.dict(os.environ, {'STUDIO_UNATTENDED': '1'}), self.assertRaises(StudioError):
            video.review(self.path, 'c2', '움직임 좋아요')
        video.review(self.path, 'c2', '움직임 좋아요')
        shot = load_shot(self.path, 'c2')
        self.assertEqual(shot['route']['composite']['key'], got['key'])
        made = latest_composite(self.path, shot, shot['duration_frames'])
        self.assertTrue(made['manifest']['generated_video'])
        self.assertEqual(medium_of(shot, made), 'generated')

    def test_a_review_preview_shows_the_marked_paid_clips_without_passing_them(self):
        """2026-10-27: the user paid for the first cuts and wanted to see them in the reel; only a person's review makes a
        paid clip the cut, so the preview for that review cuts the marked paid clips in for this edit only - the shots,
        their reviews and the free preview stay as they were, and the edit says which clips no person passed."""
        from studio.common import read_json as rj
        from studio2 import reel, sheet, video
        sheet.approve(self.path, '좋아요 다 진행해 주세요')
        video.make(self.path, 'c2')
        before = load_shot(self.path, 'c2')['route']['composite']
        got = video.composite(self.path, 'c2', reader=place_reader, checker=marks_ok)
        free = reel.preview(self.path)
        paid = reel.preview(self.path, with_paid=True)
        self.assertEqual(paid['unreviewed_paid'], ['c2'])
        self.assertNotEqual(paid['candidate_id'], free['candidate_id'])
        manifest = rj(self.path / 'final' / paid['candidate_id'] / 'manifest.json')
        self.assertEqual(manifest['unreviewed_paid'], ['c2'])
        snapshot = rj(self.path / 'final' / paid['candidate_id'] / 'edit.snapshot.json')
        self.assertIn(got['key'], str(snapshot['shots']))
        self.assertEqual(load_shot(self.path, 'c2')['route']['composite'], before)   # not the cut: no person passed it
        self.assertIsNone(rj(self.path / 'final' / free['candidate_id'] / 'manifest.json').get('unreviewed_paid'))
        video.review(self.path, 'c2', '칩이 녹아내려요, 다시', accept=False)   # turned down: no preview shows it again
        self.assertEqual(reel.preview(self.path, with_paid=True)['unreviewed_paid'], [])

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


    def test_the_request_is_kept_with_the_video(self):
        """What the clip was asked (the cut's camera, what holds, the prompt) is kept with it: the plan may change after the
        clip is made (fab c04 was re-planned as a ride), and the checks read the clip against what it was asked."""
        from studio2 import sheet, video
        sheet.approve(self.path, '좋아요 다 진행해 주세요')
        got = video.make(self.path, 'c2')
        cut = self.body['cuts'][1]
        self.assertEqual(got['asked']['camera'], cut['cutaway']['camera'])
        self.assertEqual(got['asked']['prompt'], sheet.latest(self.path)['cuts'][1]['prompt'])
        self.assertIn('holds', got['asked'])

    def test_shape_contact_and_time_are_checked(self):
        """2026-10-27: generated clips came back with 25 wafers merged into one cylinder (c03), a box hanging below the hoist
        meant to hold it (c01) and a push that drifted back out (c03). The check reads shape and contact on frames across
        the clip against the approved first picture and the request, and measures flicker, jumps and the camera."""
        from studio2 import sheet, video
        sheet.approve(self.path, '좋아요 다 진행해 주세요')
        video.make(self.path, 'c2')
        calls = []

        def reader(images, prompt, schema, target):
            calls.append((images, prompt, schema))
            video_reader(ok=True)(images, prompt, schema, target)
        got = video.check(self.path, 'c2', reader=reader, placer=place_reader)
        hold = next(c for c in calls if 'answers' in c[2]['properties'] and 'shape_kept' in c[2]['properties']['answers']['items']['properties']['id']['enum'])
        self.assertEqual(len(hold[0]), 1 + video.HOLD_FRAMES)            # the approved first picture and frames across the clip
        self.assertIn(video.made(self.path, 'c2')['asked']['prompt'][:40], ' '.join(hold[1].split()))
        self.assertTrue(got['answers']['shape_kept']['ok'])
        for name in ('flicker', 'jumps', 'camera'):
            self.assertIn(name, got['measured'])
        self.assertEqual(set(got['verdicts']), set(video.table()['checks']))

    def test_a_calibrated_floor_refuses_the_pass_and_advice_does_not(self):
        from studio2 import sheet, video
        sheet.approve(self.path, '좋아요 다 진행해 주세요')
        video.make(self.path, 'c2')
        base = video.table()
        floor = {**base, 'checks': {**base['checks'], 'shape_kept': {**base['checks']['shape_kept'], 'floor': True}}}
        advice = {**base, 'checks': {**base['checks'], 'shape_kept': {**base['checks']['shape_kept'], 'floor': False}}}
        video.composite(self.path, 'c2', reader=place_reader, checker=marks_ok)
        with mock.patch.object(video, 'table', return_value=advice):
            got = video.check(self.path, 'c2', reader=video_reader(ok=False), placer=place_reader)
        self.assertIn('shape_kept', got['warnings'])
        self.assertNotIn('shape_kept', got['failures'])
        self.assertTrue(any(f.startswith('VIDEO_SHAPE_LOST') for f in got['findings']))
        with mock.patch.object(video, 'table', return_value=floor):
            got = video.check(self.path, 'c2', reader=video_reader(ok=False), placer=place_reader)
        self.assertIn('shape_kept', got['failures'])
        with self.assertRaises(StudioError) as caught:
            video.review(self.path, 'c2', '움직임 좋아요')
        self.assertEqual(caught.exception.code, 'VIDEO_CHECK_FLOOR')
        video.review(self.path, 'c2', '웨이퍼가 녹아요, 다시', accept=False)   # turning it down is always open

    def test_a_contact_the_plan_names_is_measured_on_the_parts_located_on_the_clip(self):
        """The plan's hold names its pair (video.holds: held, holder, kind); the check locates the cut's parts on frames
        across the clip (the composite's frames and readings, shared) and measures the pair frame by frame, and crops each
        part to compare with the clip's own first frame - never with the approved picture (fab c03, 2026-10-27)."""
        from studio2 import sheet, video
        body = self.body
        body['cuts'][1]['video'] = {'holds': [{'says': 'The floor stays level under the filters.', 'held': 'floor', 'holder': 'filters',
                                               'kind': 'carried'}]}
        from studio import decisions
        decisions.write(self.path, 'cuts', body, agent_note='a test plans these cuts')
        from studio2 import motion
        motion.render(self.path, 'c2')
        sheet.build(self.path)
        sheet.approve(self.path, '좋아요 다 진행해 주세요')
        made = video.make(self.path, 'c2')
        self.assertEqual(made['asked']['holds'][0]['holder'], 'filters')
        self.assertEqual([p['id'] for p in made['asked']['parts']], ['filters', 'floor'])
        placed = []

        def placer(images, prompt, schema, target):
            placed.append(images[0])
            place_reader(images, prompt, schema, target)
        got = video.check(self.path, 'c2', reader=video_reader(ok=True), placer=placer)
        pair = got['measured']['contact']['pairs'][0]
        self.assertEqual((pair['held'], pair['holder'], pair['kind']), ('floor', 'filters', 'carried'))
        self.assertEqual(len(pair['frames']), len(video.locate_times(made['frame_count'] / 30)))
        self.assertIsNotNone(got['verdicts']['contact_apart']['value'])
        self.assertIsNotNone(got['verdicts']['shape_detail']['value'])
        self.assertIsNotNone(got['verdicts']['shape_count']['value'])
        self.assertGreater(got['measured']['shape']['likeness'], 0.5)
        n = len(placed)
        video.composite(self.path, 'c2', reader=placer, checker=marks_ok)   # the composite reads its own question (flows, path)
        video.check(self.path, 'c2', reader=video_reader(ok=True), placer=placer)
        self.assertEqual(len(placed), n + len(video.locate_times(made['frame_count'] / 30)))   # the check reuses the composite's readings

    def test_a_clip_made_before_its_request_was_kept_gets_it_from_its_own_request(self):
        """fab c01-c04 (2026-10-27) were made before `asked` was kept: their check stopped (VIDEO_ASKED_UNKNOWN). A clip's
        asked is written, once, from its own request/request.json - the prompt it was sent, the camera read back from
        those words - and the parts located on it; nothing it was not sent (holds) is made up."""
        from studio2 import sheet, video
        sheet.approve(self.path, '좋아요 다 진행해 주세요')
        made = video.make(self.path, 'c2')
        folder = video.folder_of(self.path, 'c2', made['fingerprint'])
        row = read_json(folder / 'video.json')
        row.pop('asked')
        write_json(folder / 'video.json', row)
        sent = 'Photoreal. The camera pushes in slowly and steadily toward the floor, about 8% closer. The floor glows.'
        write_json(folder / 'request' / 'request.json', {'endpoint': 'fal-ai/veo3.1/image-to-video', 'arguments': {'prompt': sent, 'duration': '4s'}})
        video.composite(self.path, 'c2', reader=place_reader, checker=marks_ok)
        with advice_only():
            got = video.check(self.path, 'c2', reader=video_reader(ok=True), placer=place_reader)
        asked = read_json(folder / 'video.json')['asked']
        self.assertEqual(asked['camera'], {'move': 'push', 'amount': 0.08})
        self.assertEqual(asked['prompt'], sent)
        self.assertEqual(asked['holds'], [])
        self.assertEqual(sorted(p['id'] for p in asked['parts']), ['filters', 'floor'])
        self.assertIn('request/request.json', asked['from'])
        self.assertIsNotNone(got['measured']['camera']['ratio'])

    def test_a_clip_made_for_an_earlier_request_is_checked_by_its_fingerprint(self):
        """The plan may change after a clip is made (fab c04 became a ride): the clip is still checked, against what it was
        asked, by naming it (video check --video <fingerprint>); without it, only the current request's clip is."""
        from studio import decisions
        from studio2 import sheet, video
        sheet.approve(self.path, '좋아요 다 진행해 주세요')
        made = video.make(self.path, 'c2')
        body = self.body
        body['cuts'][1]['cutaway']['camera'] = {'move': 'pull', 'amount': 0.05}
        decisions.write(self.path, 'cuts', body, agent_note='a test plans these cuts')
        with self.assertRaises(StudioError) as caught:
            video.check(self.path, 'c2', reader=video_reader(ok=True), placer=place_reader)
        self.assertEqual(caught.exception.code, 'VIDEO_UNMADE')
        got = video.check(self.path, 'c2', reader=video_reader(ok=True), placer=place_reader, video=made['fingerprint'])
        self.assertEqual(got['video'], made['fingerprint'])
        self.assertEqual(read_json(video.folder_of(self.path, 'c2', made['fingerprint']) / 'check.json')['video'], made['fingerprint'])

    def test_a_check_is_a_floor_only_where_every_reading_separates(self):
        """Calibration (studio2 video calibrate): labelled clips, each check read on every run; a measure is a floor only
        when a limit puts every labelled good on one side and every labelled bad on the other in every run, a reading
        only when it answers every good ok and every bad not ok in every run."""
        from studio2 import video
        from tests.test_studio2_video_measure import clip_of, textured
        still = textured(self.root / 'still.png')
        push = clip_of(still, self.root / 'push.mp4', '1+0.1*in/59')
        back = clip_of(still, self.root / 'back.mp4', 'if(lt(in,30),1+0.12*in/30,1.12-0.12*(in-30)/29)')
        asked = {'camera': {'move': 'push', 'amount': 0.1}, 'prompt': 'Slow, steady dolly in toward the blocks.', 'holds': []}
        items = [{'name': 'good', 'clip': str(push), 'first': str(still), 'asked': asked, 'labels': {'camera_reverse': True, 'shape_kept': True}},
                 {'name': 'back', 'clip': str(back), 'first': str(still), 'asked': asked, 'labels': {'camera_reverse': False, 'shape_kept': False}}]
        set_file = self.root / 'set.json'
        write_json(set_file, {'items': items})
        got = video.calibrate(set_file, self.root / 'cal', runs=2, reader=video_reader(ok=True))
        self.assertTrue(got['checks']['camera_reverse']['floor'])
        rev = got['checks']['camera_reverse']
        self.assertTrue(max(rev['calibration']['good']) < rev['limit'] < min(rev['calibration']['bad']))
        self.assertFalse(got['checks']['shape_kept']['floor'])          # the reader said ok to the bad one: advice
        self.assertFalse(got['checks']['flicker']['floor'])             # nothing labelled: no floor

if __name__ == '__main__':
    unittest.main()
