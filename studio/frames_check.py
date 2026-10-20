"""frames check: can a shot's frame pictures be shot - right after they are read, before any build.

  studio frames check --project P --shot S [--apply] [--moment-s 420] [--shot-s 1200]

Opens the shot's last authored checkpoint (authored.blend of its newest version, kept even when a build failed; with
none, or when it no longer stands for the shot - its author script, a module beside it, a spec or the shot changed
since: checkpoint_stale - the author step alone is run to make one, blender._build_shot stop_after='authored', no version) and runs only
what the camera needs (fill, drives, the declared cheats, the support check, the openings, the camera solve by each
picture's intent: blender_ops/frames_check.py) - nothing saved, nothing rendered. Each pictured moment comes back as one of
  as_intended     it keeps its picture's size class, side of level and lens class, nothing said
  cheat_recorded  kept with the cheats already recorded for it
  moved           solved, but it gave part of its picture's intent up (listed: FRAME_INTENT_DEPARTED, a warning) and no
                  cheat brings it back
  cheat           a declared cheat (cheat_core.CHEATS) makes it solve - or brings a moved moment back to its intent -
                  checked again with every other moment's chosen cheats applied as a build applies them (verify: a
                  conflict stays a cheat, said not verified)
  redraw          FRAME_NEEDS_REDRAW, after the whole search: only a cheat on what the shot's lines speak of solves it,
                  or nothing does - draw the picture again (the code and numbers say why)
  stopped         --moment-s / --shot-s ran out first: the run stopped, it is not a verdict
The cheats are scored on the probe and only the best solved in full; the verdict is the whole search's, never the
clock's.
  other           the scene fails before or apart from the camera: a subject sinking into its support, a cut that
                  misses, a stale or misread reading (FRAME_SUBJECT_MISREAD from the solved row's own extents)
FRAME_FLATTENED, SUBJECT_END_ON and the solve's warnings ride with every solved moment.
Writes concepts/<shot>/frames_check.json and frames_check.html (picture | what the check found | what to redraw).
--apply records, for verified moments only, the cheats with their pictures (frames.json pictures.<cid>.cheats, by the
agent: the user left the camera to it - project.policy.viewpoint_cheat - and a scene cheat within the table's limits on
an unlocked thing changes no fact) and a moved or view-cheated moment's angle as a departure under that policy (counted;
a departure in the user's own words is kept as it is). A lens is not a picture departure (DEPART_ITEMS has none): a
lens cheat is shown, not counted. frames apply then carries the cheats into the move. Why (2026-10-14): a reel found
pictures it could not shoot only when a build failed, and the only answer was a duller view ("실제를 고집하다 영상이
노잼될 우려").
"""
from __future__ import annotations

import html
import json
from pathlib import Path
import shutil
import tempfile
import time

from . import cheat_core
from .common import CODE, StudioError, blender_binary, now, read_json, run_command, write_json
from .project import load_project, load_shot, project_dir, shot_path

from .blender_ops.cheat_table import DROP_LOCKED_WORDS as DROP_LOCKED   # noqa: E402 - the sheet's words for B 불가

STATUS_WORDS = {'as_intended': '그림의 의도대로', 'cheat_recorded': '치팅(기록된 것)으로 그림의 의도대로',
                'moved': '움직여서 풀림', 'cheat': '치팅으로 해결', 'redraw': '다시 그리기',
                'stopped': '중단됨 (실행 시간이 다 됨, 판정 아님)', 'error': '검사 오류', 'other': '다른 실패',
                'fail': '안 풀림', 'unchecked': '확인 못 함 (실행 시간이 다 됨, 판정 아님)'}


def latest_authored(path, shot_id):
    """The newest authored.blend among the shot's versions (failed builds keep theirs), or None."""
    folder = shot_path(path, shot_id).parent / 'versions'
    found = sorted(folder.glob('*/authored.blend'), key=lambda p: p.stat().st_mtime) if folder.is_dir() else []
    return found[-1] if found else None


def _job(path, project, shot, work, lock, moment_s, shot_s):
    from .blender import _checked_specs, _picture_layout, generator_inputs, probe_inputs
    specs = _checked_specs(path, shot)
    spec_paths = {}
    for subject_id, spec in specs.items():
        spec_paths[subject_id] = str(work / 'subjects' / f'{subject_id}.spec.json')
        write_json(Path(spec_paths[subject_id]), spec)
    style = read_json(path / 'style.json') if (path / 'style.json').exists() else {}
    return {**generator_inputs(path, project, shot['shot_id'], shot, spec_paths, style), **_picture_layout(path, shot),
            'output_dir': str(work),
            'probe': probe_inputs(path, shot, project['output']['fps'], style, (project['output']['width'], project['output']['height'])),
            'locked': lock, 'check': {'moment_s': moment_s, 'shot_s': shot_s}}


def classify(moment, pre_camera, reading, misread_why):
    """The moment's status: other when the scene fails apart from the camera (before it, or a misread picture). A cheat
    that broke another moment when the cheats were applied together stays a cheat, said not verified (the sheet: 함께
    적용하면 다른 장면이 깨짐) - the search finished, the verdict is its own."""
    if pre_camera or reading or misread_why:
        return 'other'
    return moment.get('status') or 'other'


def redraw_note(moment):
    if moment.get('status') != 'redraw':
        return None
    first = moment['as_is']
    locked = moment.get('locked_solves') or []
    offered = proposal_lines(moment)
    tail = (' | 제안 (고르기 전까지 다시 그리기): ' + ' / '.join(offered)) if offered else ''
    if locked:
        c = locked[0]
        return (f"FRAME_NEEDS_REDRAW: only {c['kind']} {c['target']} {c['value']} solves it, and {c['why_locked']} - draw the picture "
                f"so the line's facts hold as they are ({first['code']}: {first['message'][:240]})" + tail)
    tried = len(moment.get('attempts') or [])
    return f"FRAME_NEEDS_REDRAW: no cheat within the table's limits solves it ({tried} tried) - {first['code']}: {first['message'][:360]}" + tail


def _short(part):
    return str(part).split('/')[-1]


