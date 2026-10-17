from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import os
import shutil
import tempfile
import time

from .common import REPO, StudioError, blender_binary, check_id, file_hash, lock, now, read_json, run_command, safe_path, stable_hash, write_json
from .fidelity import spec_sha256
from .gates import is_error, severity_map
from .project import METADATA_SCOPES, SHOT_CONTENT, load_project, load_shot, project_dir, shot_path, validate_schema, validate_shot


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


def _photo_poses(path, spec_paths):
    from .photo_match import photo_poses   # each photo view judged in the pose the photo shows (fidelity photo checks)
    return photo_poses(path, {k: read_json(Path(v)) for k, v in (spec_paths or {}).items()})


def generator_inputs(path, project, shot_id, shot, spec_paths, style=None, motion_style=None):
    """What blender_ops/generate.py needs besides the scene - shared by shot build and the workbench session, so a
    session generates exactly what a build of the same shot would."""
    if style is None:
        style = read_json(path / 'style.json') if (path / 'style.json').exists() else {}
    motion_style = motion_style if motion_style is not None else _motion_style(shot)
    from .world import load as load_world, lit, shell_for
    world = load_world(path)
    shot = lit(shot, world)   # the world's light where the shot sets none: one light for Blender and the prompt
    shell = shell_for(shot, world, style)   # the ground to the horizon and the sky (env_shell.py), when the world declares them
    return {'project_id': project['project_id'], 'project_dir': str(path), 'shot_id': shot_id, 'shot': shot, 'fps': project['output']['fps'],
            'style': style, 'library_root': str(REPO / 'library'), 'output_size': [project['output']['width'], project['output']['height']],
            'ui_rect': ui_rect(style, (project['output']['width'], project['output']['height'])),
            'subject_spec_paths': spec_paths, 'gate_severity': severity_map(project, shot),
            **({'shell': shell} if shell else {}),
            'photo_poses': _photo_poses(path, spec_paths),
            **({'motion_style': motion_style} if motion_style else {}),
            'simulation_cache': str(path / 'cache' / 'simulation'),   # trajectories already computed for the same inputs (simulate.py)
            'contrib': _contrib_table(path, shot, {k: read_json(Path(v)) for k, v in spec_paths.items()}, None),
            'exposure': _exposables(path, spec_paths),
            # where each frame picture has its subject and the things round it (the camera keeps that layout)
            **_picture_layout(path, shot),
            # what the shot's lines speak of, for a shot that declares cheats: cheat_scene refuses to move a thing that
            # carries one, by any id its objects answer to (the host's gate judges the cheat's own ids: cheat_core)
            **({'locked': _locked(path, shot)} if any(f.get('cheats') for f in ((shot['camera'].get('move') or {}).get('frames') or [])) else {})}


def _picture_layout(path, shot):
    """{'picture_layout': ..., 'picture_layout_notes': [...]} for a frame_to_frame move (its camera keeps each picture's
    layout: camera_moves_core.picture_terms), else {}."""
    move = (shot.get('camera') or {}).get('move') or {}
    if move.get('type') != 'frame_to_frame':
        return {}
    from .frame_targets import picture_layout
    notes = []
    layout = picture_layout(path, shot, notes)
    return {**({'picture_layout': layout} if layout else {}), **({'picture_layout_notes': notes} if notes else {})}


def _locked(path, shot):
    from .cheat_core import locked
    return locked(path, shot)


def _exposables(path, spec_paths):
    """{subject: {part: exposable_share}} from each built subject's fresh exposure survey (studio/explains.py): the
    camera's diagnosis judges hidden against what any side shows of a part (camera_frame.diagnose_part, A3-3). A subject
    with no fresh survey is left out - its parts are judged against all of them, as before (exposure_lines says so)."""
    from .explains import exposure
    out = {}
    for subject_id in sorted(spec_paths or {}):
        record, problem = exposure(path, subject_id)
        if record and not problem:
            out[subject_id] = {p: r['exposable_share'] for p, r in record['parts'].items() if r.get('exposable_share') is not None}
    return out


def exposure_lines(path, shot, spec_paths):
    """EXPOSURE_MISSING / EXPOSURE_STALE (warnings): a must-see part of the shot's pictures whose subject has no exposure
    survey the camera can use (none, another schema, its spec or builders changed since): the camera judges the part
    hidden against all of it, not against what any side shows of it (_exposables) - a part inside its housing may read
    as blocked. Survey it to judge it as built."""
    from .explains import exposure, exposure_command, exposure_why
    frames = ((shot.get('camera') or {}).get('move') or {}).get('frames') or []
    seen = sorted({str(ref) for fr in frames for ref in fr.get('must_see') or [] if '/' in str(ref)})
    out = []
    for subject_id in sorted(spec_paths or {}):
        parts = [ref for ref in seen if ref.split('/', 1)[0] == subject_id]
        problem = exposure(path, subject_id)[1] if parts else None
        if problem:
            out.append(f"{problem}: {shot['shot_id']}: {subject_id}: {exposure_why(path, subject_id)}; {parts} are judged hidden "
                       f"against all of them, not against what any side shows - {exposure_command(subject_id, path)}")
            continue
        record = exposure(path, subject_id)[0] or {} if parts else {}
        # a layer lying on a must-see part like a coat, not declared one (blender_ops/coats.py) - one line per subject; a
        # layer the shot must show itself is a part, never a coat (subjects.lint_spec), so it is not offered as one
        named = {ref.split('/', 1)[1] for ref in parts}
        laid = [(other, ref.split('/', 1)[1], share) for ref in parts
                for other, share in ((record.get('parts') or {}).get(ref.split('/', 1)[1], {}).get('coated_by') or {}).items()
                if share >= COAT_MIN_SHARE and other not in named]
        if laid:
            out.append(f"COAT_UNTAGGED: {shot['shot_id']}: {subject_id}: " + '; '.join(f'{o} lies on {p} like a coat ({s:.0%} of the survey\'s rays '
                                                                                         'meet it first)' for o, p, s in laid)
                       + ' - not declared, so each reads as a separate part in front of it; if it is a layer on that part, give its spec part '
                       + ', '.join(f'"coats": "{p}" ({o})' for o, p, _s in laid))
    return out


