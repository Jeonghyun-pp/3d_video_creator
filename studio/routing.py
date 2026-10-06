"""Per-shot route: blender | generative | hybrid, decided from declared features, never from goal text.

Principle (handoff §7, 2026-10-04 hybrid correction):
- Anything that must be exact (geometry, motion, identity across shots, anchored labels) is
  structural and stays in Blender.
- Generation models own only what Blender cannot make well: unstructured phenomena (fluid,
  fire, weather), real-place atmosphere, or realism beyond the available assets.
- Structural + beyond-asset look = hybrid: Blender renders the complete motion pass, a
  video-input model restyles the surface only. First-frame-only models invent motion and are
  never hybrid.
Paid generation needs a recorded user approval (status approved, decided_by user, evidence) and
a cost estimate inside the project budget.
"""
from __future__ import annotations

import re
import statistics
from copy import deepcopy
from pathlib import Path

from .common import StudioError, file_hash, lock, now, read_json, stable_hash, write_json
from .project import load_project, load_shot, project_dir, route_of, shot_path, validate_schema, validate_shot

FEATURES = ('exact_geometry', 'exact_motion', 'cross_shot_identity', 'anchored_text', 'simple_hard_surface',
            'unstructured_phenomena', 'real_place_atmosphere', 'photoreal_beyond_assets', 'ai_label_unacceptable')
FEATURE_DEFINITIONS = {
    'exact_geometry': 'part count, dimensions or connections must be correct',
    'exact_motion': 'mechanism or camera motion must follow a defined path/timing',
    'cross_shot_identity': 'the same object must look identical in another shot',
    'anchored_text': 'labels or numbers must sit on 3D positions',
    'simple_hard_surface': 'packshot-like simple solid (revolve, box, glass, can)',
    'unstructured_phenomena': 'fluid, fire, smoke, weather, crowds',
    'real_place_atmosphere': 'mood of a real place, aerial city, historical scene',
    'photoreal_beyond_assets': 'needs realism the available assets/materials cannot reach',
    'ai_label_unacceptable': 'client/education use where an AI-generated label is not allowed'}
STRUCTURAL = {'exact_geometry', 'exact_motion', 'cross_shot_identity', 'anchored_text'}
GENERATIVE_ONLY = {'unstructured_phenomena', 'real_place_atmosphere', 'photoreal_beyond_assets'}
# Model registry is an allow-list: operation -> fal endpoint. Prices live in one place, fal_client.PRICING (per endpoint,
# with the provider's billing rule); estimates here and the ledger reservation both come from it.
MODELS = {
    'veo-3.1': {'operations': {'text_to_video': 'fal-ai/veo3.1', 'image_to_video': 'fal-ai/veo3.1/image-to-video'}},
    'seedance-2.5': {'operations': {'video_to_video': 'bytedance/seedance-2.5/reference-to-video'}},
    'wan-2.2-vace': {'operations': {'video_to_video': 'fal-ai/wan-22-vace-fun-a14b/depth'}},
    'luma-ray-modify': {'operations': {'video_to_video': 'fal-ai/luma-dream-machine/ray-2/modify'}},
    'kling-o1-edit': {'operations': {'video_to_video': 'fal-ai/kling-video/o1/video-to-video/edit'}},
}


def endpoint_for(spec):
    return MODELS.get(spec['model'], {}).get('operations', {}).get(spec['operation'])


ASSUMED_SECONDS_PER_FRAME = {'GPU': 1.1, 'CPU': 12.0}
NO_TEXT = re.compile(r'no (on-?screen )?(text|letters|captions|words|typography)|without (any )?(text|letters)|텍스트 없|글자 없|문자 없', re.I)
QUOTED = re.compile(r'["“”「」『』]')
PREVIS_FIRST = re.compile(r'follow the input video|input video.{0,40}(camera|timing|position)|입력 영상', re.I)


def infer_features(shot, all_shots=()):
    """Features implied by shot structure plus those declared on the route."""
    features = set(route_of(shot).get('features', []))
    if shot.get('actions'):
        features.add('exact_motion')
    if shot.get('labels'):
        features.add('anchored_text')
    mine = {a.get('asset_id') for a in shot.get('asset_instances', []) if a.get('asset_id')}
    for other in all_shots:
        if other['shot_id'] != shot['shot_id'] and mine & {a.get('asset_id') for a in other.get('asset_instances', [])}:
            features.add('cross_shot_identity')
    return sorted(features)


