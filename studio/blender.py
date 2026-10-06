from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
import time

from .common import REPO, StudioError, blender_binary, check_id, file_hash, lock, now, read_json, run_command, safe_path, stable_hash, write_json
from .fidelity import spec_sha256
from .gates import severity_map
from .project import METADATA_SCOPES, load_project, load_shot, project_dir, shot_path, validate_schema, validate_shot


def _checked_specs(path, shot):
    """Load and lint every subject spec the shot names; a spec with lint errors never reaches Blender."""
    from .subjects import lint_spec, load_spec
    specs = {}
    for ref in shot.get('subjects', []):
        spec = load_spec(path, ref['subject_id'])
        lint = lint_spec(spec, path)
        if lint['errors']:
            raise StudioError('SUBJECT_SPEC_INVALID', f"{ref['subject_id']}: " + '; '.join(lint['errors'][:6]),
                              recovery='Run subject lint and fix the spec before building')
        specs[ref['subject_id']] = spec
    return specs


def generator_inputs(path, project, shot_id, shot, spec_paths, style=None, motion_style=None):
    """What blender_ops/generate.py needs besides the scene - shared by shot build and the workbench session, so a
    session generates exactly what a build of the same shot would."""
    if style is None:
        style = read_json(path / 'style.json') if (path / 'style.json').exists() else {}
    motion_style = motion_style if motion_style is not None else _motion_style(shot)
    return {'project_id': project['project_id'], 'project_dir': str(path), 'shot_id': shot_id, 'shot': shot, 'fps': project['output']['fps'],
            'style': style, 'library_root': str(REPO / 'library'), 'output_size': [project['output']['width'], project['output']['height']],
            'subject_spec_paths': spec_paths, 'gate_severity': severity_map(project, shot), **({'motion_style': motion_style} if motion_style else {})}


def _motion_style(shot):
    """The motion style a camera move takes its numbers from (move.style, else camera.motion_style); the
    version records its hash, so a re-learned style is a visible dependency change."""
    camera = shot['camera']
    name = (camera.get('move') or {}).get('style') or camera.get('motion_style')   # the same rule qa._shot_style reads
    if not name:
        return None
    from .motion_style import load
    return load(name)


def build_shot(path, shot_id, script, base=None, shot_override=None, expected_revision=None, expect=None, diagnosis=None, record=None):
    """Build a new immutable version; shots with subject specs also go through the repair policy (studio/repair.py)."""
    path = project_dir(path)
    shot = shot_override if shot_override is not None else load_shot(path, shot_id)
    if not shot.get('subjects'):
        return _build_shot(path, shot_id, script, base, shot_override, expected_revision, expect, diagnosis, record)
    from . import repair
    budget_warning = repair.check_budget(path, shot_id)
    try:
        result = _build_shot(path, shot_id, script, base, shot_override, expected_revision, expect, diagnosis, record)
    except StudioError as error:
        if error.code not in ('INPUT_INVALID', 'SUBJECT_SPEC_INVALID', 'REVISION_CONFLICT', 'PRESERVE_VIOLATION'):
            repair.record(path, shot_id, None, base, diagnosis, error=error.code)  # a failed attempt still spends budget
        raise
    result['repair'] = repair.record(path, shot_id, result['scene_version'], base, diagnosis, extra=record)
    if budget_warning:
        result['warnings'] = result.get('warnings', []) + [budget_warning]
    if result['repair'].get('reverted_to'):
        result['warnings'] = result.get('warnings', []) + [f"REPAIR_REVERTED: {result['scene_version']} scored below "
                                                           f"{result['repair']['reverted_to']}; shot.json points back to the best version"]
    return result


SIDECARS = ('places.json', 'modeling.json')   # project data an author script reads directly


