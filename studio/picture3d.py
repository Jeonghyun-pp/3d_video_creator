"""3D-first frame pictures: a shot's pictures painted over its own 3D, so the picture and the build agree by construction.

The order per shot (picture3d run):
  candidates  the intent poses of each pictured moment (frames candidates); the user's pick, else the engine's first
  passes      at that pose and frame: clay, depth, lines, the id row and each claimed thing's state
              (blender_ops/picture_passes.py) - nothing heavy rendered
  claims      what the shot's lines claim about each thing they speak of: the state and the count the words say
              (one text-only read, cached; picture3d mark-claim corrects it)
  check       the 3D against the claims, from the 3D's own data: PICTURE3D_CLAIM_UNSEEN / STATE_DIFFERS / COUNT_DIFFERS
  paint       the image tool paints over the clay with the depth, the lines and a style picture (one at a time)
  register    the painting laid back onto the clay (one scale + shift, blender_ops/picture_register.py); moved past
              REGISTER_LIMIT it is painted again once, then the clay itself stands in (PICTURE_PAINT_SHIFTED)
  read        the painting read for the claimed things (advisory): dropped, another state or count than the 3D
              (PICTURE_PAINT_*) - painted again once with what it got wrong, within PAINT_MAX
  record      the registered painting becomes the moment's frame picture (concept add, derived from the clay), its
              reading taken from the 3D itself (frame_targets.derive_from_passes: no labeller), the pose pinned
              (chosen: by the user's pick, else by the agent); then frames apply
  sheet       clay | painting | the picture it replaces | the facts, per moment
Nothing is picked or approved here: a recorded picture is the agent's proposal (concept record `proposal`); the
storyboard is still approved in the user's words.

Why (2026-10-16, solver_regress pilot): pictures drawn from text and a 3D built apart agreed on 65 % of what was asked;
the camera can never fix a fin drawn deployed that the build folds. Painted over the 3D (clay + depth + lines + style)
a picture kept the build's parts and states; it came back moved 5-12 %, hence registration. The user: "자고 올테니
4단계 -> C단계 순서로 하고 전체 작업 끝내놔" (2026-10-17).
"""
from __future__ import annotations

import html
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .common import REPO, StudioError, blender_binary, file_hash, lock, now, read_json, run_command, stable_hash, write_json
from .project import load_project, load_shot, project_dir

PASS_HEIGHT = 1280       # the passes' height in px; the width follows the project's aspect (720 at 9:16)
REGISTER_LIMIT = 0.10    # a painting laid back onto its clay may leave at most this share of the frame unpainted on any side
                         # (filled by mirroring; an enlarged painting only crops): past it, or a fit at the search's edge, it is
                         # painted again. A working bound, not a measured one: the 2026-10-16 pilot's paintings came back moved
                         # 5-12 %; how wide a mirrored strip shows as a seam is for the sheet's eye to say.
PAINT_MAX = 2            # paintings per moment at most (one first, one again for a shift or for what the reading found)
MIN_VISIBLE_PX = 16      # a named thing counts as seen when its box is this many output px thick (and that squared in area)
PAINT_TIMEOUT_S = 900    # one painting's time limit (the pilot's took 53-60 s)
GENERATE_ONLY_MAX = 2    # shots of a reel drawn from text only, without 3D (project.policy.generate_only_max overrides)
PROBE_KEYS_MAX = 25      # named things one id pass separates (frame_probe's palette less subject and support)
CODEX_IMAGES = Path.home() / '.codex' / 'generated_images'   # where the image tool leaves what it made
KEEP = ("keep the camera, every object's position, size, silhouette, count and state exactly as in the first image; "
        "only change surfaces, lighting, sky and ground; no text, no labels, no captions.")
PAINT_PROMPT = ("The FIRST attached image is a grey 3D render (clay). The SECOND is its depth map (white = near, darker = farther, "
                "black = empty background). The THIRD is the exact outlines of every object in the render - follow these lines "
                "exactly. The FOURTH is a style reference only. Paint the FIRST image into a finished photoreal storyboard frame in "
                "the look of the FOURTH image: {keep} Every outline in the THIRD image must stay where it is. {shape}")
CLAIM_STATES = ('extended', 'retracted', 'contact', 'clear', 'on', 'off', 'open', 'closed', 'unknown')


def _folder(path, shot_id):
    return path / 'concepts' / shot_id / 'picture3d'


def _at(at):
    return f'{float(at):.2f}'


def _moment_dir(path, shot_id, at):
    return _folder(path, shot_id) / _at(at)


def _pass_size(project):
    out = project['output']
    return [max(2, round(PASS_HEIGHT * out['width'] / out['height'] / 2) * 2), PASS_HEIGHT]


# ---------------------------------------------------------------- poses
def poses(path, shot_id):
    """[{at, concept_id, frame, eye, aim, lens_mm, roll_deg, view, candidate, by}] per pictured moment: the user's newest
    pick of the moment (frames pick, on its current picture or one it supersedes) when one is recorded, else the
    engine's first candidate (frames candidates). A moment is its time: concept_id is its current picture. A pick whose
    candidate the sheet no longer holds as picked (made again since) is refused, never posed from another candidate."""
    from .concept import frame_at
    from .frame_targets import _store, user_pick
    sheet = path / 'concepts' / shot_id / 'candidates' / 'candidates.json'
    if not sheet.is_file():
        raise StudioError('INPUT_INVALID', f'{shot_id}: no candidates sheet to take the poses from',
                          recovery=f'studio frames candidates --project {path} --shot {shot_id}')
    found = read_json(sheet)
    shot = load_shot(path, shot_id)
    store = _store(path, shot_id)
    out = []
    for m in found['moments']:
        if not m.get('candidates'):
            continue
        current = (frame_at(path, shot_id, m['at']) or {}).get('concept_id') or m.get('concept_id')
        chosen = user_pick(path, shot_id, current, store)
        if chosen:
            cand = next((c for c in m['candidates'] if c['n'] == chosen.get('candidate')
                         and all(abs(float(a) - float(b)) < 1e-4 for a, b in zip(c['eye'], chosen.get('eye') or []))), None)
            if cand is None:
                raise StudioError('INPUT_INVALID', f"{shot_id} t={m['at']}: the user's pick (candidate {chosen.get('candidate')}) "
                                  'is not on the candidates sheet as picked (made again since)',
                                  recovery=f'studio frames pick --project {path} --shot {shot_id} --moment {m["at"]} (the user, again)')
            pose = {'eye': chosen['eye'], 'aim': chosen['aim'], 'lens_mm': chosen['lens_mm'], 'view': chosen['view']}
        else:
            cand = m['candidates'][0]
            pose = {'eye': cand['eye'], 'aim': cand['aim'], 'lens_mm': cand['lens_mm'], 'view': cand['view']}
        # how it was placed and where the subject stood: the pin the painted picture records keeps them (frames pick_pose)
        pose.update({k: (chosen or cand)[k] for k in ('placement', 'subject_at', 'subject_pose') if (chosen or cand).get(k)})
        frame = cand.get('frame', round(float(m['at']) * (shot['duration_frames'] - 1)))
        out.append({'at': float(m['at']), 'concept_id': current, 'frame': int(frame), **pose,
                    'roll_deg': float(cand.get('roll_deg') or 0.0), 'candidate': cand['n'], 'by': 'user' if chosen else 'agent',
                    **({'user_words': chosen.get('user_words')} if chosen else {})})
    return out