def shown_through_lines(shot, show_report):
    """SHOWN_THROUGH (said, not a gate): a part a moment shows faded because it stood in front of another part the
    moment shows (show_core.SHOWN_THROUGH, show_scene.apply) - the picture should draw it so."""
    return [f"SHOWN_THROUGH: {shot['shot_id']} t={row['at']}: {sorted(row['shown_through'])} drawn see-through so the parts behind "
            f"them show ({row['how']}) - the picture should draw them so" for row in show_report.get('openings') or [] if row.get('shown_through')]


COAT_MIN_SHARE = 0.02   # survey rays a layer must meet first before it is said to lie on a part like a coat (a stray hit is not)


def ui_rect(style, output_size):
    """The part of the frame no platform UI covers, normalized [x0, y0, x1, y1]: the style's title_safe_rect_normalized
    when it declares one; else, for a vertical output, the 9:16 feed rect (titles.TITLE_SAFE - Meta's Reels guide keeps
    14 % top, 35 % bottom and 6 % sides clear, within a point); a horizontal output has no feed UI over it: the frame."""
    from .titles import TITLE_SAFE
    if (style or {}).get('title_safe_rect_normalized'):
        return list(style['title_safe_rect_normalized'])
    width, height = output_size
    return list(TITLE_SAFE) if height > width else [0.0, 0.0, 1.0, 1.0]


def probe_inputs(path, shot, fps, style=None, output_size=(1080, 1920)):
    """What the frame probe (blender_ops/frame_probe.py) judges against: the shot's role, what counts as its subject (the
    camera's target, shot.subjects, inline scene instances, screen.subject; fill_brief role-subject copies are found in
    Blender), the ids that must exist (screen.subject, screen.keep - checked at build time), its key parts - declared
    (shot.key_parts), kept in frame by an approved storyboard, the rig's look target, or a part a screen target is
    about - and the screen block with the platform UI rect (ui_rect)."""
    from .generative.policy import role_of
    from .storyboard import envelope
    camera = shot['camera']
    rig, move = camera.get('rig') or {}, camera.get('move') or {}
    target = (move.get('params') or {}).get('target')
    subjects = [i for i in [rig.get('subject'), move.get('look_target'), target if isinstance(target, str) else None] if i]
    subjects += [s['subject_id'] for s in shot.get('subjects', [])] + [i['id'] for i in (shot.get('scene') or {}).get('instances', [])]
    keys = {k['id']: dict(k) for k in shot.get('key_parts', [])}
    focus_frames = []
    board = envelope(path, shot['shot_id'])
    if board and board.get('status') == 'approved':
        for row in board['approval']['contract']['frames']:
            focus_frames.append(row['frame'])
            for ident, box in row.get('focus', {}).items():
                if isinstance(box, list):   # what the approved board showed in frame stays a key part in the window around it
                    keys.setdefault(ident, {'id': ident, 'source': 'storyboard'})
    for target in move.get('frames') or []:   # the moments a frame picture shows are measured against it (frame_targets)
        focus_frames.append(round(target['at'] * (shot['duration_frames'] - 1)))
    if move.get('frames'):   # the things around the subject a picture shows get their own class: their size says how near
        from .frame_targets import context_elements, layout_elements
        for ids in list(context_elements(path, shot).values()) + [e['ids'] for e in layout_elements(path, shot).values()]:
            for ident in ids:   # every thing a picture can draw gets a class: the frame is compared thing by thing
                keys.setdefault(ident, {'id': ident, 'source': 'context'})
    if rig.get('look_target'):   # what a declared rig keeps watching is a key part of the shot
        keys.setdefault(rig['look_target'], {'id': rig['look_target'], 'source': 'rig'})
    screen = shot.get('screen') or None
    declared = list((screen or {}).get('subject', []))   # a scene with no subject object (a street, a section) names its own
    subjects += declared
    for ident in declared:   # the shot's focus gets its own class so what the shot is about can be measured (patterns.focus_of)
        keys.setdefault(ident, {'id': ident, 'source': 'focus'})
    for target in (screen or {}).get('targets', []):   # a part a target measures must have its own class in the id pass
        if target['of'] not in ('subject', 'all'):
            keys.setdefault(target['of'], {'id': target['of'], 'source': 'screen'})
    details = list((screen or {}).get('details', []))   # what the shot explains must show at a readable size (DETAIL_NOT_SHOWN)
    ids = [d['id'] for d in details]
    if len(ids) != len(set(ids)):
        raise StudioError('INPUT_INVALID', f"{shot['shot_id']}: screen.details names {sorted({i for i in ids if ids.count(i) > 1})} twice")
    for detail in details:   # its own class in the id pass; a part declared elsewhere keeps its own rules and window
        keys.setdefault(detail['id'], {'id': detail['id'], 'source': 'details'})
    exempt = list(range(round(0.25 * fps) + 1)) if move.get('whip_in_deg') else []   # camera_moves.WHIP_S: a deliberate blur
    return {'role': role_of(shot.get('route')), 'subjects': list(dict.fromkeys(subjects)), 'declared_subjects': declared,
            'keep': [i for i in list((screen or {}).get('keep', [])) + list((screen or {}).get('generate_only', [])) if i != '@background'],
            'key_parts': list(keys.values()),
            'concealed_parts': [dict(c) for c in shot.get('concealed_parts', [])], 'frames': focus_frames, 'exempt_frames': exempt,
            'screen': screen, 'ui_rect': ui_rect(style, output_size), 'output_size': list(output_size), 'details': details}