def classify(features, policy=None):
    """First matching rule wins. Returns (mode, rule_id, reason, confidence)."""
    f = set(features)
    policy = policy or {}
    structural = f & STRUCTURAL
    beyond = f & {'unstructured_phenomena', 'photoreal_beyond_assets'}
    if 'ai_label_unacceptable' in f or policy.get('allow_generative') is False:
        return 'blender', 'R1_no_generation', 'AI label not acceptable or generation disabled by policy', 'high'
    if structural and beyond:
        return 'hybrid', 'R2_structure_plus_look', f'exact {sorted(structural)} with {sorted(beyond)}: Blender motion pass, video-input restyle', 'medium'
    if structural:
        return 'blender', 'R3_structural', f'exact {sorted(structural)} must come from Blender', 'high'
    if 'simple_hard_surface' in f:
        return 'blender', 'R4_simple_hard_surface', 'simple solid packshot reads photoreal in Blender', 'high'
    if f & GENERATIVE_ONLY:
        return 'generative', 'R5_generative_look', f'{sorted(f & GENERATIVE_ONLY)} without structural requirements', 'medium'
    return 'blender', 'R0_default_blender', 'no features declared; default to Blender and ask the user', 'low'


def _frame_seconds(path, device):
    times = []
    for render in sorted(Path(path).glob('shots/*/renders/*/render.json')):
        data = read_json(render)
        frame_times = render.parent / 'frame_times.json'
        if data.get('device') == device and frame_times.is_file():
            raw = read_json(frame_times)
            values = raw.values() if isinstance(raw, dict) else raw
            times += [v if isinstance(v, (int, float)) else v.get('seconds', 0) for v in values]
    return (statistics.median(times), 'measured') if times else (ASSUMED_SECONDS_PER_FRAME[device], 'assumed')


def estimate(shot, route, path=None, device='GPU'):
    if route['mode'] == 'blender':
        seconds, source = _frame_seconds(path, device) if path else (ASSUMED_SECONDS_PER_FRAME[device], 'assumed')
        return {'est_cost_usd': 0.0, 'est_minutes': round(shot['duration_frames'] * seconds / 60, 2), 'estimate_source': source}
    spec = route['generative']
    if spec.get('usd_per_second') is not None:   # a quote recorded on the shot overrides the table
        cost = round(spec['duration_seconds'] * spec['usd_per_second'] * spec['max_attempts'], 2)
        return {'est_cost_usd': cost, 'est_minutes': None, 'estimate_source': 'quoted'}
    endpoint = endpoint_for(spec)
    if endpoint is None:
        return {'est_cost_usd': None, 'est_minutes': None, 'estimate_source': 'unknown'}
    from .generative.clip import _billed_output_seconds
    from .generative.fal_client import estimate_usd
    from .generative.review import pad_seconds
    # video inputs are the full-length motion pass (hybrid contract), held for any padding the model needs
    input_seconds = spec['duration_seconds'] + pad_seconds(spec) if spec['operation'] == 'video_to_video' else 0.0
    try:
        per_call = estimate_usd(endpoint, _billed_output_seconds(spec), input_seconds)
    except StudioError:
        return {'est_cost_usd': None, 'est_minutes': None, 'estimate_source': 'unknown'}
    return {'est_cost_usd': round(per_call * spec['max_attempts'], 2), 'est_minutes': None, 'estimate_source': 'quoted'}


def propose_route(shot, policy=None, all_shots=(), path=None, generative=None):
    features = infer_features(shot, all_shots)
    mode, rule, reason, confidence = classify(features, policy)
    route = {'mode': mode, 'features': features, 'rule_id': rule, 'reason': reason, 'confidence': confidence,
             'decided_by': 'agent', 'status': 'proposed', 'approved_at': None, 'approval_evidence': None}
    previous = route_of(shot).get('generative')
    if mode != 'blender' and route_of(shot).get('role'):  # the role is a person's or agent's call, never re-derived
        route['role'] = route_of(shot)['role']
    if mode != 'blender':
        if not (generative or previous):
            route.update({'est_cost_usd': None, 'est_minutes': None, 'estimate_source': 'unknown'})
            return route  # cannot be a valid stored route until a generative spec exists; reported, not applied
        route['generative'] = deepcopy(generative or previous)
    route.update(estimate(shot, route, path))
    return route