# ---------------------------------------------------------------- claims
def _claim_texts(path, shot, thing_id):
    """The sentences said about one thing in the shot: each claim whose `about` names it exactly, and the text of each
    line carrying such a claim (a line about something else says nothing of it)."""
    from .decisions import envelope
    body = {layer: (envelope(path, layer) or {}).get('body') or {} for layer in ('facts', 'script', 'shotlist')}
    entry = next((s for s in body['shotlist'].get('shots') or [] if s.get('shot_id') == shot['shot_id']), None)
    line_ids = list((entry or {}).get('line_ids') or [])
    lines = {l['line_id']: l for l in body['script'].get('lines') or []}
    claims = {c['claim_id']: c for c in body['facts'].get('claims') or []}
    texts = []
    for line_id in line_ids:   # a claim about exactly this thing, and the said line that carries it - no other line
        line = lines.get(line_id) or {}
        about = [claims.get(c) or {} for c in line.get('claim_ids') or []]
        about = [c for c in about if thing_id in (c.get('about') or [])]
        texts += [c.get('text') or '' for c in about] + ([line.get('text') or ''] if about else [])
    return list(dict.fromkeys(t for t in texts if t))


def claim_things(path, shot):
    """{thing id: {kind, ids, parts, owner, texts}} for what the shot's lines speak of (cheat_core.locked: the same
    things no cheat may change)."""
    from .cheat_core import locked
    from .explains import resolve
    from .world import load as load_world
    world = load_world(path) or {}
    out = {}
    for thing in locked(path, shot)['things']:
        resolved = resolve(path, thing['id'], world) or {}
        out[thing['id']] = {'kind': thing['kind'], 'ids': thing['ids'], 'parts': list(resolved.get('parts') or []),
                            'owner': resolved.get('subject'), 'words': resolved.get('words'),
                            'texts': _claim_texts(path, shot, thing['id'])}
    return out


def _claims_schema(ids):
    return {'type': 'object', 'additionalProperties': False, 'required': ['things'], 'properties': {'things': {
        'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['id', 'state', 'count'],
                                   'properties': {'id': {'enum': list(ids)}, 'state': {'enum': list(CLAIM_STATES)},
                                                  'count': {'type': ['integer', 'null']}}}}}}


def _claims_prompt(things):
    lines = ['Below are things a short explainer video shot speaks of, each with the sentences said about it. For each thing, '
             'say only what the sentences themselves state about it during this shot - never what is typical:',
             '- state: extended (swung out, deployed, unfolded), retracted (folded, stowed, lying along its body), contact '
             '(touching the ground or deck it stands on), clear (above it, not touching), on (burning, lit, firing, running), '
             'off, open, closed - or unknown when the sentences do not state one',
             '- count: how many of the thing the sentences state (an integer), or null when they state no number', '']
    for thing_id, row in things.items():
        lines.append(f'id={thing_id} ({row.get("words") or thing_id}): ' + ' / '.join(row['texts'] or ['(nothing said)']))
    return '\n'.join(lines)


def claims(project, shot_id, force=False, reader=None):
    """What the shot's lines claim about each thing they speak of: {thing: {..., state, count, by: reader|mark}} in
    concepts/<shot>/picture3d/claims.json - one text-only read (cached by the texts), corrected by mark-claim."""
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    things = claim_things(path, shot)
    file = _folder(path, shot_id) / 'claims.json'
    old = read_json(file) if file.is_file() else {}
    key = stable_hash({k: v['texts'] for k, v in things.items()})[:16]
    marks = old.get('marks') or {}
    answers = old.get('answers') if old.get('key') == key and not force else None
    asked = {k: v for k, v in things.items() if v['texts']}
    if answers is None:
        answers = {}
        if asked:
            from .screen_grammar import codex_json
            read = reader or codex_json
            target = _folder(path, shot_id) / 'claims_read.json'
            try:
                got = read([], _claims_prompt(asked), _claims_schema(asked), target)
            except Exception as exc:  # noqa: BLE001 - a failed read leaves every state unknown, said
                raise StudioError('LABELLER_FAILED', f'{shot_id}: the claims read failed: {exc}', retryable=True) from exc
            answers = {r['id']: {'state': r['state'], 'count': r['count']} for r in got['answer']['things']}
    rows = {}
    for thing_id, row in things.items():
        said = {**(answers.get(thing_id) or {'state': 'unknown', 'count': None}), 'by': 'reader'}
        if thing_id in marks:
            said = {**said, **{k: v for k, v in marks[thing_id].items() if k in ('state', 'count')}, 'by': 'mark', 'note': marks[thing_id].get('note')}
        rows[thing_id] = {**row, **said}
    write_json(file, {'key': key, 'answers': answers, 'marks': marks, 'things': rows, 'read_at': now()})
    return {'shot_id': shot_id, 'things': rows, 'artifacts': [str(file)]}


def mark_claim(project, shot_id, thing, state=None, count=None, note=None):
    """Correct what the lines claim about a thing (a person or the agent reading the lines): replaces the read."""
    path = project_dir(project)
    if state is not None and state not in CLAIM_STATES:
        raise StudioError('INPUT_INVALID', f'state {state!r}: one of {list(CLAIM_STATES)}')
    if not note or not note.strip():
        raise StudioError('INPUT_INVALID', 'mark-claim needs --note: who read the lines and why')
    things = claim_things(path, load_shot(path, shot_id))
    if thing not in things:
        raise StudioError('INPUT_INVALID', f'{shot_id}: the lines speak of {sorted(things)}, not {thing!r}')
    file = _folder(path, shot_id) / 'claims.json'
    data = read_json(file) if file.is_file() else {}
    data.setdefault('marks', {})[thing] = {**({'state': state} if state is not None else {}),
                                           **({'count': int(count)} if count is not None else {}), 'note': note.strip(), 'at': now()}
    write_json(file, data)
    return claims(project, shot_id)