def proposal_lines(moment):
    """A blocked moment's proposals in Korean (the sheet, the redraw note): A - a picture redrawn from a pose outside its
    intent (camera_moves.wide_search: what it gives up, what it shows, how the redrawn picture would solve), B - a
    must-see part dropped from the moment (blender frames_check.drop_options; one the lines explain and no other moment
    shows is said 'B 불가'). Proposed, never taken: the user picks (frames pick / frames drop)."""
    found = moment.get('proposals') or {}
    out = []
    for n, a in enumerate(found.get('a') or []):
        shown = ', '.join(f"{_short(k)} {v:g} px" for k, v in (a.get('must_see_px') or {}).items())
        after = STATUS_WORDS.get((a.get('resolve') or {}).get('status'), (a.get('resolve') or {}).get('status') or '?')
        out.append(f"A{n + 1}: {', '.join(a.get('departed') or []) or '그림과 같은 자리'}에서 보면 {shown} 보임 (의도에서 벗어남; "
                   f"그 자리로 다시 그리면: {after})")
    if not found.get('a') and 'a' in found:
        out.append(f"A: 의도 밖 각도에서도 꼭 보일 부분이 다 보이는 자리 없음 ({(found.get('a_search') or {}).get('probed', 0)}곳 확인)")
    if (found.get('a_search') or {}).get('stopped'):
        out.append(f"A: 넓은 탐색이 시간 안에 끝나지 않음 ({found['a_search']['stopped']} 단계에서 멈춤, 판정 아님)")
    for b in found.get('b') or []:
        if b.get('locked'):
            out.append(f"B: {_short(b['part'])} 빼기 — {DROP_LOCKED}")
        elif b['status'] == 'fail':
            out.append(f"B: {_short(b['part'])} 빼도 안 풀림 ({b.get('code')})")
        elif b['status'] == 'unchecked':
            out.append(f"B: {_short(b['part'])} 빼기 — {STATUS_WORDS['unchecked']}")
        else:
            left = ', '.join(f"{_short(k)} {v:g} px" for k, v in ((b.get('row') or {}).get('must_see_px') or {}).items())
            out.append(f"B: {_short(b['part'])} 빼면 {STATUS_WORDS[b['status']]}" + (f" ({left})" if left else '')
                       + f", 뺀 부품은 {max(list((b.get('dropped_px') or {}).values()) or [0]):g} px")
    return out


CAMERA_STEP = ('move', 'movement', 'keys', 'rig')   # what frames apply writes: read by generate's camera, after the checkpoint
WRITTEN_BACK = ('revision', 'scene_version', 'author')   # what a build writes into the shot: not what the author step read
                                                         # (the author script itself is compared by its content)


def checkpoint_stale(path, shot, blend):
    """Why an authored checkpoint no longer stands for the shot - [] when it does: what made it (the author_job.json, the
    author script and the modules beside it, the project data it reads, the subject specs its folder keeps) against what
    a fresh build of the shot would run now. Content, not file times: a copy of the project or a touched file is not a
    change. A revision (--base) checkpoint is its base plus a patch script a fresh build does not run: never the shot's."""
    from .blender import _author_companions, _checked_specs, _sidecars
    from .common import file_hash
    from .world import lit, load as load_world
    folder = blend.parent
    if not (folder / 'author_job.json').is_file():
        return ['the checkpoint keeps no record of what made it (author_job.json)']
    made = read_json(folder / 'author_job.json')
    if made.get('base_version'):
        return [f"it is a revision on {made['base_version']} (a patch on its checkpoint; a fresh build runs the shot's author)"]
    out = []
    author = made_author(shot, folder)
    script = (path / author).resolve() if author else None
    if script is not None and script.is_file():
        kept = folder / 'author.py'
        if not kept.is_file() or file_hash(kept) != file_hash(script):
            out.append(f"the author script {author} changed")
        out += [f'{m.name} beside the author script changed' for m in _author_companions(script)
                if not (folder / m.name).is_file() or file_hash(folder / m.name) != file_hash(m)]
    for subject_id, spec in sorted(_checked_specs(path, shot).items()):
        kept = folder / 'subjects' / f'{subject_id}.spec.json'
        if not kept.is_file() or read_json(kept) != spec:
            out.append(f"{subject_id}'s spec changed")
    then_data, now_data = made.get('project_sidecars'), _sidecars(path)
    if then_data is None and now_data:
        out.append(f"the checkpoint keeps no record of the project data an author reads ({sorted(now_data)})")
    elif then_data is not None and then_data != now_data:
        out.append(f"the project data an author reads changed ({sorted(k for k in set(then_data) | set(now_data) if then_data.get(k) != now_data.get(k))})")
    lit_now = lit(shot, load_world(path))
    now_shot, then_shot = (_read_by_author(s) for s in (lit_now, made.get('shot') or {}))
    changed = sorted(k for k in set(now_shot) | set(then_shot) if now_shot.get(k) != then_shot.get(k))
    if changed:
        out.append(f'the shot changed since ({changed})')
    # what the author read of the fields left out above (the camera step's, what a build writes back): read the same
    # (read_record; review 2026-10-17: an author leaned its subject by move.frames[].tilt_deg). A checkpoint whose author
    # ran without that record cannot say what it read: made again
    if script is not None:
        if not (folder / 'author_reads.json').is_file():
            out.append('the checkpoint keeps no record of what its author read of the shot (author_reads.json)')
        else:
            from .blender_ops.read_record import changed as reads_changed
            left_out = [r for r in read_json(folder / 'author_reads.json').get('shot') or [] if _left_out(r[1])]
            moved = reads_changed(left_out, made.get('shot') or {}, lit_now)
            if moved:
                out.append(f'what the author read of the shot changed since ({moved[:6]})')
    return out


def _left_out(path):
    """Whether a read's path lies in what _read_by_author leaves out (the camera step's fields, what a build writes back)."""
    return bool(path) and (path[0] in WRITTEN_BACK or (path[0] == 'camera' and (len(path) == 1 or path[1] in CAMERA_STEP)))


def _read_by_author(shot):
    """The shot as the author step can read it: without what a build writes back (WRITTEN_BACK) and without the camera
    fields frames apply rewrites (CAMERA_STEP), which only the camera step after the checkpoint reads."""
    out = {k: v for k, v in shot.items() if k not in WRITTEN_BACK}
    if isinstance(out.get('camera'), dict):
        out['camera'] = {k: v for k, v in out['camera'].items() if k not in CAMERA_STEP}
    return out


def made_author(shot, folder):
    """The author script a checkpoint ran: the shot's, else the one its build was given (a failed first build never
    wrote it back to the shot; its author_job.json keeps it) - or None."""
    if shot.get('author'):
        return shot['author']
    job = folder / 'author_job.json'
    return ((read_json(job) if job.is_file() else {}).get('shot') or {}).get('author')


def authored_checkpoint(path, shot_id):
    """The newest authored.blend of the shot while it still stands for the shot (checkpoint_stale), else one made by the
    author step alone (no version, no camera): (blend, made or None, why it was made again)."""
    blend = latest_authored(path, shot_id)
    stale = checkpoint_stale(path, load_shot(path, shot_id), blend) if blend is not None else []
    if blend is not None and not stale:
        return blend, None, []
    from .blender import _build_shot
    shot = load_shot(path, shot_id)
    author = made_author(shot, blend.parent) if blend is not None and not shot.get('author') else None
    made = _build_shot(path, shot_id, (path / author) if author else None, diagnosis='frames check: the authored checkpoint only',
                       stop_after='authored')
    return Path(made['authored']), made, stale


SOLVED = ('as_intended', 'cheat_recorded', 'moved', 'cheat')   # camera statuses of a moment that solves


