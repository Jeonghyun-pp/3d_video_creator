"""Takes made from a shot's motion (storyboard take_rows / takes), judged as clips (judge), picked by the user's words or
- in a delegated run - by the agent on a judged sheet (pick --agent-note). 2026-10-22: the motion between two stills
was nobody's choice; the camera is now chosen as a motion, from takes watched as clips."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from studio import storyboard
from studio.common import StudioError, write_json
from studio.project import init_project, load_project, load_shot, shot_path

MOVE = {'recipe': 'look_up', 'reveals': 'the hull overhead', 'key_at': 0.6, 'why': 'from below the weight is felt'}


def clip(path):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=s=90x160:r=30', '-frames:v', '30', '-pix_fmt', 'yuv420p',
                    str(path)], check=True)


class TakesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(init_project('tk', {'request': 'x', 'shots': [{'shot_id': s, 'frame_count': 60} for s in ('a', 's', 'b')]},
                                   self.tmp.name)['project_path'])
        shot = load_shot(self.p, 's')
        shot.update({'frame': {'focus': ['boat'], 'size': 'medium'}, 'direction': {'move': MOVE},
                     'show': [{'at': 'start', 'parts': ['boat'], 'how': 'exterior', 'why': 'the whole boat'},
                              {'at': 0.5, 'parts': ['boat/tank'], 'how': 'cutaway', 'why': 'the tank inside'}],
                     'camera': {**shot['camera'], 'movement': 'rig', 'move': {'type': 'frame_to_frame', 'params': {'target': 'boat'}, 'frames': [
                         {'at': a, 'height_share': 0.3, 'centre': [0.5, 0.5], 'view': {'elevation_deg': 0.0, 'azimuth_deg': 0.0}}
                         for a in (0.0, 1.0)]}}})
        write_json(shot_path(self.p, 's'), shot)
        neighbour = load_shot(self.p, 'a')
        neighbour['direction'] = {'move': {**MOVE, 'recipe': 'pass_by'}}
        write_json(shot_path(self.p, 'a'), neighbour)

    def tearDown(self):
        self.tmp.cleanup()

    def test_takes_are_the_shots_own_recipe_others_and_its_pictured_camera(self):
        rows, refused = storyboard.take_rows(self.p, load_shot(self.p, 's'))
        made = [r['recipe'] for r in rows if r['recipe']]
        self.assertEqual(made[0], 'look_up')                         # the director's own first
        self.assertEqual(len(made), storyboard.TAKES_MADE)
        self.assertNotEqual(made[1], 'pass_by')                       # the neighbour's feel comes last
        self.assertEqual(rows[0]['id'], 'P')                          # the camera its pictures set, kept to compare
        self.assertTrue(refused)                                      # a recipe the shot cannot take is said with why
        self.assertTrue(all(r['why'] for r in refused))
        for row in rows[1:]:
            self.assertEqual(row['change']['camera']['move']['recipe']['id'], row['recipe'])
        with self.assertRaises(StudioError):
            storyboard.take_rows(self.p, {**load_shot(self.p, 's'), 'direction': {}})   # no motion chosen: no takes

    def sheet(self, judged=None):
        folder = self.p / 'decisions' / 'sheets' / 'storyboard_s' / 'v01'; folder.mkdir(parents=True)
        takes = []
        for tid in ('A', 'B'):
            version = f'v000{len(takes) + 1}'
            vdir = shot_path(self.p, 's').parent / 'versions' / version; vdir.mkdir(parents=True)
            clip(vdir / 'animatic.mp4')
            write_json(vdir / 'shot.snapshot.json', {**load_shot(self.p, 's'), 'scene_version': version})
            takes.append({'id': tid, 'label': tid, 'recipe': None, 'version': version, 'change': {},
                          'animatic': str((vdir / 'animatic.mp4').relative_to(self.p))})
        env = {'schema_version': 1, 'shot_id': 's', 'history': [], 'status': 'variants', 'approval': None, 'sheet': None,
               'variants': {'rev': 'v01', 'path': str((folder / 'sheet.md').relative_to(self.p)),
                            'base_content_sha256': storyboard.content_sha256(load_shot(self.p, 's')), 'takes': takes,
                            **({'judge': judged} if judged else {})},
               'body': {'frames': [0, 59], 'focus': {}, 'captions': {}}}
        write_json(storyboard._envelope_path(self.p, 's'), env)

    def test_a_judge_reads_every_take_beside_its_neighbours_and_records_its_pick(self):
        self.sheet()
        asked = []

        def reader(images, prompt, schema, target):
            asked.append((images, prompt))
            write_json(target, {'answer': {'takes': [{'id': t, 'reads': True, 'key_seen': t == 'B', 'shows_the_line': True, 'dull': t == 'A',
                                                      'harsh': False, 'note': 'n'} for t in ('A', 'B')],
                                           'rank': ['B', 'A'], 'pick': 'B', 'why': 'B shows the hull overhead'}})
        result = storyboard.judge(self.p, 's', 'v01', reader=reader)
        self.assertEqual(result['judge']['pick'], 'B')
        self.assertEqual(len(asked[0][0]), 2)   # the takes' strips (the neighbours have no clip yet)
        self.assertIn('the hull overhead', asked[0][1])
        self.assertEqual(storyboard.envelope(self.p, 's')['variants']['judge']['pick'], 'B')

    def test_the_agent_picks_only_a_judged_sheet_in_a_delegated_run(self):
        self.sheet()
        with self.assertRaises(StudioError):   # no delegation: the user's words only
            storyboard.pick(self.p, 's', 'v01', 'B', agent_note='the judge picked B, it reads best')
        project = load_project(self.p)
        project['delegation'] = {'user_words': '알아서 끝내줘요', 'scope': [], 'recorded_at': 'x'}
        write_json(self.p / 'project.json', project)
        with self.assertRaises(StudioError) as caught:   # delegated, but nobody watched the clips
            storyboard.pick(self.p, 's', 'v01', 'B', agent_note='the judge picked B, it reads best')
        self.assertEqual(caught.exception.code, 'TAKES_UNJUDGED')
        env = storyboard.envelope(self.p, 's')
        env['variants']['judge'] = {'pick': 'B', 'why': 'w', 'rank': ['B', 'A'], 'takes': []}
        write_json(storyboard._envelope_path(self.p, 's'), env)
        tasted, written = [], []
        with mock.patch.object(storyboard, '_write_sheet', side_effect=lambda path, sid, env, captions: written.append(env) or {'rev': 'r01'}), \
                mock.patch('studio.taste_picks.record', lambda *a, **k: tasted.append(a)):
            storyboard.pick(self.p, 's', 'v01', 'B', agent_note='the judge picked B, it reads best')
        self.assertEqual(load_shot(self.p, 's')['scene_version'], 'v0002')
        last = written[0]['history'][-1]
        self.assertEqual((last['user_words'], last['agent_note']), (None, 'the judge picked B, it reads best'))
        self.assertEqual(tasted, [])   # the agent's pick is not the user's taste


class ReelTest(TakesTest):
    """2026-10-22: judged beside its two neighbours only, five shots took the same pull-back; the reel's watcher's redo
    list was written and nothing read it."""

    def test_the_reels_common_motion_comes_last_and_an_extra_fresh_take_joins(self):
        for sid in ('a', 'b'):   # both other shots already look up
            other = load_shot(self.p, sid)
            other['direction'] = {'move': {**MOVE, 'recipe': 'look_up'}}
            write_json(shot_path(self.p, sid), other)
        rows, _ = storyboard.take_rows(self.p, load_shot(self.p, 's'))
        made = [r['recipe'] for r in rows if r['recipe']]
        self.assertEqual(made[0], 'look_up')
        self.assertEqual(len(made), storyboard.TAKES_MADE + 1)          # its own recipe is the reel's common one: one more
        self.assertNotIn('look_up', made[1:])

    def test_the_agent_keeps_the_reels_common_motion_or_a_sent_back_one_only_with_why(self):
        self.sheet()
        project = load_project(self.p)
        project['delegation'] = {'user_words': '알아서 끝내줘요', 'scope': [], 'recorded_at': 'x'}
        write_json(self.p / 'project.json', project)
        env = storyboard.envelope(self.p, 's')
        env['variants']['judge'] = {'pick': 'B', 'why': 'w', 'rank': ['B', 'A'], 'takes': []}
        for take in env['variants']['takes']:
            take['recipe'] = 'look_up'
        env['reopened'] = {'round': 1, 'why': 'same as its neighbours', 'try': 'pass by', 'rejected': 'look_up'}
        write_json(storyboard._envelope_path(self.p, 's'), env)
        for sid in ('a', 'b'):
            other = load_shot(self.p, sid)
            other['direction'] = {'move': {**MOVE, 'recipe': 'look_up'}}
            write_json(shot_path(self.p, sid), other)
        with self.assertRaises(StudioError) as caught:
            storyboard.pick(self.p, 's', 'v01', 'B', agent_note='the judge picked B, it reads best')
        self.assertEqual(caught.exception.code, 'TAKE_REPEATS_REEL')
        for sid in ('a', 'b'):   # the reel moves otherwise now: only the watcher's sent-back feel stands in the way
            other = load_shot(self.p, sid)
            other['direction'] = {'move': {**MOVE, 'recipe': 'pass_by' if sid == 'a' else 'dig_in'}}
            write_json(shot_path(self.p, sid), other)
        with self.assertRaises(StudioError) as caught:
            storyboard.pick(self.p, 's', 'v01', 'B', agent_note='the judge picked B, it reads best')
        self.assertEqual(caught.exception.code, 'TAKE_SAME_AS_REJECTED')
        written = []
        with mock.patch.object(storyboard, '_write_sheet', side_effect=lambda path, sid, env, captions: written.append(env) or {'rev': 'r01'}):
            storyboard.pick(self.p, 's', 'v01', 'B', agent_note='the judge picked B, it reads best', repeat_why='the boat must be felt overhead here')
        self.assertNotIn('reopened', written[0])                          # the pick answers the watcher
        self.assertEqual(written[0]['history'][-1]['ops'][0]['repeat_why'], 'the boat must be felt overhead here')


if __name__ == '__main__':
    unittest.main()
