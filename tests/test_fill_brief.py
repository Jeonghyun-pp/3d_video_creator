"""Fill briefs (studio/fill.py, env_fill_core.level_layout): lint, the user's words, approval bound to the brief, layouts."""
from pathlib import Path
import sys
import tempfile
import unittest

from studio.common import StudioError, read_json, write_json
from studio.fill import approve, brief_sha256, lint, propose, require_approved, revise
from studio.project import init_project, load_shot, shot_path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'studio' / 'blender_ops'))
import env_fill_core as core  # noqa: E402

BRIEF = {'status': 'proposed', 'topic': 'how 80 rebar columns carry the station',
         'request_trace': [{'phrase': '기둥철근', 'source': 'request'}],
         'levels': [{'level_id': 'B3', 'items': [
             {'item_id': 'cols', 'role': 'subject', 'element': 'escalator', 'layout': 'along_edge', 'pitch_m': 9, 'why': "request '기둥철근'", 'source': 'agent'},
             {'item_id': 'people', 'role': 'ambient', 'element': 'pedestrian_*', 'layout': 'density', 'density_per_100m2': 1.0, 'why': 'life', 'source': 'agent'}]},
             {'level_id': 'B4', 'void': True, 'note': 'plant room, not part of the story', 'items': []}]}


class FillBriefTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(init_project('fill_test', {'request': '삼성역 기둥철근', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, self.tmp.name)['project_path'])
        self.draft = Path(self.tmp.name) / 'brief.json'
        write_json(self.draft, BRIEF)

    def tearDown(self):
        self.tmp.cleanup()

    def test_lint_wants_reasons_and_few_identity_cues(self):
        bad = {**BRIEF, 'levels': [{'level_id': 'B1', 'items': [
            {'item_id': f'i{k}', 'role': 'identity', 'element': e, 'layout': 'along_edge', 'count': 2, 'why': 'x' if k == 0 else 'cue', 'source': 'agent'}
            for k, e in enumerate(('bus', 'taxi', 'car'))]}]}
        result = lint(bad)
        self.assertTrue(any('no reason' in e for e in result['errors']))
        self.assertTrue(any('identity kinds' in w for w in result['warnings']))   # taste: a warning by default (studio/gates.py)
        self.assertEqual(lint({**BRIEF, 'levels': [{'level_id': 'B1', 'items': [{**BRIEF['levels'][0]['items'][0], 'element': 'fare_gate_line'}]}]})['missing'][0]['element'], 'fare_gate_line')

    def test_sheet_phrases_include_the_narration(self):
        from studio.fill import _phrases
        shot = read_json(shot_path(self.project, 's'))
        shot['narration']['text'] = '승강장 기둥 속 주철근'
        self.assertIn('주철근', _phrases(self.project, shot))

    def test_propose_revise_approve_keeps_the_users_words(self):
        out = propose(self.project, 's', self.draft)
        self.assertEqual(out['errors'], [])
        self.assertTrue(Path(out['sheet']).is_file())
        with self.assertRaises(StudioError) as caught:
            require_approved(load_shot(self.project, 's'))
        self.assertEqual(caught.exception.code, 'FILL_BRIEF_UNAPPROVED')
        words = '사람은 빼고 기둥만 보이게 해줘'
        revise(self.project, 's', words, remove=['pedestrian_*'])
        brief = load_shot(self.project, 's')['fill_brief']
        self.assertEqual(brief['history'][-1]['user_words'], words)
        self.assertEqual(brief['excluded'][-1]['source'], 'user')
        self.assertNotIn('pedestrian_*', [i['element'] for lv in brief['levels'] for i in lv['items']])
        approve(self.project, 's', '좋아 이대로 가자')
        require_approved(load_shot(self.project, 's'))
        shot = read_json(shot_path(self.project, 's'))           # an edit after approval makes it stale
        shot['fill_brief']['levels'][0]['items'][0]['pitch_m'] = 6
        write_json(shot_path(self.project, 's'), shot)
        with self.assertRaises(StudioError) as caught:
            require_approved(load_shot(self.project, 's'))
        self.assertEqual(caught.exception.code, 'FILL_BRIEF_STALE')

    def test_agent_wrapped_words_are_refused(self):
        propose(self.project, 's', self.draft)
        with self.assertRaises(StudioError):
            approve(self.project, 's', 'User (paraphrased): approve')

    def test_user_additions_are_source_user(self):
        propose(self.project, 's', self.draft)
        revise(self.project, 's', '열차도 하나 넣어줘', add=['identity:bus@B3:along_edge:1'])
        items = load_shot(self.project, 's')['fill_brief']['levels'][0]['items']
        added = [i for i in items if i['element'] == 'bus'][0]
        self.assertEqual((added['source'], added['count']), ('user', 1))
        self.assertIn('열차도 하나', added['why'])

    def test_any_value_of_the_brief_by_path(self):
        propose(self.project, 's', self.draft)
        revise(self.project, 's', '기둥 간격을 좀 넓혀주세요, 12미터로', ops=[{'op': 'set', 'path': '/levels/0/items/0/pitch_m', 'value': 12},
                                                                     {'op': 'set', 'path': '/levels/1/note', 'value': '기계실'}])
        brief = load_shot(self.project, 's')['fill_brief']
        self.assertEqual((brief['levels'][0]['items'][0]['pitch_m'], brief['levels'][1]['note']), (12, '기계실'))
        self.assertIn('/levels/0/items/0/pitch_m: 9 → 12', brief['history'][-1]['change'])
        self.assertEqual(brief['status'], 'proposed')
        for ops, words in [([{'op': 'set', 'path': '/approval', 'value': None}], 'decision record'),
                           ([{'op': 'set', 'path': '/levels/0/items/1/count', 'value': 3}], 'nothing reads'),     # a density fill has no count
                           ([{'op': 'set', 'path': '/levels/0/items/0/bogus', 'value': 3}], 'not a value')]:
            with self.assertRaises(StudioError) as caught:
                revise(self.project, 's', '이것도 바꿔 주세요 부탁해요', ops=ops)
            self.assertIn(words, caught.exception.message)

    def test_hash_ignores_status_and_history(self):
        self.assertEqual(brief_sha256({**BRIEF, 'status': 'approved', 'history': [1]}), brief_sha256(BRIEF))


class LevelLayoutTest(unittest.TestCase):
    RECT = (4.5, 60.0, 17.0, 220.0)

    def test_layouts_are_deterministic_and_inside(self):
        for item in ({'item_id': 'a', 'layout': 'along_edge', 'edge': 'inner', 'pitch_m': 9},
                     {'item_id': 'b', 'layout': 'line_across', 'count': 6, 'at': 0.2},
                     {'item_id': 'c', 'layout': 'grid', 'pitch_m': 5},
                     {'item_id': 'd', 'layout': 'cluster', 'count': 6},
                     {'item_id': 'e', 'layout': 'density', 'density_per_100m2': 1.5}):
            a = core.level_layout(self.RECT, item, seed=3)
            self.assertEqual(a, core.level_layout(self.RECT, item, seed=3))
            self.assertTrue(a and all(4.5 <= x <= 17 and 60 <= y <= 220 for x, y, _ in a), item)

    def test_obstacles_are_kept_clear(self):
        item = {'item_id': 'p', 'layout': 'density', 'density_per_100m2': 4}
        pts = core.level_layout(self.RECT, item, obstacles=[(10.0, y, 1.5) for y in range(60, 221, 9)], seed=1)
        self.assertTrue(all((x - 10) ** 2 + (y - oy) ** 2 >= 1.5 ** 2 for x, y, _ in pts for oy in range(60, 221, 9)))

    def test_density_scales_with_area(self):
        item = {'item_id': 'p', 'layout': 'density', 'density_per_100m2': 2}
        area = (17 - 4.5 - 1.6) * (220 - 60 - 1.6)
        self.assertEqual(len(core.level_layout(self.RECT, item)), round(area * 2 / 100))


if __name__ == '__main__':
    unittest.main()
