from __future__ import annotations

import argparse
import os
from copy import deepcopy
from pathlib import Path
import shutil
import uuid

from jsonschema import Draft202012Validator
from .common import DEFAULT_FONT, REPO, StudioError, check_id, file_hash, font_file, lock, now, read_json, safe_path, stable_hash, write_json


def load_schema(name):
    return read_json(REPO / 'schemas' / 'studio-v1' / f'{name}.schema.json')


def validate_schema(data, name):
    schema = load_schema(name)
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: str(list(e.path)))
    if errors:
        raise StudioError('INPUT_INVALID', '; '.join(f'{list(e.path)}: {e.message}' for e in errors[:8]))


# Shot fields a revision may change without Blender (metadata scopes), and the user's decisions recorded on a shot.
# Selecting or reverting to another scene version keeps these: they are not part of the scene.
METADATA_SCOPES = {'labels': {'labels', 'titles'}, 'audio': {'narration'}, 'edit': {'labels', 'narration', 'titles'}, 'route': {'route'}}
METADATA_FIELDS = frozenset().union(*METADATA_SCOPES.values()) | {'fill_brief'}


def project_files(data, name):
    """Project-relative file references in a document, found by the schema's `x-project-file` marks (not by key names):
    [(pointer, value, kind, nullable)], kind 'input' (must exist to proceed) or 'derived' (a cache the tools rebuild)."""
    schema = read_json(REPO / 'schemas' / 'studio-v1' / f'{name}.schema.json')
    found = []

    def deref(node):
        seen = 0
        while isinstance(node, dict) and '$ref' in node and seen < 20:
            target = schema
            for part in node['$ref'].lstrip('#/').split('/'):
                target = target[part]
            node, seen = target, seen + 1
        return node

    def branches(node):
        node = deref(node)
        yield node
        for key in ('allOf', 'anyOf', 'oneOf'):
            for sub in node.get(key, []) if isinstance(node, dict) else []:
                yield from branches(sub)
        for key in ('then', 'else'):
            if isinstance(node, dict) and isinstance(node.get(key), dict):
                yield from branches(node[key])

    def walk(value, node, pointer):
        for branch in branches(node):
            if not isinstance(branch, dict):
                continue
            if 'x-project-file' in branch and isinstance(value, str):
                types = branch.get('type')
                found.append((pointer, value, branch['x-project-file'], isinstance(types, list) and 'null' in types))
            if isinstance(value, dict):
                for key, child in value.items():
                    sub = branch.get('properties', {}).get(key)
                    if sub is None and isinstance(branch.get('additionalProperties'), dict):
                        sub = branch['additionalProperties']
                    if sub is not None:
                        walk(child, sub, f'{pointer}/{key}')
            elif isinstance(value, list) and isinstance(branch.get('items'), dict):
                for index, child in enumerate(value):
                    walk(child, branch['items'], f'{pointer}/{index}')
    walk(data, schema, '')
    return sorted(set(found))


def missing_files(path, data, name):
    """File references in `data` that do not exist under the project: [{pointer, path, kind, nullable}]."""
    root = project_dir(path)
    return [{'pointer': pointer, 'path': value, 'kind': kind, 'nullable': nullable}
            for pointer, value, kind, nullable in project_files(data, name) if not (root / value).is_file()]


def project_dir(path):
    path = Path(path).resolve()
    return path.parent if path.name == 'project.json' else path


def load_project(path):
    data = read_json(project_dir(path) / 'project.json')
    validate_schema(data, 'project')
    return data



def project_content_hash(project):
    """Fingerprint content decisions independently of manually maintained revision numbers."""
    return stable_hash({key: project[key] for key in ('output', 'audio', 'shots', 'style_id', 'brief')})


def shot_path(project, shot_id):
    return safe_path(project_dir(project), f'shots/{check_id(shot_id)}/shot.json')


def load_shot(project, shot_id):
    shot = read_json(shot_path(project, shot_id))
    validate_schema(shot, 'shot')
    return shot


LEGACY_ROUTE = {'mode': 'blender', 'rule_id': 'legacy_default', 'decided_by': 'agent', 'status': 'proposed', 'features': []}


def route_of(shot):
    """Route at read time. Legacy shots without a route are Blender; nothing is written back."""
    return shot.get('route') or dict(LEGACY_ROUTE)


SHOT_SYSTEM_FIELDS = ('schema_version', 'shot_id', 'revision', 'scene_version')   # set by the studio, never by a brief
BRIEF_SHOT_INPUTS = ('frame_count', 'route_features', 'generative')   # read by init_project, not stored as shot fields