def _motion_style(shot):
    """The motion style a camera move takes its numbers from (move.style, else camera.motion_style); the
    version records its hash, so a re-learned style is a visible dependency change."""
    camera = shot['camera']
    name = (camera.get('move') or {}).get('style') or camera.get('motion_style')   # the same rule qa._shot_style reads
    if not name:
        return None
    from .motion_style import load
    return load(name)


def _count_appearance(path, shot_id, result, counted):
    """Every build of the shot itself after its first counts against its appearance budget (takes built side by side by
    storyboard variants, passed count_appearance=False, do not): the build result says how many remain - keep closing the gap to the concept while
    builds remain, then list what still differs. Why (floor_noise, 2026-10-09): checks passed, 3/4 of the budget was
    left, and the run stopped at a blockout."""
    if not counted:
        return result
    from . import repair
    result['appearance'] = repair.appearance(path, shot_id, result['scene_version'])
    if result['appearance']['remaining'] == 0:
        result['warnings'] = result.get('warnings', []) + [
            f"APPEARANCE_BUDGET_SPENT: {result['appearance']['used']} appearance builds on {shot_id}; list what still differs from its "
            'target - concept frame or reference - with crops (reference_critique) and move on']
    from .story import missing as story_missing   # a project with no story builds on, and is told (warning)
    result['warnings'] = result.get('warnings', []) + story_missing(path)
    return result


def build_shot(path, shot_id, script, base=None, shot_override=None, expected_revision=None, expect=None, diagnosis=None, record=None,
               count_appearance=True):
    """Build a new immutable version; shots with subject specs also go through the repair policy (studio/repair.py)."""
    path = project_dir(path)
    shot = shot_override if shot_override is not None else load_shot(path, shot_id)
    # before spending a build: a shot list that cuts where an imitated reference runs on is wrong (references.check_cuts)
    from .references import check_cuts
    check_cuts(path, shot_loader=lambda sid: shot if sid == shot_id else load_shot(path, sid))
    from . import repair
    if not shot.get('subjects'):
        result = _build_shot(path, shot_id, script, base, shot_override, expected_revision, expect, diagnosis, record)
        return _count_appearance(path, shot_id, result, count_appearance)
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
    return _count_appearance(path, shot_id, result, count_appearance)


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


def _contrib_table(path, shot, specs, layout, warnings=None):
    """Every contrib entry the build uses, resolved and checked (studio/contrib.py); unknown params refused; params the
    entry ignores (studio/contrib_probe.py) refused for a draft, warned for a promoted version (versions never change:
    the fix is a new draft)."""
    from .blender_ops.builder_params import SMOOTHING
    from .contrib import refs_in, resolve
    documents = [shot, *specs.values(), *(i['spec'] for i in ((layout or {}).get('scene') or {}).get('instances', []))]
    table = resolve(path, set().union(*(refs_in(d) for d in documents)))
    for spec in [*specs.values(), *(i['spec'] for i in ((layout or {}).get('scene') or {}).get('instances', []))]:
        for b in spec.get('builders', []):
            entry = table.get(b['builder'])
            reserved = sorted(set(SMOOTHING) & set(entry['params'])) if entry else []
            if reserved:   # every contrib mesh part takes these for its shading (assemble._geometry); an entry may not claim them
                raise StudioError('SUBJECT_SPEC_INVALID', f"{b['builder']} declares {reserved}, which are the part's shading keys; rename them in the entry")
            unknown = sorted(set(b.get('params') or {}) - set(entry['params']) - set(SMOOTHING)) if entry else []
            if unknown:
                raise StudioError('SUBJECT_SPEC_INVALID', f"{b['part_id']}: {b['builder']} does not read {unknown} (its manifest params: {sorted(entry['params'])})")
    from .contrib_probe import probe
    for ref, entry in sorted(table.items()):   # SKILL #8: a parameter that changes nothing is a placeholder
        found = probe(entry)
        if found.get('error') or found.get('unused'):
            message = (f"{ref}: " + (f"probe failed: {found['error']}" if found.get('error') else
                       f"params {found['unused']} change nothing in its output - use them, or remove them from the manifest and the function"))
            if entry.get('draft'):
                raise StudioError('CONTRIB_PARAM_UNUSED', message, recovery='Make each manifest param shape the output (SKILL #8)')
            if warnings is not None:
                warnings.append(f'CONTRIB_PARAM_UNUSED: {message} (a promoted version cannot change: make a new draft)')
    return table


def _sandbox_rules(path, staging, script, companions, spec_paths, author_lint, rig_lint):
    """What the runtime sandbox (blender_ops/sandbox.py) judges against: the author's files, where they may write, the
    inputs they may not touch, the imports they may make - the same allow-list the lint used. Contrib entries are not
    listed: they run only by blender_ops/contrib_loader.run, under the stricter pure rule (the policy rides in
    job['contrib'])."""
    from .author_lint import PROFILES, engine_modules
    project_modules = {p.stem for p in path.glob('*.py')}
    staged = [staging / 'author.py', *(staging / m.name for m in companions)] if script is not None else []
    return {'author_files': [str(p) for p in staged] + list((author_lint or {}).get('modules', {})),
            'write_roots': [str(staging), tempfile.gettempdir()],
            'protected': [str(staging / n) for n in ('author_job.json', 'layout.json', 'pre_author_state.json')] + list(spec_paths.values()),
            'allowed_imports': sorted(PROFILES['author']['imports'] | engine_modules() | {m.stem for m in companions} | project_modules),
            'rig_files': list((rig_lint or {}).get('modules', {})),
            'rig_allowed_imports': sorted(PROFILES['rig']['imports'] | project_modules),
            'engine_mode': 'enforce'}   # enforced after a recorded cycle (10-07: the only records were __pycache__ writes, now gone)