def _verified(found):
    """Whether, with every chosen cheat applied together, each moment that solved (as it stood or with its cheat)
    still solves: True, False, or None (nothing chosen, or not checked). A moment that solved in no way is not asked."""
    rows = found.get('verify')
    if not isinstance(rows, dict) or 'error' in rows or 'skipped' in rows:
        return None if not rows else False
    solved = {str(m['at']) for m in found.get('moments') or [] if m.get('status') in SOLVED}
    return all(r['ok'] for at, r in rows.items() if at in solved)


def check(project, shot_id, apply=False, moment_s=420.0, shot_s=1200.0):
    from .frame_targets import END_ON_DEG, flattened, misread, reading_checks, targets
    from .gates import severity_for
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    project_row = load_project(path)
    blend, made, stale = authored_checkpoint(path, shot_id)
    severity = severity_for(path, shot)
    reading = [r for r in reading_checks(path, shot) if severity.get(r['code'], 'error') == 'error']
    lock = cheat_core.locked(path, shot)
    folder = path / 'concepts' / shot_id
    folder.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='.frames-check-', dir=folder))
    started = time.monotonic()
    failed = None
    try:
        job = _job(path, project_row, shot, work, lock, moment_s, shot_s)
        write_json(work / 'job.json', job)
        command = [blender_binary(), '--background', '--factory-startup', '--disable-autoexec', str(blend), '--python-exit-code', '1',
                   '--python', str(CODE / 'studio/blender_ops/frames_check.py'), '--', str(work / 'job.json')]
        try:
            run_command(command, work / 'frames_check.log', timeout=int(shot_s) + 900)
        except StudioError as error:   # killed or crashed: what was written after each moment is kept, said incomplete
            failed = {'code': error.code, 'message': error.message[-600:]}
            if (work / 'frames_check.log').is_file():
                shutil.copy2(work / 'frames_check.log', folder / 'frames_check.log')
            if not (work / 'frames_check.json').is_file():
                raise
        found = read_json(work / 'frames_check.json')
    finally:
        shutil.rmtree(work, ignore_errors=True)
    pictures = {t['concept_id']: t for t in targets(path, shot_id)}
    size = (project_row['output']['width'], project_row['output']['height'])
    rows = []
    for m in found.get('moments') or []:
        picture = pictures.get(m.get('concept_id')) or {}
        solved = (m.get('row') or ((m.get('solved_by') or {}).get('row'))) or {}
        end_on = solved.get('sight_deg') is not None and solved['sight_deg'] < END_ON_DEG
        flat = flattened(picture, None, (solved.get('view') or [None])[0], None if end_on else solved.get('tilt_deg'),
                         solved.get('lens_mm')) if picture and solved else []
        wrong = misread(picture['check']['box'], picture['check']['cut_edges'], size, solved.get('spans_m'), fore=picture.get('fore')) \
            if picture and solved.get('spans_m') else None
        verified = _verified(found) if m.get('status') == 'cheat' else None
        notes = [*((solved.get('warnings') or [])), *(m.get('moved') or [])]
        if end_on and picture.get('tilt_deg') is not None:
            notes.append(f"SUBJECT_END_ON: the long axis {solved['sight_deg']:.0f} deg from the line of sight: its lean is not compared")
        if wrong:
            notes.append(f'FRAME_SUBJECT_MISREAD: {wrong}')
        rows.append({**m, 'status': classify(m, found.get('pre_camera'), reading, wrong), 'camera_status': m.get('status'),
                     'verified': verified, 'redraw': redraw_note(m), 'flattened': flat, 'notes': list(dict.fromkeys(notes)),
                     'picture_path': next((str(p['path']) for p in _frames(path, shot_id) if p['concept_id'] == m.get('concept_id')), None)})
    result = {'shot_id': shot_id, 'checked_at': now(), 'scene': str(blend.relative_to(path)), **({'scene_remade_because': stale} if stale else {}),
              **({'scene_made': made['authored']} if made else {}),
              'complete': bool(found.get('complete')) and not failed, **({'stopped': failed} if failed else {}),
              'reading': reading, 'pre_camera': found.get('pre_camera') or [], 'error': found.get('error'), 'locked': lock,
              'moments': rows, 'verify': found.get('verify'), 'seconds': round(time.monotonic() - started, 1),
              'blender_seconds': found.get('seconds'), 'policy': cheat_core.viewpoint_policy(path)}
    if apply:
        result['applied'] = _record(path, shot_id, rows, result['policy'])
    write_json(folder / 'frames_check.json', result)
    sheet = _sheet(path, shot_id, result)
    return {'shot_id': shot_id, 'complete': result['complete'], 'scene': result['scene'],
            **({'scene_remade_because': stale} if stale else {}),
            'moments': [{k: r.get(k) for k in ('at', 'concept_id', 'status', 'verified', 'redraw', 'flattened', 'notes', 'seconds')}
                        | ({'proposals': proposal_lines(r)} if r.get('proposals') else {})
                        | {'cheats': (r.get('solved_by') or {}).get('cheats'),
                           'probed': len(r.get('probes') or (r.get('search') or {}).get('probes') or []),
                           'solved_in_full': len(r.get('attempts') or (r.get('search') or {}).get('attempts') or [])} for r in rows],
            'pre_camera': result['pre_camera'], 'reading': reading, 'error': result['error'], 'seconds': result['seconds'],
            # what --apply recorded and what it skipped and why (E2E 2026-10-21: a lens cheat skipped for want of the
            # project's viewpoint policy was said only in frames_check.json, and read as recorded)
            **({'applied': result['applied']} if apply else {}),
            'artifacts': [str(folder / 'frames_check.json'), str(sheet)]}


def uncheat(project, shot_id, concept_id, kinds=None):
    """Take recorded cheats off a picture (all, or the named kinds): frames apply then carries the move without them."""
    from .common import lock
    from .frame_targets import _store, _store_path
    path = project_dir(project)
    unknown = sorted(set(kinds or []) - set(cheat_core.CHEATS))
    if unknown:
        raise StudioError('INPUT_INVALID', f'unknown cheat kinds {unknown} (known: {sorted(cheat_core.CHEATS)})')
    with lock(path / '.project.lock'):
        store = _store(path, shot_id)
        row = store['pictures'].get(concept_id)
        if row is None:
            raise StudioError('INPUT_INVALID', f'{shot_id}: no reading of {concept_id}')
        removed = [c for c in row.get('cheats') or [] if not kinds or c['kind'] in kinds]
        row['cheats'] = [c for c in row.get('cheats') or [] if c not in removed]
        write_json(_store_path(path, shot_id), store)
    return {'shot_id': shot_id, 'concept_id': concept_id, 'removed': removed, 'cheats': row['cheats'],
            'next': f'studio frames apply --project {project} --shot {shot_id}'}


def _frames(path, shot_id):
    from .concept import frames
    return frames(path, shot_id)