# ---------------------------------------------------------------- passes
def _key_ids(path, shot, things):
    """The ids one id pass names, most important first (claimed parts and elements, must-see, the layout's things, the
    context), at most PROBE_KEYS_MAX."""
    from .frame_targets import context_elements, layout_elements
    move = (shot.get('camera') or {}).get('move') or {}
    ids = []
    for row in things.values():
        ids += row['parts'] or [i for i in row['ids'] if row['kind'] == 'element' and i != row.get('owner')]
    ids += [i for f in move.get('frames') or [] for i in f.get('must_see') or []]
    ids += [i for info in layout_elements(path, shot).values() for i in info['ids']]
    ids += [i for group in context_elements(path, shot).values() for i in group]
    ids = list(dict.fromkeys(str(i) for i in ids if i))
    return ids[:PROBE_KEYS_MAX], ids[PROBE_KEYS_MAX:]


def render_passes(path, shot_id, moments):
    """The passes (blender_ops/picture_passes.py) of `moments` - each {at, frame, out, eye/aim/lens_mm/view/roll_deg (a
    pose, subject_at: carried with its subject) or place (a frame row: where its numbers put the camera), outline: ids}
    - on the shot's authored checkpoint, with the claimed things' states. -> (the checkpoint, key ids, ids left out)."""
    from . import cheat_core
    from .frames_check import _job, authored_checkpoint
    shot = load_shot(path, shot_id)
    project_row = load_project(path)
    things = claim_things(path, shot)
    keys, dropped = _key_ids(path, shot, things)
    move = (shot.get('camera') or {}).get('move') or {}
    target = (move.get('params') or {}).get('target')
    subjects = list(dict.fromkeys([*([target] if isinstance(target, str) else []), *[s['subject_id'] for s in shot.get('subjects') or []]]))
    blend, _made, _stale = authored_checkpoint(path, shot_id)
    width, height = _pass_size(project_row)
    work = Path(tempfile.mkdtemp(prefix='.picture3d-', dir=path / 'concepts' / shot_id))
    try:
        job = _job(path, project_row, shot, work, cheat_core.locked(path, shot), 600.0, 1800.0)
        job['picture3d'] = {'width': width, 'height': height, 'ids': keys, 'subjects': subjects,
                            'states': {k: {'kind': v['kind'], 'ids': v['ids'], 'parts': v['parts'], 'owner': v['owner']} for k, v in things.items()},
                            'moments': list(moments)}
        write_json(work / 'job.json', job)
        run_command([blender_binary(), '--background', '--factory-startup', '--disable-autoexec', str(blend), '--python-exit-code', '1',
                     '--python', str(REPO / 'studio/blender_ops/picture_passes.py'), '--', str(work / 'job.json')],
                    work / 'picture_passes.log', timeout=1800)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return blend, keys, dropped


def passes(project, shot_id, moments=None):
    """Render each pictured moment's passes at its pose (blender_ops/picture_passes.py) into
    concepts/<shot>/picture3d/<at>/: clay, depth, lines, region, id.json, state.json."""
    path = project_dir(project)
    wanted = [m for m in poses(path, shot_id) if moments is None or _at(m['at']) in {_at(a) for a in moments}]
    if not wanted:
        raise StudioError('INPUT_INVALID', f'{shot_id}: no pictured moment with a pose')
    started = time.monotonic()
    blend, keys, dropped = render_passes(path, shot_id, [{**m, 'out': str(_moment_dir(path, shot_id, m['at']))} for m in wanted])
    for m in wanted:   # the pose the passes were drawn at (a pose recorded on another scene is carried with its subject there)
        folder = _moment_dir(path, shot_id, m['at'])
        drawn = (read_json(folder / 'id.json').get('pose') or {}) if (folder / 'id.json').is_file() else {}
        write_json(folder / 'pose.json', {**m, **{k: drawn[k] for k in ('eye', 'aim', 'roll_deg', 'subject_at', 'subject_pose') if k in drawn},
                                          'scene': str(blend.relative_to(path)), 'at_made': now()})
    return {'shot_id': shot_id, 'moments': [_at(m['at']) for m in wanted], 'keys': keys, 'keys_left_out': dropped,
            'seconds': round(time.monotonic() - started, 1), 'artifacts': [str(_folder(path, shot_id))]}


# ---------------------------------------------------------------- facts
def _visible(folder, ids):
    """{id: px box} of the ids the moment's id pass sees at least MIN_VISIBLE_PX thick."""
    info = read_json(folder / 'id.json') if (folder / 'id.json').is_file() else {}
    seen = info.get('visible') or {}
    return {i: seen[i] for i in ids if i in seen and seen[i]['thick_px'] >= MIN_VISIBLE_PX and seen[i]['px'] >= MIN_VISIBLE_PX ** 2}


def _current_ats(path, shot_id):
    """The moments the shot pictures now ({'0.00', ...}, from its candidates sheet and picks), or None without a sheet."""
    if not (path / 'concepts' / shot_id / 'candidates' / 'candidates.json').is_file():
        return None
    return {_at(p['at']) for p in poses(path, shot_id)}