def lint_prompt(text, mode):
    problems = []
    if not NO_TEXT.search(text):
        problems.append('missing a "no text/letters/captions" clause; generated text is garbled')
    if QUOTED.search(text):
        problems.append('quoted strings invite the model to render text; describe signage without quotes')
    if mode == 'hybrid' and not PREVIS_FIRST.search(text):
        problems.append('hybrid prompt must say to follow the input video camera, timing and positions exactly')
    if mode == 'hybrid' and re.search(r'\b(dolly|orbit|pan|zoom|tracking shot|camera moves?)\b', text, re.I):
        problems.append('hybrid prompt must not describe camera motion; the previs owns the camera')
    return problems


def _policy(project):
    return project.get('route_policy', {'allow_generative': True, 'budget_usd': 0, 'turnaround_required': True})


def _approved_total(path, project, exclude=None):
    total = 0.0
    for entry in project['shots']:
        if entry['shot_id'] == exclude:
            continue
        route = route_of(load_shot(path, entry['shot_id']))
        if route.get('status') == 'approved' and route.get('est_cost_usd'):
            total += route['est_cost_usd']
    return total


def assert_route(shot, operation, path=None):
    """Gate for build/render/generate. Raises StudioError with a ROUTE_* code."""
    route = route_of(shot)
    mode = route['mode']
    allowed = {'build': {'blender', 'hybrid'}, 'render': {'blender', 'hybrid'}, 'generate': {'generative', 'hybrid'}}[operation]
    if mode not in allowed:
        raise StudioError('ROUTE_MISMATCH', f"Shot {shot['shot_id']} is routed {mode}; {operation} is not allowed",
                          recovery='Change the route with a route-scope revision, or use the matching command')
    if path is not None and operation in ('build', 'generate'):   # the decision ladder (projects that use it)
        from .decisions import require
        require(path, operation)
    if operation in ('render', 'generate'):   # what fills the shot is the user's call, approved before pixels are spent
        from .fill import require_approved
        require_approved(shot, path)
    if operation != 'generate':
        return route
    if route.get('status') != 'approved' or route.get('decided_by') != 'user' or not route.get('approval_evidence'):
        raise StudioError('ROUTE_APPROVAL_REQUIRED', f"Shot {shot['shot_id']}: paid generation needs a recorded user approval",
                          recovery='Ask the user, then run route approve with their words as --evidence')
    spec = route['generative']
    model = MODELS.get(spec['model'])
    if model is None:
        raise StudioError('ROUTE_MODEL_UNKNOWN', f"Model {spec['model']} is not in the routing registry",
                          recovery='Add it to studio/routing.py MODELS (operation -> endpoint) and its price to fal_client.PRICING')
    if spec['operation'] not in model['operations'] or (mode == 'hybrid' and spec['operation'] != 'video_to_video'):
        raise StudioError('ROUTE_MODEL_MISMATCH', f"{spec['model']} {spec['operation']} cannot serve a {mode} shot; hybrid needs a video-input model",
                          recovery='Use a video_to_video model (seedance-2.5, wan-2.2-vace, luma-ray-modify, kling-o1-edit)')
    if path is not None:
        from .fidelity import require_fidelity
        require_fidelity(path, shot, purpose='paid generation')  # never pay to restyle a wrong shape
        prompt = Path(path) / spec['prompt_ref']
        if not prompt.is_file():
            raise StudioError('GENERATION_PROMPT_INVALID', f"Prompt file missing: {spec['prompt_ref']}")
        problems = lint_prompt(prompt.read_text(encoding='utf-8'), mode)
        if problems:
            raise StudioError('GENERATION_PROMPT_INVALID', '; '.join(problems))
        for item in spec.get('inputs', []):
            source = Path(path) / item['path']
            if not source.is_file() or (item.get('sha256') and file_hash(source) != item['sha256']):
                raise StudioError('ROUTE_INPUT_MISSING', f"Generation input missing or changed: {item['path']}")
        problems = reference_problems(path, spec)
        if problems:
            raise StudioError('REFERENCE_NOT_CLEARED', '; '.join(problems),
                              recovery='Use a still rendered from this project (generate still) or a cleared library asset')
        if mode == 'hybrid' and not any(i['kind'] == 'previs' for i in spec.get('inputs', [])):
            raise StudioError('ROUTE_INPUT_MISSING', 'Hybrid shots need the full-length Blender motion pass as a previs input')
        project = load_project(path)
        budget = _policy(project)['budget_usd']
        if route.get('est_cost_usd') is None:
            raise StudioError('ROUTE_ESTIMATE_MISSING', 'No cost estimate; record the price in fal_client.PRICING (or usd_per_second on the shot)')
        if _approved_total(path, project, shot['shot_id']) + route['est_cost_usd'] > budget:
            raise StudioError('BUDGET_EXCEEDED', f'Approved generation would exceed the project budget ${budget}')
        # the approval covers exactly the request on the sheet the user saw; any later change needs a new sheet
        from .generative.review import request_fingerprint
        binding = route.get('approval_binding')
        if not binding:
            raise StudioError('ROUTE_APPROVAL_STALE', f"Shot {shot['shot_id']}: approved without a generation review sheet",
                              recovery='Run generate review, show the sheet, then route approve --review <id> --user-words "<their words>"')
        if binding['fingerprint'] != request_fingerprint(path, shot)['fingerprint']:
            raise StudioError('ROUTE_APPROVAL_STALE', f"Shot {shot['shot_id']}: prompt, inputs, model or arguments changed after approval",
                              recovery='Run generate review again, show the new sheet and get a new approval')
    return route