def _input_hashes(staging, job):
    names = ['author_job.json', 'layout.json', 'author.py'] + [Path(p).relative_to(staging).as_posix() for p in job['subject_spec_paths'].values()]
    names += [p.name for p in staging.glob('*.py') if p.name != 'author.py']
    return {name: file_hash(staging / name) for name in names if (staging / name).is_file()}


def _os_sandbox(command, staging):
    """STUDIO_OS_SANDBOX=1 (macOS): the author process gets no network and writes only to its build folder and temp."""
    import os, sys
    if os.environ.get('STUDIO_OS_SANDBOX') != '1' or sys.platform != 'darwin':
        return command
    profile = ('(version 1)(allow default)(deny network*)(deny file-write*)'
               f'(allow file-write* (subpath "{Path(staging).resolve()}") (subpath "{Path(tempfile.gettempdir()).resolve()}") (subpath "/private/var/folders"))')
    return ['sandbox-exec', '-p', profile] + [str(c) for c in command]


def _audit(folder):
    return read_json(folder / 'author_audit.json') if (folder / 'author_audit.json').is_file() else None


def _author_error(staging, error):
    """A failed author step: a sandbox refusal, the author's own exception, or the layout before it."""
    report = staging / 'sandbox_report.json'
    if report.is_file() and read_json(report)['violations']:
        first = read_json(report)['violations'][0]
        return StudioError('AUTHOR_SANDBOX_VIOLATION', f"{first['reason']} ({first['event']})",
                           recovery='Author scripts change the scene; files, processes and network belong to the studio tools')
    log = (staging / 'author.log').read_text(errors='replace') if (staging / 'author.log').is_file() else str(error)
    if 'ADDON_NOT_BUNDLED' in log:
        return StudioError('ADDON_NOT_BUNDLED', log[log.index('ADDON_NOT_BUNDLED'):][:400], recovery='Only add-ons shipped with Blender can be enabled')
    if 'author.py' in log or 'STUDIO_SANDBOX' in log:
        return StudioError('AUTHOR_SCRIPT_FAILED', log[-1500:], recovery='Fix the author script; author.log in the failed version has the traceback')
    return error


def plain_materials(shot, inventory):
    """MATERIAL_NOT_CATALOG lines: under a photoreal look, built parts (spec parts, scene primitives) painted with a plain
    material instead of a catalog kind (look_materials makes those as StudioMat_*). Why (2026-10-10): a floor cutaway
    painted every layer with a home-made grey, the catalog kinds made for those layers were never used, and the look
    read as one blue-grey."""
    if not str((shot.get('render') or {}).get('look_preset') or '').startswith('photoreal'):
        return []
    primitives = {r['id'] for r in (shot.get('scene') or {}).get('primitives', [])}
    # clay stand-ins: the regions a generative model fills (screen.generate_only) need a shape, not a material
    regions = [g for g in (shot.get('screen') or {}).get('generate_only') or [] if not g.startswith('@')]
    filled = lambda sid: any(sid == g or str(sid).startswith(g + '/') for g in regions)   # noqa: E731
    plain = sorted({m for o in inventory.get('objects', []) if (o.get('part_id') or o.get('studio_id') in primitives)
                    and not filled(o.get('studio_id'))
                    for m in o.get('materials', []) if m and not m.startswith('StudioMat_')})
    if not plain:
        return []
    kinds = sorted(read_json(REPO / 'studio/blender_ops/look_data/material_catalog.json')['kinds'])
    return [f"MATERIAL_NOT_CATALOG: plain materials on built parts under {shot['render']['look_preset']}: {', '.join(plain[:8])}"
            f"{' ...' if len(plain) > 8 else ''} - paint them with catalog kinds (catalog_key + catalog_overrides; a texture set "
            f"from generate register-texture) instead: {', '.join(kinds)}"]


def display_undisclosed(declared, labels):
    """Declarations (display_scale.json rows) no label discloses: a label anchored to the declared object, inside it
    (`<id>/part`, `<id>.n`) or on the group it belongs to. What the label says is the author's (e.g. "확대 표현 ×3")."""
    from .blender_ops.ids_core import in_group, spellings
    anchors = [s for label in labels or [] if label.get('anchor') for s in spellings(label['anchor'])]
    return [row for row in declared
            if not any(a == row['id'] or in_group(a, row['id']) or in_group(row['id'], a) for a in anchors)]


def _judged(lines, severity, recovery):
    """[{code, text}] under the project's gate policy: the first error is raised, the rest come back as warning lines."""
    for line in lines:
        if is_error(line['code'], severity):
            raise StudioError(line['code'], line['text'], recovery=recovery)
    return [f"{line['code']}: {line['text']}" for line in lines]


