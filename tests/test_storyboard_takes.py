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
        from studio.project import validate_shot
        fresh = {**load_shot(self.p, 's'), 'camera': {'projection': 'perspective', 'movement': 'authored', 'target_anchor': None, 'keys': []}}
        for row in storyboard.take_rows(self.p, fresh)[0]:   # a fresh shot's camera takes a made move as a valid shot
            validate_shot({**fresh, **row['change']})

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


class DirectorsTakesTest(TakesTest):
    """2026-10-23, r3: compared takes took recipe defaults (ten of fourteen picked cuts framed by nobody), four own takes
    failed and the agent picked around them, three cuts were picked though no take read, two picked takes rushed."""

    def direct(self, **move):
        shot = load_shot(self.p, 's')
        shot['direction'] = {'move': {**MOVE, **move}}
        write_json(shot_path(self.p, 's'), shot)
        return shot

    def test_compared_takes_keep_the_directors_framing_or_are_refused_with_why(self):
        shot = self.direct(recipe='look_up', settings={'height_share': 0.55, 'elevation_deg': -15, 'azimuth_deg': 40})
        rows, refused = storyboard.take_rows(self.p, shot)
        own = next(r for r in rows if r.get('own'))
        self.assertEqual(own['recipe'], 'look_up')
        from studio.recipe_moves import book
        for row in rows:
            if row['recipe'] and not row['own']:
                self.assertEqual(row['chosen']['azimuth_deg'], 'director', row['recipe'])   # the angle always goes with it
                same = book()[row['recipe']]['make']['focus'] == book()['look_up']['make']['focus']
                # the size only where it sizes the same thing (a whole hull's size is no size for its tank): else said
                self.assertEqual(storyboard._framing_defaults(row), [] if same else ['height_share'], row['recipe'])
        said = {r['recipe']: r['why'] for r in refused}
        self.assertIn('pass_by', said)                                  # -15 lies outside pass_by's -12..12
        self.assertIn('outside its range', said['pass_by'])

    def test_the_directors_compared_takes_come_first(self):
        shot = self.direct(others=[{'recipe': 'watch', 'settings': {'height_share': 0.7}, 'why': 'hold and let the tank fill'}])
        rows, _ = storyboard.take_rows(self.p, shot)
        made = [r for r in rows if r['recipe']]
        self.assertEqual([r['recipe'] for r in made[:2]], ['look_up', 'watch'])
        self.assertEqual(made[1]['why'], 'hold and let the tank fill')
        self.assertFalse(made[1]['own'])

    def test_an_own_move_the_engine_cannot_make_is_the_directors_error(self):
        unmade = next(r for r, row in __import__('studio.recipe_moves', fromlist=['book']).book().items() if not row.get('make'))
        with self.assertRaises(StudioError) as caught:
            storyboard.take_rows(self.p, self.direct(recipe=unmade))
        self.assertEqual(caught.exception.code, 'RECIPE_UNMADE')

    def delegate(self, judged_reads=(True, True), failed=(), comfort=None):
        self.sheet()
        project = load_project(self.p)
        project['delegation'] = {'user_words': '알아서 끝내줘요', 'scope': [], 'recorded_at': 'x'}
        write_json(self.p / 'project.json', project)
        env = storyboard.envelope(self.p, 's')
        env['variants']['judge'] = {'pick': 'B', 'why': 'w', 'rank': ['B', 'A'],
                                    'takes': [{'id': t, 'reads': r, 'note': 'n'} for t, r in zip(('A', 'B'), judged_reads)]}
        env['variants']['failed'] = list(failed)
        if comfort:
            env['variants']['takes'][1]['comfort'] = comfort
        write_json(storyboard._envelope_path(self.p, 's'), env)

    def pick(self, **kw):
        written = []
        with mock.patch.object(storyboard, '_write_sheet', side_effect=lambda path, sid, env, captions: written.append(env) or {'rev': 'r01'}):
            storyboard.pick(self.p, 's', 'v01', 'B', agent_note='the judge picked B, it reads best', **kw)
        return written[0]

    def test_an_unbuilt_own_take_holds_the_agents_pick_until_said(self):
        self.delegate(failed=[{'id': 'A', 'label': 'look_up', 'code': 'FRAME_UNSOLVED', 'why': 'swung', 'own': True}])
        with self.assertRaises(StudioError) as caught:
            self.pick()
        self.assertEqual(caught.exception.code, 'TAKE_OWN_UNBUILT')
        env = self.pick(because={'TAKE_OWN_UNBUILT': 'the held view says the same and the solve is an engine gap'})
        self.assertIn('TAKE_OWN_UNBUILT', env['history'][-1]['ops'][0]['because'])

    def test_no_reading_take_is_never_picked_by_the_agent(self):
        self.delegate(judged_reads=(False, False))
        with self.assertRaises(StudioError) as caught:
            self.pick(because={'TAKE_OWN_UNBUILT': 'nothing to do with it at all'})
        self.assertEqual(caught.exception.code, 'TAKE_NONE_READS')
        with self.assertRaises(StudioError):
            storyboard._because(['TAKE_NONE_READS=it reads well enough'])   # no reason passes it

    def test_a_take_the_watcher_did_not_read_is_never_the_agents_pick(self):
        """2026-10-23 r3 s01: the take that read was held back by a floor, and the unread one was taken in its place."""
        self.delegate(judged_reads=(True, False))
        with self.assertRaises(StudioError) as caught:
            self.pick()                              # B: unread
        self.assertEqual(caught.exception.code, 'TAKE_UNREAD')

    def test_a_change_shown_as_a_speck_needs_its_reason(self):
        self.delegate()
        env = storyboard.envelope(self.p, 's')
        env['variants']['takes'][1]['small'] = ['s change fill: at its largest it stands 0.078 of the frame tall - close on it']
        write_json(storyboard._envelope_path(self.p, 's'), env)
        with self.assertRaises(StudioError) as caught:
            self.pick()
        self.assertEqual(caught.exception.code, 'CHANGE_TOO_SMALL')
        self.pick(because={'CHANGE_TOO_SMALL': 'the next cut closes on it; here it is the whole boat sinking'})

    def test_a_rushing_take_needs_its_reason(self):
        self.delegate(comfort={'scale': 4.3, 'lines': ["CAMERA_TOO_FAST: the camera's scale over its fastest 0.5 s is 4.3 - start nearer"]})
        with self.assertRaises(StudioError) as caught:
            self.pick()
        self.assertEqual(caught.exception.code, 'CAMERA_TOO_FAST')
        self.pick(because={'CAMERA_TOO_FAST': 'the jolt is the impact the line names'})

    def test_a_shot_directed_again_too_often_stops(self):
        env = {'schema_version': 1, 'shot_id': 's', 'history': [], 'status': 'none', 'takes_rounds': storyboard.DIRECT_RETRIES + 1}
        write_json(storyboard._envelope_path(self.p, 's'), env)
        with self.assertRaises(StudioError) as caught:
            storyboard.takes(self.p, 's')
        self.assertEqual(caught.exception.code, 'TAKES_RETRIES_SPENT')
        with mock.patch.object(storyboard, 'take_rows', side_effect=StudioError('INPUT_INVALID', 'stop here')):
            with self.assertRaises(StudioError) as caught:
                storyboard.takes(self.p, 's', user_words='한 번 더 해봐')
        self.assertEqual(caught.exception.message, 'stop here')            # the user's words opened another round
        self.assertEqual(storyboard.envelope(self.p, 's')['takes_rounds'], 1)


if __name__ == '__main__':
    unittest.main()