def _write_shot(path, shot_id, updated, expected_revision):
    with lock(path / '.project.lock', blocking=False):
        if load_shot(path, shot_id)['revision'] != expected_revision:
            raise StudioError('REVISION_CONFLICT', 'Shot changed while the route was being updated')
        updated['revision'] += 1
        validate_shot(updated)
        write_json(shot_path(path, shot_id), updated)


ROUTE_DECISION_KEYS = ('mode', 'features', 'rule_id', 'generative', 'role')


def _decision(route):
    """What a route decides. Estimates (minutes measured from renders, cost) change as work accumulates; a plan that
    only re-estimates must not rewrite shots (that bumps revisions and stales every edit candidate)."""
    return stable_hash({k: route.get(k) for k in ROUTE_DECISION_KEYS})


def plan(path, apply=False):
    path = project_dir(path)
    project = load_project(path)
    policy = _policy(project)
    shots = [load_shot(path, e['shot_id']) for e in project['shots']]
    rows, conflicts = [], []
    for shot in shots:
        current = route_of(shot)
        proposed = propose_route(shot, policy, shots, path)
        locked = current.get('decided_by') == 'user' or current.get('status') == 'approved'
        if locked and current['mode'] != proposed['mode']:
            conflicts.append({'shot_id': shot['shot_id'], 'current': current['mode'], 'proposed': proposed['mode'], 'rule_id': proposed['rule_id']})
        chosen = current if locked else proposed
        from .generative.policy import role_of
        rows.append({'shot_id': shot['shot_id'], 'mode': chosen['mode'], 'role': role_of(chosen) if chosen['mode'] != 'blender' else None,
                     'rule_id': chosen.get('rule_id'), 'reason': chosen.get('reason'),
                     'features': proposed['features'], 'confidence': chosen.get('confidence'), 'status': chosen.get('status'),
                     'est_cost_usd': chosen.get('est_cost_usd'), 'est_minutes': chosen.get('est_minutes'),
                     'estimate_source': chosen.get('estimate_source'), 'locked': locked,
                     'needs_generative_spec': chosen['mode'] != 'blender' and 'generative' not in chosen})
        if apply and not locked and not rows[-1]['needs_generative_spec'] and _decision(current) != _decision(proposed):
            updated = deepcopy(shot); updated['route'] = proposed
            _write_shot(path, shot['shot_id'], updated, shot['revision'])
    total = round(sum(r['est_cost_usd'] or 0 for r in rows), 2)
    result = {'schema_version': 1, 'project_id': project['project_id'], 'created_at': now(), 'policy': policy, 'shots': rows,
              'total_est_cost_usd': total, 'over_budget': total > policy['budget_usd'], 'conflicts': conflicts, 'applied': apply}
    write_json(path / 'route_plan.json', result)
    table = ['| shot | route | role | rule | est. $ | est. min | status | reason |', '|---|---|---|---|---|---|---|---|']
    table += [f"| {r['shot_id']} | {r['mode']} | {r['role'] or '-'} | {r['rule_id']} | {r['est_cost_usd']} | {r['est_minutes']} | {r['status']} | {r['reason']} |" for r in rows]
    (path / 'route_plan.md').write_text('\n'.join(table) + f"\n\nTotal ${total} / budget ${policy['budget_usd']}\n", encoding='utf-8')
    return {**result, 'artifacts': [str(path / 'route_plan.json'), str(path / 'route_plan.md')]}


