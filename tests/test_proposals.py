"""A blocked moment's choices (frames check proposals, frames proposals / pick / drop): A - a picture redrawn from a pose
outside its intent, put on the candidates sheet for the user's pick; B - a must-see part dropped from one moment, never
one the lines explain that no other moment shows. A diner at a corner table, not the reel the rule came from (N+1)."""
import unittest
from unittest import mock

from studio import frame_targets as ft, frames_check
from studio.blender_ops.cheat_table import DROP_LOCKED_WORDS, drop_locked
from studio.common import StudioError, read_json, write_json

from tests.test_cheats import cafe, say
from tests.test_frame_reading_checks import BASE
from tests.test_frames_pick import sheet

LOCK = {'things': [{'id': 'diner/feat.hands', 'kind': 'feature', 'ids': ['diner/feat.hands', 'diner/hand_l', 'diner/hand_r']}]}


def a_pose(n=0):
    return {'framing': 'close', 'view': [-60.0, 30.0 + 15 * n], 'lens_mm': 24.0, 'placement': {'aim': 'parts', 'closer': 1.0},
            'eye': [1.0, -2.0, 0.2], 'aim': [0.0, 0.0, 1.0], 'roll_deg': 0.0, 'subject_at': [0.0, 0.0, 0.9], 'frame': 59,
            'must_see_px': {'diner/cup': 41.0}, 'departure': 9.5, 'departed': ['아래로 70° (그림 +10° → -60°)', '클로즈업'],
            'resolve': {'status': 'as_intended', 'lost': []}}


class DropLockTest(unittest.TestCase):
    def test_a_part_the_lines_explain_may_be_dropped_only_where_another_moment_shows_it(self):
        here = {'at': 1.0, 'must_see': ['diner/hand_l', 'diner/cup']}
        alone = [{'at': 0.0, 'must_see': ['diner/cup']}, here]
        both = [{'at': 0.0, 'must_see': ['diner/hand_r']}, here]   # another hand of the same feature shows at the start
        self.assertIn(DROP_LOCKED_WORDS, drop_locked('diner/hand_l', here, alone, LOCK))
        self.assertIsNone(drop_locked('diner/hand_l', here, both, LOCK))
        self.assertIsNone(drop_locked('diner/cup', here, alone, LOCK))      # no line explains the cup
        whole = {'things': [{'id': 'diner', 'kind': 'subject', 'ids': ['diner']}]}   # the lines explain the whole subject
        self.assertIn(DROP_LOCKED_WORDS, drop_locked('diner/cup', here, [here, {'at': 0.0, 'must_see': ['table/top']}], whole))
        self.assertIsNone(drop_locked('diner/cup', here, alone, whole))     # another moment shows a part of it


class ProposalWordsTest(unittest.TestCase):
    def test_a_blocked_moment_says_its_choices_in_korean_and_stays_redraw(self):
        moment = {'status': 'redraw', 'as_is': {'code': 'FRAME_UNSOLVED', 'message': 'no view'}, 'attempts': [],
                  'proposals': {'a': [a_pose()], 'b': [
                      {'part': 'diner/cup', 'status': 'as_intended', 'row': {'must_see_px': {'diner/hand_l': 50.0}}, 'dropped_px': {'diner/cup': 4.0}},
                      {'part': 'diner/hand_l', 'status': 'as_intended', 'locked': 'SHOT_EXPLAINS_UNSHOWN: ...'}]}}
        lines = frames_check.proposal_lines(moment)
        self.assertTrue(lines[0].startswith('A1: 아래로 70°') and 'cup 41 px' in lines[0] and '그림의 의도대로' in lines[0], lines[0])
        self.assertIn('cup 빼면 그림의 의도대로', lines[1])
        self.assertIn(DROP_LOCKED_WORDS, lines[2])
        note = frames_check.redraw_note(moment)
        self.assertTrue(note.startswith('FRAME_NEEDS_REDRAW') and '제안' in note, note)
        nothing = frames_check.proposal_lines({'proposals': {'a': [], 'a_search': {'probed': 3960}, 'b': []}})
        self.assertIn('3960곳', nothing[0])