def check(project, shot_id):
    """The 3D against what the lines claim, from the 3D's own data over the pictured moments (facts.json):
    PICTURE3D_CLAIM_UNSEEN a claimed thing shows at no moment; PICTURE3D_STATE_DIFFERS the claimed state's own kind
    (part_state_core.KIND: a claimed contact by contact, never by how far a part reaches) was measured and is the
    opposite at every moment it was measured (a state the shot changes is met when one moment has it; a kind nothing
    measures is never judged; a lit part is lit whether or not it is in frame); PICTURE3D_COUNT_DIFFERS the lines state
    another number than the 3D has in the claimed state (lit, deployed: part_state_core.in_state) at every pictured
    moment - than the spec builds when no state is said or measured. Only the
    moments the shot pictures now count (an older moment's folder stays on disk). Severity from the project's gate
    policy (all three broken: errors unless softened)."""
    from .blender_ops.part_state_core import MEASURE, differs, in_state, kind, normal
    from .gates import severity_for
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    claimed = (read_json(_folder(path, shot_id) / 'claims.json') if (_folder(path, shot_id) / 'claims.json').is_file() else {}).get('things') or {}
    current = _current_ats(path, shot_id)
    moments = sorted(p for p in _folder(path, shot_id).glob('*.*') if p.is_dir() and (p / 'state.json').is_file()
                     and (current is None or p.name in current))
    rows, findings = [], []
    for thing_id, said in claimed.items():
        ids = said['parts'] or said['ids']
        seen_at, states_at = [], []
        for folder in moments:
            state = (read_json(folder / 'state.json').get('things') or {}).get(thing_id) or {}
            visible = _visible(folder, ids)
            rendered = state.get('render') == 'on' and state.get('in_frame', True)   # lit out of frame is not seen
            if visible or rendered:
                seen_at.append(folder.name)
            measured = {k: state[k] for k in MEASURE.values() if normal(state.get(k)) != 'unknown'}
            states_at.append({'at': folder.name, 'measured': measured, 'visible': sorted(visible),
                              'parts': {p: {k: v for k, v in r.items() if k in ('extension', 'contact', 'gap_m', 'reach_share', 'render')}
                                        for p, r in (state.get('parts') or {}).items()}})
        row = {'thing': thing_id, 'kind': said['kind'], 'claimed': {'state': said.get('state'), 'count': said.get('count'), 'by': said.get('by')},
               'seen_at': seen_at, 'moments': states_at}
        if not seen_at and moments:
            findings.append({'code': 'PICTURE3D_CLAIM_UNSEEN', 'thing': thing_id,
                             'text': f'{shot_id}: the lines speak of {thing_id} and it shows at no pictured moment {[f.name for f in moments]}'})
        want = normal(said.get('state'))
        field = MEASURE.get(kind(want))
        got = [s['measured'][field] for s in states_at if field in s['measured']]
        if got and all(differs(want, g) for g in got):
            findings.append({'code': 'PICTURE3D_STATE_DIFFERS', 'thing': thing_id,
                             'text': f'{shot_id}: the lines say {thing_id} is {want}; the 3D has it {sorted(set(got))} at every pictured moment it measures'})
        count = said.get('count')
        if isinstance(count, int) and said['kind'] == 'feature' and said['parts']:
            # a count goes with its state: "3 engines relit" counts the lit ones (part_state_core.in_state), not the nine
            # built - met when one pictured moment has that many (a shot that changes it), judged at the moments some
            # part is in the state (none anywhere: the state is what differs, said once); with no state said, or no part
            # measured in it, the parts the spec builds
            counted = [n for n in (in_state(want, s['parts']) for s in states_at) if n is not None] if want != 'unknown' else []
            row['counted'] = counted
            if any(counted) and count not in counted:
                findings.append({'code': 'PICTURE3D_COUNT_DIFFERS', 'thing': thing_id,
                                 'text': f'{shot_id}: the lines say {count} of {thing_id} {want}; the 3D has {sorted(set(counted))} {want} '
                                         f'at the pictured moments'})
            elif not counted and count != len(said['parts']):
                findings.append({'code': 'PICTURE3D_COUNT_DIFFERS', 'thing': thing_id,
                                 'text': f'{shot_id}: the lines say {count} of {thing_id}; the spec builds {len(said["parts"])}'})
        rows.append(row)
    severity = severity_for(path, shot)
    errors = [f for f in findings if severity.get(f['code'], 'error') == 'error']
    warnings = [f for f in findings if f not in errors]
    out = {'shot_id': shot_id, 'checked_at': now(), 'things': rows, 'gate_failures': errors, 'warnings': warnings}
    write_json(_folder(path, shot_id) / 'facts.json', out)
    return {**out, 'artifacts': [str(_folder(path, shot_id) / 'facts.json')]}


# ---------------------------------------------------------------- paint
def _newest(after):
    files = [p for p in CODEX_IMAGES.rglob('*.png') if p.stat().st_mtime > after] if CODEX_IMAGES.is_dir() else []
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def _codex_binary():
    """The codex command, found as screen_grammar.codex_json finds it (PATH, else ~/.local/bin)."""
    return shutil.which('codex') or str(Path.home() / '.local/bin/codex')


def _codex_image(cmd, cwd, timeout):
    """Run one image request in its own process group: on the time limit the whole group is killed and waited for, so
    no child of it can still write an image once the paint lock is released. -> {returncode, stdout, stderr, timed_out}."""
    import os
    import signal
    child = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, start_new_session=True)
    try:
        out, err = child.communicate(timeout=timeout)
        return {'returncode': child.returncode, 'stdout': out, 'stderr': err, 'timed_out': False}
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = child.communicate()
        return {'returncode': child.returncode, 'stdout': out or '', 'stderr': err or '', 'timed_out': True}


def _event_images(stdout, after):
    """Image files the --json event stream names (any string value ending in an image suffix, in any event), that exist
    and were written after the request began: the painting this call made, not the newest in a shared folder."""
    found = []

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str) and v.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
            p = Path(v).expanduser()
            if p.is_file() and p.stat().st_mtime > after:
                found.append(p)
    for line in (stdout or '').splitlines():
        try:
            walk(json.loads(line))
        except ValueError:
            continue
    return list(dict.fromkeys(found))


def _style(path, shot_id, at):
    """The style picture a painting takes its look from: the shot's picked or beat concept, else a neighbour's painted
    picture, else the moment's own earlier picture (look only: the 3D parts stay the painting's)."""
    from .concept import _neighbour, frames, picked
    chosen = picked(path, shot_id)
    if chosen and (path / chosen['path']).is_file():
        return path / chosen['path']
    for step in (-1, 1):
        other = _neighbour(path, shot_id, step)
        made = sorted(_folder(path, other).glob('*/registered.png')) if other else []
        if made:
            return made[0]
    own = [c for c in frames(path, shot_id) if abs(c['frame']['at'] - float(at)) < 1e-6 and (path / c['path']).is_file()]
    return path / own[-1]['path'] if own else None


def paint_prompt(path, shot_id, finding=None):
    """The Test 2 prompt (2026-10-16 pilot) with the shot's claimed states named, and what the last painting got wrong."""
    claimed = (read_json(_folder(path, shot_id) / 'claims.json') if (_folder(path, shot_id) / 'claims.json').is_file() else {}).get('things') or {}
    # a claimed count goes with its state: "engines: on" alone let the painter light every engine (2026-10-17: the lines
    # relight 3 of 9, the painting lit 7)
    shape = '; '.join(f"{t}: {r['state']}" + (f" (exactly {r['count']} of them, no more)" if r.get('count') else '')
                      for t, r in claimed.items() if r.get('state') not in (None, 'unknown'))
    return PAINT_PROMPT.format(keep=KEEP, shape=(f'The 3D shows these as they are and they stay so: {shape}.' if shape else '')
                               + (f' The last painting got this wrong, keep it as the clay shows: {finding}.' if finding else ''))


def paint_into(target, attach, prompt, runner=None, label='paint'):
    """One painting by the image tool (Codex, the subscription) from `attach` (clay, depth, lines, a style picture) and
    `prompt`, copied to `target` while one painting at a time holds (.codex_image.lock): the image the call's --json
    events name, else the newest made since it began. -> (the image it was made from or None, how it was found, the
    call's tail when none). runner(cmd, cwd, timeout) -> {returncode, stdout, stderr, timed_out} (tests)."""
    started = time.time()
    with lock(REPO / '.studio' / 'codex_image.lock'), tempfile.TemporaryDirectory() as tmp:   # one painting at a time on this machine
        cmd = [_codex_binary(), 'exec', '--json', '--skip-git-repo-check', '-s', 'read-only', '-C', tmp,
               f'Use your image generation tool to create exactly one image.\n{prompt}\nThen reply with the word DONE.']
        for a in attach:
            cmd += ['-i', str(a)]
        try:
            done = (runner or _codex_image)(cmd, tmp, PAINT_TIMEOUT_S)
        except FileNotFoundError as exc:
            raise StudioError('PAINT_FAILED', f'{label}: the codex command is not installed ({exc})',
                              recovery='install the Codex CLI (codex) or put it on PATH') from None
        tail = ((done.get('stdout') or '')[-300:] + (done.get('stderr') or '')[-300:])
        made, how = None, None
        if done.get('timed_out'):
            tail = f'timeout {PAINT_TIMEOUT_S} s; ' + tail
        elif done.get('returncode') != 0:
            tail = f"exit {done.get('returncode')}; " + tail
        else:
            named = _event_images(done.get('stdout'), started)
            made, how = (named[-1], 'event') if named else (_newest(started), 'newest')   # the events name it; else the
            # newest image made since the request began, while this call holds the lock (another codex session on the
            # machine painting at the same time could be taken for it: paint.json records which way it was found)
            if made is not None:
                shutil.copy2(made, target)   # copied while the lock holds: nothing written after it counts
    return made, how, tail