def _record(path, shot_id, rows, policy):
    """frames check --apply: for moments whose cheats were verified together, the cheats onto their pictures (by the
    agent); a view cheat, and a moved moment's own view step, as an angle departure under the project's policy (counted
    against the limit; one in the user's own words is kept as it is). A lens cheat is shown, not counted."""
    from .frame_targets import _store, _store_path, depart
    store = _store(path, shot_id)
    applied, angles = [], []
    for row in rows:
        picture = store['pictures'].get(row['concept_id'])
        if picture is None:
            continue
        if row['status'] == 'moved' and any(((row.get('row') or {}).get('offset_from_picture') or {}).values()):
            angles.append((row['concept_id'], f"the solve's own view step: {'; '.join(row.get('moved') or [])[:220]}"))
            continue
        if row['status'] != 'cheat':
            continue
        if row.get('verified') is not True:   # said, never dropped quietly
            applied.append({'concept_id': row['concept_id'], 'skipped': 'its cheats were not verified with the other moments '
                            + ('(they break a moment that solved)' if row.get('verified') is False else '(no verify ran)')})
            continue
        cheats = row['solved_by']['cheats']
        if any(cheat_core.CHEATS[c['kind']]['targets'] == 'camera' for c in cheats) and not policy:
            applied.append({'concept_id': row['concept_id'], 'skipped': 'a lens or view cheat needs project.policy.viewpoint_cheat'})
            continue
        before = f"as it stood {row['as_is']['code']} ({str(row['as_is'].get('message'))[:160]})" if row['as_is'].get('code') else \
            f"as it stood it gave up: {'; '.join(row.get('moved') or [])[:160]}"
        solved = row['solved_by']['row']
        why = f"frames check: {before}; with this it solves at {solved.get('lens_mm')} mm, view {solved.get('view')}"
        entries = [cheat_core.entry(c['kind'], c.get('target'), c['value'], why, 'agent', now(),
                                    'policy.viewpoint_cheat' if cheat_core.CHEATS[c['kind']]['targets'] == 'camera' else 'cheat_table')
                   for c in cheats]
        picture['cheats'] = [c for c in picture.get('cheats') or [] if (c['kind'], json.dumps(c.get('target'))) not in
                             {(e['kind'], json.dumps(e.get('target'))) for e in entries}] + entries
        applied.append({'concept_id': row['concept_id'], 'cheats': entries,
                        'words': [cheat_core.CHEATS[e['kind']]['words'] for e in entries]})
        views = [e for e in entries if cheat_core.CHEATS[e['kind']]['applies'] == 'view']
        if views:
            angles.append((row['concept_id'], f"view cheat {views[0]['value']}"))
    write_json(_store_path(path, shot_id), store)
    for concept_id, why in angles:   # the picture's viewpoint kept different: a departure under the policy, counted
        held = next((d for d in _store(path, shot_id)['pictures'][concept_id].get('departs') or [] if d['item'] == 'angle'), None)
        if held and held.get('user_words'):
            continue   # the user's own call on this angle stands as it is
        if not policy:
            applied.append({'concept_id': concept_id, 'skipped': f'{why}: an angle departure needs project.policy.viewpoint_cheat'})
            continue
        try:
            depart(path, shot_id, concept_id, 'angle', why)
        except StudioError as error:   # past the limit: a view cheat is not kept; the picture is redrawn instead
            store = _store(path, shot_id)
            row = store['pictures'][concept_id]
            row['cheats'] = [c for c in row.get('cheats') or [] if cheat_core.CHEATS[c['kind']]['applies'] != 'view']
            write_json(_store_path(path, shot_id), store)
            applied.append({'concept_id': concept_id, 'skipped': f'{error.code}: {error.message}'})
    return applied


def _cheat_words(cheat):
    """A cheat as the sheet says it: the table's words, then what it touches and by how much."""
    row = cheat_core.CHEATS.get(cheat['kind']) or {}
    target = cheat.get('target')
    target = ', '.join(target) if isinstance(target, list) else (target or '')
    return f"{row.get('words', cheat['kind'])} — {cheat['kind']} {target} {cheat['value']}"


def _sheet(path, shot_id, result):
    folder = path / 'concepts' / shot_id
    out = folder / 'frames_check.html'
    rows = []
    for m in result['moments']:
        image = f"<img src='{html.escape(str(Path('..') / '..' / m['picture_path']))}' loading='lazy'>" if m.get('picture_path') else '—'
        found = STATUS_WORDS.get(m['status'], m['status'])
        detail = []
        row = m.get('row') or (m.get('solved_by') or {}).get('row') or {}
        if row:
            detail.append(f"{row.get('lens_mm')} mm, 그림에서 {row.get('offset_from_picture')}")
        if m.get('solved_by'):   # a lens cheat is shown, not counted as a departure (DEPART_ITEMS has no lens)
            detail += [_cheat_words(c) for c in m['solved_by']['cheats']]
            detail.append({True: '모든 순간에서 함께 확인됨', False: '함께 적용하면 다른 순간이 깨짐', None: '함께 확인 안 됨'}[m.get('verified')])
        detail += [f"기록된 치팅: {_cheat_words(c)}" for c in m.get('recorded') or []]
        if (m.get('as_is') or {}).get('code'):
            detail.append(f"그대로: {m['as_is']['code']}")
        detail += [f"FRAME_FLATTENED: {f}" for f in m.get('flattened') or []] + list(m.get('notes') or [])
        detail += [f"제안 {line}" for line in proposal_lines(m)]
        detail += [f"{p['code']}: {p['text'][:200]}" for p in result['pre_camera']]
        rows.append(f"<tr><td>{image}<div>{html.escape(str(m.get('concept_id')))} t={m['at']}</div></td><td><b>{found}</b>"
                    f"{''.join('<div>' + html.escape(d) + '</div>' for d in detail)}<div class='s'>{m.get('seconds')} s</div></td>"
                    f"<td>{html.escape(m.get('redraw') or '—')}</td></tr>")
    reading = ''.join(f"<p class='warn'>{html.escape(r['code'])}: {html.escape(r['text'])}</p>" for r in result['reading'])
    error = f"<p class='warn'>{html.escape(str(result['error']))}</p>" if result.get('error') else ''
    out.write_text("<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                   f"<title>{shot_id} frames check</title><style>body{{font:14px sans-serif;background:#18181a;color:#eee;margin:16px}}"
                   "img{max-width:220px}td{vertical-align:top;padding:8px;border-bottom:1px solid #333}.warn{color:#f99}.s{color:#999}</style></head><body>"
                   f"<h1>{shot_id} — 그림을 찍을 수 있나</h1><p>{html.escape(result['scene'])}"
                   f"{' (다시 만듦: ' + html.escape('; '.join(result['scene_remade_because'])) + ')' if result.get('scene_remade_because') else ''} · {result['seconds']} s"
                   f"{'' if result.get('complete') else ' · 끝까지 못 감'}</p>{reading}{error}"
                   "<table><tr><th>그림</th><th>결과</th><th>다시 그리기</th></tr>" + ''.join(rows) + '</table></body></html>', encoding='utf-8')
    return out


CANDIDATES_KEEP = 3       # poses per moment on the candidates sheet (the engine's choice first)
CANDIDATES_HEIGHT = 480   # px of each candidate render (Workbench)


def candidates(project, shot_id, keep=CANDIDATES_KEEP, height=CANDIDATES_HEIGHT):
    """The shot's intent poses to pick from, as a sheet: on the authored checkpoint (authored_checkpoint, as frames check
    opens it) every pictured moment solved by its picture's intent keeping up to `keep` judged poses, each rendered from
    its pose (blender_ops/frame_candidates.py, Workbench), beside the picture - concepts/<shot>/candidates/ (sheet.png,
    sheet.html, candidates.json). frames pick records one, in the user's own words; without one the engine's first
    stands. Why (2026-10-15): a camera is placed by its picture's intent and looked at, then the scene is picked again
    until it reads - fast enough to go round."""
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    blend, made, stale = authored_checkpoint(path, shot_id)
    folder = path / 'concepts' / shot_id / 'candidates'
    folder.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='.frames-candidates-', dir=path / 'concepts' / shot_id))
    started = time.monotonic()
    try:
        job = _job(path, load_project(path), shot, work, cheat_core.locked(path, shot), 600.0, 1800.0)
        job.update({'keep': int(keep), 'height': int(height)})
        write_json(work / 'job.json', job)
        run_command([blender_binary(), '--background', '--factory-startup', '--disable-autoexec', str(blend), '--python-exit-code', '1',
                     '--python', str(CODE / 'studio/blender_ops/frame_candidates.py'), '--', str(work / 'job.json')],
                    work / 'frame_candidates.log', timeout=1800)
        found = read_json(work / 'candidates.json')
        if folder.is_dir():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        for m in found['moments']:
            for c in m['candidates']:
                shutil.copy2(work / c['image'], folder / c['image'])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    pictures = {p['concept_id']: p for p in _frames(path, shot_id)}
    for m in found['moments']:
        m['picture_path'] = str((pictures.get(m['concept_id']) or {}).get('path') or '') or None
    found.update({'scene': str(blend.relative_to(path)), 'made_at': now(), 'blender_seconds': found.get('seconds'),
                  'seconds': round(time.monotonic() - started, 1),
                  **({'scene_remade_because': stale} if stale else {})})
    write_json(folder / 'candidates.json', found)
    if merge_proposals(path, shot_id):   # a blocked moment's A poses stay on the sheet the user picks from
        found = read_json(folder / 'candidates.json')
    sheet = _candidates_sheet(path, folder, found)
    return {'shot_id': shot_id, 'seconds': found['seconds'], 'blender_seconds': found.get('blender_seconds'),
            'moments': [{'at': m['at'], 'concept_id': m['concept_id'], 'candidates': len(m['candidates']),
                         **({'error': m['error']['code']} if m.get('error') else {})} for m in found['moments']],
            'artifacts': [str(folder / 'candidates.json'), str(sheet['png']), str(sheet['html'])],
            'next': f'studio frames pick --project {project} --shot {shot_id} --moment <at or concept> --candidate <n> --user-words "..."'}