class ProposalPickTest(unittest.TestCase):
    def setUp(self):
        self.scene = cafe(self)
        self.scene.read([BASE, BASE])
        say(self.scene, ['diner'])
        sheet(self.scene)
        root = self.scene.p / 'concepts' / 's' / 'proposals'
        root.mkdir(parents=True, exist_ok=True)
        (root / '1.00' / 'a1').mkdir(parents=True)
        from PIL import Image
        Image.new('RGB', (54, 96), (90, 90, 90)).save(root / '1.00' / 'a1' / 'clay.png')
        self.blend = 'shots/s/versions/authored_x/authored.blend'
        cands = self.scene.p / 'concepts' / 's' / 'candidates' / 'candidates.json'
        write_json(cands, {**read_json(cands), 'scene': self.blend})
        write_json(root / 'proposals.json', {'shot_id': 's', 'scene': self.blend, 'moments': [
            {'at': 1.0, 'concept_id': 'c02', 'a': [{**a_pose(), 'n': 1, 'image': 'concepts/s/proposals/1.00/a1/clay.png'}], 'b': []}]})

    def test_an_a_pose_is_a_candidate_kept_when_the_sheet_is_merged_again_and_picked_in_the_users_words(self):
        self.assertEqual(frames_check.merge_proposals(self.scene.p, 's'), 1)
        self.assertEqual(frames_check.merge_proposals(self.scene.p, 's'), 1)   # again: replaced, not doubled
        found = read_json(self.scene.p / 'concepts' / 's' / 'candidates' / 'candidates.json')
        rows = [c for c in found['moments'][1]['candidates'] if c.get('source') == 'proposal']
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['n'], rows[0]['placement'], rows[0]['subject_at']), (2, {'aim': 'parts', 'closer': 1.0}, [0.0, 0.0, 0.9]))
        frames_check.pick(self.scene.p, 's', 'c02', 2, 'A안으로 가자')
        chosen = ft._store(self.scene.p, 's')['pictures']['c02']['chosen']
        self.assertEqual((chosen['view'], chosen['subject_at'], chosen['by']), ([-60.0, 30.0], [0.0, 0.0, 0.9], 'user'))
        self.assertEqual(chosen['scene'], self.blend)                          # said if the pick fails on another scene

    def test_proposals_solved_on_another_scene_leave_the_sheet(self):
        self.assertEqual(frames_check.merge_proposals(self.scene.p, 's'), 1)
        cands = self.scene.p / 'concepts' / 's' / 'candidates' / 'candidates.json'
        write_json(cands, {**read_json(cands), 'scene': 'shots/s/versions/authored_y/authored.blend'})   # authored again
        self.assertEqual(frames_check.merge_proposals(self.scene.p, 's'), 0)
        self.assertFalse([c for m in read_json(cands)['moments'] for c in m['candidates'] if c.get('source') == 'proposal'])

    def test_the_page_asks_for_a_or_b_per_shot(self):
        page = frames_check.proposals_page(self.scene.p)
        text = page.read_text(encoding='utf-8')
        self.assertIn('샷마다 A/B를 말씀해 주세요', text)
        self.assertIn('frames drop', text)
        self.assertIn('A1: 아래로 70°', text)


class DropTest(unittest.TestCase):
    def setUp(self):
        self.scene = cafe(self)
        self.scene.read([BASE, BASE])
        say(self.scene, ['diner/feat.hands'])
        root = self.scene.p / 'decisions'
        body = read_json(root / 'shotlist.json')
        body['body']['shots'][0]['show'] = [{'at': 'end', 'parts': ['diner/hand_l', 'diner/cup'], 'how': 'exterior', 'why': 'x'},
                                            {'at': 'start', 'parts': ['diner/cup'], 'how': 'exterior', 'why': 'y'}]
        write_json(root / 'shotlist.json', body)

    def drop(self, part, locked=None, listed='approved', approve=None, lint_errors=()):
        calls = []
        def revise(project, layer, words, ops=None, agent_note=None):
            calls.append(('revise', ops))
            write_json(self.scene.p / 'decisions' / 'shotlist.json', {'status': 'proposed', 'half': True})   # what a real revise leaves
            return {'rev': 'r02'}
        approve = approve or (lambda project, layer, words, rev: calls.append(('approve', rev)) or {'state': 'approved'})
        with mock.patch('studio.decisions.revise', revise), mock.patch('studio.decisions.approve', approve), \
                mock.patch('studio.decisions.state', lambda path, layer: {'state': listed, 'reason': 'r'}), \
                mock.patch('studio.decisions.validate_body', lambda layer, body: None), \
                mock.patch('studio.decisions.lint', lambda path, layer, body: {'errors': list(lint_errors), 'warnings': []}), \
                mock.patch('studio.decisions.parents_of', lambda path, layer: []), \
                mock.patch('studio.cheat_core.locked', lambda path, shot: locked or {'things': []}):
            out = frames_check.drop(self.scene.p, 's', '1.0', part, '컵은 빼도 돼')
        return out, calls

    def test_a_draft_list_is_refused_so_the_drops_words_never_sign_unseen_edits(self):
        with self.assertRaises(StudioError) as caught:
            self.drop('diner/cup', listed='proposed')
        self.assertEqual(caught.exception.code, 'DECISION_UNAPPROVED')

    def test_a_drop_approve_would_refuse_writes_nothing(self):
        before = (self.scene.p / 'decisions' / 'shotlist.json').read_bytes()
        with self.assertRaises(StudioError):
            self.drop('diner/cup', lint_errors=['SHOT_EMPTY: x'])
        self.assertEqual((self.scene.p / 'decisions' / 'shotlist.json').read_bytes(), before)

    def test_a_failed_approve_puts_the_approved_list_back(self):
        before = (self.scene.p / 'decisions' / 'shotlist.json').read_bytes()
        def busy(project, layer, words, rev):
            raise StudioError('BUSY', 'held')
        with self.assertRaises(StudioError):
            self.drop('diner/cup', approve=busy)
        self.assertEqual((self.scene.p / 'decisions' / 'shotlist.json').read_bytes(), before)

    def test_the_part_comes_off_the_moments_show_row_revised_and_approved_in_the_users_words(self):
        out, calls = self.drop('diner/cup')
        self.assertEqual(calls, [('revise', [{'op': 'remove', 'path': '/shots/0/show/0/parts/1'}]), ('approve', 'r02')])
        self.assertIn('frames apply', out['next'])

    def test_a_part_the_lines_explain_and_no_other_moment_shows_is_refused(self):
        with self.assertRaises(StudioError) as caught:
            self.drop('diner/hand_l', LOCK)
        self.assertIn('SHOT_EXPLAINS_UNSHOWN', caught.exception.message)
        with self.assertRaises(StudioError):
            self.drop('diner/spoon')                                             # not shown at that moment


if __name__ == '__main__':
    unittest.main()
