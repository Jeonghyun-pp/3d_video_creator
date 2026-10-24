"""A coloured render and a candidate read a shot's quality on its version, not on whether the agent ran the checks
(studio/quality_gate.py, studio/excuses.py). 2026-10-24 audit: every floor lived in `storyboard pick` and the reel's
watcher was read by no gate - a run that skipped them rendered cuts nobody measured or watched."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from studio import excuses, quality_gate, reel_judge
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, shot_path
from studio.reel import CLAY


def clip(path, colour):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=c={colour}:s=90x160:r=30', '-frames:v', '30',
                    '-pix_fmt', 'yuv420p', str(path)], check=True)


class QualityGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(init_project('qg', {'request': 'x', 'shots': [{'shot_id': s, 'frame_count': 30} for s in ('a', 'b', 'c')]},
                                   self.tmp.name)['project_path'])
        (self.p / CLAY).mkdir(parents=True)
        for n, sid in enumerate(('a', 'b', 'c')):
            self.build(sid, 'v0001', colour=('red', 'green', 'blue')[n])

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, sid, version, colour='red', measure=True, scale=0.1, still=None):
        """The shot at `version`, measured (unless not), and its clay clip of that version."""
        file = shot_path(self.p, sid)
        shot = read_json(file)
        shot['scene_version'] = version
        if still:
            shot['direction'] = {'still': still}
        write_json(file, shot)
        folder = file.parent / 'versions' / version
        folder.mkdir(parents=True, exist_ok=True)
        write_json(folder / 'shot.snapshot.json', shot)
        if measure:
            write_json(folder / 'camera_dynamics.json', {'version': version, 'comfort': {'scale': scale, 'travel': 0.0, 'turn_deg': 0.0, 'at_s': {}},
                                                         'nothing': [], 'small': [], 'masked': []})
        clip(self.p / CLAY / f'{sid}.mp4', colour)
        made = self.p / CLAY / 'clay.json'
        rows = {r['shot_id']: r for r in (read_json(made)['shots'] if made.is_file() else [])}
        rows[sid] = {'shot_id': sid, 'version': version, 'clip': f'{sid}.mp4'}
        write_json(made, {'schema_version': 1, 'shots': list(rows.values())})

    def pick(self, sid, because=None, user_words=None):
        from studio.storyboard import _envelope_path, envelope
        env = envelope(self.p, sid) or {'schema_version': 1, 'shot_id': sid, 'history': [], 'status': 'none'}
        env['history'].append({'at': '2026-10-24T00:00:00+00:00', 'user_words': user_words,
                               'ops': [{'op': 'pick', 'variant': 'A', 'sheet': 'v01', **({'because': because} if because else {})}]})
        env.pop('reopened', None)
        write_json(_envelope_path(self.p, sid), env)

    def watch(self, wanting=()):
        def read(images, prompt, schema, target):
            write_json(target, {'answer': {'shots': [{'shot_id': s, 'reads': True, 'change_seen': True, 'names_change': True,
                                                      'dull': s in wanting, 'repeats_before': False, 'harsh': False, 'note': 'n'}
                                                     for s in ('a', 'b', 'c')], 'hook_holds': True, 'flow': 'f', 'redo': []}})
        return reel_judge.judge(self.p, reader=read, force=True)

    def reads(self, accepted):
        def read(images, prompt, schema, target):
            n = schema['properties']['reasons']['minItems']
            write_json(target, {'answer': {'reasons': [{'n': i, 'accepted': accepted, 'why': 'w'} for i in range(n)]}})
        return excuses.judge(self.p, reader=read)

    def code(self, sid='a'):
        with self.assertRaises(StudioError) as caught:
            quality_gate.require_shot(self.p, sid)
        return caught.exception.code

    def ready(self):
        for sid in ('a', 'b', 'c'):
            self.pick(sid)
        self.watch()

    def test_a_shot_nobody_measured_or_picked_does_not_render(self):
        self.build('a', 'v0002', measure=False)
        self.assertEqual(self.code(), 'SHOT_UNMEASURED')
        self.build('a', 'v0002')
        self.assertEqual(self.code(), 'SHOT_UNPICKED')
        self.pick('a')
        from studio.storyboard import _envelope_path, envelope
        env = envelope(self.p, 'a')
        env['reopened'] = {'why': 'dull', 'at': '2026-10-24T01:00:00+00:00'}   # the reel's watcher sent it back after the pick
        write_json(_envelope_path(self.p, 'a'), env)
        self.assertEqual(self.code(), 'SHOT_UNPICKED')

    def test_the_reel_watcher_must_have_seen_the_cut_and_its_neighbours_as_they_are(self):
        for sid in ('a', 'b', 'c'):
            self.pick(sid)
        self.assertEqual(self.code(), 'REEL_UNJUDGED')
        self.watch()
        quality_gate.require_shot(self.p, 'a')
        self.build('b', 'v0002', colour='white')        # a's neighbour changed: what a was watched beside is gone
        self.assertEqual(self.code('a'), 'REEL_JUDGE_STALE')
        self.watch()
        self.build('c', 'v0002', colour='black')        # c is not a's neighbour: a's verdict still holds
        quality_gate.require_shot(self.p, 'a')

    def test_a_hard_floor_holds_whatever_the_reason_and_only_the_users_words_keep_it(self):
        self.ready()
        self.build('a', 'v0002', scale=0.7)             # past the comfort limit (0.6), under 1.5x: a reason's to answer
        self.pick('a'); self.watch()
        quality_gate.require_shot(self.p, 'a')
        self.build('a', 'v0003', scale=1.0)             # 1.5x the limit or more: no reason passes
        self.pick('a', because={'CAMERA_TOO_FAST': 'the rush is the point of the shot'}); self.watch()
        self.assertEqual(self.code(), 'SHOT_FLOOR')
        with mock.patch.dict('os.environ', {'STUDIO_UNATTENDED': ''}):
            quality_gate.allow(self.p, 'a', 'CAMERA_TOO_FAST', '이 컷은 빠르게 가도 괜찮아요')
        self.assertEqual(self.code(), 'EXCUSE_UNREAD')   # past the floor; the pick's reason is still read

    def test_a_finding_is_fixed_answered_or_waits_on_the_user_never_left_open(self):
        for sid in ('a', 'b', 'c'):
            self.pick(sid)
        self.watch(wanting=('a',))
        self.assertEqual(self.code(), 'SHOT_UNPICKED')   # the finding reopened it: take again, or answer
        quality_gate.answer(self.p, 'a', 'dull', 'held on purpose for the beat')
        self.assertEqual(self.code(), 'EXCUSE_UNREAD')
        self.reads(accepted=False)
        self.assertEqual(self.code(), 'EXCUSE_REJECTED')
        quality_gate.answer(self.p, 'a', 'dull', 'the burst needs the generative pass the user has not capped', awaits='paid')
        self.assertEqual(self.code(), 'REEL_FINDING_OPEN')   # waiting on the user keeps it from rendering: no way round a fix
        quality_gate.answer(self.p, 'a', 'dull', 'the pause lets the line about the weight land before the dive')
        self.reads(accepted=True)
        quality_gate.require_shot(self.p, 'a')

    def test_a_reason_written_in_place_of_a_fix_is_read_with_the_picture(self):
        self.ready()
        self.build('a', 'v0002', still='hold'); self.pick('a'); self.watch()
        self.assertEqual(self.code(), 'EXCUSE_UNREAD')
        got = self.reads(accepted=False)
        self.assertEqual(got['refused'][0]['row'], 'still')
        self.assertEqual(self.code(), 'EXCUSE_REJECTED')
        self.build('a', 'v0003', colour='yellow', still='hold')   # a new version is read again
        self.pick('a'); self.watch()
        self.assertEqual(self.code(), 'EXCUSE_UNREAD')

    def test_the_users_own_pick_reasons_are_theirs_and_not_read(self):
        self.ready()
        self.pick('a', because={'CHANGE_MASKED': 'keep it moving'}, user_words='이대로 좋아요')
        self.watch()
        quality_gate.require_shot(self.p, 'a')

    def test_a_generated_cut_is_left_to_the_person_who_watches_it(self):
        self.build('b', 'v0002', measure=False)
        real = quality_gate.load_shot
        routed = lambda path, sid: {**real(path, sid), **({'route': {'mode': 'generative'}} if sid == 'b' else {})}   # noqa: E731
        with mock.patch.object(quality_gate, 'load_shot', routed):
            quality_gate.require_shot(self.p, 'b')

    def test_a_coloured_render_and_a_candidate_meet_the_gate(self):
        from studio import jobs
        with mock.patch('studio.routing.assert_route'), mock.patch('studio.decisions.require'), \
                mock.patch('studio.storyboard.require'), mock.patch('studio.fidelity.require_fidelity'), \
                mock.patch.object(jobs, '_require_direction'), mock.patch.object(jobs, '_require_turnaround'):
            shot = read_json(shot_path(self.p, 'a'))
            with self.assertRaises(StudioError) as caught:
                jobs.render_gates(self.p, {}, shot, 'v0001', 'look')
            self.assertEqual(caught.exception.code, 'SHOT_UNPICKED')
            jobs.render_gates(self.p, {}, shot, 'v0001', 'layout')   # the clay the watcher needs is never held


if __name__ == '__main__':
    unittest.main()
