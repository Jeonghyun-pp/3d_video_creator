"""Contract and safety checks; Blender integration is a separate smoke script."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from studio.common import StudioError, file_hash, font_file, read_json, safe_path, write_json
from studio.project import default_shot, deliver, init_project, load_project, shot_path, validate_project, validate_shot


class CoreContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        result = init_project('test_project', {'request':'Test', 'shots':[{'shot_id':'shot_01','frame_count':30},{'shot_id':'shot_02','frame_count':60}]}, self.root)
        self.project = Path(result['project_path'])

    def tearDown(self):
        self.temp.cleanup()

    def test_contiguous_timeline_and_duration(self):
        self.assertEqual(validate_project(self.project)['frame_count'],90)
        data = load_project(self.project); data['shots'][1]['start_frame'] = 31
        write_json(self.project/'project.json',data)
        with self.assertRaises(StudioError) as error:
            validate_project(self.project)
        self.assertEqual(error.exception.code,'TIMING_CONFLICT')

    def test_half_open_and_conflicting_actions(self):
        shot = default_shot('test',30,{'request':'test'})
        action = {'action_id':'move','type':'explode','targets':[{'instance_id':'x','part_id':'panel'}],
                  'start_frame':0,'end_frame':30,'easing':'linear','params':{'direction_source':'axis','distance_m':1}}
        shot['actions']=[action]
        validate_shot(shot)
        duplicate=copy.deepcopy(action); duplicate['action_id']='overlap'; duplicate['start_frame']=29
        shot['actions'].append(duplicate)
        with self.assertRaises(StudioError):
            validate_shot(shot)
        shot['actions']=[action]; action['end_frame']=31
        with self.assertRaises(StudioError):
            validate_shot(shot)

    def test_unknown_fields_and_path_escape(self):
        shot = default_shot('test',30,{'request':'test'}); shot['silent_typo']=True
        with self.assertRaises(StudioError):
            validate_shot(shot)
        with self.assertRaises(StudioError):
            safe_path(self.project,'../outside')

    def test_scratch_never_promoted(self):
        directory=self.project/'final'/'test'; directory.mkdir(parents=True)
        (directory/'candidate.mp4').write_bytes(b'candidate')
        write_json(directory/'manifest.json',{'speech_status':'scratch'})
        write_json(self.project/'review.json',{'reviewer_kind':'human'})
        with self.assertRaises(StudioError) as error:
            deliver(self.project,'test',self.project/'review.json')
        self.assertEqual(error.exception.code,'QUALITY_GATE_FAILED')

    def test_metadata_revision_keeps_scene_and_rejects_stale_base(self):
        from studio.blender import revise_shot
        shot_file = shot_path(self.project, 'shot_01')
        before = read_json(shot_file)
        change = self.project / 'change.json'
        write_json(change, {'base_revision': before['revision'], 'scope': 'labels', 'targets': [],
                            'change': {'labels': [{'label_id': 'label', 'text': 'Panel', 'anchor': 'x/panel/center',
                            'start_frame': 0, 'end_frame': 30, 'slot': 'upper_left', 'occlusion_policy': 'hide'}]}, 'preserve': []})
        result = revise_shot(self.project, 'shot_01', change)
        after = read_json(shot_file)
        self.assertFalse(result['render_invalidated'])
        self.assertEqual(before['scene_version'], after['scene_version'])
        self.assertEqual(before['revision']+1, after['revision'])
        with self.assertRaises(StudioError) as error:
            revise_shot(self.project, 'shot_01', change)
        self.assertEqual(error.exception.code, 'REVISION_CONFLICT')

    def test_look_only_does_not_advance_motion_stage(self):
        from studio.project import status_project
        shot_file = shot_path(self.project, 'shot_01')
        shot = read_json(shot_file); shot['scene_version'] = 'v0001'; write_json(shot_file, shot)
        render = shot_file.parent / 'renders' / 'look' / 'render.json'
        write_json(render, {'scene_version': 'v0001', 'status': 'complete', 'full_sequence': False, 'clip_path': None})
        status = status_project(self.project)
        self.assertEqual(status['stage'], 'briefed')
        self.assertEqual(status['shots'][0]['renders'], [])

    def test_render_budget_deduplicates_cancelled_attempts(self):
        from studio.jobs import record_render_time
        run_path = next(self.project.glob('runs/*/run.json'))
        job_path = run_path.parent / 'jobs' / 'job_test' / 'job.json'
        job = {'job_id': 'job_test', 'attempt': 1}
        record_render_time(job_path, job, 2)
        record_render_time(job_path, job, 3)
        record_render_time(job_path, job, 1)
        self.assertEqual(read_json(run_path)['elapsed']['render_wall_seconds'], 3)
        job['attempt'] = 2
        record_render_time(job_path, job, 4)
        self.assertEqual(read_json(run_path)['elapsed']['render_wall_seconds'], 7)

    def test_saved_run_distinguishes_active_and_recoverable_jobs(self):
        from studio.project import reconcile_runs, resume_project
        run_path = next(self.project.glob('runs/*/run.json'))
        run_id = read_json(run_path)['run_id']
        state = {'stage': 'motion_ready', 'shots': [{'renders': ['valid_clip']}], 'jobs': [
            {'run_id': run_id, 'job_id': 'old_cancelled', 'status': 'cancelled', 'error': None},
            {'run_id': run_id, 'job_id': 'active', 'status': 'running', 'error': None}]}
        reconcile_runs(self.project, state)
        saved = read_json(run_path)
        self.assertEqual(saved['stage'], 'motion_ready')
        self.assertEqual(saved['status'], 'running')
        self.assertEqual(saved['pending_jobs'], ['active'])
        self.assertEqual(saved['recoverable_jobs'], ['old_cancelled'])
        state['jobs'][1]['status'] = 'complete'; state['stage'] = 'candidate_ready'
        reconcile_runs(self.project, state)
        saved = read_json(run_path)
        self.assertEqual(saved['status'], 'complete')
        self.assertEqual(saved['pending_jobs'], [])
        self.assertEqual(saved['recoverable_jobs'], ['old_cancelled'])

    def test_candidate_rejects_audio_change_without_revision_bump(self):
        from studio.project import project_content_hash, status_project
        project = load_project(self.project)
        style = read_json(self.project / 'style.json')
        directory = self.project / 'final' / 'valid_candidate'
        directory.mkdir(parents=True)
        candidate = directory / 'candidate.mp4'
        candidate.write_bytes(b'immutable candidate fixture')
        digest = file_hash(candidate)
        snapshot = {'project_revision': project['revision'], 'project_content_hash': project_content_hash(project),
                    'style_snapshot': style, 'font_sha256': file_hash(font_file(style, self.project)),
                    'shots': [{'shot_id': entry['shot_id'], 'shot_snapshot': read_json(shot_path(self.project, entry['shot_id']))}
                              for entry in project['shots']]}
        write_json(directory / 'edit.snapshot.json', snapshot)
        write_json(directory / 'manifest.json', {'candidate_id': 'valid_candidate', 'speech_status': 'final', 'profile': 'candidate',
                   'output_path': str(candidate.relative_to(self.project)), 'output_sha256': digest})
        write_json(directory / 'qa.json', {'candidate_hash': digest, 'technical_pass': True})
        review = self.project / 'human-review.json'
        write_json(review, {'candidate_hash': digest, 'reviewer_kind': 'human', 'approval_evidence': 'Unit-test confirmation fixture',
                           'facts_approved': True, 'script_approved': True, 'assets_approved': True, 'visual_approved': True})
        self.assertEqual(deliver(self.project, 'valid_candidate', review)['status'], 'approved')
        project['limits']['render_wall_minutes'] += 10
        write_json(self.project / 'project.json', project)
        self.assertEqual(deliver(self.project, 'valid_candidate', review)['status'], 'approved')
        project['audio']['voice_id'] = 'different_voice'
        write_json(self.project / 'project.json', project)
        self.assertEqual(project['revision'], snapshot['project_revision'])
        with self.assertRaises(StudioError) as error:
            deliver(self.project, 'valid_candidate', review)
        self.assertEqual(error.exception.code, 'QUALITY_GATE_FAILED')
        self.assertIn('project content', error.exception.message)
        self.assertNotIn('valid_candidate', status_project(self.project)['current_candidates'])

    def test_explicit_preserve_replaces_previous_guard_and_omission_retains_it(self):
        from studio.blender import revise_shot
        shot_file = shot_path(self.project, 'shot_01')
        shot = read_json(shot_file); shot['scene_version'] = 'v0001'; write_json(shot_file, shot)
        change = self.project / 'change.json'
        write_json(change, {'base_revision': shot['revision'], 'scope': 'edit', 'targets': [], 'change': {},
                            'preserve': ['scene_version', 'camera', 'actions']})
        revise_shot(self.project, 'shot_01', change)
        current = read_json(shot_file)
        camera_change = {'base_revision': current['revision'], 'scope': 'camera', 'targets': ['camera'], 'change': {}}
        write_json(change, camera_change)
        with patch('studio.blender.build_shot') as build:
            with self.assertRaises(StudioError) as error:
                revise_shot(self.project, 'shot_01', change)
            self.assertEqual(error.exception.code, 'PRESERVE_VIOLATION')
            build.assert_not_called()
        camera_change['preserve'] = ['geometry', 'materials']
        write_json(change, camera_change)
        with patch('studio.blender.build_shot', return_value={'scene_version': 'v0002'}) as build:
            revised = revise_shot(self.project, 'shot_01', change)
            self.assertTrue(revised['render_invalidated'])
            self.assertEqual(build.call_args.args[4]['preserve'], ['geometry', 'materials'])
        camera_change['preserve'] = []
        write_json(change, camera_change)
        with patch('studio.blender.build_shot', return_value={'scene_version': 'v0002'}) as build:
            revise_shot(self.project, 'shot_01', change)
            self.assertEqual(build.call_args.args[4]['preserve'], [])
        # Mocked build must not alter the actual stored scene or guards.
        self.assertEqual(read_json(shot_file), current)

    def test_agent_cannot_claim_human_approval(self):
        from studio.project import record_review
        with self.assertRaises(StudioError):
            record_review(self.project,{'candidate_hash':'abc','reviewer_kind':'agent','verdict':'human_approved','approval_evidence':'auto'})


if __name__=='__main__':
    unittest.main()


class FromExampleTest(unittest.TestCase):
    def test_example_project_is_ready_to_render_and_reports_missing_inputs(self):
        from studio.project import from_example, load_shot, missing_files
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'samsung'
            result = from_example('samsung_cutaway', target)
            self.assertEqual(len(list((target / 'runs').glob('*/run.json'))), 1)        # render submit needs a run
            for entry in load_project(target)['shots']:
                self.assertEqual([r for r in missing_files(target, load_shot(target, entry['shot_id']), 'shot') if r['kind'] == 'derived'], [])
            self.assertIn('prompts/s01.txt', [r['path'] for r in result['missing_inputs']])
            self.assertIn('prompts/s01.txt', [r['path'] for r in validate_project(target)['missing_files']])

    def test_failed_copy_leaves_nothing_behind(self):
        from studio import project as projectmod
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'samsung'
            with patch.object(projectmod, 'validate_project', side_effect=StudioError('INPUT_INVALID', 'broken')), self.assertRaises(StudioError):
                projectmod.from_example('samsung_cutaway', target)
            self.assertEqual(list(Path(tmp).iterdir()), [])
            projectmod.from_example('samsung_cutaway', target)                         # the retry is not blocked


class AuthorCompanionTest(unittest.TestCase):
    def test_a_revision_patch_beside_the_author_is_never_replaced_by_it(self):
        from studio.blender import _author_companions
        from studio.common import REPO
        with tempfile.TemporaryDirectory(dir=REPO / 'examples') as tmp:   # outside studio/ and tests/, like a production
            folder = Path(tmp)
            for name in ('author.py', 'patch.py', 'lib.py'):
                (folder / name).write_text('# ' + name)
            self.assertEqual([p.name for p in _author_companions(folder / 'patch.py')], ['lib.py'])