def _build_shot(path, shot_id, script, base=None, shot_override=None, expected_revision=None, expect=None, diagnosis=None, record=None,
                stop_after=None):
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
        checked = layout_lint(path, shot, author=script is not None or bool(shot.get('author')))
        if checked['errors']:
            raise StudioError('LAYOUT_INVALID', '; '.join(checked['errors'][:6]), recovery='Fix shot.scene (or its set); see studio/layout.py')
        layout = layout_resolve(path, shot)
    # A fresh build runs the shot's own author script (shot.author), so a rebuild never silently drops it (archcut3 s02
    # v0006 lost its materials and gait that way); a revision (--base) starts from a checkpoint the script already shaped,
    # and --script there is a patch run on top of it.
    recorded = (path / shot['author']).resolve() if shot.get('author') else None
    if not base and script is None:
        script = recorded
    elif not base and recorded is not None and Path(script).resolve() != recorded:
        raise StudioError('AUTHOR_SCRIPT_CONFLICT', f"--script {script} is not the shot's author script {shot['author']}",
                          recovery='Change shot.author (studio shot set) to switch scripts, or build without --script')
    if script is None and layout is None:
        raise StudioError('INPUT_INVALID', 'Nothing to build: give --script, shot.author or a shot.scene')
    if script is not None:
        script = Path(script).resolve()
        if not script.is_file():
            raise StudioError('INPUT_INVALID', f'Author script not found: {script}')
        # Only trusted repository/project scripts may execute. Asset downloads are data.
        if not script.is_relative_to(REPO) and not script.is_relative_to(path):
            raise StudioError('INPUT_INVALID', 'Author script must be inside the repository or project')
        if not base:   # the script a fresh build ran is the shot's from now on (written with the shot after the build)
            shot['author'] = os.path.relpath(script, path)
    rig = shot['camera'].get('rig')
    rig_script = None
    if rig and rig.get('script'):
        rig_script = (path / rig['script']).resolve()
        if not rig_script.is_relative_to(path) or not rig_script.is_file():
            raise StudioError('INPUT_INVALID', f"Camera rig script not found in project: {rig['script']}")
    # Code an agent wrote that runs inside Blender is linted before Blender starts (studio/author_lint.py)
    from .author_lint import require_clean
    author_lint = require_clean(script, 'author', [path]) if script is not None else None
    rig_lint = require_clean(rig_script, 'rig', [path]) if rig_script is not None else None
    specs = _checked_specs(path, shot)
    # the frame pictures' readings against what the shot builds now: read for other words, or for another thing than the
    # move frames, is refused before Blender starts; things linked after the reading are said (frame_targets.reading_checks)
    from .frame_targets import reading_checks
    # (an authored checkpoint alone, stop_after='authored' for frames check, builds no camera: these are its report's to say)
    judge = _judged if stop_after is None else (lambda lines, severity, recovery: [f"{l['code']}: {l['text']}" for l in lines])
    reading_warnings = judge(reading_checks(path, shot), severity_map(project, shot), 'frames measure reads the pictures again, then frames apply')
    # the declared cheats the move carries: within their limits, never on what the shot's lines speak of (cheat_core)
    from .cheat_core import touch_lines
    reading_warnings += judge(touch_lines(path, shot), severity_map(project, shot), 'frames check says which cheat keeps the picture, '
                              'else draw the picture again')
    contrib_warnings = []
    contrib_table = _contrib_table(path, shot, specs, layout, contrib_warnings)
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
                   'base_version': base, 'project_sidecars': _sidecars(path),   # what the author step reads beside the shot (frames check)
                   'output_dir': str(staging), 'script_path': str(staging / 'author.py') if script is not None else None,
                   **({'layout_path': str(staging / 'layout.json')} if layout is not None else {}), **({'expect': expect} if expect else {}),
                   'probe': probe_inputs(path, snapshot, project['output']['fps'], style, (project['output']['width'], project['output']['height'])), 'contrib': contrib_table, 'record': record or {},
                   **({'stop_after': stop_after} if stop_after else {})}
            job['sandbox'] = _sandbox_rules(path, staging, script, companions, spec_paths, author_lint, rig_lint)
            write_json(staging / 'author_job.json', job)
            # The shot and style a version is built from are inputs: written before Blender runs, so a failed build keeps
            # them too and can be opened, fixed and committed in the workbench (2026-10-07: failed versions had none).
            snapshot['revision'] += 1
            write_json(staging / 'shot.snapshot.json', snapshot)
            write_json(staging / 'style.snapshot.json', style)
            trusted = _input_hashes(staging, job)   # what the host wrote; re-checked after the author's process ends
            blender = [blender_binary(), '--background', '--factory-startup', '--disable-autoexec']
            started = time.monotonic()
            # Stage 1: the author step in its own process (build_author.py); it hands on authored.raw.blend and nothing else.
            author_command = blender + ([str(base_scene)] if base_scene else []) + [
                '--python-exit-code', '1', '--python', str(REPO / 'studio/blender_ops/build_author.py'), '--', str(staging / 'author_job.json')]
            try:
                run_command(_os_sandbox(author_command, staging), staging / 'author.log', timeout=1800)
            except StudioError as error:
                raise _author_error(staging, error) from error
            changed = [name for name, digest in trusted.items() if not (staging / name).is_file() or file_hash(staging / name) != digest]
            if changed:
                raise StudioError('AUTHOR_SANDBOX_VIOLATION', f'the author step changed inputs the build trusts: {changed}',
                                  recovery='An author script writes only its own data files into the build output; the job, layout and specs are read-only')
            # Stage 2: the trusted build (build_scene.py) - on the base checkpoint first when a revision preserves data.
            command = blender + [str(base_scene if base_scene else staging / 'authored.raw.blend'), '--python-exit-code', '1',
                                 '--python', str(REPO / 'studio/blender_ops/build_scene.py'), '--', str(staging / 'author_job.json')]
            try:
                run_command(command, staging / 'build.log', timeout=1800)
            except StudioError as error:
                audit_path = staging / 'author_audit.json'
                if audit_path.is_file() and read_json(audit_path)['errors']:
                    first = read_json(audit_path)['errors'][0]
                    raise StudioError(first['code'], first['detail'], recovery='See author_audit.json in the failed version') from error
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
                # before the camera's own failure: the openings are checked before the camera is solved (show_scene.check_cuts)
                show_path = staging / 'show_report.json'
                if show_path.is_file() and read_json(show_path).get('gate_failures'):
                    raise StudioError('SHOW_CUT_MISSES', read_json(show_path)['gate_failures'][0]['why'],
                                      recovery='See show_report.json in the failed version (in_front, cutter, checked): the cut must '
                                               "uncover each part it opens from the picture's view - check the moment's parts and view, "
                                               'or open it another way (show)') from error
                move_path = staging / 'camera_move_report.json'
                if move_path.is_file() and not read_json(move_path).get('ok'):
                    raise StudioError('CAMERA_MOVE_FAILED', read_json(move_path)['error'],
                                      recovery='Check the move params reference objects/anchors that exist in the scene; see camera_move_report.json.') from error
                fill_path = staging / 'fill_report.json'
                if fill_path.is_file() and read_json(fill_path)['gate_failures']:
                    first = read_json(fill_path)['gate_failures'][0]
                    raise StudioError(first['gate'], 'Fill gates failed: ' + str(read_json(fill_path)['gate_failures'][:5]),
                                      recovery='Fill the seen level from the brief (or declare it void with a reason), move ambient copies off the subject, or revise the brief with the user') from error
                support_path = staging / 'support_report.json'
                if support_path.is_file() and read_json(support_path)['gate_failures']:
                    raise StudioError('SUBJECT_SINKS', read_json(support_path)['gate_failures'][0]['why'],
                                      recovery='See support_report.json in the failed version: pose the subject on its support at that '
                                               'frame, or move the support') from error
                kinematics_path = staging / 'kinematics_report.json'
                if kinematics_path.is_file() and any(r['interference'] for r in read_json(kinematics_path)['drives']):
                    raise StudioError('MECHANISM_INTERFERENCE', 'Coupled parts pass through each other: ' +
                                      str([c for r in read_json(kinematics_path)['drives'] for c in r['interference']][:5]),
                                      recovery='Check gear phases and centre distances (gear_core.planetary_layout), joint origins and axes') from error
                if 'CAMERA_ANCHOR' in str(error):
                    code = 'CAMERA_ANCHOR_OUT_OF_VIEW' if 'OUT_OF_VIEW' in str(error) else 'INPUT_INVALID'
                    raise StudioError(code, str(error)[-800:], recovery='Move the keys so the camera keeps camera.target_anchor in frame, or name the anchor it is about') from error
                if 'STAND_IN_DUPLICATE' in str(error):
                    raise StudioError('INPUT_INVALID', str(error)[-600:], recovery='One maker per element: drop scene.stand_ins for an '
                                                                                  'element the author builds, or the author\'s objects') from error
                if 'MECHANISM' in str(error):
                    raise StudioError('MECHANISM_INVALID', str(error)[-1200:], recovery='Fix the spec joints / couplings (kinematics_core.COUPLINGS)') from error
                frame_path = staging / 'frame_report.json'
                if frame_path.is_file() and read_json(frame_path)['gate_failures']:   # the probe runs last: nothing after it failed
                    first = read_json(frame_path)['gate_failures']
                    raise StudioError(first[0]['code'], 'Frame probe: ' + '; '.join(json.dumps(f)[:240] for f in first[:4]),
                                      recovery=f"Open the id images in the failed version's frame_probe/ to see what the camera shows; {first[0].get('hint', '')}") from error
                for code in ('EXPRESSIVE_APPLY_FAILED', 'ADDON_NOT_BUNDLED'):
                    if code in str(error):
                        raise StudioError(code, str(error)[-800:], recovery='Fix shot.render (grade / compositor / engine_settings / addons); '
                                                                            'see blender_ops/expressive_core.py for what each reads') from error
                if 'KEY_PART_UNKNOWN' in str(error):
                    raise StudioError('INPUT_INVALID', str(error)[-600:], recovery='shot.key_parts, concealed_parts, screen.subject and screen.keep ids must '
                                                                                  'name objects in the scene (studio id, inst/part, or a group id: <id>.n / <id>/part; '
                                                                                  'the message lists the closest built ids), and no object may be both key and concealed') from error
                rig_path = staging / 'camera_rig_report.json'
                if rig_path.is_file() and read_json(rig_path)['gate_failures']:
                    raise StudioError('CAMERA_RIG_GUARD_FAILED', 'Camera rig guards failed: ' + str(read_json(rig_path)['gate_failures'][:5]),
                                      recovery='Inspect camera_rig_report.json in the failed version; adjust offsets, lens, screen_anchor or guards.') from error
                raise
            if stop_after == 'authored':   # the authored checkpoint only (frames check): kept beside the versions, no version made
                if not (staging / 'authored.blend').is_file():
                    raise StudioError('SCENE_INVALID', 'the author step left no authored checkpoint')
                kept = versions / ('authored_' + staging.name.removeprefix('.building-'))
                staging.rename(kept)
                return {'shot_id': shot_id, 'authored': str(kept / 'authored.blend'), 'warnings': reading_warnings}
            inventory = read_json(staging / 'inventory.json')
            if not (staging / 'scene.blend').is_file() or not (staging / 'authored.blend').is_file() or not inventory['camera'] or inventory['missing_files']:
                raise StudioError('SCENE_INVALID', 'Author script did not produce a renderable scene')
            write_json(staging / 'dependencies.json', {'scene_sha256': file_hash(staging / 'scene.blend'), 'authored_sha256': file_hash(staging / 'authored.blend'),
                                                       'author_sha256': file_hash(staging / 'author.py') if script is not None else None,
                                                       'author_lines': _author_lines(staging / 'author.py') if script is not None else 0,
                                                       **({'layout_sha256': layout['layout_sha256'], 'exemplar_specs': layout['exemplar_specs']} if layout else {}),
                                                       'base_version': base, 'shot_hash': stable_hash(snapshot), 'style_hash': stable_hash(style),
                                                       'blender_version': inventory['blender_version'], 'external_files': inventory['external_files'],
                                                       **({'subject_specs': {k: spec_sha256(v) for k, v in sorted(specs.items())}} if specs else {}),
                                                       **({'motion_style_hash': stable_hash(motion_style)} if motion_style else {}),
                                                       **({'author_modules': {m.name: file_hash(m) for m in companions}} if companions else {}),
                                                       **({'project_sidecars': sidecars} if (sidecars := _sidecars(path)) else {}),
                                                       **({'author_lint_modules': author_lint['modules']} if author_lint else {}),
                                                       **({'rig_script_modules': rig_lint['modules']} if rig_lint else {}),
                                                       'isolation': {k: file_hash(REPO / 'studio' / k) for k in ('author_lint.py', 'blender_ops/sandbox.py',
                                                                                                                 'blender_ops/build_author.py', 'blender_ops/author_audit.py')},
                                                       **({'linked_libraries': audit['libraries']} if (audit := _audit(staging)) and audit['libraries'] else {})})
            display = read_json(staging / 'display_scale.json') if (staging / 'display_scale.json').is_file() else []
            display_warnings = []
            # a label is owed where the shot's lines speak of the thing (its size is then a claim); elsewhere a size cheat
            # is said, never refused (the user, 2026-10-14, on labelling exaggerated sizes: "상관없을듯")
            from .cheat_core import locked as locked_by_lines, _same
            spoken = locked_by_lines(path, shot)['ids'] if display else []
            bare = display_undisclosed(display, shot.get('labels'))
            for claimed, undisclosed in ((True, [r for r in bare if any(_same(r['id'], i) for i in spoken)]),
                                         (False, [r for r in bare if not any(_same(r['id'], i) for i in spoken)])):
                if not undisclosed:
                    continue
                message = ('drawn larger or smaller than life with no label saying so: '
                           + '; '.join(f"{r['id']} x{r['factor']:g} ({r['reason']})" for r in undisclosed[:6])
                           + ('' if claimed else " - no line of the shot speaks of it, so it is said, not refused"))
                if claimed and severity_map(project, shot).get('DISPLAY_SCALE_UNDISCLOSED') == 'error':
                    raise StudioError('DISPLAY_SCALE_UNDISCLOSED', message,
                                      recovery='add a label anchored to each (e.g. "확대 표현 ×3") while it is on screen, or build it at real size')
                display_warnings.append(f'DISPLAY_SCALE_UNDISCLOSED: {message}')
            write_json(staging / 'changes.json', {'base_version': base, 'version': version, 'created_at': now(), 'build_seconds': round(time.monotonic()-started, 3), 'author_original': str(script) if script is not None else None,
                                                   **({'diagnosis': diagnosis} if diagnosis else {}), **(record or {})})
            fidelity = None
            if (staging / 'fidelity_geometry.json').is_file():
                from .fidelity import build_report
                geometry = read_json(staging / 'fidelity_geometry.json')
                reports = [build_report(specs[g['subject_id']], g, path, staging) for g in geometry['subjects']]
                fidelity = {'passed': all(r['passed'] for r in reports), 'subjects': reports}
                write_json(staging / 'fidelity_report.json', fidelity)
            # gates that refuse the build are judged before the version is committed and selected: raised after the rename,
            # they left a selected vNNNN that render jobs then used (2026-10-12 audit). The except below files staging away.
            from . import patterns   # the shot against what its role's shots usually do (advice; SCREEN_PATTERN_OFF is taste)
            pattern = patterns.compare_shot(path, shot, staging)
            pattern_off = patterns.off_lines(pattern)
            if pattern_off and severity_map(project, shot).get('SCREEN_PATTERN_OFF') == 'error':
                raise StudioError('SCREEN_PATTERN_OFF', pattern_off[0][len('SCREEN_PATTERN_OFF: '):],
                                  recovery='change the framing, or declare the departure and its reason in screen.pattern_departs')
            from .world import load as load_world
            world = load_world(path) or {}
            look_warnings = (read_json(staging / 'look_report.json') if (staging / 'look_report.json').exists() else {}).get('warnings', [])
            casts = [w for w in look_warnings if str(w).startswith('LIGHT_CAST')]
            if casts and world.get('time_light') and severity_map(project, shot).get('WORLD_LIGHT_CAST') == 'error':
                raise StudioError('WORLD_LIGHT_CAST', casts[0], recovery='give the lamp a colour temperature near the world light '
                                  f"({world['time_light'].get('temperature_k', '?')} K) in the author script, or change world.time_light")
            # the pictured moments as built (frame_targets.gate_lines): a misread picture is refused, the rest is said
            from .frame_targets import gate_lines
            staged = lambda name: read_json(staging / name) if (staging / name).is_file() else {}  # noqa: E731
            picture_warnings = _judged(gate_lines(path, shot, staged('frame_report.json'), staged('camera_rig_report.json'),
                                                  staged('camera_move_report.json')), severity_map(project, shot),
                                       "see the failed version's camera_move_report.json and frame_report.json")
            # the environment the engine built (env_shell_report.json) and the scale the shot shows: under the policy
            shell_report = staged('env_shell_report.json')
            environment = [{'code': 'ENV_SHELL_HELD', 'text': shell_report['held'].split(': ', 1)[1]}] if shell_report.get('held') else []
            environment += [{'code': 'ENV_SHELL_GROUND_OUT', 'text': shell_report['ground_left_out']}] if shell_report.get('ground_left_out') else []
            environment += [{'code': 'ENV_SHELL_CLEAR_SHORT', 'text': shell_report['ground_clear_short']}] if shell_report.get('ground_clear_short') else []
            environment += [{'code': 'ENV_SHELL_DOME_SHORT', 'text': shell_report['dome_short']}] if shell_report.get('dome_short') else []
            if shell_report.get('built') and '@background' not in ((shot.get('screen') or {}).get('generate_only') or []):
                environment.append({'code': 'ENV_SHELL_CLAY', 'text': f"the shell's {', '.join(shell_report['built'])} stay as built (clay) in "
                                    'the final pixels: no model paints them in this shot (world resync, or a hybrid route)'})
            from .world import scale_ref_lines
            environment += [{'code': line.split(': ', 1)[0], 'text': line.split(': ', 1)[1]} for line in scale_ref_lines(shot, staged('frame_report.json'), world)]
            environment_warnings = _judged(environment, severity_map(project, shot), 'add the environment or a thing of known size, or depart '
                                           '(screen.pattern_departs item scale_ref_missing)')
            staging.rename(destination)
            for track in sorted((destination / 'simulation_cache').glob('*.json')):   # kept for the next build with the same inputs
                kept = path / 'cache' / 'simulation' / track.name
                if not kept.exists():
                    kept.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(track, kept)
            write_json(shot_path(path, shot_id), snapshot)
            rig_report = read_json(destination / 'camera_rig_report.json') if (destination / 'camera_rig_report.json').exists() else None
            frame_report = read_json(destination / 'frame_report.json') if (destination / 'frame_report.json').exists() else None
            move_report = read_json(destination / 'camera_move_report.json') if (destination / 'camera_move_report.json').exists() else {}
            warnings = frozen_warnings + contrib_warnings + (list(rig_report['warnings']) if rig_report else []) + (frame_report['warnings'] if frame_report else []) \
                + list(move_report.get('warnings') or [])   # e.g. FRAME_PATH_BLOCKED between a shot's pictures
            for report_name in ('support_report.json', 'show_report.json'):   # gates softened by policy: warnings
                warnings += (read_json(destination / report_name) if (destination / report_name).is_file() else {}).get('warnings', [])
            warnings += shown_through_lines(shot, read_json(destination / 'show_report.json') if (destination / 'show_report.json').is_file() else {})
            light_rows = None
            if (shot.get('screen') or {}).get('light'):   # the light the screen asks for, against the rig the look applied
                from .blender_ops.screen_core import judge_light
                look_report = read_json(destination / 'look_report.json') if (destination / 'look_report.json').exists() else {}
                missed, light_rows = judge_light(shot['screen']['light'], ((look_report.get('passes') or {}).get('lighting') or {}).get('rig'))
                warnings += [f"{f['code']}: {json.dumps({k: v for k, v in f.items() if k != 'code'})[:240]}" for f in missed]
            warnings += (_audit(destination) or {}).get('warnings', []) + display_warnings
            warnings += pattern_off + patterns.framing_lines(pattern) + patterns.project_records(path, shot) + plain_materials(shot, inventory)
            from .frame_targets import build_lines as frame_target_lines   # the built moments against the shot's frame pictures
            move_report = read_json(destination / 'camera_move_report.json') if (destination / 'camera_move_report.json').is_file() else {}
            warnings += frame_target_lines(path, shot, frame_report or {}, rig_report or {}, move_report)
            warnings += reading_warnings + picture_warnings + exposure_lines(path, shot, spec_paths)
            try:
                usual = patterns.section(patterns.load(patterns.library_name(path)), 'all')[1]['items'].get('void_share', {}).get('p90')
            except StudioError:
                usual = None
            from .world import context_void
            warnings += context_void(frame_report or {}, shot, world, usual, style)
            warnings += environment_warnings
            if casts and world.get('time_light'):   # one light for the render and the generated world around it (an error was raised above)
                warnings += [f'WORLD_LIGHT_CAST: {c}' for c in casts]
            if shot['camera'].get('energy') == 'high' and not rig:
                warnings.append('CAMERA_ENERGY_UNSUPPORTED: energy high without camera.rig; static keys rarely read as fast motion')
            from .contrib import auto_promote   # drafts this passing build used go into the library, pinned and traced
            promoted = auto_promote(path, contrib_table, {'shot_id': shot_id, 'scene_version': version, 'fidelity': fidelity})
            if promoted:
                warnings.append(f'CONTRIB_PROMOTED: {promoted} - pin these in place of @draft')
            return {'project_id': project['project_id'], 'shot_id': shot_id, 'scene_version': version, 'status': 'built',
                    'camera_rig': rig_report['summary'] if rig_report else None, 'warnings': warnings,
                    'frame': {**frame_report['summary'], 'seconds': frame_report['seconds']} if frame_report else None,
                    'screen': {**({k: frame_report['screen'][k] for k in ('targets', 'score', 'subject_speed_p95')} if frame_report and frame_report.get('screen') else {}),
                               **({'light': light_rows} if light_rows is not None else {}),
                               **({'pattern': pattern} if pattern else {})} or None,
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
    # 'scene' takes any content-layer field (project.SHOT_FIELD_LAYER) - what storyboard revise and workbench edits send -
    # plus the goal; a content field added to the schema is revisable without touching this line.
    allowed = {**METADATA_SCOPES, 'camera': {'camera'}, 'motion': {'actions'}, 'style': {'render'},
               'scene': set(SHOT_CONTENT) | {'goal'}, 'asset': {'asset_instances'}}
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
    if not script and scope == 'style':   # the look and the expressive settings are generated from shot.render on every build
        script = request_dir / 'patch.py'
        script.write_text('# style revision: shot.render changed; generate.py re-applies the look and the declared settings\n')
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