def paint(project, shot_id, at, attempt=1, finding=None, runner=None):
    """Paint the moment's clay into a frame picture with the image tool (Codex, the subscription): clay + depth + lines +
    a style picture, the Test 2 prompt (2026-10-16 pilot) and the claimed states named; one painting at a time
    (.codex_image.lock), the image the call's --json events name (else the newest made since it began), a failed or
    timed-out call none. runner(cmd, cwd, timeout) -> {returncode, stdout, stderr, timed_out} (tests). -> paint_<attempt>.png, paint.json."""
    path = project_dir(project)
    folder = _moment_dir(path, shot_id, at)
    for name in ('clay.png', 'depth.png', 'lines.png'):
        if not (folder / name).is_file():
            raise StudioError('INPUT_INVALID', f'{shot_id} t={at}: no {name}', recovery=f'studio picture3d passes --project {project} --shot {shot_id}')
    style = _style(path, shot_id, at)
    prompt = paint_prompt(path, shot_id, finding)
    attach = [folder / 'clay.png', folder / 'depth.png', folder / 'lines.png'] + ([style] if style else [])
    started = time.time()
    target = folder / f'paint_{attempt}.png'
    made, how, tail = paint_into(target, attach, prompt, runner, f'{shot_id} t={at}')
    seconds = round(time.time() - started, 1)
    record = folder / 'paint.json'
    rows = read_json(record) if record.is_file() else {}
    row = {'attempt': attempt, 'seconds': seconds, 'ok': made is not None, 'prompt': prompt, **({'found_by': how} if made else {}),
           'attached': [str(a.relative_to(path)) if a.is_relative_to(path) else str(a) for a in attach], 'at': now(),
           **({'finding': finding} if finding else {}), **({} if made else {'tail': tail})}
    rows[str(attempt)] = row
    write_json(record, rows)
    if made is None:
        raise StudioError('PAINT_FAILED', f'{shot_id} t={at}: the image tool made no image ({tail[-200:]})', retryable=True)
    return {'shot_id': shot_id, 'at': _at(at), 'attempt': attempt, 'painting': str(target), 'seconds': seconds}


def register(project, shot_id, at, attempt=1):
    """Lay paint_<attempt>.png onto the moment's clay (blender_ops/picture_register.py) at the output size ->
    registered_<attempt>.png and the fit (over_limit: unpainted_share past REGISTER_LIMIT or a fit at the search's edge -
    painted again)."""
    path = project_dir(project)
    folder = _moment_dir(path, shot_id, at)
    painted = folder / f'paint_{attempt}.png'
    if not painted.is_file():
        raise StudioError('INPUT_INVALID', f'{shot_id} t={at}: no paint_{attempt}.png')
    out = load_project(path)['output']
    with tempfile.TemporaryDirectory() as tmp:
        job = {'items': [{'folder': str(folder), 'painted': str(painted), 'out': str(folder / f'registered_{attempt}.png')}],
               'output': [out['width'], out['height']], 'limit': REGISTER_LIMIT, 'result': str(Path(tmp) / 'result.json')}
        write_json(Path(tmp) / 'job.json', job)
        run_command([blender_binary(), '--background', '--factory-startup', '--python-exit-code', '1', '--python',
                     str(REPO / 'studio/blender_ops/picture_register.py'), '--', str(Path(tmp) / 'job.json')], Path(tmp) / 'log.txt', timeout=900)
        row = read_json(Path(tmp) / 'result.json')[0]
    return {'shot_id': shot_id, 'at': _at(at), 'attempt': attempt, **row}


# ---------------------------------------------------------------- read
def _read_schema(ids):
    return {'type': 'object', 'additionalProperties': False, 'required': ['answers'], 'properties': {'answers': {
        'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['id', 'present', 'state', 'count'],
                                   'properties': {'id': {'enum': list(ids)}, 'present': {'enum': ['yes', 'no', 'unsure']},
                                                  'state': {'enum': list(CLAIM_STATES)}, 'count': {'type': ['integer', 'null']}}}}}}


def read(project, shot_id, at, attempt=1, reader=None):
    """Read the registered painting for the things the lines claim that the 3D shows at this moment, and compare with
    the 3D (advisory): PICTURE_PAINT_DROPPED (the 3D shows it, the painting not), PICTURE_PAINT_STATE (the opposite of
    what the 3D measures of the same kind), PICTURE_PAINT_COUNT (another number than the 3D shows). -> read_<attempt>.json, the findings."""
    from .blender_ops.part_state_core import MEASURE, differs, kind
    path = project_dir(project)
    folder = _moment_dir(path, shot_id, at)
    image = folder / f'registered_{attempt}.png'
    claimed = (read_json(_folder(path, shot_id) / 'claims.json') if (_folder(path, shot_id) / 'claims.json').is_file() else {}).get('things') or {}
    state = (read_json(folder / 'state.json') if (folder / 'state.json').is_file() else {}).get('things') or {}
    asked = {}
    for thing_id, said in claimed.items():
        ids = said['parts'] or said['ids']
        visible = _visible(folder, ids)
        st = state.get(thing_id) or {}
        if visible or (st.get('render') == 'on' and st.get('in_frame', True)):   # lit out of frame: nothing to paint
            asked[thing_id] = {'words': said.get('words') or thing_id, 'visible': len(visible), 'state': state.get(thing_id) or {}}
    if not asked or not image.is_file():
        return {'shot_id': shot_id, 'at': _at(at), 'asked': [], 'findings': []}
    lines = ['Look at the attached image (one frame of an explainer video storyboard). Answer only from what is visible in this '
             'image. For each item: present (yes/no/unsure), state (extended = swung out/deployed, retracted = folded along the body, '
             'contact = touching the ground/deck, clear = above it, on = burning/lit/firing, off, open, closed, unknown) and count '
             '(how many are visible, or null). Items:']
    lines += [f'- id={k}: {v["words"]}' for k, v in asked.items()]
    from .screen_grammar import codex_json
    got = (reader or codex_json)([image], '\n'.join(lines), _read_schema(asked), folder / f'read_{attempt}.json')
    findings = []
    for answer in got['answer']['answers']:
        want = asked.get(answer['id'])
        if want is None:
            continue
        measured = want['state'].get(MEASURE.get(kind(answer['state'])) or '')   # the 3D's measure of the painted state's kind
        if answer['present'] == 'no':
            findings.append({'code': 'PICTURE_PAINT_DROPPED', 'thing': answer['id'], 'text': f"{answer['id']} shows in the 3D and not in the painting"})
        elif differs(answer['state'], measured):
            findings.append({'code': 'PICTURE_PAINT_STATE', 'thing': answer['id'],
                             'text': f"{answer['id']} painted {answer['state']}, the 3D has it {measured}"})
        if isinstance(answer.get('count'), int) and want['visible'] > 1 and answer['count'] != want['visible']:
            findings.append({'code': 'PICTURE_PAINT_COUNT', 'thing': answer['id'],
                             'text': f"{answer['id']}: {answer['count']} painted, {want['visible']} in the 3D"})
    write_json(folder / f'findings_{attempt}.json', findings)
    return {'shot_id': shot_id, 'at': _at(at), 'asked': sorted(asked), 'findings': findings}


