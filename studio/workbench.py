"""Workbench: a resident Blender session with typed tools, millisecond measurements and ID previews.

A session works on a copy of a shot version (or an empty scene for subject work). It is never the
source of truth: ``workbench commit`` writes the changed subject specs, turns the session's write ops
into a patch script and runs the normal ``shot build --base``; the build re-measures what the session
measured and fails with WORKBENCH_REPLAY_MISMATCH if the replay differs, so a version always comes
from the immutable pipeline.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import sys
import time

from .common import REPO, StudioError, blender_binary, blender_env, check_id, now, read_json, safe_path, write_json
from copy import deepcopy

from .project import load_project, load_shot, project_dir, shot_path, validate_shot

READY_TIMEOUT_S = 120
CALL_TIMEOUT_S = 600
SPEC_TOOLS = {'build_subject', 'set_spec_param', 'set_spec'}
SHOT_TOOLS = {'set_camera_rig', 'apply_shot'}  # written into shot.json at commit; the build re-bakes them
INTERNAL_WRITE = {'apply_shot'}   # called by the host itself (_forward), never by an agent
HOST_TOOLS = {'set_shot_value'}   # run on the host: validated by the shot edit grammar, then apply_shot in the session


def tool_kinds():
    """{tool: READ|WRITE|CONTROL} of the session tools, read from workbench_tools.TOOLS (no Blender import), plus the
    host tools - one list for the MCP description, the CLI and the regeneration rule."""
    import ast
    tree = ast.parse((REPO / 'studio/blender_ops/workbench_tools.py').read_text())
    table = next(n.value for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, 'id', '') == 'TOOLS' for t in n.targets))
    kinds = {k.value: v.elts[1].id.lower() for k, v in zip(table.keys, table.values)}
    return {**kinds, **{name: 'write' for name in HOST_TOOLS}}
OBJECT_TOOLS = {'set_transform': 'id', 'set_modifier_input': 'id'}


def session_dir(project, session_id):
    return safe_path(project_dir(project), f'workbench/{check_id(session_id)}')


def _session(project, session_id):
    path = session_dir(project, session_id) / 'session.json'
    if not path.is_file():
        raise StudioError('INPUT_INVALID', f'No workbench session {session_id}')
    return read_json(path)


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start(project, shot_id=None, version=None, subjects=None, allow_exec=False, user_words=None):
    if allow_exec:   # free bpy in the session is the user's call, in their words - the session can then never be committed
        from .generative.review import check_user_words
        user_words = check_user_words(user_words, 'workbench start --allow-exec')
    path = project_dir(project)
    output = load_project(path)['output']
    shot = load_shot(path, shot_id) if shot_id else None
    session_id = 'wb' + secrets.token_hex(4)
    directory = session_dir(path, session_id)
    directory.mkdir(parents=True)
    scene_copy = None
    if shot:
        version = check_id(version or shot.get('scene_version') or '')
        source = safe_path(shot_path(path, shot_id).parent, f'versions/{version}/authored.blend')
        if not (source.parent / 'scene.blend').is_file():
            raise StudioError('INPUT_INVALID', f'{shot_id} has no built version {version}')
        if not source.is_file():   # commit replays on the authored checkpoint (build_scene.py); a legacy version has none
            raise StudioError('BASE_NOT_REVISABLE', f'{version} was built before authored checkpoints',
                              recovery=f'Build {shot_id} fresh (shot build without --base), then start the workbench on the new version')
        scene_copy = directory / 'scene.blend'
        shutil.copy2(source, scene_copy)  # the session never opens the immutable version itself
        shutil.copy2(source, directory / 'authored.pristine.blend')   # what a shot edit regenerates from
    subject_ids = list(dict.fromkeys(list(subjects or []) + [s['subject_id'] for s in (shot or {}).get('subjects', [])]))
    spec_paths = {}
    from .subjects import load_spec
    for subject_id in subject_ids:
        spec_paths[subject_id] = str(directory / 'specs' / f'{subject_id}.json')
        write_json(Path(spec_paths[subject_id]), load_spec(path, subject_id))
    _refresh_contrib(path, directory)
    sock_dir = Path(tempfile.mkdtemp(prefix='stwb-'))  # AF_UNIX paths are limited to ~104 bytes on macOS
    token_path = directory / 'token'
    token_path.write_text(secrets.token_hex(16))
    os.chmod(token_path, 0o600)
    session = {'session_id': session_id, 'project_dir': str(path), 'shot_id': shot_id, 'base_version': version if shot else None,
               'socket': str(sock_dir / 's'), 'token_path': str(token_path), 'allow_exec': bool(allow_exec),
               **({'allow_exec_words': user_words} if allow_exec else {}), 'spec_paths': spec_paths,
               'output_size': [output['width'], output['height']], 'fps': output['fps'], 'shot': shot, 'started_at': now(), 'status': 'starting',
               **({'pristine': str(directory / 'authored.pristine.blend')} if shot else {})}
    write_json(directory / 'session.json', session)
    command = [blender_binary(), '--background', '--factory-startup', '--disable-autoexec']
    if scene_copy:
        command.append(str(scene_copy))
    command += ['--python', str(REPO / 'studio/blender_ops/workbench_server.py'), '--', str(directory / 'session.json')]
    if sys.platform == 'darwin':   # the studio's own sandbox (studio/broker.py): GPU yes, network no, writes only in the repository
        from .broker import profile
        command = ['sandbox-exec', '-p', profile(), *command]
    log = (directory / 'server.log').open('w')
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                               env=blender_env())   # no keys or tokens inside the session
    started = time.monotonic()
    while not (directory / 'ready').exists():
        if process.poll() is not None:
            raise StudioError('COMMAND_FAILED', 'Workbench server exited: ' + (directory / 'server.log').read_text()[-2000:])
        if time.monotonic() - started > READY_TIMEOUT_S:
            process.kill()
            raise StudioError('TIMEOUT', 'Workbench server did not become ready')
        time.sleep(0.05)
    session.update({'pid': process.pid, 'status': 'running', 'ready_seconds': round(time.monotonic() - started, 2)})
    write_json(directory / 'session.json', session)
    return {'session_id': session_id, 'status': 'running', 'pid': process.pid, 'ready_seconds': session['ready_seconds'],
            'subjects': subject_ids, 'base_version': session['base_version'], 'artifacts': [str(directory / 'session.json')]}


def _request(session, tool, args):
    payload = {'token': Path(session['token_path']).read_text().strip(), 'tool': tool, 'args': args or {}}
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(CALL_TIMEOUT_S)
    try:
        client.connect(session['socket'])
    except OSError as exc:
        raise StudioError('INPUT_INVALID', f"Workbench session {session['session_id']} is not running: {exc}") from exc
    with client:
        client.sendall((json.dumps(payload) + '\n').encode())
        data = b''
        while not data.endswith(b'\n'):
            chunk = client.recv(1 << 20)
            if not chunk:
                break
            data += chunk
    return json.loads(data.decode())


def _base_snapshot(path, session):
    return read_json(shot_path(path, session['shot_id']).parent / 'versions' / session['base_version'] / 'shot.snapshot.json')


def _authored_by_script(path, shot_id, version):
    """True when a real author script made this version's authored scene (a workbench patch only replays typed ops on
    top of its base, so look through it to the base)."""
    folder = shot_path(path, shot_id).parent / 'versions' / version
    if not read_json(folder / 'dependencies.json').get('author_sha256'):
        return False
    changes = read_json(folder / 'changes.json')
    if changes.get('workbench_session'):
        return bool(changes.get('base_version')) and _authored_by_script(path, shot_id, changes['base_version'])
    return True


def _regenerate(path, session, shot):
    """Make the session what a build of `shot` makes (apply_shot): the authored checkpoint, or the scene built fresh
    from its data when shot.scene differs from the base version's, the session's other write ops, the generators."""
    from .blender import _checked_specs, generator_inputs
    from .layout import lint as layout_lint, resolve as layout_resolve
    project = load_project(path)
    layout = None
    if shot.get('scene') != _base_snapshot(path, session).get('scene'):
        if _authored_by_script(path, session['shot_id'], session['base_version']):
            raise StudioError('INPUT_INVALID', f"{session['shot_id']} is built by an author script; its scene data cannot be rebuilt in a session "
                              '(a fresh build would drop the script) - change the scene in the script, or move it to shot.scene first')
        checked = layout_lint(path, shot)
        if checked['errors']:
            raise StudioError('LAYOUT_INVALID', '; '.join(checked['errors'][:6]))
        layout = layout_resolve(path, shot)['scene']
    _checked_specs(path, shot)
    job = generator_inputs(path, project, session['shot_id'], shot, session.get('spec_paths', {}))
    replay_ops = [op for op in effective_ops(read_ops(path, session['session_id'])) if op['tool'] not in SHOT_TOOLS]
    return _forward(session, 'apply_shot', {'shot': shot, 'job': job, 'replay_ops': replay_ops, 'pristine': session['pristine'], 'layout': layout})


def set_shot_value(project, session_id, ops):
    """Change any value of the shot in the session - the storyboard edit grammar (word-ops or set/add/remove by path,
    refused when nothing reads the value) - and regenerate the session the way a build would."""
    from .storyboard import apply_ops
    path = project_dir(project)
    session = _session(path, session_id)
    if not session.get('shot_id'):
        raise StudioError('INPUT_INVALID', 'set_shot_value needs a session started on a shot version')
    ops = ops if isinstance(ops, list) else [ops]
    if any(op['tool'] == 'set_camera_keys' for op in effective_ops(read_ops(path, session_id))):
        raise StudioError('INPUT_INVALID', 'this session set camera keys by hand, which stop the build from applying actions and camera data; '
                          'restore to before them, or change /camera/keys with set_shot_value instead')
    shot = _forward(session, 'current_shot', {})['shot']
    change, said = apply_ops(shot, ops)
    new = {**deepcopy(shot), **change}
    validate_shot(new)
    result = _regenerate(path, session, new)
    return {**result, 'changes': said}


def _refresh_contrib(path, directory, extra=None):
    """The contrib entries the session's specs (and a spec edit about to run) use, resolved and checked on the host
    (studio/contrib.py) into contrib.json, which the session reads before it builds - as a build does with job['contrib']."""
    from .contrib import refs_in, resolve
    refs = set().union(*(refs_in(read_json(f)) for f in sorted((Path(directory) / 'specs').glob('*.json')))) | refs_in(extra or {})
    write_json(Path(directory) / 'contrib.json', resolve(path, refs))


def _forward(session, tool, args):
    reply = _request(session, tool, args)
    if not reply.get('ok'):
        raise StudioError('WORKBENCH_TOOL_FAILED', f"{tool}: {reply.get('error')}", recovery='Fix the arguments; the session state is unchanged by a failed tool')
    return reply['result']


def call(project, session_id, tool, args=None, raw=False):
    if tool in INTERNAL_WRITE:
        raise StudioError('INPUT_INVALID', f'{tool} is internal: the session applies a shot only through set_shot_value',
                          recovery='Change shot values with set_shot_value {ops: [...]}')
    session = _session(project, session_id)
    started = time.perf_counter()
    if tool == 'set_shot_value':
        result = set_shot_value(project, session_id, (args or {}).get('ops') or (args or {}))
        return {'session_id': session_id, 'tool': tool, 'round_trip_ms': round((time.perf_counter() - started) * 1000, 2), 'result': result}
    if tool in SPEC_TOOLS:   # a spec edit may name a contrib entry: resolve it before the session builds with it
        _refresh_contrib(project_dir(project), session_dir(project, session_id), args)
    if tool == 'set_camera_keys' and session.get('shot_id') and _forward(session, 'current_shot', {})['generated']:
        raise StudioError('INPUT_INVALID', 'this session edited the shot; hand camera keys would stop actions - change /camera/keys with set_shot_value')
    reply = _request(session, tool, args)
    round_trip = round((time.perf_counter() - started) * 1000, 2)
    if not reply.get('ok'):
        raise StudioError('WORKBENCH_TOOL_FAILED', f"{tool}: {reply.get('error')}", recovery='Fix the arguments; the session state is unchanged by a failed tool')
    result = reply['result']
    if (tool_kinds().get(tool) == 'write' and tool != 'apply_shot' and session.get('shot_id')
            and _forward(session, 'current_shot', {})['generated']):   # one state rule: authored + ops + shot, regenerated
        result = {**(result or {}), 'regenerated': _regenerate(project_dir(project), session, _forward(session, 'current_shot', {})['shot'])}
    if tool == 'subject_report' and not raw:
        from .fidelity import build_report
        directory = session_dir(project, session_id)
        spec = read_json(directory / 'specs' / f"{result['subject_id']}.json")
        out = directory / 'report'; out.mkdir(exist_ok=True)
        report = build_report(spec, result['geometry'], project_dir(project), out)
        result = {'subject_id': result['subject_id'], 'passed': report['passed'], 'failures': report['failures'], 'summary': report['summary'],
                  'checks': report['checks'], 'whole': result['geometry']['whole'], 'overlays': sorted(str(p) for p in out.glob('silhouette_*.png'))}
    if tool == 'variant_save':
        result['metrics'] = _record_variant(project, session_id, args['name'], args.get('note', ''), result.get('rig'))
    return {'session_id': session_id, 'tool': tool, 'server_ms': reply.get('ms'), 'round_trip_ms': round_trip, 'result': result}


def _variants_path(project, session_id):
    return session_dir(project, session_id) / 'variants.json'


def _record_variant(project, session_id, name, note, rig=None):
    """Fidelity summary of every session subject at the moment a variant is saved (host-judged)."""
    session = _session(project, session_id)
    metrics = {}
    for subject_id in session.get('spec_paths', {}):
        report = call(project, session_id, 'subject_report', {'subject_id': subject_id})['result']
        metrics[subject_id] = {'passed': report['passed'], 'summary': report['summary'], 'failures': report['failures'][:6]}
    path = _variants_path(project, session_id)
    variants = read_json(path) if path.is_file() else {}
    variants[name] = {'note': note, 'metrics': metrics, 'camera_rig': rig, 'saved_at': now()}
    write_json(path, variants)
    return metrics


def variants(project, session_id):
    path = _variants_path(project, session_id)
    return {'session_id': session_id, 'variants': read_json(path) if path.is_file() else {}}


def compare(project, session_id, names, frames=(1,), views=('shot',), size=360):
    """Render each saved variant at the same frames/views, side by side, then return to the current state.

    Rows = variants, columns = view@frame; writes workbench/<session>/compare_<n>.jpg. Exploration only:
    nothing here creates a version or spends the repair budget."""
    from PIL import Image, ImageDraw
    saved = variants(project, session_id)['variants']
    missing = [n for n in names if n not in saved]
    if missing or len(names) < 2:
        raise StudioError('INPUT_INVALID', f'compare needs >= 2 saved variants (missing {missing}; saved {sorted(saved)})')
    directory = session_dir(project, session_id)
    call(project, session_id, 'checkpoint', {'name': '__compare_return'})
    rows = {}
    try:
        for name in names:
            call(project, session_id, 'variant_restore', {'name': name})
            shot = call(project, session_id, 'preview', {'views': list(views), 'passes': ['shaded'], 'size': size, 'frames': list(frames)})['result']
            rows[name] = {key: images['shaded'] for key, images in sorted(shot['images'].items(), key=lambda kv: (kv[0].split('@')[0], int(kv[0].split('@')[1])))}
    finally:
        call(project, session_id, 'restore', {'name': '__compare_return'})
    columns = list(next(iter(rows.values())).keys())
    tiles = {(r, c): Image.open(rows[r][c]).convert('RGB') for r in rows for c in columns if c in rows[r]}
    w = max(t.width for t in tiles.values()); h = max(t.height for t in tiles.values())
    label_w, header_h = 150, 24
    sheet = Image.new('RGB', (label_w + w * len(columns), header_h + h * len(rows)), (30, 30, 30))
    draw = ImageDraw.Draw(sheet)
    for j, column in enumerate(columns):
        draw.text((label_w + j * w + 6, 6), column, fill=(230, 230, 230))
    for i, name in enumerate(rows):
        draw.text((6, header_h + i * h + 6), name, fill=(255, 210, 120))
        for j, column in enumerate(columns):
            if (name, column) in tiles:
                sheet.paste(tiles[(name, column)], (label_w + j * w, header_h + i * h))
    number = len(list(directory.glob('compare_*.jpg'))) + 1
    out = directory / f'compare_{number:02d}.jpg'
    sheet.save(out, quality=88)
    return {'session_id': session_id, 'sheet': str(out), 'rows': list(rows), 'columns': columns,
            'metrics': {n: saved[n] for n in names}, 'artifacts': [str(out)]}


def stop(project, session_id):
    session = _session(project, session_id)
    try:
        _request(session, 'shutdown', {})
    except (StudioError, OSError):
        pass
    deadline = time.monotonic() + 10
    while session.get('pid') and _alive(session['pid']) and time.monotonic() < deadline:
        time.sleep(0.05)
    if session.get('pid') and _alive(session['pid']):
        os.kill(session['pid'], 9)
    shutil.rmtree(Path(session['socket']).parent, ignore_errors=True)
    session['status'] = 'stopped' if session.get('status') != 'committed' else 'committed'
    write_json(session_dir(project, session_id) / 'session.json', session)
    return {'session_id': session_id, 'status': session['status']}


def read_ops(project, session_id):
    path = session_dir(project, session_id) / 'ops.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.is_file() else []


SAVE_TOOLS = {'checkpoint': lambda a: a['name'], 'variant_save': lambda a: f"variant.{a['name']}"}
RESTORE_TOOLS = {'restore': lambda a: a['name'], 'variant_restore': lambda a: f"variant.{a['name']}"}


def effective_ops(ops):
    """Write ops in order; a restore returns to exactly the op list saved with its checkpoint (so nested
    rewinds such as compare's variant hops replay correctly); failed and read calls are dropped."""
    out, marks = [], {}
    for op in ops:
        if not op.get('ok', True):
            continue
        if op['tool'] in SAVE_TOOLS:
            marks[SAVE_TOOLS[op['tool']](op['args'])] = list(out)
        elif op['tool'] in RESTORE_TOOLS:
            out = list(marks[RESTORE_TOOLS[op['tool']](op['args'])])
        elif op.get('kind') == 'write':
            out.append({'tool': op['tool'], 'args': op['args']})
    return out


PATCH = '''"""Generated by workbench commit {session_id}: replays the session's write ops on the base version."""
import json
from pathlib import Path
from workbench_tools import replay

OPS = json.loads({ops!r})
specs = {{sid: json.loads(Path(p).read_text()) for sid, p in STUDIO_JOB.get('subject_spec_paths', {{}}).items()}}
replay(STUDIO_JOB, OPS, specs)
'''


def replayable(ops):
    """No exec that ran: the server marks the session non-replayable before the code runs, so an exec that raised halfway
    (and may have changed the scene) counts too; an exec refused before running (session without --allow-exec) does not."""
    return not any((op['tool'] == 'exec' and op.get('ok')) or op.get('non_replayable') for op in ops)


def commit(project, session_id, diagnosis=None, chosen_variant=None, why=None):
    path = project_dir(project)
    directory = session_dir(path, session_id)
    session = _session(path, session_id)
    if not session.get('shot_id'):
        raise StudioError('INPUT_INVALID', 'Only a session started on a shot version can be committed; use the specs it wrote for subject work')
    ops = read_ops(path, session_id)
    if not replayable(ops):
        raise StudioError('WORKBENCH_REPLAY_MISMATCH', 'Session used exec; its changes cannot be replayed. Express them with typed tools in a new session.')
    effective = effective_ops(ops)
    if not effective:
        raise StudioError('INPUT_INVALID', 'Session has no write operations to commit')
    considered = sorted(variants(path, session_id)['variants'])   # the arguments first, then the state they apply to
    if chosen_variant and chosen_variant not in considered:
        raise StudioError('INPUT_INVALID', f'{chosen_variant} is not a saved variant ({considered})')
    if chosen_variant and not (why and len(why.strip()) >= 8):
        raise StudioError('INPUT_INVALID', 'Choosing a variant needs --why (what made it better than the others)')
    shot = load_shot(path, session['shot_id'])
    started_revision = (session.get('shot') or {}).get('revision', shot['revision'])
    if shot['revision'] != started_revision:   # the session's edits were made on the shot as it was when the session started
        raise StudioError('REVISION_CONFLICT', f"{session['shot_id']} changed since this session started (revision {started_revision} -> {shot['revision']}); "
                          'start a new session on the current version')
    current = call(path, session_id, 'current_shot')['result']
    generated = current['generated']
    shot_subjects = {s['subject_id'] for s in shot.get('subjects', [])}
    changed = sorted({op['args']['subject_id'] for op in effective if op['tool'] in SPEC_TOOLS})
    outside = [s for s in changed if s not in shot_subjects]
    if outside:
        raise StudioError('INPUT_INVALID', f'Session changed subjects the shot does not declare: {outside}')
    object_ids = sorted({op['args'][key] for op in effective for tool, key in OBJECT_TOOLS.items() if op['tool'] == tool})
    camera_ops = [op for op in effective if op['tool'] == 'set_camera_keys']
    rig_ops = [op for op in effective if op['tool'] in SHOT_TOOLS]
    if not rig_ops:  # a committed rig is re-baked by the build and verified by camera_rig_report instead
        object_ids += sorted({op['args'].get('camera') or '@camera' for op in camera_ops})
    shot_override, base = None, session['base_version']
    if generated:
        # The session edited the shot and ran the build's generators: the shot it ended with is the one committed (set_shot_value
        # and set_camera_rig folded in order, restores included), built fresh when its scene data changed, and the build must
        # end where the session ended - after its generators, not before.
        shot_override = {**deepcopy(current['shot']), 'revision': shot['revision']}
        validate_shot(shot_override)
        if shot_override.get('scene') != _base_snapshot(path, session).get('scene'):
            base = None
        count = shot_override['duration_frames']
        expect = {'after': call(path, session_id, 'generated_snapshot', {'frames': sorted({0, count // 2, count - 1}), 'subject_ids': changed,
                                                                         'object_ids': object_ids})['result']}
    else:
        if rig_ops:
            shot_override = deepcopy(shot)
            shot_override['camera']['rig'] = rig_ops[-1]['args']['rig']
            shot_override['camera']['movement'] = 'rig'
            validate_shot(shot_override)
        expect = call(path, session_id, 'replay_snapshot', {'subject_ids': changed, 'object_ids': object_ids})['result']
    from .subjects import lint_spec, spec_path
    backups = {}
    for subject_id in changed:
        spec = read_json(directory / 'specs' / f'{subject_id}.json')
        lint = lint_spec(spec, path)
        if lint['errors']:
            raise StudioError('SUBJECT_SPEC_INVALID', f'{subject_id}: ' + '; '.join(lint['errors'][:6]))
        backups[subject_id] = read_json(spec_path(path, subject_id))
    patch = directory / 'patch.py'
    patch.write_text(PATCH.format(session_id=session_id, ops=json.dumps(effective)))
    write_json(directory / 'commit_expect.json', expect)
    for subject_id in changed:
        write_json(spec_path(path, subject_id), read_json(directory / 'specs' / f'{subject_id}.json'))
    record = {'workbench_session': session_id, **({'variants_considered': considered, 'chosen_variant': chosen_variant, 'why': why}
                                                  if considered or chosen_variant else {})}
    from .blender import build_shot
    try:
        result = build_shot(path, session['shot_id'], patch, base=base, expect=expect, diagnosis=diagnosis, record=record,
                            shot_override=shot_override, expected_revision=started_revision)
    except Exception:
        for subject_id, spec in backups.items():  # the spec files follow the versions, never a failed attempt
            write_json(spec_path(path, subject_id), spec)
        raise
    session.update({'status': 'committed', 'committed_version': result['scene_version'], 'committed_at': now()})
    write_json(directory / 'session.json', session)
    warnings = list(result.get('warnings', []))
    if camera_ops and (shot['camera'].get('rig') or shot['camera'].get('move')) and not rig_ops:
        warnings.append('CAMERA_KEYS_OVERRIDDEN_BY_RIG: this shot bakes its camera.rig / camera.move after the patch; explore with set_camera_rig '
                        'or set_shot_value /camera/move/... instead of set_camera_keys')
    return {**result, 'warnings': warnings, 'session_id': session_id, 'replayed_ops': len(effective), 'specs_written': changed, 'shot_edited': generated,
            'built_fresh': generated and base is None,
            'camera_keys_replayed': bool(camera_ops), 'variants_considered': considered, 'chosen_variant': chosen_variant}


def list_sessions(project):
    root = project_dir(project) / 'workbench'
    rows = []
    for path in sorted(root.glob('wb*/session.json')) if root.is_dir() else []:
        s = read_json(path)
        rows.append({'session_id': s['session_id'], 'shot_id': s.get('shot_id'), 'status': s.get('status'),
                     'running': bool(s.get('pid')) and _alive(s['pid']) and s.get('status') == 'running', 'ops': len(read_ops(project, s['session_id']))})
    return {'sessions': rows}


def register_commands(subparsers):
    parser = subparsers.add_parser('workbench', help='Resident Blender session: typed tools, ID previews, replayed commits')
    commands = parser.add_subparsers(dest='workbench_command', required=True)
    p = commands.add_parser('start'); p.add_argument('--project', required=True); p.add_argument('--shot'); p.add_argument('--version')
    p.add_argument('--subject', action='append', default=[]); p.add_argument('--allow-exec', action='store_true')
    p.add_argument('--user-words', help="with --allow-exec: the user's own words allowing free bpy in this session")
    p.set_defaults(handler=lambda a: start(a.project, a.shot, a.version, a.subject, a.allow_exec, a.user_words))
    p = commands.add_parser('call'); p.add_argument('--project', required=True); p.add_argument('--session', required=True)
    p.add_argument('--tool', required=True); p.add_argument('--args', default='{}')
    p.set_defaults(handler=lambda a: call(a.project, a.session, a.tool, json.loads(a.args)))
    p = commands.add_parser('stop'); p.add_argument('--project', required=True); p.add_argument('--session', required=True)
    p.set_defaults(handler=lambda a: stop(a.project, a.session))
    p = commands.add_parser('commit'); p.add_argument('--project', required=True); p.add_argument('--session', required=True); p.add_argument('--diagnosis')
    p.add_argument('--chosen-variant'); p.add_argument('--why')
    p.set_defaults(handler=lambda a: commit(a.project, a.session, a.diagnosis, a.chosen_variant, a.why))
    p = commands.add_parser('variants', help='saved exploration variants with their fidelity summaries')
    p.add_argument('--project', required=True); p.add_argument('--session', required=True)
    p.set_defaults(handler=lambda a: variants(a.project, a.session))
    p = commands.add_parser('compare', help='render saved variants at the same frames side by side (contact sheet)')
    p.add_argument('--project', required=True); p.add_argument('--session', required=True); p.add_argument('--names', required=True)
    p.add_argument('--frames', default='1'); p.add_argument('--views', default='shot'); p.add_argument('--size', type=int, default=360)
    p.set_defaults(handler=lambda a: compare(a.project, a.session, a.names.split(','), [int(f) for f in a.frames.split(',')], a.views.split(','), a.size))
    p = commands.add_parser('list'); p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: list_sessions(a.project))