def approve(path, shot_id, evidence, budget_usd=None, review_id=None, agent_note=None):
    """Record the user's approval in their own words. Paid shots must name the generation review sheet the user saw
    (generate review); the approval is bound to that exact request (review.binding_for)."""
    from .generative.review import binding_for, check_user_words, record_approval
    path = project_dir(path)
    if shot_id == 'all':
        if not review_id:
            raise StudioError('INPUT_INVALID', '--shot all needs --review')
        shots = list(read_json(path / 'reviews' / f'gen_{review_id}' / 'review.json')['shots'])
        results = [approve(path, sid, evidence, budget_usd if i == 0 else None, review_id, agent_note) for i, sid in enumerate(shots)]
        return {'status': 'approved', 'shots': [r['shot_id'] for r in results], 'review_id': review_id}
    if not isinstance(evidence, str) or len(evidence.strip()) < 8:
        raise StudioError('INPUT_INVALID', "Approval evidence must quote the user's decision")
    shot = load_shot(path, shot_id)
    if 'route' not in shot:
        raise StudioError('INPUT_INVALID', 'Run route plan --apply (or a route revision) before approving')
    binding = None
    if shot['route']['mode'] != 'blender':
        evidence = check_user_words(evidence)
        if not review_id:
            raise StudioError('ROUTE_REVIEW_MISSING', f'{shot_id}: paid shots are approved against a generation review sheet',
                              recovery='Run generate review, show the sheet, then route approve --review <id>')
        binding = binding_for(path, shot, review_id, agent_note)
    project = load_project(path)
    if budget_usd is not None:   # checked in memory first: a refused approval leaves project.json untouched
        project = {**project, 'route_policy': {**_policy(project), 'budget_usd': float(budget_usd)}}
    route = deepcopy(shot['route'])
    if route['mode'] != 'blender':
        if route.get('est_cost_usd') is None:
            raise StudioError('ROUTE_ESTIMATE_MISSING', 'Cannot approve a paid shot without a cost estimate')
        if _approved_total(path, project, shot_id) + route['est_cost_usd'] > _policy(project)['budget_usd']:
            raise StudioError('BUDGET_EXCEEDED', f"Approving ${route['est_cost_usd']} exceeds budget ${_policy(project)['budget_usd']}",
                              recovery='Ask the user for a budget and pass --budget-usd')
    if budget_usd is not None:
        with lock(path / '.project.lock', blocking=False):
            stored = load_project(path); stored['route_policy'] = project['route_policy']; stored['revision'] += 1
            validate_schema(stored, 'project'); write_json(path / 'project.json', stored)
    route.update({'status': 'approved', 'decided_by': 'user', 'approved_at': now(), 'approval_evidence': evidence.strip()})
    if binding:
        route['approval_binding'] = binding
    updated = deepcopy(shot); updated['route'] = route
    _write_shot(path, shot_id, updated, shot['revision'])
    if binding:
        record_approval(path, review_id, shot_id, evidence.strip())
    record = {'shot_id': shot_id, 'route': route, 'shot_revision': updated['revision']}
    review = path / 'reviews' / f"route_{stable_hash(record)[:16]}.json"
    write_json(review, record)
    return {'status': 'approved', 'shot_id': shot_id, 'route': route, 'artifacts': [str(review), str(shot_path(path, shot_id))]}


# Provisional (uncalibrated) floor for the structure a hybrid control hands the model, from the 2026-10-05
# Samsung station controls: shadowless clay of a box-blockout hall measured coverage 0.31, 13.5 trackable tiles;
# the same scenes with shadowed clay (unusable) measured 0.02 / 4. Warning only until a paid A/B calibrates it.
CONTROL_FLOOR = {'coverage': 0.25, 'distinct_tiles': 10}