def _candidate_words(c):
    """A candidate's lines on the sheet, in Korean."""
    offset = c.get('offset') or {}
    lines = [f"후보 {c['n']}{' (엔진의 선택)' if c['n'] == 0 else ''}",
             f"시점 높이 {c['view'][0]:+.0f}°, 방위 {c['view'][1]:.0f}° (그림에서 {offset.get('azimuth_deg', 0):+.0f}°)",
             f"렌즈 {c['lens_mm']:g} mm" + (f", 점수 {c['score']:.2f}" if isinstance(c.get('score'), (int, float)) else '')]
    if c.get('must_see_px'):
        lines.append('꼭 보일 부분: ' + ', '.join(f"{k.split('/')[-1]} {v:g} px" for k, v in c['must_see_px'].items()))
    lines += [f'의도에서 벗어남: {d}' for d in c.get('departed') or []]
    return lines


def _candidates_sheet(path, folder, found):
    """sheet.png (a row per moment: its picture, then its candidates, captioned) and sheet.html beside it."""
    from PIL import Image, ImageDraw
    rows, size, caption = [], None, 120
    for m in found['moments']:
        tiles = []
        picture = path / m['picture_path'] if m.get('picture_path') else None
        for source in ([picture] if picture and picture.is_file() else []) + [folder / c['image'] for c in m['candidates']]:
            image = Image.open(source).convert('RGB')
            size = size or image.size
            tiles.append(image.resize((round(image.width * size[1] / image.height), size[1])))
        rows.append((m, tiles))
    if size is None:
        size = (270, 480)
    width = max([sum(t.width for t in tiles) + 10 * (len(tiles) + 1) for _, tiles in rows] or [size[0]])
    sheet = Image.new('RGB', (width, len(rows) * (size[1] + caption + 10) + 10), (24, 24, 26))
    draw = ImageDraw.Draw(sheet)
    y = 10
    for m, tiles in rows:
        x = 10
        for i, tile in enumerate(tiles):
            sheet.paste(tile, (x, y))
            x += tile.width + 10
        draw.text((10, y + size[1] + 4), f"t={m['at']} {m.get('concept_id') or ''}" + (f"  {m['error']['code']}" if m.get('error') else ''),
                  fill=(230, 230, 230))
        y += size[1] + caption + 10
    png = folder / 'sheet.png'
    sheet.save(png)
    cells = []
    for m in found['moments']:
        pic = f"<td><img src='{html.escape(str(Path('..') / '..' / '..' / m['picture_path']))}'><div>그림</div></td>" if m.get('picture_path') else ''
        cands = ''.join(f"<td><img src='{html.escape(c['image'])}'>" + ''.join(f'<div>{html.escape(line)}</div>' for line in _candidate_words(c))
                        + '</td>' for c in m['candidates'])
        error = f"<td class='warn'>{html.escape(m['error']['code'])}: {html.escape(m['error']['message'][:300])}</td>" if m.get('error') else ''
        cells.append(f"<tr><th>t={m['at']}<br>{html.escape(str(m.get('concept_id')))}</th>{pic}{cands}{error}</tr>")
    page = folder / 'sheet.html'
    page.write_text("<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                    f"<title>{found['shot_id']} candidates</title><style>body{{font:14px sans-serif;background:#18181a;color:#eee;margin:16px}}"
                    "img{max-width:220px}td,th{vertical-align:top;padding:8px;border-bottom:1px solid #333}.warn{color:#f99}</style></head><body>"
                    f"<h1>{found['shot_id']} — 카메라 후보 고르기</h1><p>고르려면: studio frames pick --shot {found['shot_id']} --moment &lt;t 또는 그림&gt; "
                    "--candidate &lt;번호&gt; --user-words \"...\" (고르지 않으면 엔진의 선택, 후보 0)</p>"
                    "<table>" + ''.join(cells) + '</table></body></html>', encoding='utf-8')
    return {'png': png, 'html': page}


def pick(project, shot_id, moment, candidate, user_words):
    """Record the user's pick of a moment's candidate (frames candidates' sheet) with its picture: frames.json
    pictures.<concept>.chosen {view, lens_mm, eye, aim, placement, candidate, by: user, user_words, at}. frames apply
    carries it into move.frames[]; the intent solve tries it first and says when it no longer holds. Only the user's own
    words record a pick (generative.review.check_user_words); without one the engine's first candidate stands."""
    from .common import lock
    from .concept import frame_at
    from .frame_targets import _store, _store_path
    from .generative.review import check_user_words
    words = check_user_words(user_words, 'frames pick')
    path = project_dir(project)
    sheet = path / 'concepts' / shot_id / 'candidates' / 'candidates.json'
    if not sheet.is_file():
        raise StudioError('INPUT_INVALID', f'{shot_id}: no candidates sheet', recovery=f'studio frames candidates --project {project} --shot {shot_id}')
    found = read_json(sheet)
    row = next((m for m in found['moments'] if m.get('concept_id') == str(moment) or _same_at(m['at'], moment)), None)
    if row is None:
        raise StudioError('INPUT_INVALID', f"{shot_id}: no moment {moment!r} on the sheet ({[(m['at'], m['concept_id']) for m in found['moments']]})")
    chosen = next((c for c in row['candidates'] if c['n'] == int(candidate)), None)
    if chosen is None:
        raise StudioError('INPUT_INVALID', f"{shot_id} t={row['at']}: no candidate {candidate} (0..{len(row['candidates']) - 1})")
    entry = {**pick_pose(chosen, f"{shot_id} t={row['at']} candidate {candidate}"), 'candidate': int(candidate),
             **({'scene': chosen.get('scene') or found['scene']} if chosen.get('scene') or found.get('scene') else {}),   # the checkpoint
             # it was solved on (a proposal's own): said if it fails
             'by': 'user', 'user_words': words, 'at': now()}
    # the moment's picture now: a picture painted since the sheet was made (picture3d) supersedes the sheet's id
    current = (frame_at(path, shot_id, row['at']) or {}).get('concept_id') or row['concept_id']
    with lock(path / '.project.lock'):
        store = _store(path, shot_id)
        picture = store['pictures'].get(current)
        if picture is None:
            raise StudioError('INPUT_INVALID', f"{shot_id}: no reading of {current} (frames measure first)")
        picture['chosen'] = entry
        write_json(_store_path(path, shot_id), store)
    return {'shot_id': shot_id, 'concept_id': current, 'at': row['at'], 'chosen': entry,
            'next': f'studio frames apply --project {project} --shot {shot_id}'}


def pick_pose(row, where):
    """The pose a pick records from a candidate row (frames candidates, a proposal): view, lens, eye, aim, roll, its
    placement always {aim, closer} (camera_moves_core.placement_of: a missing one read as placed as the picture is) and
    where the subject stood when it was solved (subject_at: the pose moves with the subject on a scene authored again
    since). A placement placing cannot make is refused here, not when the build reads it."""
    from .blender_ops.camera_moves_core import placement_of
    try:
        placement = placement_of(row)
    except ValueError as error:
        raise StudioError('INPUT_INVALID', f'{where}: {error}') from None
    return {'view': [float(v) for v in row['view']], 'lens_mm': float(row['lens_mm']), 'eye': [float(v) for v in row['eye']],
            'aim': [float(v) for v in row['aim']],
            **({'roll_deg': float(row['roll_deg'])} if row.get('roll_deg') is not None else {}), 'placement': placement,
            **({'subject_at': [float(v) for v in row['subject_at']]} if row.get('subject_at') else {}),
            **({'subject_pose': [float(v) for v in row['subject_pose']]} if row.get('subject_pose') else {})}


def _same_at(at, moment):
    try:
        return abs(float(at) - float(moment)) < 1e-6
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- proposals: a blocked moment's A/B, the user's choice
PROPOSAL_HEIGHT = 480   # px of each tile on the proposals page


def _proposals_dir(path, shot_id):
    return path / 'concepts' / shot_id / 'proposals'


def proposals(project, shot_id, paint=False, runner=None):
    """A blocked moment's choices as a page (frames check found them: proposals {a, b}): its picture | the clay at the
    pose its picture was drawn from, with the must-see parts outlined where they stand (hidden or not) | each A pose's
    clay (a picture redrawn from outside its intent; --paint: painted over that clay by the image tool, the free Codex
    subscription) | each B option's clay (a must-see part dropped). Writes concepts/<shot>/proposals/ (the passes of each
    pose, proposals.json) and review/proposals.html (every shot with proposals, one page), and puts the A poses on the
    candidates sheet as candidates (source 'proposal') so frames pick records one in the user's words; frames drop takes
    a B. Nothing is adopted here."""
    from PIL import Image, ImageDraw
    from .picture3d import _pass_size, _style, paint_into, paint_prompt, render_passes
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    found = path / 'concepts' / shot_id / 'frames_check.json'
    if not found.is_file():
        raise StudioError('INPUT_INVALID', f'{shot_id}: no frames check yet', recovery=f'studio frames check --project {project} --shot {shot_id}')
    checked = read_json(found)
    blocked = [m for m in checked.get('moments') or [] if m.get('proposals')]
    if not blocked:
        raise StudioError('INPUT_INVALID', f'{shot_id}: no blocked moment with proposals in the last frames check',
                          recovery=f'studio frames check --project {project} --shot {shot_id} (a moment that ends redraw carries them)')
    count = shot['duration_frames']
    frames_by_at = {round(float(f['at']), 4): f for f in ((shot.get('camera') or {}).get('move') or {}).get('frames') or []}
    root = _proposals_dir(path, shot_id)
    if root.is_dir():
        shutil.rmtree(root)
    rows, moments = [], []
    for m in blocked:
        at, frame = float(m['at']), round(float(m['at']) * (count - 1))
        fr = frames_by_at.get(round(at, 4)) or {}
        must = list(fr.get('must_see') or [])
        here = root / f'{at:.2f}'
        tiles = [{'kind': 'intended', 'out': here / 'intended',
                  'pass': {'place': fr, 'outline': must, 'lens_mm': float(fr.get('lens_mm') or 24.0),
                           'view': [fr['view']['elevation_deg'], fr['view'].get('azimuth_deg', 0.0)]}}] if fr else []
        for n, a in enumerate(m['proposals'].get('a') or []):
            tiles.append({'kind': 'a', 'n': n, 'row': a, 'out': here / f'a{n + 1}',
                          'pass': {k: a[k] for k in ('eye', 'aim', 'lens_mm', 'view', 'roll_deg', 'subject_at', 'subject_pose') if k in a}})
        for n, b in enumerate(m['proposals'].get('b') or []):
            row = b.get('row')
            tiles.append({'kind': 'b', 'n': n, 'row': b, 'out': here / f'b{n + 1}',
                          **({'pass': {'eye': row['position'], 'aim': row['aim'], 'lens_mm': row['lens_mm'], 'view': row['view'],
                                       'roll_deg': row.get('roll_deg') or 0.0, 'outline': [b['part']],
                                       **{k: row[k] for k in ('subject_at', 'subject_pose') if row.get(k)}}} if row else {})})
        for t in tiles:
            if t.get('pass'):
                moments.append({'at': at, 'frame': frame, 'concept_id': m.get('concept_id'), 'out': str(t['out']), **t['pass']})
        rows.append({'moment': m, 'at': at, 'frame': frame, 'tiles': tiles})
    started = time.monotonic()
    if moments:
        render_passes(path, shot_id, moments)
    width, height = _pass_size(load_project(path))
    record = {'shot_id': shot_id, 'made_at': now(), 'scene': checked.get('scene'), 'moments': []}   # the scene its poses stand on
    asked = []   # paintings the studio had to ask for (outside_model): every tile asked in this one round
    for r in rows:
        m, at = r['moment'], r['at']
        out = {'at': at, 'concept_id': m.get('concept_id'), 'picture': m.get('picture_path'), 'lines': proposal_lines(m), 'a': [], 'b': []}
        for t in r['tiles']:
            clay = t['out'] / 'clay.png'
            if not clay.is_file():
                if t['kind'] == 'b':
                    out['b'].append({**_b_row(t['row']), 'image': None})
                continue
            info = read_json(t['out'] / 'id.json') if (t['out'] / 'id.json').is_file() else {}
            if info.get('outline'):   # where the named parts stand from this pose, hidden or not
                image = Image.open(clay).convert('RGB')
                draw = ImageDraw.Draw(image)
                for part, box in info['outline'].items():
                    xy = [box[0] * image.width, box[1] * image.height, box[2] * image.width, box[3] * image.height]
                    draw.rectangle(xy, outline=(255, 80, 60), width=3)
                    draw.text((xy[0] + 3, max(0, xy[1] - 12)), _short(part), fill=(255, 80, 60))
                image.save(t['out'] / 'outlined.png')
            shown = t['out'] / ('outlined.png' if info.get('outline') else 'clay.png')
            if t['kind'] == 'intended':
                out['intended'] = {'image': str(shown.relative_to(path)), 'pose': info.get('pose')}
            elif t['kind'] == 'a':
                painted = None
                if paint:
                    style = _style(path, shot_id, at)
                    attach = [t['out'] / 'clay.png', t['out'] / 'depth.png', t['out'] / 'lines.png'] + ([style] if style else [])
                    try:
                        made, _how, tail = paint_into(t['out'] / 'paint.png', attach, paint_prompt(path, shot_id), runner,
                                                      f"{shot_id} t={at} A{t['n'] + 1}")
                    except StudioError as error:
                        if error.code != 'OUTSIDE_MODEL_ASKED':
                            raise
                        asked += error.affected_ids
                        made, tail = None, 'asked (studio ask pending)'
                    painted = str((t['out'] / 'paint.png').relative_to(path)) if made else None
                    if not made:
                        out.setdefault('paint_failed', []).append(f"A{t['n'] + 1}: {tail[-160:]}")
                out['a'].append({**t['row'], 'n': t['n'] + 1, 'image': str(shown.relative_to(path)), **({'painted': painted} if painted else {})})
            else:
                out['b'].append({**_b_row(t['row']), 'image': str(shown.relative_to(path))})
        record['moments'].append(out)
    record['seconds'] = round(time.monotonic() - started, 1)
    root.mkdir(parents=True, exist_ok=True)
    write_json(root / 'proposals.json', record)
    added = merge_proposals(path, shot_id)
    page = proposals_page(path)
    if asked:   # the clay proposals stand; their paintings wait for the answers, then this runs again
        from .outside_model import asked_error
        raise asked_error(asked, f'{shot_id} proposals')
    return {'shot_id': shot_id, 'moments': [{'at': r['at'], 'lines': r['lines'], 'a': len(r['a']), 'b': len(r['b'])} for r in record['moments']],
            'candidates_added': added, 'seconds': record['seconds'], 'size': [width, height],
            'artifacts': [str(root / 'proposals.json'), str(page)],
            'next': [f'A: studio frames pick --project {project} --shot {shot_id} --moment <at> --candidate <n> --user-words "..." '
                     '(the candidates sheet lists each A as a candidate, source proposal), then studio picture3d run',
                     f'B: studio frames drop --project {project} --shot {shot_id} --moment <at> --part <part> --user-words "..."']}


def _b_row(b):
    return {k: b.get(k) for k in ('part', 'status', 'lost', 'locked', 'dropped_px', 'code') if b.get(k) is not None} | \
        {'must_see_px': (b.get('row') or {}).get('must_see_px')}


def merge_proposals(path, shot_id):
    """The A poses of the shot's proposals (concepts/<shot>/proposals/proposals.json) put on its candidates sheet as
    candidates after the engine's own (source 'proposal'), each with its pose, placement {aim, closer}, subject_at, the
    scene it was solved on and its clay as the image: frames pick takes one like any. Kept when frames candidates makes
    the sheet again on the same scene; solved on another (the shot authored again since), they leave the sheet - frames
    check and frames proposals make them again. -> how many were added."""
    record = _proposals_dir(path, shot_id) / 'proposals.json'
    sheet = path / 'concepts' / shot_id / 'candidates' / 'candidates.json'
    if not record.is_file() or not sheet.is_file():
        return 0
    from .blender_ops.camera_moves_core import placement_of
    found = read_json(sheet)
    proposed = read_json(record)
    current = proposed.get('scene') is not None and proposed.get('scene') == found.get('scene')
    added = 0
    for row in found['moments']:
        row['candidates'] = [c for c in row['candidates'] if c.get('source') != 'proposal']
    for p in proposed['moments'] if current else []:
        row = next((m for m in found['moments'] if _same_at(m['at'], p['at'])), None)
        if row is None:
            continue
        for a in p.get('a') or []:
            image = sheet.parent / f"{p.get('concept_id') or p['at']}_proposal_a{a['n']}.png"
            source = path / (a.get('painted') or a['image'])
            if source.is_file():
                shutil.copy2(source, image)
            row['candidates'].append({
                'n': len(row['candidates']), 'view': a['view'], 'lens_mm': a['lens_mm'], 'score': None,
                'offset': None, 'must_see_px': a.get('must_see_px'), 'departed': a.get('departed') or [],
                'placement': placement_of(a), 'eye': a['eye'], 'aim': a['aim'], 'roll_deg': a.get('roll_deg') or 0.0,
                **{k: a[k] for k in ('subject_at', 'subject_pose') if a.get(k)}, 'frame': a.get('frame'),
                'image': image.name if image.is_file() else None, 'source': 'proposal', 'proposal': f"A{a['n']}",
                'scene': proposed['scene']})
            added += 1
    write_json(sheet, found)
    return added


def proposals_page(path):
    """review/proposals.html: every shot's blocked moments with their A/B choices, one page - the user says A or B per
    shot (frames pick / frames drop record it in their words)."""
    rows = []
    for record in sorted((path / 'concepts').glob('*/proposals/proposals.json')):
        found = read_json(record)
        for m in found['moments']:
            cells = []
            if m.get('picture'):
                cells.append(_tile(m['picture'], ['원래 그림']))
            if m.get('intended'):
                cells.append(_tile(m['intended']['image'], ['그림의 자리 (클레이)', '빨간 상자: 꼭 보일 부분이 있는 곳']))
            for a in m.get('a') or []:
                after = STATUS_WORDS.get((a.get('resolve') or {}).get('status'), '?')
                cells.append(_tile(a.get('painted') or a['image'],
                                   [f"A{a['n']}: " + (', '.join(a.get('departed') or []) or '그림과 같은 자리'),
                                    '보임: ' + ', '.join(f"{_short(k)} {v:g} px" for k, v in (a.get('must_see_px') or {}).items()),
                                    f'그 자리로 다시 그리면: {after}', '의도에서 벗어남 — 고르기 전에는 쓰지 않음']))
            for b in m.get('b') or []:
                lines = [f"B: {_short(b['part'])} 빼기", DROP_LOCKED if b.get('locked') else STATUS_WORDS.get(b.get('status'), b.get('status') or '')]
                if b.get('dropped_px'):
                    lines.append(f"뺀 부품은 {max(b['dropped_px'].values()):g} px로 보임")
                cells.append(_tile(b.get('image'), lines))
            rows.append(f"<section><h2>{html.escape(found['shot_id'])} t={m['at']} {html.escape(str(m.get('concept_id') or ''))}</h2>"
                        f"<ul>{''.join('<li>' + html.escape(line) + '</li>' for line in m.get('lines') or [])}</ul>"
                        f"<div class='row'>{''.join(cells)}</div></section>")
    out = path / 'review' / 'proposals.html'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                   "<title>막힌 장면 제안</title><style>body{font:14px sans-serif;background:#18181a;color:#eee;margin:16px}"
                   ".row{display:flex;flex-wrap:wrap;gap:10px}figure{margin:0;width:240px}img{width:240px;border:1px solid #333}"
                   "figcaption div{margin-top:2px}code{background:#2a2a2e;padding:2px 4px}section{border-bottom:1px solid #333;padding:12px 0}</style>"
                   "</head><body><h1>막힌 장면 — 샷마다 A/B를 말씀해 주세요</h1>"
                   "<p>A (다른 각도로 다시 그리기): <code>studio frames pick --shot &lt;샷&gt; --moment &lt;t&gt; --candidate &lt;번호&gt; "
                   "--user-words \"...\"</code> 다음 <code>studio picture3d run</code><br>B (부품 빼기): <code>studio frames drop --shot &lt;샷&gt; "
                   "--moment &lt;t&gt; --part &lt;부품&gt; --user-words \"...\"</code> 다음 <code>studio frames apply</code></p>"
                   + ''.join(rows) + '</body></html>', encoding='utf-8')
    return out


def _tile(image, lines):
    img = f"<img src='{html.escape(str(Path('..') / image))}' loading='lazy'>" if image else "<div class='s'>그림 없음</div>"
    return f"<figure>{img}<figcaption>{''.join('<div>' + html.escape(str(line)) + '</div>' for line in lines)}</figcaption></figure>"


def drop(project, shot_id, moment, part, user_words):
    """Option B in the user's words: the part taken off what the moment must show - the shot list's show row of the
    moment loses it (decide revise, a JSON-pointer remove; the row goes when it was its only part), approved in the same
    words (decide approve on the sheet the revise made) - then frames apply carries it into the shot. A part the lines
    explain that no other moment shows (cheat_table.drop_locked: SHOT_EXPLAINS_UNSHOWN) is refused here: dropping it
    takes a fact off the screen."""
    from .blender_ops.cheat_table import drop_locked
    from .blender_ops.show_core import at_share, show_of
    from .decisions import apply_ops, approve, envelope, lint, parents_of, revise, settle, state, validate_body
    from .generative.review import check_user_words
    words = check_user_words(user_words, 'frames drop')
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    share = float(moment)
    # a drop is one approved change on top of an approved list: building on a draft would sign the draft's other,
    # unseen edits with the drop's words (2026-10-17 review)
    current = state(path, 'shotlist')
    if current['state'] != 'approved':
        raise StudioError('DECISION_UNAPPROVED', f"frames drop needs an approved shot list; it is {current['state']}: {current['reason']}",
                          recovery='approve or revert the shot list draft first, then drop')
    body = envelope(path, 'shotlist')['body']
    index = next((i for i, s in enumerate(body.get('shots') or []) if s.get('shot_id') == shot_id), None)
    if index is None:
        raise StudioError('INPUT_INVALID', f'{shot_id}: not in the shot list')
    if body['shots'][index].get('show') is None:
        raise StudioError('INPUT_INVALID', f'{shot_id}: its show is read from an older direction.reveal; write show rows first')
    rows = show_of(body['shots'][index])
    at = next((j for j, r in enumerate(rows) if abs(at_share(r['at']) - share) <= 0.02), None)
    if at is None or part not in (rows[at].get('parts') or []):
        raise StudioError('INPUT_INVALID', f"{shot_id} t={share}: the shot list shows no {part} at this moment "
                          f"({[(r['at'], r.get('parts')) for r in rows]})")
    frames = ((shot.get('camera') or {}).get('move') or {}).get('frames') or []
    fr = next((f for f in frames if abs(float(f['at']) - share) < 1e-6), {'at': share, 'must_see': [part]})
    locked = drop_locked(part, fr, frames, cheat_core.locked(path, shot))
    if locked:
        raise StudioError('INPUT_INVALID', f'{shot_id} t={share}: {locked}',
                          recovery='take the line off the shot, show the part at another moment, or choose A (a picture redrawn)')
    left = [p for p in rows[at]['parts'] if p != part]
    pointer = f'/shots/{index}/show/{at}' + ('' if not left else f"/parts/{rows[at]['parts'].index(part)}")
    ops = [{'op': 'remove', 'path': pointer}]
    # what approve would refuse is checked on the revised body before anything is written
    trial = apply_ops(body, ops)
    validate_body('shotlist', trial)
    errors = lint(path, 'shotlist', trial)['errors']
    if errors:
        raise StudioError('DECISION_INVALID', 'shotlist after the drop: ' + '; '.join(errors[:6]), recovery='choose A, or revise the shot list by hand')
    for parent in parents_of(path, 'shotlist'):
        if state(path, parent)['state'] != 'approved':
            raise StudioError('DECISION_UNAPPROVED', f'shotlist is bound to {parent}, which is not approved', recovery=f'approve {parent} first')
    def steps():   # one hold over revise + approve; a failed approve puts the approved list back (decisions.settle)
        revised = revise(project, 'shotlist', words, ops=ops,
                         agent_note=f'frames drop: {part} off {shot_id} t={share} (option B of a blocked moment)')
        return revised, approve(project, 'shotlist', words, revised['rev'])
    revised, approved = settle(project, 'shotlist', steps, 'drop_undone', detail={'shot_id': shot_id, 'part': part})
    return {'shot_id': shot_id, 'at': share, 'part': part, 'removed': pointer, 'revised': revised['rev'],
            'approved': approved.get('rev') or approved.get('state'),
            'next': f'studio frames apply --project {project} --shot {shot_id}'}