def _author_companions(script):
    """Python modules beside the author script (not the repository's own code folders)."""
    folder = script.parent
    if folder == REPO or folder.is_relative_to(REPO / 'studio') or folder.is_relative_to(REPO / 'tests'):
        return []
    # the script is staged as author.py: a neighbour with that name (the original author beside a revision patch)
    # must never replace it
    return sorted(p for p in folder.glob('*.py') if p != script and p.name != 'author.py')


def _author_lines(file):
    """Code lines an author script adds (not blank, not comments): the measure a declarative scene drives to zero."""
    return sum(1 for line in file.read_text().splitlines() if line.strip() and not line.strip().startswith('#'))


def _sidecars(path):
    return {name: file_hash(path / name) for name in SIDECARS if (path / name).is_file()}


def _build_shot(path, shot_id, script, base=None, shot_override=None, expected_revision=None, expect=None, diagnosis=None, record=None):
    path = project_dir(path)
    from .freeze import require_code_frozen
    frozen_warnings = require_code_frozen()   # frozen code changed without the user's words: refuse before anything is built
    project = load_project(path)
    shot = validate_shot(deepcopy(shot_override) if shot_override is not None else load_shot(path, shot_id))
    from .routing import assert_route
    assert_route(shot, 'build', path)
    layout = None
    if shot.get('scene') and not base:   # a declarative scene is built fresh; a revision patch works on its checkpoint
        from .layout import lint as layout_lint, resolve as layout_resolve
        checked = layout_lint(path, shot, author=script is not None)
        if checked['errors']:
            raise StudioError('LAYOUT_INVALID', '; '.join(checked['errors'][:6]), recovery='Fix shot.scene (or its set); see studio/layout.py')
        layout = layout_resolve(path, shot)
    if script is None and layout is None:
        raise StudioError('INPUT_INVALID', 'Nothing to build: give --script or a shot.scene')
    if script is not None:
        script = Path(script).resolve()
        if not script.is_file():
            raise StudioError('INPUT_INVALID', f'Author script not found: {script}')
        # Only trusted repository/project scripts may execute. Asset downloads are data.
        if not script.is_relative_to(REPO) and not script.is_relative_to(path):
            raise StudioError('INPUT_INVALID', 'Author script must be inside the repository or project')
    rig = shot['camera'].get('rig')
    if rig and rig.get('script'):
        rig_script = (path / rig['script']).resolve()
        if not rig_script.is_relative_to(path) or not rig_script.is_file():
            raise StudioError('INPUT_INVALID', f"Camera rig script not found in project: {rig['script']}")
    specs = _checked_specs(path, shot)
    directory = shot_path(path, shot_id).parent
    with lock(path / '.project.lock', blocking=False):
        if expected_revision is not None and load_shot(path, shot_id)['revision'] != expected_revision:
            raise StudioError('REVISION_CONFLICT', 'Shot changed while revision was being prepared')
        versions = directory / 'versions'; versions.mkdir(parents=True, exist_ok=True)
        number = max([int(p.name[1:]) for p in versions.glob('v[0-9]*') if p.name[1:].isdigit()] or [0]) + 1
        version = f'v{number:04d}'
        destination = versions / version
        base_scene = None
        if base:
            # A revision starts from the base's authored checkpoint, never from its finished scene (build_scene.py).
            base_scene = safe_path(versions, f'{check_id(base)}/authored.blend')
            if not (base_scene.parent / 'scene.blend').is_file():
                raise StudioError('INPUT_INVALID', f'Base snapshot missing: {base}')
            if not base_scene.is_file():
                raise StudioError('BASE_NOT_REVISABLE', f'{base} was built before authored checkpoints; it cannot be revised in place',
                                  recovery=f'Build {shot_id} fresh (shot build without --base), then revise the new version')
            baseline_shot = read_json(base_scene.parent / 'shot.snapshot.json')
            for token in shot.get('preserve', []):
                if token == 'scene_version':
                    raise StudioError('PRESERVE_VIOLATION', 'A Blender revision cannot preserve the same scene_version')
                if token in {'actions', 'asset_instances', 'narration', 'labels', 'render', 'duration_frames'} and shot[token] != baseline_shot[token]:
                    raise StudioError('PRESERVE_VIOLATION', f'Preserved shot field changed: {token}')
        staging = Path(tempfile.mkdtemp(prefix='.building-', dir=versions))
        try:
            companions = []
            if script is not None:
                shutil.copy2(script, staging / 'author.py')
                # Modules next to the author script (a production's own library, e.g. samsung_lib.py) travel with it: the
                # version keeps the code it was built from, and the author imports them from its own folder wherever the
                # project lives. Sidecar data the author reads (places.json, modeling.json) is hashed the same way.
                companions = _author_companions(script)
                for module in companions:
                    shutil.copy2(module, staging / module.name)
            if layout is not None:
                write_json(staging / 'layout.json', layout)
            snapshot = deepcopy(shot); snapshot['scene_version'] = version
            style_path = path / 'style.json'
            style = read_json(style_path) if style_path.exists() else {}
            if style:
                validate_schema(style, 'style')
            motion_style = _motion_style(shot)
            spec_paths = {}
            for subject_id, spec in specs.items():
                # The version measures and keeps the spec it was built from, not whatever the file says later.
                spec_paths[subject_id] = str(staging / 'subjects' / f'{subject_id}.spec.json')
                write_json(Path(spec_paths[subject_id]), spec)
            job = {**generator_inputs(path, project, shot_id, snapshot, spec_paths, style, motion_style),
                   'base_version': base, 'output_dir': str(staging), 'script_path': str(staging / 'author.py') if script is not None else None,
                   **({'layout_path': str(staging / 'layout.json')} if layout is not None else {}), **({'expect': expect} if expect else {})}
            write_json(staging / 'author_job.json', job)
            command = [blender_binary(), '--background', '--factory-startup', '--disable-autoexec']
            if base_scene:
                command.append(str(base_scene))
            command += ['--python-exit-code', '1', '--python', str(REPO / 'studio/blender_ops/build_scene.py'), '--', str(staging / 'author_job.json')]
            started = time.monotonic()
            try:
                run_command(command, staging / 'build.log', timeout=1800)
            except StudioError as error:
                preserve_path = staging / 'preserve.json'
                if preserve_path.is_file() and not read_json(preserve_path).get('ok'):
                    raise StudioError('PRESERVE_VIOLATION', 'Revision changed protected scene data: ' + str(read_json(preserve_path).get('issues')), recovery='Inspect the failed version preserve.json and narrow the patch.') from error
                look_path = staging / 'look_report.json'
                if look_path.is_file() and read_json(look_path)['gate_failures']:
                    raise StudioError('LOOK_QA_FAILED', 'Look gates failed: ' + str(read_json(look_path)['gate_failures'][:5]),
                                      recovery='Inspect look_report.json in the failed version') from error
                if 'LOOK_QA_FAILED' in str(error):
                    raise StudioError('LOOK_QA_FAILED', str(error)[-1500:], recovery='Inspect build.log in the failed version') from error
                replay_path = staging / 'replay_report.json'
                if replay_path.is_file() and not read_json(replay_path)['ok']:
                    raise StudioError('WORKBENCH_REPLAY_MISMATCH', 'Replay differs from the session: ' + str(read_json(replay_path)['issues'][:5]),
                                      recovery='Inspect replay_report.json in the failed version; the session used state the typed ops do not capture') from error
                move_path = staging / 'camera_move_report.json'
                if move_path.is_file() and not read_json(move_path).get('ok'):
                    raise StudioError('CAMERA_MOVE_FAILED', read_json(move_path)['error'],
                                      recovery='Check the move params reference objects/anchors that exist in the scene; see camera_move_report.json.') from error
                fill_path = staging / 'fill_report.json'
                if fill_path.is_file() and read_json(fill_path)['gate_failures']:
                    first = read_json(fill_path)['gate_failures'][0]
                    raise StudioError(first['gate'], 'Fill gates failed: ' + str(read_json(fill_path)['gate_failures'][:5]),
                                      recovery='Fill the seen level from the brief (or declare it void with a reason), move ambient copies off the subject, or revise the brief with the user') from error
                kinematics_path = staging / 'kinematics_report.json'
                if kinematics_path.is_file() and any(r['interference'] for r in read_json(kinematics_path)['drives']):
                    raise StudioError('MECHANISM_INTERFERENCE', 'Coupled parts pass through each other: ' +
                                      str([c for r in read_json(kinematics_path)['drives'] for c in r['interference']][:5]),
                                      recovery='Check gear phases and centre distances (gear_core.planetary_layout), joint origins and axes') from error
                if 'CAMERA_ANCHOR' in str(error):
                    code = 'CAMERA_ANCHOR_OUT_OF_VIEW' if 'OUT_OF_VIEW' in str(error) else 'INPUT_INVALID'
                    raise StudioError(code, str(error)[-800:], recovery='Move the keys so the camera keeps camera.target_anchor in frame, or name the anchor it is about') from error
                if 'MECHANISM' in str(error):
                    raise StudioError('MECHANISM_INVALID', str(error)[-1200:], recovery='Fix the spec joints / couplings (kinematics_core.COUPLINGS)') from error
                rig_path = staging / 'camera_rig_report.json'
                if rig_path.is_file() and read_json(rig_path)['gate_failures']:
                    raise StudioError('CAMERA_RIG_GUARD_FAILED', 'Camera rig guards failed: ' + str(read_json(rig_path)['gate_failures'][:5]),
                                      recovery='Inspect camera_rig_report.json in the failed version; adjust offsets, lens, screen_anchor or guards.') from error
                raise
            inventory = read_json(staging / 'inventory.json')
            if not (staging / 'scene.blend').is_file() or not (staging / 'authored.blend').is_file() or not inventory['camera'] or inventory['missing_files']:
                raise StudioError('SCENE_INVALID', 'Author script did not produce a renderable scene')
            snapshot['revision'] += 1
            write_json(staging / 'shot.snapshot.json', snapshot)
            write_json(staging / 'style.snapshot.json', style)
            write_json(staging / 'dependencies.json', {'scene_sha256': file_hash(staging / 'scene.blend'), 'authored_sha256': file_hash(staging / 'authored.blend'),
                                                       'author_sha256': file_hash(staging / 'author.py') if script is not None else None,
                                                       'author_lines': _author_lines(staging / 'author.py') if script is not None else 0,
                                                       **({'layout_sha256': layout['layout_sha256'], 'exemplar_specs': layout['exemplar_specs']} if layout else {}),
                                                       'base_version': base, 'shot_hash': stable_hash(snapshot), 'style_hash': stable_hash(style),
                                                       'blender_version': inventory['blender_version'], 'external_files': inventory['external_files'],
                                                       **({'subject_specs': {k: spec_sha256(v) for k, v in sorted(specs.items())}} if specs else {}),
                                                       **({'motion_style_hash': stable_hash(motion_style)} if motion_style else {}),
                                                       **({'author_modules': {m.name: file_hash(m) for m in companions}} if companions else {}),
                                                       **({'project_sidecars': sidecars} if (sidecars := _sidecars(path)) else {})})
            write_json(staging / 'changes.json', {'base_version': base, 'version': version, 'created_at': now(), 'build_seconds': round(time.monotonic()-started, 3), 'author_original': str(script) if script is not None else None,
                                                   **({'diagnosis': diagnosis} if diagnosis else {}), **(record or {})})
            fidelity = None
            if (staging / 'fidelity_geometry.json').is_file():
                from .fidelity import build_report
                geometry = read_json(staging / 'fidelity_geometry.json')
                reports = [build_report(specs[g['subject_id']], g, path, staging) for g in geometry['subjects']]
                fidelity = {'passed': all(r['passed'] for r in reports), 'subjects': reports}
                write_json(staging / 'fidelity_report.json', fidelity)
            staging.rename(destination)
            write_json(shot_path(path, shot_id), snapshot)
            rig_report = read_json(destination / 'camera_rig_report.json') if (destination / 'camera_rig_report.json').exists() else None
            warnings = frozen_warnings + (list(rig_report['warnings']) if rig_report else [])
            if shot['camera'].get('energy') == 'high' and not rig:
                warnings.append('CAMERA_ENERGY_UNSUPPORTED: energy high without camera.rig; static keys rarely read as fast motion')
            return {'project_id': project['project_id'], 'shot_id': shot_id, 'scene_version': version, 'status': 'built',
                    'camera_rig': rig_report['summary'] if rig_report else None, 'warnings': warnings,
                    'fidelity': {'passed': fidelity['passed'], 'failures': [f for r in fidelity['subjects'] for f in r['failures']][:12],
                                 'deviations': [f"{r['subject_id']}:{d['check']} ({d['reason']})" for r in fidelity['subjects'] for d in r.get('deviations_applied', [])],
                                 'unused_deviations': [f"{r['subject_id']}:{u}" for r in fidelity['subjects'] for u in r.get('unused_deviations', [])]} if fidelity else None,
                    'look': {k: look[k] for k in ('preset', 'applied', 'skipped', 'warnings', 'scene_state_sha256')} if (look := read_json(destination / 'look_report.json') if (destination / 'look_report.json').exists() else None) else None,
                    'inventory': inventory, 'preserve_report': read_json(destination / 'preserve.json') if (destination / 'preserve.json').exists() else None,
                    'artifacts': [str(destination / 'scene.blend'), str(destination / 'inventory.json')]}
        except Exception:
            failed = versions / ('failed_' + staging.name.removeprefix('.building-'))
            if staging.exists():
                staging.rename(failed)
            raise