def _control_warnings(path, shot):
    from .generative.control import latest_control
    current = latest_control(path, shot)
    if not current:
        return [{'code': 'W5_control_missing', 'shot_id': shot['shot_id'],
                 'message': 'no control pass for this scene version: run generate control and read control.json richness before paying'}]
    rich = current.get('richness')
    if not rich:
        return []
    low = {k: rich[k] for k, floor in CONTROL_FLOOR.items() if rich.get(k) is not None and rich[k] < floor}
    if low:
        return [{'code': 'W5_control_coarse', 'shot_id': shot['shot_id'],
                 'message': f'control structure below the provisional floor {CONTROL_FLOOR}: {low}; add structure (building-element exemplars) before paying'}]
    return []


REFERENCE_MODELS = ('seedance-2.5', 'kling-o1-edit', 'luma-ray-modify')   # adapters that send reference_image inputs
UNCLEARED_PARTS = ('reference', 'references', 'internal')                # path parts that mark third-party study material


def reference_problems(path, spec):
    """Look reference and first-frame images leave the studio: only our own renders, generated frames or cleared
    assets may be sent. Third-party reference material (reference reels, internal studies) never is."""
    path = Path(path)
    style = read_json(path / 'style.json') if (path / 'style.json').is_file() else {}
    blocked = {file_hash(path / r) for r in style.get('reference_paths', []) if (path / r).is_file()}
    problems = []
    for item in spec.get('inputs', []):
        if item['kind'] not in ('reference_image', 'first_frame'):
            continue
        source = path / item['path']
        parts = {part.lower() for part in Path(item['path']).parts}
        own = any(part in parts for part in ('renders', 'generated', 'control', 'stills')) and source.resolve().is_relative_to(path.resolve())
        if any(any(tag in part for tag in UNCLEARED_PARTS) for part in parts) or (source.is_file() and file_hash(source) in blocked):
            problems.append(f"{item['path']}: third-party reference material is never sent to a generation provider")
        elif not own and not _cleared_asset(source):
            problems.append(f"{item['path']}: not a render/generated frame of this project nor a cleared library asset")
    return problems


def _cleared_asset(source):
    from .common import REPO
    try:
        relative = source.resolve().relative_to((REPO / 'library').resolve())
    except ValueError:
        return False
    for parent in relative.parents:
        manifest = REPO / 'library' / parent / 'asset.json'
        if manifest.is_file():
            data = read_json(manifest)
            return (data.get('source') or {}).get('use_status', data.get('use_status')) in ('cleared', 'studio_cleared')
    return False


