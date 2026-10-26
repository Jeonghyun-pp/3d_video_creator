"""The approval sheet (studio2/sheet.py): one page for the reel - per cut its first, middle and last pictures, its line
and what the viewer learns, its motion in words, its free preview clip, the paid model and its cost; the total at the
bottom - and the user's words recorded as the paid approval (v1's check_user_words), bound to exactly what would be
sent; a cut the words name for change is reopened, never approved; no words are taken in a run without its user."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from studio import decisions
from studio.common import StudioError, read_json
from tests import studio2_fixture as fx
from tests.test_studio2_foundation import FakeCodex
from tests.test_studio2_pictures import answer_all, marks_ok, place_reader


def made_reel(test, body=None):
    """test.path: a v2 project whose two cuts are planned, drawn, checked, picked, located and made into clips."""
    from studio2 import checks, frames, locate, motion
    test.path = fx.project(test.root)
    test.body = body or fx.body()
    decisions.write(test.path, 'cuts', test.body, agent_note=fx.TEST_NOTE)
    for cut_id, names in (('c1', ('first',)), ('c2', ('first', 'last'))):
        for frame in names:
            frames.make(test.path, cut_id, frame, runner=FakeCodex(test.root, ['image']))
            checks.check(test.path, cut_id, frame, reader=answer_all(), matcher=answer_all())
            frames.pick(test.path, cut_id, frame, 1, 'a test pick: it shows what the cut needs')
        locate.locate(test.path, cut_id, reader=place_reader, checker=marks_ok)
        motion.render(test.path, cut_id)


class SheetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        made_reel(self)
        self.env = mock.patch.dict(os.environ, {'STUDIO_UNATTENDED': ''})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_the_sheet_shows_each_cut_and_the_reels_cost(self):
        from studio2 import sheet
        got = sheet.build(self.path)
        page = Path(got['page']).read_text(encoding='utf-8')
        for line in fx.LINES:
            self.assertIn(line['text'], page)
        self.assertIn('air falls from the ceiling through the floor', page)      # what the viewer learns
        self.assertIn('the centre chip goes dark', page)                          # the motion in words
        self.assertIn('veo-3.1', page)
        self.assertIn('$0.80', page)                                              # a 4 s cut: 4 s of image-to-video at $0.20
        self.assertIn('$1.60', page)                                              # the reel's total
        self.assertEqual(got['total_usd'], 1.6)
        rows = read_json(Path(got['sheet']))['cuts']
        self.assertEqual([r['cut'] for r in rows], ['c1', 'c2'])
        self.assertTrue(all((Path(got['page']).parent / r['clip']).is_file() for r in rows))   # the free preview, beside the page
        self.assertEqual(sorted(rows[1]['pictures']), ['first', 'last', 'middle'])             # the middle: from the preview

    def test_the_users_words_approve_what_the_sheet_showed(self):
        from studio2 import sheet
        sheet.build(self.path)
        got = sheet.approve(self.path, '좋아요 다 진행해 주세요')
        self.assertEqual(got['approved'], ['c1', 'c2'])
        self.assertEqual(sheet.approved(self.path, 'c1')['user_words'], '좋아요 다 진행해 주세요')
        from studio2 import frames   # the first picture changes after the words: the approval no longer holds for it
        cut = self.body['cuts'][0]
        (frames.folder(self.path, cut) / 'first_1.png').write_bytes(b'not the approved picture')
        self.assertIsNone(sheet.approved(self.path, 'c1'))

    def test_a_cut_the_words_name_for_change_is_reopened(self):
        from studio2 import sheet
        sheet.build(self.path)
        got = sheet.approve(self.path, 'c2 칩이 더 어두워져야 해요, 나머지는 좋아요', cuts=['c2'])
        self.assertEqual(got['approved'], ['c1'])
        self.assertEqual(got['reopened'], ['c2'])
        self.assertIsNone(sheet.approved(self.path, 'c2'))
        self.assertIn('frames make --cut c2', ' '.join(got['next']))
        with self.assertRaises(StudioError):   # the words name c2: it is not approved by leaving --cuts out
            sheet.approve(self.path, 'c2 칩이 더 어두워져야 해요')

    def test_no_words_are_taken_without_the_user(self):
        from studio2 import sheet
        sheet.build(self.path)
        with mock.patch.dict(os.environ, {'STUDIO_UNATTENDED': '1'}), self.assertRaises(StudioError) as caught:
            sheet.approve(self.path, '좋아요 다 진행해 주세요')
        self.assertEqual(caught.exception.code, 'USER_WORDS_UNATTENDED')

    def test_a_sheet_older_than_the_cuts_is_not_approved(self):
        from studio2 import sheet
        sheet.build(self.path)
        body = deepcopy(self.body)
        body['cuts'][0]['duration_s'] = 5.0   # a longer cut: another request, another price
        decisions.write(self.path, 'cuts', body, agent_note=fx.TEST_NOTE)
        with self.assertRaises(StudioError) as caught:
            sheet.approve(self.path, '좋아요 다 진행해 주세요')
        self.assertEqual(caught.exception.code, 'SHEET_STALE')

    def test_a_cut_failing_the_style_floor_is_not_put_to_the_user(self):
        from studio2 import sheet
        with mock.patch('studio2.style.failing', side_effect=lambda path, cut: {'belongs': 'no', 'further': 'nothing opened'} if cut['cut_id'] == 'c1' else None):
            with self.assertRaises(StudioError) as caught:
                sheet.build(self.path)
        self.assertEqual(caught.exception.code, 'STYLE_OFF')


if __name__ == '__main__':
    unittest.main()
