"""The shot list read as a chain of decisions, in words (studio/plan_read.py): each cut's chain goes to a reader, its
answers are flow findings on the cut as it was read, answered by a fix or a departure read with the picture; the sheet
shows the same chain, and the reasons a plan gives for its motion and its place are read again with the clay
(library/excuses.json). Why (2026-10-26): the fab's first cut flew in where its beat's picture was a box snatched from a
hand, and every field was filled."""
from pathlib import Path
import tempfile
import unittest

from studio import decisions, excuses, plan_read, reel_flow
from studio.common import write_json
from studio.project import init_project
from tests.decisions_fixture import settle

STORY = {'beats': [{'story_beat_id': 'b1', 'function': 'hook', 'tension': 0.7, 'length_share': 1.0, 'what_happens': 'a hoist snatches the box from a hand',
                    'picture_idea': 'low angle: the box shoots up past the reaching glove'}]}
SCRIPT = {'lines': [{'line_id': 'l1', 'text': '손을 뻗는 순간, 레일이 상자를 낚아챕니다.', 'story_beat_id': 'b1'}]}
SHOW_HOW = {'point': 'machines keep the wafers away from hands', 'proof': ['box_snatched'],
            'way': {'id': 'hold_change', 'rejected': {'id': 'follow', 'why': 'the box leaves the frame, the hand stays'}}, 'picture': 'beat'}
FLY = {'recipe': 'fly_in', 'reveals': 'the roof, the ceiling, then the box', 'key_at': 0.8,
       'why': "the reel's journey in one flight from outside the building down to the box"}


def body(move=FLY, **extra):
    shot = {'shot_id': 's01', 'purpose': 'the hook: the box snatched from a hand', 'line_ids': ['l1'], 'duration_s': 4.2,
            'frame': {'focus': ['foup'], 'size': 'close'},
            'direction': {'show_how': SHOW_HOW, 'move': move, 'journey': {'from': 0, 'to': 1, 'role': 'travel', 'why': 'in to the box'},
                          'change': [{'id': 'box_snatched', 'parts': ['foup'], 'kind': 'move', 'from': 0, 'to': 0.5, 'at': [0.7, 0.86],
                                      'why': 'the hoist lifts it'}]}, **extra}
    return {'journey': [{'place': 'fab_building', 'scale': 'building', 'why': 'outside'}, {'place': 'foup', 'scale': 'object', 'why': 'the box'}],
            'point': {'says': 'a speck kills a chip', 'proof': {'what': 'the box is snatched', 'rows': ['s01/box_snatched']},
                      'opening': {'does': 'asks why the hand may not touch', 'why': 'the question the reel answers'}},
            'shots': [shot]}


def answer(derived=False, better=False, carried=True):
    def read(images, prompt, schema, target):
        read.prompts.append((images, prompt))
        write_json(target, {'answer': {'cuts': [{'shot_id': 's01', 'derived': derived, 'derived_why': 'the flight decorates; the snatch is the proof',
                                                 'stronger': {'better': better, 'what': 'a low hold on the hand' if better else '',
                                                              'why': 'the beat picture proves it' if better else ''}, 'note': 'n'}],
                                       'proof_carried': {'holds': carried, 'why': 'the snatch is small'}, 'opening_done': {'holds': True, 'why': 'w'}}})
    read.prompts = []
    return read


class PlanReadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(init_project('pr', {'request': 'x', 'shots': [{'shot_id': 's01', 'frame_count': 126}]}, self.tmp.name)['project_path'])
        settle(self.p, ['shotlist'], bodies={'facts': {'sources': [], 'claims': []}, 'story': STORY, 'script': SCRIPT, 'shotlist': body()})

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_cut_goes_to_the_reader_as_its_chain(self):
        reader = answer()
        got = plan_read.read(self.p, reader=reader)
        images, prompt = reader.prompts[0]
        self.assertEqual(images, [])   # words only: the picture is the blind viewer's (studio/reel_see.py)
        for said in ('low angle: the box shoots up past the reaching glove', 'foup: move from 0 to 0.5', 'it proves its point by: box_snatched', 'machines keep the wafers away from hands', 'hold_change',
                     'not follow', 'kept', 'travel from fab_building (building) to foup (object)', "the reel's journey in one flight",
                     'a speck kills a chip', 'asks why the hand may not touch', '손을 뻗는 순간'):
            self.assertIn(said, prompt)
        self.assertEqual(got['findings'], {'s01': ['PLAN_NOT_DERIVED']})
        plan_read.read(self.p, reader=reader)   # the same plan is not asked again
        self.assertEqual(len(reader.prompts), 1)

    def test_its_answers_are_flow_findings_on_the_cut_as_it_was_read(self):
        plan_read.read(self.p, reader=answer(derived=False, better=True, carried=False))
        found = plan_read.lines(self.p, body())
        self.assertEqual(sorted(l.split(':')[0] for l in found), ['PLAN_NOT_DERIVED', 'PLAN_STRONGER', 'PROOF_NOT_CARRIED'])
        self.assertTrue(set(plan_read.FINDINGS) | set(plan_read.REEL_FINDINGS) <= set(reel_flow.DEPARTS))
        self.assertTrue(reel_flow.unresolved(found))
        # a departure answers it and is not a new plan: the reading still holds for the cut
        departed = body(pattern={'departs': [{'item': 'plan_not_derived', 'why': 'the flight lands on the box before the snatch'},
                                             {'item': 'plan_stronger', 'why': 'the low hold is the next cut'}]})
        self.assertEqual([l.split(':')[0] for l in plan_read.lines(self.p, departed)], ['PROOF_NOT_CARRIED'])
        self.assertEqual(plan_read.unread(self.p, departed), [])
        # a cut changed since is no longer what was read
        held = body(move={**FLY, 'recipe': 'watch', 'why': 'the camera holds low so the snatch is all that moves'})
        self.assertEqual(plan_read.unread(self.p, held), ['s01'])
        self.assertFalse([l for l in plan_read.lines(self.p, held) if l.startswith('PLAN_')])

    def test_the_shot_list_lint_reads_the_answers(self):
        plan_read.read(self.p, reader=answer(derived=False))
        report = decisions.lint(self.p, 'shotlist', body())
        self.assertTrue(any(w.startswith('PLAN_NOT_DERIVED: s01') for w in report['warnings']))

    def test_the_sheet_shows_the_chain_and_the_journey(self):
        sheet = '\n'.join(decisions._render_body('shotlist', body(), self.p))
        for said in ('**point**: a speck kills a chip', '**proof**: the box is snatched (s01/box_snatched)', '**opening**: asks why the hand may not touch',
                     'show how: machines keep the wafers away from hands', 'by box_snatched', 'way hold_change (not follow:',
                     "beat's picture kept", 'journey: travel 0 fab_building → 1 foup — in to the box', '**places**: fab_building (building) → foup (object)'):
            self.assertIn(said, sheet)

    def test_a_plans_reasons_for_its_motion_and_place_are_read_with_the_clay(self):
        rows = {r['path']: r for r in excuses.rows() if r.get('path')}
        for where in ('direction.move.why', 'direction.journey.why', 'direction.show_how.picture.departs'):
            self.assertIn(where, rows)
        shot = {'direction': {'move': FLY, 'journey': {'why': 'in to the box'}, 'show_how': {**SHOW_HOW, 'picture': {'departs': 'the box seen from inside instead'}}}}
        said = [excuses._shot_reasons(shot, rows[w]) for w in rows if w.startswith('direction.')]
        self.assertIn([{'code': 'move_why', 'text': FLY['why']}], said)
        self.assertIn([{'code': 'picture_departs', 'text': 'the box seen from inside instead'}], said)
        self.assertEqual(excuses._shot_reasons({'direction': {'show_how': SHOW_HOW}}, rows['direction.show_how.picture.departs']), [])


    def test_a_render_waits_until_the_plan_of_its_cut_was_read(self):
        """Every device runs (2026-10-24, the user: skip it and the render stops); what it finds is answered, not a floor."""
        from studio import quality_gate
        from studio.common import StudioError
        with self.assertRaises(StudioError) as caught:
            quality_gate.plan_was_read(self.p, 's01')
        self.assertEqual(caught.exception.code, 'PLAN_UNREAD')
        plan_read.read(self.p, reader=answer(derived=False))
        quality_gate.plan_was_read(self.p, 's01')   # read as it stands: a finding is answered on the shot list, not here


if __name__ == '__main__':
    unittest.main()