def lint(path):
    path = project_dir(path)
    project = load_project(path)
    policy = _policy(project)
    errors, warnings = [], []
    total = 0.0
    for entry in project['shots']:
        shot = load_shot(path, entry['shot_id'])
        route = route_of(shot)
        sid = shot['shot_id']
        if 'route' not in shot:
            warnings.append({'code': 'W1_no_route', 'shot_id': sid, 'message': 'legacy shot without route; treated as blender'})
            continue
        if route['mode'] != 'blender':
            spec = route['generative']
            prompt = path / spec['prompt_ref']
            if not prompt.is_file():
                errors.append({'code': 'E1_prompt_missing', 'shot_id': sid})
            else:
                text = prompt.read_text(encoding='utf-8')
                errors += [{'code': 'E2_prompt_invalid', 'shot_id': sid, 'message': p} for p in lint_prompt(text, route['mode'])]
                from .generative.clip import PROMPT_WORD_LIMIT
                if len(text.split()) > PROMPT_WORD_LIMIT:
                    warnings.append({'code': 'W7_prompt_long', 'shot_id': sid,
                                     'message': f'{len(text.split())} words > {PROMPT_WORD_LIMIT}: instructions get diluted; keep items short'})
            items = spec.get('prompt_spec') or {}
            clash = sorted({a.strip().lower() for a in items.get('add', [])} & {k.strip().lower() for k in items.get('keep', [])})
            if clash:
                errors.append({'code': 'E6_add_conflicts_keep', 'shot_id': sid, 'message': f'the same item is both added and kept: {clash}'})
            if spec['model'] in REFERENCE_MODELS and not any(i['kind'] == 'reference_image' for i in spec.get('inputs', [])):
                warnings.append({'code': 'W8_look_refs_missing', 'shot_id': sid,
                                 'message': f"{spec['model']} takes look reference images; with none the clip copies the grey clay (A/B s02 Seedance)"})
            for problem in reference_problems(path, spec):
                errors.append({'code': 'E7_reference_not_cleared', 'shot_id': sid, 'message': problem})
            if not policy['allow_generative']:
                errors.append({'code': 'E3_policy_forbids_generation', 'shot_id': sid})
            if abs(spec['duration_seconds'] * project['output']['fps'] - shot['duration_frames']) > project['output']['fps'] * 0.5:
                warnings.append({'code': 'W2_duration_mismatch', 'shot_id': sid, 'message': 'generated length differs from shot length by >0.5 s'})
            if route.get('est_cost_usd') is None:
                errors.append({'code': 'E4_estimate_missing', 'shot_id': sid})
            if route['status'] != 'approved':
                warnings.append({'code': 'W3_awaiting_approval', 'shot_id': sid})
            if not route.get('role'):
                from .generative.policy import role_of
                warnings.append({'code': 'W6_role_missing', 'shot_id': sid,
                                 'message': f"no route.role; treated as {role_of(route)} (explain = structure-checked + labels, mood = captions only)"})
            from .generative.policy import role_of
            if route['mode'] == 'hybrid' and role_of(route) == 'explain':   # control structure matters where it is judged
                warnings += _control_warnings(path, shot)
                overlays = [k for k in ('labels', 'graphics') if shot.get(k)]
                if overlays:
                    warnings.append({'code': 'W11_explain_overlays_need_structure', 'shot_id': sid,
                                     'message': f'{overlays} ride only on a take that passed the structure gate (its 2D anchors); '
                                                'a user-picked take cannot carry them'})
            from .generative.inputs import input_warnings
            warnings += input_warnings(path, shot)
            total += route.get('est_cost_usd') or 0
        if route.get('confidence') == 'low':
            warnings.append({'code': 'W4_low_confidence', 'shot_id': sid, 'message': route.get('reason')})
    if total > policy['budget_usd']:
        errors.append({'code': 'E5_over_budget', 'message': f'${round(total, 2)} > ${policy["budget_usd"]}'})
    return {'status': 'ok' if not errors else 'errors', 'errors': errors, 'warnings': warnings, 'total_est_cost_usd': round(total, 2)}


def check(path, shot_id, operation):
    path = project_dir(path)
    route = assert_route(load_shot(path, shot_id), operation, path)
    return {'status': 'allowed', 'shot_id': shot_id, 'operation': operation, 'route_mode': route['mode']}


def register_commands(subparsers):
    parser = subparsers.add_parser('route', help='Per-shot blender/generative/hybrid routing, estimates and recorded approvals')
    commands = parser.add_subparsers(dest='route_command', required=True)
    p = commands.add_parser('plan'); p.add_argument('--project', required=True); p.add_argument('--apply', action='store_true')
    p.set_defaults(handler=lambda a: plan(a.project, a.apply))
    p = commands.add_parser('approve'); p.add_argument('--project', required=True)
    p.add_argument('--shot', required=True, help="shot id, or 'all' for every shot on --review")
    words = p.add_mutually_exclusive_group(required=True)
    words.add_argument('--user-words', dest='evidence', help="The user's own words approving this sheet and cost, verbatim")
    words.add_argument('--evidence', dest='evidence', help='alias of --user-words')
    p.add_argument('--review', help='the generate review sheet the user saw (required for generative/hybrid shots)')
    p.add_argument('--agent-note', help='your own note (kept apart from the user words)')
    p.add_argument('--budget-usd', type=float)
    p.set_defaults(handler=lambda a: approve(a.project, a.shot, a.evidence, a.budget_usd, a.review, a.agent_note))
    p = commands.add_parser('check'); p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.add_argument('--operation', choices=['build', 'render', 'generate'], required=True)
    p.set_defaults(handler=lambda a: check(a.project, a.shot, a.operation))
    p = commands.add_parser('lint'); p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: lint(a.project))