def default_shot(shot_id, frame_count, brief):
    return {
        'schema_version': 1, 'shot_id': shot_id, 'revision': 1,
        'goal': brief.get('goal', brief.get('request', '')),
        'duration_frames': frame_count, 'asset_instances': [], 'scene_version': None,
        'actions': [], 'camera': {'projection': 'perspective', 'movement': 'authored', 'target_anchor': None, 'keys': []},
        'narration': {'text': '', 'claim_ids': [], 'audio_path': None, 'alignment_path': None, 'cues': []},
        'labels': [], 'render': {'engine': 'CYCLES', 'look_frames': [0, frame_count // 2, frame_count - 1], 'style_id': 'technical_cool_v1'},
        'review_targets': [], 'preserve': []}


def init_project(identifier, brief_path, root=None):
    identifier = check_id(identifier)
    brief = read_json(brief_path) if not isinstance(brief_path, dict) else deepcopy(brief_path)
    if not isinstance(brief.get('request'), str) or not brief['request'].strip():
        raise StudioError('INPUT_INVALID', 'brief.request must be nonempty text')
    path = Path(root or REPO / 'projects').resolve() / identifier
    if path.exists():
        raise StudioError('REVISION_CONFLICT', f'Project already exists: {path}')
    output = {'width': 1080, 'height': 1920, 'fps': 30, 'target_seconds': 18, 'duration_policy': 'flexible'}
    output.update(brief.get('output', {}))
    policy = {'allow_generative': True, 'budget_usd': 0, 'turnaround_required': True}
    policy.update(brief.get('route_policy', {}))
    raw_shots = brief.get('shots') or [{'shot_id': 'shot_01', 'frame_count': round(output['target_seconds'] * output['fps'])}]
    timeline, shots, offset = [], [], 0
    for raw in raw_shots:
        sid = check_id(raw['shot_id'])
        length = raw.get('frame_count', raw.get('duration_frames', 180))
        shot = default_shot(sid, length, brief)
        # A brief shot may carry any field the shot schema has (key_parts, scene, titles, subjects, ...): every one is
        # kept - not a list of the ones known today - and a key that is neither a shot field nor read here is refused,
        # so nothing the user wrote disappears silently (2026-10-07: key_parts were dropped at init).
        fields = set(load_schema('shot')['properties']) - set(SHOT_SYSTEM_FIELDS)
        unknown = sorted(set(raw) - fields - set(BRIEF_SHOT_INPUTS) - {'shot_id'})
        if unknown:
            raise StudioError('INPUT_INVALID', f"brief shot {sid}: {unknown} are not shot fields "
                                               f"(shot fields: {sorted(fields)}; read by init: {list(BRIEF_SHOT_INPUTS)})")
        for key in sorted(fields & set(raw)):
            if key == 'route':
                continue   # routed below (proposed from route_features when absent)
            if isinstance(shot.get(key), dict) and isinstance(raw[key], dict):
                shot[key].update(deepcopy(raw[key]))   # defaults the brief does not mention stay
            else:
                shot[key] = deepcopy(raw[key])
        if 'route' in raw:
            shot['route'] = deepcopy(raw['route'])
        else:
            from .routing import propose_route
            # Features declared in the brief drive routing; goal text never does.
            shot['route'] = {'features': list(raw.get('route_features', []))}
            proposed = propose_route(shot, policy, generative=raw.get('generative'))
            del shot['route']
            if proposed['mode'] == 'blender' or 'generative' in proposed:
                shot['route'] = proposed
        validate_shot(shot)
        timeline.append({'shot_id': sid, 'start_frame': offset, 'frame_count': length})
        shots.append(shot)
        offset += length
    project = {'schema_version': 1, 'project_id': identifier, 'revision': 1,
               'brief': {key: brief.get(key, default) for key, default in [('request', ''), ('mode', 'reel'), ('key_message', ''), ('subject_mode', 'schematic'), ('references', []), ('preserve', [])]},
               'output': output, 'style_id': brief.get('style_id', 'technical_cool_v1'), 'shots': timeline, 'claims': [],
               'audio': {'provider': 'say', 'voice_id': 'Yuna', 'model_id': None, 'speech_status': 'scratch'},
               'limits': {'look_iterations_per_shot': 3, 'motion_iterations_per_shot': 3, 'render_wall_minutes': 120, 'downloads_bytes': 3221225472},
               'route_policy': policy}
    project['audio'].update(brief.get('audio', {}))
    project['limits'].update(brief.get('limits', {}))
    validate_schema(project, 'project')
    if len({s['shot_id'] for s in timeline}) != len(timeline):
        raise StudioError('INPUT_INVALID', 'Duplicate shot IDs')
    for shot in shots:
        write_json(shot_path(path, shot['shot_id']), shot)
    write_json(path / 'project.json', project)
    write_json(path / 'sources.json', {'schema_version': 1, 'sources': [], 'claims': []})
    style = {'style_id': project['style_id'], 'revision': 1, 'reference_paths': [], 'camera_defaults': {},
             'palette_srgb': {'accent': [0.93, 0.20, 0.16]}, 'world': {}, 'light_rig': {}, 'materials': {},
             'typography': {'font_path': DEFAULT_FONT},
             'safe_rect_normalized': [0.07, 0.10, 0.86, 0.78], 'label_slots': {}, 'audio_defaults': {}}
    library_style = REPO / 'library' / 'styles' / f"{project['style_id']}.json"
    style = read_json(library_style) if library_style.exists() else style
    validate_schema(style, 'style')
    write_json(path / 'style.json', style)
    run_id = _new_run(path, project, brief['request'])
    out = {'project_id': identifier, 'project_path': str(path), 'run_id': run_id, 'status': 'briefed', 'artifacts': [str(path / 'project.json')]}
    from .decisions import claim_pending_delegation   # scripts/reel_agent.py --delegate left the user's words for this run
    delegation = claim_pending_delegation(path)
    if delegation:
        out['delegation'] = delegation
    return out


def _new_run(path, project, request):
    """The run record every project needs before jobs can be submitted (jobs.submit_render reads the latest run)."""
    run_id = 'run_' + uuid.uuid4().hex[:12]
    stamp = now()
    run = {'schema_version': 1, 'run_id': run_id, 'project_id': project['project_id'], 'request': request, 'base_revision': project['revision'],
           'status': 'queued', 'stage': 'briefed', 'current_shot': None, 'completed_operations': [], 'pending_jobs': [],
           'best_versions': {}, 'limits': project['limits'], 'elapsed': {}, 'last_error': None, 'created_at': stamp, 'updated_at': stamp}
    write_json(Path(path) / 'runs' / run_id / 'run.json', run)
    return run_id


def unread_values(shot):
    """Values of the shot that nothing reads in their context - a typo, a key of another type, or a key the chosen type,
    profile or kind ignores. Each one would be a silent no-op, so validation refuses them. The declared-reads tables:
    camera_moves_core.PARAMS (move params), action_params (action params), camera_keys (rig/move/timing by context)."""
    from .blender_ops.action_params import unread
    from .blender_ops.camera_keys import camera_unread
    from .blender_ops.camera_moves_core import PARAMS, unknown_params
    camera = shot.get('camera') or {}
    out = []
    if camera.get('move'):
        out += [f"camera/move/params/{k} ({camera['move']['type']} reads {sorted(PARAMS[camera['move']['type']])})" for k in unknown_params(camera['move'])]
    out += camera_unread(camera)
    for action in shot.get('actions') or []:
        out += [f"actions/{action['action_id']}/{p}" for p in unread(action)]
    from .blender_ops.content_keys import content_unread
    out += content_unread(shot)
    from .blender_ops.expressive_core import unread as expressive_unread
    out += expressive_unread(shot.get('render') or {})
    if shot.get('scene'):
        from .layout import scene_unread   # the scene as written (a set it uses is checked when lint resolves it)
        out += scene_unread(shot['scene'])
    return out


def validate_shot(shot):
    validate_schema(shot, 'shot')
    camera = shot['camera']
    if any('target' not in key for key in camera.get('keys', [])) and not camera.get('target_anchor'):
        raise StudioError('INPUT_INVALID', 'a camera key without target needs camera.target_anchor (what the camera looks at)')
    never_read = unread_values(shot)
    if never_read:
        raise StudioError('INPUT_INVALID', 'nothing reads these values: ' + '; '.join(never_read[:6]),
                          recovery='Remove them, or set the value the chosen type/profile reads (the message lists it)')
    duration = shot['duration_frames']
    ids = set()
    channels = {}
    for action in shot['actions']:
        if action['action_id'] in ids:
            raise StudioError('INPUT_INVALID', 'Duplicate action ID')
        ids.add(action['action_id'])
        if not (0 <= action['start_frame'] < action['end_frame'] <= duration):
            raise StudioError('TIMING_CONFLICT', f"Invalid half-open action interval: {action['action_id']}")
        params = action['params']
        kind = action['type']
        requirements = {'explode': ['direction_source', 'distance_m'], 'peel': ['direction_source', 'distance_m', 'order'],
                        'assemble': ['source_action_id'], 'cutaway': ['cutter_object_id', 'cap_material_id'],
                        'flow': ['path_object_id', 'speed_mps', 'marker_count'], 'highlight': ['color_srgb', 'strength'],
                        'reveal': ['cutter_object_id', 'cap_material_id', 'cutter_keys'], 'simulate': ['kind', 'region', 'count'],
                        'drive': ['drives']}
        if any(k not in params for k in requirements[kind]):
            raise StudioError('INPUT_INVALID', f'{kind} needs params {requirements[kind]}')
        if kind in ('explode', 'peel') and params.get('distance_m', 0) < 0:
            raise StudioError('INPUT_INVALID', 'distance_m cannot be negative')
        if params.get('stagger_frames', 0) < 0:
            raise StudioError('INPUT_INVALID', 'stagger_frames cannot be negative')
        if params.get('direction_source') == 'axis' and sum(x*x for x in params.get('axis', [0, 0, 1])) == 0:
            raise StudioError('INPUT_INVALID', 'Motion axis cannot be zero')
        channel = 'transform' if kind in ('explode', 'peel', 'assemble', 'drive') else kind   # a drive and an explode both move parts
        if kind == 'simulate':  # targets are only colliders: several simulations may share them
            channel = f"simulate:{action['action_id']}"
        for target in action['targets']:
            key = (target['instance_id'], target['part_id'], channel)
            for previous in channels.get(key, []):
                if action['start_frame'] < previous['end_frame'] and previous['start_frame'] < action['end_frame']:
                    raise StudioError('TIMING_CONFLICT', f'Overlapping {channel} actions for {key[:2]}')
            channels.setdefault(key, []).append(action)
        if action.get('time_binding'):
            cues = {c['cue_id'] for c in shot['narration'].get('cues', [])}
            binding = action['time_binding']
            cue_ids = [binding['start_cue_id'], binding['end_cue_id']]
            # 'cam-<mark>' cues are the camera move's pass frames, resolved at build (reveal.py)
            if any(i.startswith('cam-') for i in cue_ids) and not shot['camera'].get('move'):
                raise StudioError('INPUT_INVALID', f"{action['action_id']} binds to camera cues but the shot has no camera.move")
            if any(i not in cues and not i.startswith('cam-') for i in cue_ids):
                raise StudioError('INPUT_INVALID', 'Action references missing narration cue')
        if kind == 'reveal' and [k['t'] for k in params['cutter_keys']] != sorted(k['t'] for k in params['cutter_keys']):
            raise StudioError('INPUT_INVALID', f"{action['action_id']}: cutter_keys must be ordered by t")
    for key in shot['camera']['keys']:
        if not 0 <= key['frame'] < duration:
            raise StudioError('TIMING_CONFLICT', 'Camera key outside shot')
    frames = [k['frame'] for k in shot['camera']['keys']]
    if frames != sorted(set(frames)):
        raise StudioError('INPUT_INVALID', 'Camera keys must be unique and ordered')
    rig = shot['camera'].get('rig')
    for field in ('offset_keys', 'aim_keys', 'lens_keys'):
        rig_frames = [k['frame'] for k in (rig or {}).get(field, [])]
        if any(not 0 <= f < duration for f in rig_frames):
            raise StudioError('TIMING_CONFLICT', f'Camera rig {field} outside shot')
        if rig_frames != sorted(set(rig_frames)):
            raise StudioError('INPUT_INVALID', f'Camera rig {field} must be unique and ordered')
    route = shot.get('route')
    if route and route['mode'] == 'generative':
        filled = [k for k in ('actions', 'labels', 'asset_instances') if shot[k]] + (['scene_version'] if shot['scene_version'] else [])
        if filled:
            raise StudioError('ROUTE_CONTENT_CONFLICT', f'Generative shot cannot carry Blender content: {filled}',
                              recovery='Make it hybrid (Blender motion pass + restyle) or remove the Blender-only fields')
    if route and route['mode'] != 'blender':
        from .generative.policy import role_of
        overlays = [k for k in ('labels', 'graphics') if shot.get(k)]
        if role_of(route) == 'mood' and overlays:
            raise StudioError('ROUTE_ROLE_CONFLICT', f'A mood shot carries captions only, not {overlays}',
                              recovery='Make it an explain shot (hybrid, structure-checked) or move the labels to an explain shot')
    if route and route['mode'] != 'blender' and route.get('est_cost_usd') is not None and 'budget_usd' in route['generative'] \
            and route['est_cost_usd'] > route['generative']['budget_usd']:
        raise StudioError('ROUTE_BUDGET_CONFLICT', 'Route estimate exceeds its own generative budget_usd')
    preset = shot['render'].get('look_preset')
    presets_file = REPO / 'studio/blender_ops/look_data/look_presets.json'
    if preset and presets_file.is_file() and preset not in read_json(presets_file).get('presets', {}):
        raise StudioError('INPUT_INVALID', f'Unknown look_preset {preset}')
    labels = set()
    for label in shot['labels']:
        if label['label_id'] in labels:
            raise StudioError('INPUT_INVALID', 'Duplicate label ID')
        labels.add(label['label_id'])
        if not 0 <= label['start_frame'] < label['end_frame'] <= duration:
            raise StudioError('TIMING_CONFLICT', 'Label outside shot')
    titles = set()
    for title in shot.get('titles', []):
        if title['title_id'] in titles:
            raise StudioError('INPUT_INVALID', 'Duplicate title ID')
        titles.add(title['title_id'])
        if not 0 <= title['start_frame'] < title['end_frame'] <= duration:
            raise StudioError('TIMING_CONFLICT', f"Title {title['title_id']} outside shot")
    for a in range(duration):
        active = [l for l in shot['labels'] if l['start_frame'] <= a < l['end_frame']]
        if len(active) > 2 or len({l['slot'] for l in active}) != len(active):
            raise StudioError('INPUT_INVALID', 'At most two simultaneous labels in different slots are supported')
    return shot


def validate_project(path):
    path = project_dir(path)
    project = load_project(path)
    offset, seen, missing = 0, set(), []
    for entry in project['shots']:
        if entry['shot_id'] in seen or entry['start_frame'] != offset:
            raise StudioError('TIMING_CONFLICT', 'Project shots must be unique and contiguous from zero')
        seen.add(entry['shot_id'])
        shot = validate_shot(load_shot(path, entry['shot_id']))
        if shot['duration_frames'] != entry['frame_count']:
            raise StudioError('TIMING_CONFLICT', f"Duration mismatch for {entry['shot_id']}")
        offset += entry['frame_count']
        if shot['scene_version']:
            version = safe_path(shot_path(path, shot['shot_id']).parent, f"versions/{check_id(shot['scene_version'])}")
            if not (version / 'scene.blend').is_file():
                raise StudioError('INPUT_INVALID', f'Missing scene snapshot {version}')
        missing += [{'shot_id': entry['shot_id'], **row} for row in missing_files(path, shot, 'shot')]
    if project['output']['duration_policy'] == 'strict' and abs(offset / project['output']['fps'] - project['output']['target_seconds']) > 0.5 / project['output']['fps']:
        raise StudioError('TIMING_CONFLICT', 'Strict project duration does not match shot timeline')
    return {'project_id': project['project_id'], 'status': 'valid', 'frame_count': offset, 'duration_seconds': offset / project['output']['fps'], 'shot_count': len(seen),
            'missing_files': missing}


def status_project(path):
    path = project_dir(path)
    project = load_project(path)
    shots, next_operations = [], []
    for entry in project['shots']:
        shot = load_shot(path, entry['shot_id'])
        route = route_of(shot)
        item = {'shot_id': shot['shot_id'], 'scene_version': shot['scene_version'], 'renders': [], 'route_mode': route['mode'], 'route_status': route.get('status')}
        if route['mode'] != 'blender':
            clips = [read_json(c) for c in sorted(shot_path(path, shot['shot_id']).parent.glob('generated/*/clip.json'))]
            item['renders'] = [{'profile': c.get('profile'), 'path': c.get('clip_path')} for c in clips if c.get('status') == 'complete']
            if route['mode'] == 'generative':
                if route.get('status') != 'approved':
                    next_operations.append({'operation': 'route.approve', 'shot_id': shot['shot_id'], 'reason': 'Paid generation awaits a recorded user approval'})
                elif not item['renders']:
                    next_operations.append({'operation': 'generate.submit', 'shot_id': shot['shot_id'], 'reason': 'Approved; no generated clip'})
                shots.append(item)
                continue
        for render_file in sorted(shot_path(path, shot['shot_id']).parent.glob('renders/*/render.json')):
            render = read_json(render_file)
            if render.get('scene_version') == shot['scene_version'] and render.get('status') == 'complete' and render.get('full_sequence') and render.get('frame_count') == shot['duration_frames'] and bool(render.get('clip_path')) and Path(render['clip_path']).is_file():
                item['renders'].append({'profile': render['profile'], 'path': str(render_file)})
        if not shot['scene_version']:
            next_operations.append({'operation': 'shot.build', 'shot_id': shot['shot_id'], 'reason': 'No scene snapshot'})
        elif not item['renders']:
            next_operations.append({'operation': 'render.submit', 'shot_id': shot['shot_id'], 'reason': 'No completed render'})
        shots.append(item)
    candidates = [str(x.parent) for x in sorted(path.glob('final/*/manifest.json'))]
    stage = 'briefed' if any(not s['scene_version'] and s['route_mode'] != 'generative' for s in shots) else 'assets_ready'
    if shots and all(s['renders'] for s in shots):
        stage = 'motion_ready'
        next_operations.append({'operation': 'edit.build', 'reason': 'Assemble current shot renders'})
    current_candidates = []
    for candidate_dir in candidates:
        candidate_dir = Path(candidate_dir)
        manifest = read_json(candidate_dir / 'manifest.json')
        snapshot_file = candidate_dir / 'edit.snapshot.json'
        output_path = safe_path(path, manifest['output_path'])
        if not snapshot_file.is_file() or not output_path.is_file() or file_hash(output_path) != manifest['output_sha256']:
            continue
        snapshot = read_json(snapshot_file)
        current_style = read_json(path / 'style.json') if (path / 'style.json').exists() else {}
        if snapshot.get('project_revision') == project['revision'] and snapshot.get('project_content_hash') == project_content_hash(project) and stable_hash(snapshot.get('style_snapshot', {})) == stable_hash(current_style) and all(stable_hash(load_shot(path, row['shot_id'])) == stable_hash(row['shot_snapshot']) for row in snapshot.get('shots', [])):
            current_candidates.append(manifest)
    if current_candidates:
        stage = 'candidate_ready' if any((m.get('speech_status') == 'final' or (m.get('speech_status') == 'not_applicable' and m.get('narration_present') is False)) and m.get('profile') == 'candidate' for m in current_candidates) else 'roughcut_ready'
        next_operations = [op for op in next_operations if op['operation'] != 'edit.build']
    jobs = []
    from .jobs import job_status
    for job_file in sorted(path.glob('runs/*/jobs/*/job.json')):
        jobs.append(job_status(path, job_file.parent.name))
    result = {'project_id': project['project_id'], 'stage': stage, 'shots': shots, 'candidates': candidates, 'current_candidates': [m['candidate_id'] for m in current_candidates], 'jobs': jobs, 'next_operations': next_operations}
    reconcile_runs(path, result, project)
    return result



def reconcile_runs(path, state, project=None):
    """Persist observable progress; terminal jobs are recoverable, never active pending work."""
    path = project_dir(path)
    project = project or load_project(path)
    for run_file in path.glob('runs/*/run.json'):
        with lock(run_file.parent / '.run.lock'):
            run = read_json(run_file)
            jobs = [job for job in state['jobs'] if job['run_id'] == run['run_id']]
            active = [job['job_id'] for job in jobs if job['status'] in ('queued', 'running')]
            recoverable = [job['job_id'] for job in jobs if job['status'] in ('failed', 'cancelled', 'interrupted')]
            run['pending_jobs'] = active
            run['recoverable_jobs'] = recoverable
            run['stage'] = state['stage']
            if active:
                run['status'] = 'running'
            elif state['stage'] in ('candidate_ready', 'approved') or (project['brief'].get('mode') == 'shot' and state['shots'] and all(shot['renders'] for shot in state['shots'])):
                run['status'] = 'complete'
            else:
                run['status'] = 'needs_work'
            failed = [job.get('error') for job in jobs if job.get('error')]
            run['last_error'] = failed[-1] if failed and run['status'] != 'complete' else None
            run['updated_at'] = now()
            write_json(run_file, run)


def resume_project(path, run_id):
    path = project_dir(path)
    run_file = safe_path(path, f'runs/{check_id(run_id)}/run.json')
    read_json(run_file)
    result = status_project(path)
    run = read_json(run_file)
    incomplete = [j for j in result['jobs'] if j['status'] not in ('complete',)]
    result.update({'run_id': run_id, 'run': run, 'incomplete_jobs': incomplete,
                   'note': 'These are verified pending operations. The Astra session performs authoring decisions.'})
    return result


def record_review(path, data):
    path = project_dir(path)
    review = read_json(data) if not isinstance(data, dict) else deepcopy(data)
    if review.get('reviewer_kind') not in ('agent', 'human') or review.get('verdict') not in ('auto_pass', 'needs_work', 'human_approved'):
        raise StudioError('INPUT_INVALID', 'Review needs reviewer_kind and verdict')
    if review.get('verdict') == 'human_approved' and (review['reviewer_kind'] != 'human' or not review.get('approval_evidence')):
        raise StudioError('INPUT_INVALID', 'Human approval requires a human confirmation evidence')
    if review.get('turnaround_approved') is True and (review['reviewer_kind'] != 'human' or not review.get('approval_evidence')
                                                      or not review.get('shot_id') or not review.get('scene_version')):
        raise StudioError('INPUT_INVALID', 'Turnaround approval needs a human reviewer, evidence, shot_id and scene_version')
    if not review.get('candidate_hash') and not review.get('shot_version_hash'):
        raise StudioError('INPUT_INVALID', 'Review must identify the exact candidate or shot hash')
    if review.get('facts_approved') is True:   # approving the facts means every sentence is sourced or illustrative
        from . import facts
        facts.require(path, purpose='facts approval')
    review['reviewed_at'] = review.get('reviewed_at', now())
    target = path / 'reviews' / f"{stable_hash(review)[:16]}.json"
    write_json(target, review)
    return {'status': 'recorded', 'artifacts': [str(target)], 'review': review}


def deliver(path, candidate_id, review_file):
    path = project_dir(path)
    directory = safe_path(path, f'final/{check_id(candidate_id)}')
    manifest = read_json(directory / 'manifest.json')
    review = read_json(review_file)
    candidate = directory / 'candidate.mp4'
    speech_ready = manifest.get('speech_status') == 'final' or (manifest.get('speech_status') == 'not_applicable' and manifest.get('narration_present') is False)
    if not candidate.is_file() or not speech_ready or manifest.get('profile') != 'candidate':
        raise StudioError('QUALITY_GATE_FAILED', 'Only a final-voice candidate can be promoted')
    digest = file_hash(candidate)
    if digest != manifest.get('output_sha256'):
        raise StudioError('QUALITY_GATE_FAILED', 'Candidate hash differs from its immutable manifest')
    snapshot = read_json(directory / 'edit.snapshot.json')
    project = load_project(path)
    if snapshot.get('project_revision') != project['revision'] or snapshot.get('project_content_hash') != project_content_hash(project):
        raise StudioError('QUALITY_GATE_FAILED', 'Candidate is stale relative to current project content; rebuild the edit')
    style = read_json(path / 'style.json') if (path / 'style.json').exists() else {}
    if stable_hash(snapshot.get('style_snapshot', {})) != stable_hash(style):
        raise StudioError('QUALITY_GATE_FAILED', 'Candidate style differs from current project style')
    font_path = font_file(style, path)
    if file_hash(font_path) != snapshot.get('font_sha256'):
        raise StudioError('QUALITY_GATE_FAILED', 'Candidate font changed since edit')
    for frozen in snapshot['shots']:
        if stable_hash(load_shot(path, frozen['shot_id'])) != stable_hash(frozen['shot_snapshot']):
            raise StudioError('QUALITY_GATE_FAILED', 'Candidate is stale relative to current shots')
    from .decisions import require
    require(path, 'deliver')
    from . import facts   # the narration delivered is the one the edit snapshot froze, against today's sources
    snapshot_path = directory / 'edit.snapshot.json'
    if snapshot_path.is_file():
        frozen = read_json(snapshot_path)
        if frozen.get('sources_sha256') != facts.sources_sha256(path):
            raise StudioError('QUALITY_GATE_FAILED', 'sources.json changed after this candidate was edited; edit a new candidate')
        facts.require(path, [row['shot_snapshot'] for row in frozen['shots']], purpose='delivery')
    fields = ('facts_approved', 'script_approved', 'assets_approved', 'visual_approved')
    if review.get('reviewer_kind') != 'human' or review.get('candidate_hash') != digest or not review.get('approval_evidence') or not all(review.get(k) is True for k in fields):
        raise StudioError('QUALITY_GATE_FAILED', 'Exact candidate hash and human facts/script/assets/visual confirmation are required')
    qa_file = directory / 'qa.json'
    if not qa_file.exists():
        raise StudioError('QUALITY_GATE_FAILED', 'Candidate QA must be collected before delivery')
    qa = read_json(qa_file)
    if qa.get('candidate_hash') != digest or qa.get('technical_pass') is not True:
        raise StudioError('QUALITY_GATE_FAILED', 'Candidate has not passed technical checks')
    if manifest.get('ai_generated_shots') and review.get('ai_disclosure_confirmed') is not True:
        raise StudioError('QUALITY_GATE_FAILED', f"Shots {manifest['ai_generated_shots']} are AI-generated; the human review must confirm ai_disclosure_confirmed")
    overrides = review.get('license_overrides') or {}
    for frozen in snapshot['shots']:
        for instance in frozen['shot_snapshot'].get('asset_instances', []):
            asset = REPO / 'library' / 'assets' / instance['asset_id'] / instance.get('asset_version', 'v0001') / 'asset.json'
            if asset.is_file() and read_json(asset).get('source', {}).get('use_status') != 'cleared' and not overrides.get(instance['asset_id']):
                raise StudioError('QUALITY_GATE_FAILED', f"Asset {instance['asset_id']} is not licence-cleared; the human review needs license_overrides[{instance['asset_id']}] with evidence")
    destination = path / 'approved' / candidate_id / 'master.mp4'
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate, destination)
    write_json(destination.parent / 'review.json', review)
    return {'status': 'approved', 'candidate_hash': digest, 'artifacts': [str(destination)]}


EXAMPLES = REPO / 'examples'


def from_example(example, project=None):
    """Start a working project from a tracked example (examples/<name>/project): contracts copied, versions empty.
    The example's author script stays in the example folder (its modules travel with every build)."""
    source = EXAMPLES / check_id(example) / 'project'
    if not (source / 'project.json').is_file():
        raise StudioError('INPUT_INVALID', f'No example project {example} (examples/<name>/project/project.json)')
    target = Path(project).resolve() if project else REPO / 'projects' / example
    if target.exists():
        raise StudioError('REVISION_CONFLICT', f'{target} exists; choose another --project path')
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f'.{target.name}.partial-{uuid.uuid4().hex[:8]}'
    try:   # build and check beside the target, then move into place: a failure leaves nothing behind
        shutil.copytree(source, staging)
        dropped, missing = [], []
        for entry in load_project(staging)['shots']:
            shot = load_shot(staging, entry['shot_id'])
            changed = False
            for row in missing_files(staging, shot, 'shot'):
                if row['kind'] == 'derived':   # caches the tools rebuild (scratch audio, alignments): drop the dangling reference
                    parent = shot
                    *parents, key = row['pointer'].strip('/').split('/')
                    for part in parents:
                        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
                    if row['nullable']:
                        parent[key] = None
                    else:
                        parent.pop(key)
                    dropped.append({'shot_id': entry['shot_id'], **row}); changed = True
                else:
                    missing.append({'shot_id': entry['shot_id'], **row})
            if changed:
                validate_shot(shot); write_json(shot_path(staging, entry['shot_id']), shot)
        validate_project(staging)
        project_data = load_project(staging)
        _new_run(staging, project_data, project_data['brief']['request'])
        os.rename(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    authors = sorted(str(p.relative_to(REPO)) for p in (EXAMPLES / example).glob('author*.py'))
    return {'project_path': str(target), 'example': example, 'author_scripts': authors, 'dropped_caches': dropped, 'missing_inputs': missing,
            'next': f"python -m studio shot build --project {target} --shot <id> --script {authors[0] if authors else '<author.py>'}"}


def register_commands(subparsers):
    parser = subparsers.add_parser('project', help='Project contracts, progress and resumption')
    subs = parser.add_subparsers(dest='project_command', required=True)
    init = subs.add_parser('init')
    init.add_argument('--id', required=True); init.add_argument('--brief', required=True); init.add_argument('--root')
    init.set_defaults(handler=lambda a: init_project(a.id, a.brief, a.root))
    sub = subs.add_parser('from-example', help='Copy a tracked example project (examples/<name>/project) into projects/')
    sub.add_argument('--example', required=True); sub.add_argument('--project')
    sub.set_defaults(handler=lambda a: from_example(a.example, a.project))
    for name, function in [('validate', validate_project), ('status', status_project)]:
        sub = subs.add_parser(name); sub.add_argument('--project', required=True)
        sub.set_defaults(handler=lambda a, fn=function: fn(a.project))
    sub = subs.add_parser('delegate', help="Record the user's words handing this run's decisions to the agent (no decision-ladder sheets)")
    sub.add_argument('--project', required=True); sub.add_argument('--user-words', required=True); sub.add_argument('--scope', nargs='*')
    sub.set_defaults(handler=lambda a: __import__('studio.decisions', fromlist=['delegate']).delegate(a.project, a.user_words, a.scope))
    sub = subs.add_parser('resume'); sub.add_argument('--project', required=True); sub.add_argument('--run', required=True)
    sub.set_defaults(handler=lambda a: resume_project(a.project, a.run))
    review = subparsers.add_parser('review').add_subparsers(dest='review_command', required=True).add_parser('record')
    review.add_argument('--project', required=True); review.add_argument('--input', required=True)
    review.set_defaults(handler=lambda a: record_review(a.project, a.input))
    delivery = subparsers.add_parser('deliver'); delivery.add_argument('--project', required=True); delivery.add_argument('--candidate', required=True); delivery.add_argument('--review', required=True)
    delivery.set_defaults(handler=lambda a: deliver(a.project, a.candidate, a.review))
