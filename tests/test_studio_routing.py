from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from studio.common import StudioError, read_json, write_json
from studio.project import init_project, load_shot, shot_path, status_project, validate_shot
from studio.routing import approve, assert_route, classify, estimate, lint, lint_prompt, plan, propose_route
from studio.blender import build_shot, revise_shot

ROOT = Path(__file__).resolve().parents[1]
GOOD_PROMPT = 'Photoreal steel and wet concrete. Follow the input video camera, timing and positions exactly. No text, no letters, no captions.'


def generative(model='veo-3.1', operation='image_to_video', inputs=()):
    return {'provider': 'fal', 'model': model, 'operation': operation, 'prompt_ref': 'prompts/city.txt', 'duration_seconds': 6,
            'usd_per_second': None, 'max_attempts': 2, 'text_in_frame': False, 'ai_disclosure': True, 'inputs': list(inputs)}



def approve_reviewed(path, shot_id, words, budget_usd=None):
    """Approve the way the agent must: show a generation review sheet, then record the user's words against it."""
    from studio.generative.review import build_review
    review = build_review(path, [shot_id])
    return approve(path, shot_id, words, budget_usd, review['review_id'])

class RoutingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        brief = {'request': 'route test', 'output': {'target_seconds': 6, 'fps': 30},
                 'shots': [{'shot_id': 'packshot', 'frame_count': 60, 'route_features': ['simple_hard_surface']},
                           {'shot_id': 'city', 'frame_count': 180, 'route_features': ['real_place_atmosphere'], 'generative': generative()},
                           {'shot_id': 'mech', 'frame_count': 90, 'route_features': ['exact_motion', 'photoreal_beyond_assets'],
                            'generative': generative('seedance-2.5', 'video_to_video', [{'kind': 'previs', 'path': 'previs/mech.mp4'}])}]}
        self.path = Path(init_project('route_test', brief, self.temp.name)['project_path'])
        (self.path / 'prompts').mkdir()
        (self.path / 'previs').mkdir(); (self.path / 'previs/mech.mp4').write_bytes(b'previs')

    def tearDown(self):
        self.temp.cleanup()

    def test_existing_projects_validate_without_route_and_are_untouched(self):
        paths = sorted((ROOT / 'projects').glob('**/shots/*/shot.json'))
        paths = [p for p in paths if '/versions/' not in str(p)]
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        for path in paths:
            shot = read_json(path)
            validate_shot(shot)
            if 'route' not in shot:
                self.assertEqual(assert_route(shot, 'build')['mode'], 'blender')
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})

    def test_existing_style_json_validates(self):
        from studio.project import validate_schema
        for path in (ROOT / 'projects').glob('**/style.json'):
            if '/versions/' not in str(path):
                validate_schema(read_json(path), 'style')

    def test_init_routes_from_declared_features(self):
        modes = {s: load_shot(self.path, s)['route']['mode'] for s in ('packshot', 'city', 'mech')}
        self.assertEqual(modes, {'packshot': 'blender', 'city': 'generative', 'mech': 'hybrid'})
        self.assertEqual(load_shot(self.path, 'city')['route']['est_cost_usd'], 2.4)  # 6 s x $0.20 x 2 attempts
        self.assertTrue(all(load_shot(self.path, s)['route']['status'] == 'proposed' for s in ('packshot', 'city', 'mech')))

    def test_classify_handoff_table(self):
        table = [(['simple_hard_surface'], 'blender'), (['exact_geometry', 'exact_motion'], 'blender'), (['cross_shot_identity'], 'blender'),
                 (['anchored_text'], 'blender'), (['real_place_atmosphere'], 'generative'), (['unstructured_phenomena'], 'generative'),
                 (['exact_motion', 'photoreal_beyond_assets'], 'hybrid'), (['ai_label_unacceptable', 'unstructured_phenomena'], 'blender')]
        for features, mode in table:
            with self.subTest(features=features):
                self.assertEqual(classify(features)[0], mode)

    def test_classify_unseen_cases_by_principle(self):
        self.assertEqual(classify(['exact_geometry', 'unstructured_phenomena'])[0], 'hybrid')  # molten glass in a mould
        self.assertEqual(classify(['exact_motion', 'exact_geometry'])[0], 'blender')  # satellite deploying panels
        self.assertEqual(classify(['real_place_atmosphere', 'unstructured_phenomena'])[0], 'generative')  # night market crowd
        self.assertEqual(classify([])[1], 'R0_default_blender')
        self.assertEqual(classify(['real_place_atmosphere'], {'allow_generative': False})[0], 'blender')

    def test_goal_text_is_ignored(self):
        shot = deepcopy(load_shot(self.path, 'packshot'))
        shot['goal'] = 'Fire, smoke and a photoreal Seoul skyline with crowds'
        self.assertEqual(propose_route(shot)['mode'], 'blender')

    def test_structure_implies_features(self):
        shot = deepcopy(load_shot(self.path, 'packshot')); del shot['route']
        shot['labels'] = [{'label_id': 'a'}]
        self.assertIn('anchored_text', propose_route(shot)['features'])

    def test_estimates(self):
        blender = estimate({'duration_frames': 300}, {'mode': 'blender'})
        self.assertEqual((blender['est_minutes'], blender['estimate_source']), (5.5, 'assumed'))
        unknown = estimate({'duration_frames': 60}, {'mode': 'hybrid', 'generative': generative('luma-ray-modify', 'video_to_video')})
        self.assertEqual((unknown['est_cost_usd'], unknown['estimate_source']), (None, 'unknown'))

    def test_plan_does_not_write_without_apply_and_keeps_user_decisions(self):
        before = {s: load_shot(self.path, s) for s in ('packshot', 'city', 'mech')}
        result = plan(self.path)
        self.assertEqual(before, {s: load_shot(self.path, s) for s in before})
        self.assertTrue((self.path / 'route_plan.md').is_file())
        # veo 6 s x 2 attempts + seedance billed on input + output seconds (6 + 6) x 2 attempts
        self.assertEqual(result['total_est_cost_usd'], round(2.4 + round(.2838 * 12, 2) * 2, 2))
        self.assertTrue(result['over_budget'])

    def test_one_price_table(self):
        """The approved estimate and the ledger reservation come from the same table (fal_client.PRICING)."""
        from studio.generative.clip import _billed_output_seconds
        from studio.generative.fal_client import PRICING, estimate_usd
        from studio.generative.review import pad_seconds
        from studio.routing import MODELS
        for model, entry in MODELS.items():
            for operation, endpoint in entry['operations'].items():
                self.assertIn(endpoint, PRICING)
                spec = generative(model, operation); spec['duration_seconds'] = 3.1
                route = estimate({'duration_frames': 93}, {'mode': 'hybrid', 'generative': spec})
                if route['est_cost_usd'] is None:
                    continue                          # no quote recorded yet: refused at approval, never guessed
                video_in = spec['duration_seconds'] + pad_seconds(spec) if operation == 'video_to_video' else 0.0
                per_call = estimate_usd(endpoint, _billed_output_seconds(spec), video_in)
                self.assertEqual(route['est_cost_usd'], round(per_call * spec['max_attempts'], 2), (model, operation))

    def test_plan_apply_ignores_new_estimates(self):
        plan(self.path, apply=True)
        revision = load_shot(self.path, 'packshot')['revision']
        render = self.path / 'shots/packshot/renders/fp1'; render.mkdir(parents=True)
        write_json(render / 'render.json', {'device': 'GPU'}); write_json(render / 'frame_times.json', [7.5, 8.0])
        plan(self.path, apply=True)                       # only est_minutes moved: the shot is not rewritten
        self.assertEqual(load_shot(self.path, 'packshot')['revision'], revision)

    def test_plan_apply_fills_a_written_paid_route_estimate(self):
        plan(self.path, apply=True)
        shot = load_shot(self.path, 'mech')
        self.assertIn(shot['route']['mode'], ('hybrid', 'generative'))
        shot['route']['est_cost_usd'] = None   # a route the agent wrote by hand
        write_json(shot_path(self.path, 'mech'), shot)
        plan(self.path, apply=True)
        after = load_shot(self.path, 'mech')['route']
        self.assertIsNotNone(after['est_cost_usd']); self.assertEqual(after['mode'], shot['route']['mode'])

    def test_refused_approval_leaves_the_budget_untouched(self):
        before = (self.path / 'project.json').read_bytes()
        with self.assertRaises(StudioError) as error:
            approve_reviewed(self.path, 'mech', '메커니즘 생성 승인해 줘', budget_usd=1)
        self.assertEqual(error.exception.code, 'BUDGET_EXCEEDED')
        self.assertEqual((self.path / 'project.json').read_bytes(), before)

    def test_approve_requires_evidence_review_and_budget(self):
        with self.assertRaises(StudioError):
            approve(self.path, 'city', 'ok')
        with self.assertRaises(StudioError) as error:
            approve(self.path, 'city', '도시 인트로 생성 승인')                      # no review sheet shown
        self.assertEqual(error.exception.code, 'ROUTE_REVIEW_MISSING')
        with self.assertRaises(StudioError) as error:
            approve_reviewed(self.path, 'city', 'User (10-05): approved city intro')   # an agent's wrapper, not the user's words
        self.assertEqual(error.exception.code, 'INPUT_INVALID')
        with self.assertRaises(StudioError) as error:
            approve_reviewed(self.path, 'city', '도시 인트로 생성 승인')
        self.assertEqual(error.exception.code, 'BUDGET_EXCEEDED')
        result = approve_reviewed(self.path, 'city', '도시 인트로 생성 승인, 5달러까지', budget_usd=5)
        self.assertEqual((result['route']['status'], result['route']['decided_by']), ('approved', 'user'))
        self.assertEqual(result['route']['approval_binding']['model'], 'veo-3.1')
        self.assertTrue(list((self.path / 'reviews').glob('route_*.json')))
        review = read_json(next((self.path / 'reviews').glob('gen_*/review.json')))
        self.assertEqual(review['approvals'][0]['user_words'], '도시 인트로 생성 승인, 5달러까지')
        with self.assertRaises(StudioError) as error:
            approve_reviewed(self.path, 'mech', '메커니즘도 승인해 줘')
        self.assertEqual(error.exception.code, 'BUDGET_EXCEEDED')  # 2.4 + 6.6 > 5

    def test_generate_gate_binds_approval_to_the_reviewed_request(self):
        shot = load_shot(self.path, 'city')
        with self.assertRaises(StudioError) as error:
            assert_route(shot, 'generate', self.path)
        self.assertEqual(error.exception.code, 'ROUTE_APPROVAL_REQUIRED')
        (self.path / 'prompts/city.txt').write_text('Aerial dusk over a river city. No text, no letters.')
        approve_reviewed(self.path, 'city', '도시 인트로 생성 승인, 5달러까지', budget_usd=5)
        self.assertEqual(assert_route(load_shot(self.path, 'city'), 'generate', self.path)['mode'], 'generative')
        (self.path / 'prompts/city.txt').write_text('Aerial dusk over the "Seoul" river.')
        with self.assertRaises(StudioError) as error:
            assert_route(load_shot(self.path, 'city'), 'generate', self.path)
        self.assertEqual(error.exception.code, 'GENERATION_PROMPT_INVALID')
        (self.path / 'prompts/city.txt').write_text('Aerial dusk over a river city at night. No text, no letters.')
        with self.assertRaises(StudioError) as error:                         # valid, but not what the user saw
            assert_route(load_shot(self.path, 'city'), 'generate', self.path)
        self.assertEqual(error.exception.code, 'ROUTE_APPROVAL_STALE')
        approve_reviewed(self.path, 'city', '밤 버전으로 다시 승인')
        self.assertEqual(assert_route(load_shot(self.path, 'city'), 'generate', self.path)['mode'], 'generative')

    def test_hybrid_rejects_first_frame_models_and_needs_previs(self):
        shot = deepcopy(load_shot(self.path, 'mech'))
        shot['route'].update({'status': 'approved', 'decided_by': 'user', 'approved_at': 'now', 'approval_evidence': 'User: approve hybrid'})
        shot['route']['generative'].update({'model': 'veo-3.1', 'operation': 'image_to_video'})
        with self.assertRaises(StudioError) as error:
            assert_route(shot, 'generate')
        self.assertIn(error.exception.code, ('ROUTE_MODEL_MISMATCH', 'INPUT_INVALID'))
        shot['route']['generative'].update({'model': 'unknown-model', 'operation': 'video_to_video'})
        with self.assertRaises(StudioError) as error:
            assert_route(shot, 'generate')
        self.assertEqual(error.exception.code, 'ROUTE_MODEL_UNKNOWN')

    def test_schema_conditions(self):
        base = load_shot(self.path, 'city')
        cases = []
        s = deepcopy(base); del s['route']['generative']; cases.append(s)
        s = deepcopy(base); s['route']['mode'] = 'blender'; cases.append(s)
        s = deepcopy(base); s['route']['status'] = 'approved'; cases.append(s)
        s = deepcopy(base); s['route']['generative']['text_in_frame'] = True; cases.append(s)
        s = deepcopy(load_shot(self.path, 'mech')); s['route']['generative']['operation'] = 'image_to_video'; cases.append(s)
        for case in cases:
            with self.assertRaises(StudioError) as error:
                validate_shot(case)
            self.assertEqual(error.exception.code, 'INPUT_INVALID')

    def test_generative_content_and_budget_conflicts(self):
        s = deepcopy(load_shot(self.path, 'city')); s['labels'] = [{'label_id': 'x', 'anchor': 'a', 'start_frame': 0, 'end_frame': 1, 'slot': 'top', 'text': 'x'}]
        with self.assertRaises(StudioError) as error:
            validate_shot(s)
        self.assertIn(error.exception.code, ('ROUTE_CONTENT_CONFLICT', 'INPUT_INVALID'))
        s = deepcopy(load_shot(self.path, 'city')); s['route']['generative']['budget_usd'] = 1
        with self.assertRaises(StudioError) as error:
            validate_shot(s)
        self.assertEqual(error.exception.code, 'ROUTE_BUDGET_CONFLICT')

    def test_build_rejects_generative_shot(self):
        script = self.path / 'a.py'; script.write_text('pass\n')
        with self.assertRaises(StudioError) as error:
            build_shot(self.path, 'city', script)
        self.assertEqual(error.exception.code, 'ROUTE_MISMATCH')

    def test_route_revision_resets_approval(self):
        approve_reviewed(self.path, 'city', '도시 인트로 생성 승인, 5달러까지', budget_usd=5)
        current = load_shot(self.path, 'city')
        change = self.path / 'change.json'
        approved = {**current['route'], 'status': 'approved'}
        write_json(change, {'base_revision': current['revision'], 'scope': 'route', 'targets': [], 'change': {'route': approved}, 'preserve': []})
        with self.assertRaises(StudioError):
            revise_shot(self.path, 'city', change)
        proposal = {**current['route'], 'status': 'proposed', 'decided_by': 'user'}
        write_json(change, {'base_revision': current['revision'], 'scope': 'route', 'targets': [], 'change': {'route': proposal}, 'preserve': []})
        revise_shot(self.path, 'city', change)
        route = load_shot(self.path, 'city')['route']
        self.assertEqual((route['status'], route['approval_evidence'], route['approval_binding']), ('proposed', None, None))

    def test_lint_and_status(self):
        report = lint(self.path)
        self.assertIn('E1_prompt_missing', {e['code'] for e in report['errors']})
        self.assertIn('E5_over_budget', {e['code'] for e in report['errors']})
        ops = status_project(self.path)['next_operations']
        self.assertIn(('route.approve', 'city'), {(o['operation'], o.get('shot_id')) for o in ops})

    def test_cli_route_check_exit_codes(self):
        run = lambda *args: subprocess.run([sys.executable, '-m', 'studio', 'route', *args], cwd=ROOT, capture_output=True, text=True)
        denied = run('check', '--project', str(self.path), '--shot', 'city', '--operation', 'build')
        self.assertEqual(denied.returncode, 2); self.assertEqual(json.loads(denied.stdout)['error']['code'], 'ROUTE_MISMATCH')
        self.assertEqual(run('check', '--project', str(self.path), '--shot', 'city', '--operation', 'generate').returncode, 2)
        self.assertEqual(run('check', '--project', str(self.path), '--shot', 'packshot', '--operation', 'render').returncode, 0)
        self.assertEqual(run('check', '--project', str(self.path), '--shot', 'packshot', '--operation', 'generate').returncode, 2)

    def test_lint_prompt_rules(self):
        self.assertEqual(lint_prompt(GOOD_PROMPT, 'hybrid'), [])
        self.assertTrue(lint_prompt('Steel. No text. Slow dolly in.', 'hybrid'))
        base = GOOD_PROMPT + ' '
        for motion in ('The camera pans left.', 'Pan across the engine.', 'Zoom in on the valve.', 'It orbits around the block.', 'Push in slowly.'):
            self.assertTrue(any('camera motion' in p for p in lint_prompt(base + motion, 'hybrid')), motion)
        for part in ('the oil pan under the block', 'planet orbit radius', 'a zoom lens housing'):   # nouns, not camera moves
            self.assertFalse(any('camera motion' in p for p in lint_prompt(base + part, 'hybrid')), part)


if __name__ == '__main__':
    unittest.main()