# ---------------------------------------------------------------- record
def record(project, shot_id, at, attempt, fallback=False):
    """Make the registered painting (or, `fallback`, the clay itself) the moment's frame picture: concept add derived
    from the clay, the agent's proposal; its reading from the 3D (frame_targets.derive_from_passes), the pose pinned."""
    from .concept import add, frames
    from .frame_targets import derive_from_passes
    path = project_dir(project)
    folder = _moment_dir(path, shot_id, at)
    pose = read_json(folder / 'pose.json')
    image = folder / ('clay.png' if fallback else f'registered_{attempt}.png')
    current = [c for c in frames(path, shot_id) if abs(c['frame']['at'] - float(at)) < 1e-6]
    if not current:
        raise StudioError('INPUT_INVALID', f'{shot_id} t={at}: no frame picture at this moment to replace')
    role = current[-1]['frame']['role']
    # the clay standing in was painted by no one: no prompt, no fit (those belong to the painting that failed)
    fit = None if fallback else (read_json(folder / 'register.json') if (folder / 'register.json').is_file() else {}).get(f'paint_{attempt}.png')
    paint_row = {} if fallback else (read_json(folder / 'paint.json') if (folder / 'paint.json').is_file() else {}).get(str(attempt)) or {}
    made = add(project, shot_id, str(image), paint_row.get('prompt'), 'clay_fallback' if fallback else 'paint_over_3d', role,
               float(at) if role == 'key' else None, derived_from=str(folder / 'clay.png'),
               extra={'pose': {k: pose[k] for k in ('eye', 'aim', 'lens_mm', 'roll_deg', 'view', 'frame', 'candidate') if k in pose},
                      **({'register': {k: fit[k] for k in ('scale', 'shift_share', 'unpainted_share', 'subject_f1', 'crop') if k in fit}} if fit else {}),
                      'proposal': {'by': 'agent', 'note': 'painted over the 3D at its pose (picture3d run)', 'at': now()}})
    derived = derive_from_passes(project, shot_id, made['concept_id'], read_json(folder / 'id.json'), pose)
    return {'shot_id': shot_id, 'at': _at(at), 'concept_id': made['concept_id'], 'supersedes': (made.get('derived_from') or {}).get('supersedes'),
            'source': made['source'], 'reading': derived['reading']}


# ---------------------------------------------------------------- generate-only
def generate_only_max(path):
    """The reel's text-only shot limit: project.policy.generate_only_max when set (0 allows none), else GENERATE_ONLY_MAX."""
    value = (load_project(path).get('policy') or {}).get('generate_only_max')
    return GENERATE_ONLY_MAX if value is None else int(value)


def generated_shots(path):
    return sorted(p.parent.parent.name for p in (path / 'concepts').glob('*/picture3d/text_only.json'))


def generate_only(project, shot_id, note):
    """Mark a shot to be drawn from text only, without 3D: allowed when its lines claim nothing about any thing
    (PICTURE_GENERATED_CLAIMS, always an error: a picture invents what a claim needs true) and the reel stays within
    generate_only_max shots (PICTURE_GENERATED_LIMIT, budget). The sheet flags it (PICTURE_GENERATED)."""
    from .cheat_core import locked
    from .gates import severity_for
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    if not note or not note.strip():
        raise StudioError('INPUT_INVALID', 'generate-only needs --note: why this shot needs no 3D')
    things = locked(path, shot)['things']
    if things:
        raise StudioError('PICTURE_GENERATED_CLAIMS', f"{shot_id}: its lines speak of {[t['id'] for t in things]}; a picture drawn from "
                          'text alone would make up what they claim', recovery='keep it 3D-first (picture3d run)')
    others = [s for s in generated_shots(path) if s != shot_id]
    warnings = []
    if len(others) + 1 > generate_only_max(path):
        text = f'{shot_id}: {len(others) + 1} generate-only shots, the limit is {generate_only_max(path)} ({others})'
        if severity_for(path, shot).get('PICTURE_GENERATED_LIMIT', 'error') == 'error':
            raise StudioError('PICTURE_GENERATED_LIMIT', text, recovery='keep it 3D-first, or raise project.policy.generate_only_max')
        warnings.append(f'PICTURE_GENERATED_LIMIT (warning by policy): {text}')
    file = _folder(path, shot_id) / 'text_only.json'
    write_json(file, {'shot_id': shot_id, 'note': note.strip(), 'by': 'agent', 'at': now()})
    return {'shot_id': shot_id, 'generate_only': True, 'warnings': warnings, 'artifacts': [str(file)],
            'next': f'studio concept brief --project {project} --shot {shot_id} --frame start (attaches the neighbours\' pictures)'}


# ---------------------------------------------------------------- sheet
STATUS_WORDS = {'paint_over_3d': '3D 위에 칠함', 'clay_fallback': '클레이 그대로 (칠하기가 밀림)', 'generate_only': '생성 전용 (3D 없음)'}