def inspect_shot(path, shot_id, version=None):
    path = project_dir(path)
    shot = load_shot(path, shot_id)
    version = check_id(version or shot['scene_version'] or '')
    directory = safe_path(shot_path(path, shot_id).parent, f'versions/{version}')
    inventory = read_json(directory / 'inventory.json')
    dependencies = read_json(directory / 'dependencies.json')
    if file_hash(directory / 'scene.blend') != dependencies['scene_sha256']:
        raise StudioError('REVISION_CONFLICT', 'Immutable scene snapshot was modified')
    return {'shot_id': shot_id, 'scene_version': version, 'status': 'valid', 'inventory': inventory, 'artifacts': [str(directory / 'inventory.json')]}



def revise_shot(path, shot_id, change_file, script=None):
    path = project_dir(path)
    change = read_json(change_file)
    current = load_shot(path, shot_id)
    if change.get('base_revision') != current['revision']:
        raise StudioError('REVISION_CONFLICT', 'Revision is stale; inspect current shot before retrying')
    scope = change.get('scope')
    allowed = {**METADATA_SCOPES, 'camera': {'camera'}, 'motion': {'actions'}, 'style': {'render'},
               'scene': {'goal', 'asset_instances', 'actions', 'camera', 'preserve', 'graphics', 'titles', 'scene'}, 'asset': {'asset_instances'}}
    patch = change.get('change')
    if scope not in allowed or not isinstance(patch, dict) or set(patch) - allowed[scope]:
        raise StudioError('INPUT_INVALID', f'Unexpected fields for revision scope {scope}')
    field_preserve = {'scene_version', 'actions', 'asset_instances', 'narration', 'labels', 'render', 'duration_frames'}
    updated = deepcopy(current)
    for key, value in patch.items():
        if key == 'route':
            # A route is replaced whole and always returns to proposed: approval is never patched in.
            if not isinstance(value, dict) or value.get('status', 'proposed') != 'proposed':
                raise StudioError('INPUT_INVALID', 'Route revisions must be proposals; record approval with route approve')
            updated['route'] = {**value, 'status': 'proposed', 'approved_at': None, 'approval_evidence': None, 'approval_binding': None}
            continue
        if isinstance(value, dict) and isinstance(updated.get(key), dict):
            updated[key].update(value)
        else:
            updated[key] = value
    # Optional camera fields are removed by patching them to null (e.g. rig -> keys).
    for optional in ('rig', 'move', 'motion_style', 'energy'):
        if optional in updated['camera'] and updated['camera'][optional] is None:
            del updated['camera'][optional]
    if 'preserve' in change:
        if not isinstance(change['preserve'], list) or any(not isinstance(token, str) or not token for token in change['preserve']):
            raise StudioError('INPUT_INVALID', 'preserve must contain nonempty constraint or object ID strings')
        updated['preserve'] = list(dict.fromkeys(change['preserve']))
    for token in updated.get('preserve', []):
        if token in field_preserve and updated[token] != current[token]:
            raise StudioError('PRESERVE_VIOLATION', f'Preserved shot field changed: {token}')
    if scope not in METADATA_SCOPES and 'scene_version' in updated.get('preserve', []):
        raise StudioError('PRESERVE_VIOLATION', 'A Blender revision cannot preserve the same scene_version')
    validate_shot(updated)
    request_dir = path / 'revisions' / stable_hash(change)[:16]
    request_dir.mkdir(parents=True, exist_ok=True)
    write_json(request_dir / 'change.json', change)
    if scope in METADATA_SCOPES:
        semantic_ids = [token for token in updated.get('preserve', []) if token not in {'geometry', 'materials', 'camera'} | field_preserve]
        if semantic_ids and current.get('scene_version'):
            version_dir = shot_path(path, shot_id).parent / 'versions' / current['scene_version']
            inventory = read_json(version_dir / 'inventory.json')
            known = {obj['studio_id'] for obj in inventory['objects']} | {obj['name'] for obj in inventory['objects']}
            for token in semantic_ids:
                if token not in known:
                    raise StudioError('PRESERVE_VIOLATION', f'Required object ID does not exist: {token}')
        if script:
            raise StudioError('INPUT_INVALID', 'Metadata-only revision does not execute Blender scripts')
        with lock(path / '.project.lock', blocking=False):
            if load_shot(path, shot_id)['revision'] != current['revision']:
                raise StudioError('REVISION_CONFLICT', 'Shot changed while revision was being prepared')
            updated['revision'] += 1
            write_json(shot_path(path, shot_id), updated)
        return {'status': 'revised', 'shot_id': shot_id, 'revision': updated['revision'], 'scene_version': updated['scene_version'],
                'render_invalidated': False, 'artifacts': [str(shot_path(path, shot_id))]}
    if 'scene' in patch and not script:
        # a declarative scene changed: the data says what the scene is, so it is built fresh (no patch on a checkpoint)
        result = build_shot(path, shot_id, None, None, updated, current['revision'])
        result.update({'render_invalidated': True, 'revision': updated['revision'] + 1})
        return result
    if not current['scene_version']:
        raise StudioError('INPUT_INVALID', 'Build an initial scene before revising it')
    if not script and scope in ('camera', 'motion'):
        script = request_dir / 'patch.py'
        function = 'apply_camera' if scope == 'camera' else 'apply_actions'
        if scope == 'camera' and (updated['camera'].get('rig') or updated['camera'].get('move')):
            # build_scene compiles the move / bakes the rig after this patch; apply_camera would only clear keys.
            script.write_text("import bpy\nbpy.context.scene['studio_authored_animation'] = True\n")
        else:
            script.write_text('import bpy\nfrom scene_tools import ' + function + '\n' + function + "(STUDIO_JOB['shot'])\nbpy.context.scene['studio_authored_animation'] = True\n")
    if not script:
        raise StudioError('INPUT_INVALID', f'{scope} revision requires a trusted --script that applies the requested geometry/style change')
    result = build_shot(path, shot_id, script, current['scene_version'], updated, current['revision'])
    result.update({'render_invalidated': True, 'revision': updated['revision']+1})
    return result