def sheet(project, shots):
    """One page for the shots: per moment, clay | registered painting | the picture it replaced, with the facts check
    (concepts/picture3d_sheet.html, image paths relative)."""
    from .concept import concepts
    path = project_dir(project)
    out = path / 'concepts' / 'picture3d_sheet.html'
    rows = []
    for shot_id in shots:
        folder = _folder(path, shot_id)
        facts = read_json(folder / 'facts.json') if (folder / 'facts.json').is_file() else {}
        run_log = read_json(folder / 'run.json') if (folder / 'run.json').is_file() else {}
        records = {c['concept_id']: c for c in concepts(path, shot_id)}
        head = f"<h2 id='{shot_id}'>{shot_id}</h2>"
        if (folder / 'text_only.json').is_file():
            head += "<p class='bad'>생성 전용 (3D 없음) - PICTURE_GENERATED</p>"
        for f in facts.get('gate_failures') or []:
            head += f"<p class='bad'>{html.escape(f['code'])}: {html.escape(f['text'])}</p>"
        for f in facts.get('warnings') or []:
            head += f"<p class='warn'>{html.escape(f['code'])}: {html.escape(f['text'])}</p>"
        if run_log.get('error'):
            head += f"<p class='bad'>{html.escape(str(run_log['error']))[:400]}</p>"
        cells = []
        try:
            current = _current_ats(path, shot_id)
        except StudioError:   # its poses cannot be read (a pick no longer on the sheet): every moment shown, run.json says why
            current = None
        for moment in sorted(p for p in folder.glob('*.*') if p.is_dir() and (current is None or p.name in current)):
            done = (run_log.get('moments') or {}).get(moment.name) or {}
            rec = records.get(done.get('concept_id')) or {}
            old = records.get(done.get('supersedes')) or {}
            def img(p):
                return f"<img src='{html.escape(str(Path(p).relative_to(out.parent)) if Path(p).is_relative_to(out.parent) else str(p))}'>" if p and Path(p).is_file() else "<div class='none'>없음</div>"
            notes = [STATUS_WORDS.get(rec.get('source'), '기록 안 됨') + (f" ({rec.get('concept_id')})" if rec else '')]
            fit = rec.get('register') or {}
            if fit:
                notes.append(f"맞춤: 배율 {fit.get('scale')}, 이동 {fit.get('shift_share')}")
            notes += [f"{f['code']}: {f['text']}" for f in done.get('findings') or []]
            if done.get('error'):
                notes.append(str(done['error'])[:200])
            cells.append(f"<div class='m'><b>t={moment.name}</b> <span class='s'>후보 {done.get('candidate', '?')} ({'사용자 선택' if done.get('by') == 'user' else '엔진 선택'})</span>"
                         f"<div class='row'><figure>{img(moment / 'clay.png')}<figcaption>클레이</figcaption></figure>"
                         f"<figure>{img(path / rec['path'] if rec else None)}<figcaption>칠한 그림 (제안)</figcaption></figure>"
                         f"<figure>{img(path / old['path'] if old else None)}<figcaption>이전 그림</figcaption></figure></div>"
                         + ''.join(f"<div class='d'>{html.escape(n)}</div>" for n in notes) + '</div>')
        rows.append(f"<section>{head}{''.join(cells) or '<p>아직 없음</p>'}</section>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                   "<title>3D 위에 칠한 콘티</title><style>:root{--bg:#f6f6f4;--fg:#1d1d1f;--card:#fff;--line:#ddd;--mut:#666}"
                   "@media (prefers-color-scheme:dark){:root{--bg:#151517;--fg:#eee;--card:#1f1f22;--line:#333;--mut:#999}}"
                   "body{background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,'Apple SD Gothic Neo',sans-serif;margin:0 16px 40px}"
                   "section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;margin:14px 0}"
                   ".row{display:flex;gap:8px;overflow-x:auto}figure{margin:0;width:180px;flex:0 0 auto}img{width:180px;border-radius:6px}"
                   "figcaption,.s{font-size:11px;color:var(--mut)}.d{font-size:12px;margin-top:3px}.bad{color:#c33}.warn{color:#a70}"
                   ".none{width:180px;height:100px;border:1px dashed var(--line);display:flex;align-items:center;justify-content:center;color:var(--mut)}"
                   "</style></head><body><h1>3D 위에 칠한 콘티 (에이전트 제안, 승인 전)</h1>" + ''.join(rows) + '</body></html>', encoding='utf-8')
    return {'sheet': str(out), 'shots': list(shots), 'artifacts': [str(out)]}


# ---------------------------------------------------------------- run
def _ledger(path, shot_id):
    file = _folder(path, shot_id) / 'run.json'
    return file, (read_json(file) if file.is_file() else {'steps': {}, 'moments': {}})


def _moment(project, shot_id, at, ledger, steps):
    """Paint, register, read and record one moment within PAINT_MAX paintings: a painting moved past REGISTER_LIMIT is
    painted again; one the reading finds wrong is painted again with what it got wrong; when no painting registers, the
    clay itself is recorded (PICTURE_PAINT_SHIFTED)."""
    row = {'attempts': [], 'findings': []}
    finding = None
    chosen = None
    for attempt in range(1, PAINT_MAX + 1):
        steps['paint'](project, shot_id, at, attempt, finding)
        fit = steps['register'](project, shot_id, at, attempt)
        row['attempts'].append({'attempt': attempt, 'unpainted_share': fit.get('unpainted_share'), 'scale': fit.get('scale'),
                                'subject_f1': fit.get('subject_f1')})
        if fit.get('over_limit'):
            finding = None
            continue
        got = steps['read'](project, shot_id, at, attempt)
        row['findings'] = got['findings']
        chosen = attempt
        if got['findings'] and attempt < PAINT_MAX:
            finding = '; '.join(f['text'] for f in got['findings'])
            continue
        break
    if chosen is None:
        row['findings'] = [{'code': 'PICTURE_PAINT_SHIFTED', 'text': f'{shot_id} t={at}: every painting came back moved past what laying it '
                                                                  f'back can fill ({REGISTER_LIMIT} of the frame) or did not fit; the clay stands in'}]
        made = steps['record'](project, shot_id, at, PAINT_MAX, fallback=True)
    else:
        made = steps['record'](project, shot_id, at, chosen)
    row.update({'concept_id': made['concept_id'], 'supersedes': made.get('supersedes'), 'source': made['source'], 'done_at': now()})
    return row


POSE_KEYS = ('at', 'eye', 'aim', 'lens_mm', 'view', 'roll_deg', 'frame', 'candidate', 'by')   # what a painting was painted at


def _resume_key(pose, scene_sha):
    """What a finished moment was painted from: its pose (never the picture id, which the painting itself replaces) and
    the authored 3D it was rendered in - a moment whose 3D changed since is painted again."""
    return stable_hash({'pose': {k: pose.get(k) for k in POSE_KEYS}, 'scene': scene_sha})


def run(project, shots, resume=True, steps=None, candidates_fn=None, passes_fn=None, claims_fn=None, apply_fn=None):
    """The 3D-first order for each shot, resumable (run.json: a finished moment is not painted again while its pose is
    and its 3D the same, _resume_key); a shot that fails is logged and the next goes on. Nothing is picked, approved or paid for."""
    from .frame_targets import apply as frames_apply
    from .frames_check import authored_checkpoint, candidates as frames_candidates
    path = project_dir(project)
    steps = steps or {'paint': paint, 'register': register, 'read': read, 'record': record}
    out = {}
    for shot_id in shots:
        file, ledger = _ledger(path, shot_id)
        started = time.monotonic()
        try:
            if not (path / 'concepts' / shot_id / 'candidates' / 'candidates.json').is_file():
                (candidates_fn or frames_candidates)(project, shot_id)
            wanted = poses(path, shot_id)
            blend, _made, _stale = authored_checkpoint(path, shot_id)
            scene_sha = file_hash(blend)
            sheet_rows = read_json(path / 'concepts' / shot_id / 'candidates' / 'candidates.json').get('moments') or []
            for m in sheet_rows:   # a moment the camera found no pose for keeps its old picture: said, not skipped silently
                if not m.get('candidates'):
                    ledger['moments'][_at(m['at'])] = {'error': {'code': (m.get('error') or {}).get('code') or 'FRAME_UNSOLVED',
                                                                 'message': 'no pose to paint over: ' + str((m.get('error') or {}).get('message') or '')[:300]}}
            todo = [m for m in wanted if not (resume and (ledger['moments'].get(_at(m['at'])) or {}).get('concept_id')
                                               and (ledger['moments'][_at(m['at'])].get('pose') == _resume_key(m, scene_sha)))]
            if todo:
                (passes_fn or passes)(project, shot_id, [m['at'] for m in todo])
            (claims_fn or claims)(project, shot_id)
            facts = check(project, shot_id)
            ledger['facts'] = {'errors': len(facts['gate_failures']), 'warnings': len(facts['warnings'])}
            for m in todo:
                try:
                    row = _moment(project, shot_id, m['at'], ledger, steps)
                    ledger['moments'][_at(m['at'])] = {**row, 'pose': _resume_key(m, scene_sha), 'candidate': m['candidate'], 'by': m['by']}
                except StudioError as exc:
                    ledger['moments'][_at(m['at'])] = {'error': exc.as_dict(), 'candidate': m['candidate'], 'by': m['by']}
                write_json(file, ledger)
            if all((ledger['moments'].get(_at(m['at'])) or {}).get('concept_id') for m in wanted):
                (apply_fn or frames_apply)(project, shot_id)
                ledger['applied_at'] = now()
            ledger.pop('error', None)
        except StudioError as exc:
            ledger['error'] = exc.as_dict()
        except Exception as exc:  # noqa: BLE001 - one shot's failure is logged; the next shot goes on (unattended runs)
            ledger['error'] = {'code': 'PICTURE3D_FAILED', 'message': f'{type(exc).__name__}: {exc}'[:600]}
        ledger['seconds'] = round(time.monotonic() - started, 1)
        write_json(file, ledger)
        out[shot_id] = {k: ledger.get(k) for k in ('facts', 'error', 'seconds', 'applied_at')} | \
            {'moments': {a: {k: v for k, v in r.items() if k in ('concept_id', 'source', 'findings', 'error')} for a, r in ledger['moments'].items()}}
    sheet(project, shots)
    return {'shots': out, 'sheet': str(path / 'concepts' / 'picture3d_sheet.html')}


def show(project, shot_id):
    path = project_dir(project)
    folder = _folder(path, shot_id)
    return {'shot_id': shot_id, 'run': read_json(folder / 'run.json') if (folder / 'run.json').is_file() else None,
            'facts': read_json(folder / 'facts.json') if (folder / 'facts.json').is_file() else None,
            'claims': read_json(folder / 'claims.json') if (folder / 'claims.json').is_file() else None}


def register_commands(subparsers):
    parser = subparsers.add_parser('picture3d', help="3D-first frame pictures: painted over the shot's own 3D at its chosen pose")
    commands = parser.add_subparsers(dest='picture3d_command', required=True)

    def base(name, text):
        p = commands.add_parser(name, help=text)
        p.add_argument('--project', required=True)
        return p
    p = base('run', 'candidates -> passes -> claims -> check -> paint -> register -> read -> record -> frames apply, per shot (resumable)')
    p.add_argument('--shots', nargs='+', required=True); p.add_argument('--no-resume', action='store_true')
    p.set_defaults(handler=lambda a: run(a.project, a.shots, resume=not a.no_resume))
    p = base('passes', 'Render clay, depth, lines, the id row and the claimed states at each moment\'s pose')
    p.add_argument('--shot', required=True); p.add_argument('--at', type=float, action='append')
    p.set_defaults(handler=lambda a: passes(a.project, a.shot, a.at))
    p = base('claims', 'Read what the shot\'s lines claim about each thing (state, count)')
    p.add_argument('--shot', required=True); p.add_argument('--force', action='store_true')
    p.set_defaults(handler=lambda a: claims(a.project, a.shot, a.force))
    p = base('mark-claim', 'Correct what the lines claim about one thing')
    p.add_argument('--shot', required=True); p.add_argument('--thing', required=True)
    p.add_argument('--state', choices=CLAIM_STATES); p.add_argument('--count', type=int); p.add_argument('--note', required=True)
    p.set_defaults(handler=lambda a: mark_claim(a.project, a.shot, a.thing, a.state, a.count, a.note))
    p = base('check', 'The 3D against the claims (facts.json)')
    p.add_argument('--shot', required=True)
    p.set_defaults(handler=lambda a: check(a.project, a.shot))
    for name, fn, text in (('paint', paint, 'Paint one moment over its clay (the image tool)'),
                           ('register', register, 'Lay a painting onto its clay'), ('read', read, 'Read a registered painting (advisory)')):
        p = base(name, text)
        p.add_argument('--shot', required=True); p.add_argument('--at', type=float, required=True); p.add_argument('--attempt', type=int, default=1)
        p.set_defaults(handler=(lambda f: lambda a: f(a.project, a.shot, a.at, a.attempt))(fn))
    p = base('record', 'Make a registered painting the moment\'s frame picture (the agent\'s proposal)')
    p.add_argument('--shot', required=True); p.add_argument('--at', type=float, required=True); p.add_argument('--attempt', type=int, default=1)
    p.add_argument('--clay', action='store_true', help='record the clay itself (no painting registered)')
    p.set_defaults(handler=lambda a: record(a.project, a.shot, a.at, a.attempt, a.clay))
    p = base('generate-only', 'Mark a shot drawn from text only (no part or state claims; within the reel\'s limit)')
    p.add_argument('--shot', required=True); p.add_argument('--note', required=True)
    p.set_defaults(handler=lambda a: generate_only(a.project, a.shot, a.note))
    p = base('sheet', 'The review page: clay | painting | the picture it replaced, with the facts')
    p.add_argument('--shots', nargs='+', required=True)
    p.set_defaults(handler=lambda a: sheet(a.project, a.shots))
    p = base('show', 'A shot\'s run ledger, facts and claims')
    p.add_argument('--shot', required=True)
    p.set_defaults(handler=lambda a: show(a.project, a.shot))