def export_anchors(path, shot_id, version=None):
    path = project_dir(path)
    shot = load_shot(path, shot_id)
    version = check_id(version or shot['scene_version'] or '')
    scene = safe_path(shot_path(path, shot_id).parent, f'versions/{version}/scene.blend')
    labels = [{k: label[k] for k in ('label_id', 'anchor', 'start_frame', 'end_frame')} for label in shot['labels']]
    project_output = load_project(path)['output']
    fingerprint = stable_hash({'scene': file_hash(scene), 'labels': labels, 'duration_frames': shot['duration_frames'],
                               'output_size': [project_output['width'], project_output['height']],
                               'code': file_hash(REPO / 'studio/blender_ops/scene_tools.py'), 'exporter': file_hash(REPO / 'studio/blender_ops/export_anchors.py')})[:24]
    directory = shot_path(path, shot_id).parent / 'anchors' / fingerprint
    output = directory / 'anchors.json'
    metadata = directory / 'manifest.json'
    cached = output.is_file() and metadata.is_file() and file_hash(output) == read_json(metadata).get('sha256')
    if not cached:
        write_json(directory / 'input.json', {'labels': labels, 'duration_frames': shot['duration_frames'], 'output_path': str(output),
                                              'output_size': [project_output['width'], project_output['height']]})
        command = [blender_binary(), '--background', '--factory-startup', '--disable-autoexec', str(scene), '--python-exit-code', '1',
                   '--python', str(REPO / 'studio/blender_ops/export_anchors.py'), '--', str(directory / 'input.json')]
        run_command(command, directory / 'export.log', timeout=300)
        if not output.is_file():
            raise StudioError('SCENE_INVALID', 'Anchor export did not produce coordinates')
        read_json(output)
        write_json(metadata, {'sha256': file_hash(output), 'fingerprint': fingerprint})
    return {'status': 'complete', 'shot_id': shot_id, 'scene_version': version, 'anchors_path': str(output), 'artifacts': [str(output)]}

def register_commands(subparsers):
    subs = subparsers.add_parser('shot', help='Build immutable Blender scene versions').add_subparsers(dest='shot_command', required=True)
    build = subs.add_parser('build'); build.add_argument('--project', required=True); build.add_argument('--shot', required=True); build.add_argument('--script', help='author script (optional when the shot has a declarative scene)'); build.add_argument('--base')
    build.add_argument('--diagnosis', help='the one failure this revision addresses (recorded in changes.json and the repair ledger)')
    build.set_defaults(handler=lambda a: build_shot(a.project, a.shot, a.script, a.base, diagnosis=a.diagnosis))
    select = subs.add_parser('select', help='point shot.json back at an existing version (e.g. the best one)')
    select.add_argument('--project', required=True); select.add_argument('--shot', required=True); select.add_argument('--version', required=True)
    select.set_defaults(handler=lambda a: __import__('studio.repair', fromlist=['select_version']).select_version(a.project, a.shot, a.version))
    revise = subs.add_parser('revise'); revise.add_argument('--project', required=True); revise.add_argument('--shot', required=True); revise.add_argument('--change', required=True); revise.add_argument('--script')
    revise.set_defaults(handler=lambda a: revise_shot(a.project, a.shot, a.change, a.script))
    anchors = subs.add_parser('anchors'); anchors.add_argument('--project', required=True); anchors.add_argument('--shot', required=True); anchors.add_argument('--version')
    anchors.set_defaults(handler=lambda a: export_anchors(a.project,a.shot,a.version))
    inspect = subs.add_parser('inspect'); inspect.add_argument('--project', required=True); inspect.add_argument('--shot', required=True); inspect.add_argument('--version')
    inspect.set_defaults(handler=lambda a: inspect_shot(a.project, a.shot, a.version))
