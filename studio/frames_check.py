"""frames check: can a shot's frame pictures be shot - right after they are read, before any build.

  studio frames check --project P --shot S [--apply] [--moment-s 420] [--shot-s 1200] [--solve exact|intent]

Opens the shot's last authored checkpoint (authored.blend of its newest version, kept even when a build failed; with
none, or when it no longer stands for the shot - its author script, a module beside it, a spec or the shot changed
since: checkpoint_stale - the author step alone is run to make one, blender._build_shot stop_after='authored', no version) and runs only
what the camera needs (fill, drives, the declared cheats, the support check, the openings, the camera solve:
blender_ops/frames_check.py) - nothing saved, nothing rendered. Each pictured moment comes back as one of
  as_drawn        solved at the picture's own view and lens, with no warning
  cheat_recorded  solved as drawn with the cheats already recorded for it
  moved           solved, but not as drawn: the solve's own view step, another lens, a lean not reached, the camera in a
                  volume - and no cheat brings the picture back (a viewpoint departure under the project's policy)
  cheat           a declared cheat (cheat_core.CHEATS) makes it solve - or brings a moved moment back to its picture -
                  checked again with every other moment's chosen cheats applied as a build applies them (verify)
  redraw          FRAME_NEEDS_REDRAW, after the whole search: only a cheat on what the shot's lines speak of solves it,
                  or nothing does - draw the picture again (the code and numbers say why)
  undecided       the time (--moment-s, --shot-s) ran out first: run again with more time
With the shot's move solved by its pictures' intent (move.params.solve 'intent', or --solve intent for this check) a
moment is as_intended (it keeps its picture's size class, side of level and lens class, nothing said) or moved (what
it gave up is listed: FRAME_INTENT_DEPARTED, a warning); the cheats are scored on the probe and only the best solved in
full, and the verdict is the whole search's: the time only stops a run (stopped), it never decides one.
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
from .common import REPO, StudioError, blender_binary, now, read_json, run_command, write_json
from .project import load_project, load_shot, project_dir, shot_path

STATUS_WORDS = {'as_drawn': '그림 그대로', 'as_intended': '그림의 의도대로', 'cheat_recorded': '치팅(기록된 것)으로 그림 그대로',
                'moved': '움직여서 풀림', 'cheat': '치팅으로 해결', 'redraw': '다시 그리기', 'undecided': '판정 못 함 (시간 한도)',
                'stopped': '중단됨 (실행 시간이 다 됨, 판정 아님)', 'error': '검사 오류', 'other': '다른 실패'}


def latest_authored(path, shot_id):
    """The newest authored.blend among the shot's versions (failed builds keep theirs), or None."""
    folder = shot_path(path, shot_id).parent / 'versions'
    found = sorted(folder.glob('*/authored.blend'), key=lambda p: p.stat().st_mtime) if folder.is_dir() else []
    return found[-1] if found else None


def _job(path, project, shot, work, lock, moment_s, shot_s, solve=None):
    from .blender import _checked_specs, _picture_layout, generator_inputs, probe_inputs
    specs = _checked_specs(path, shot)
    spec_paths = {}
    for subject_id, spec in specs.items():
        spec_paths[subject_id] = str(work / 'subjects' / f'{subject_id}.spec.json')
        write_json(Path(spec_paths[subject_id]), spec)
    style = read_json(path / 'style.json') if (path / 'style.json').exists() else {}
    return {**generator_inputs(path, project, shot['shot_id'], shot, spec_paths, style), **_picture_layout(path, shot, solve),
            'output_dir': str(work),
            'probe': probe_inputs(path, shot, project['output']['fps'], style, (project['output']['width'], project['output']['height'])),
            'locked': lock, 'check': {'moment_s': moment_s, 'shot_s': shot_s, **({'solve': solve} if solve else {})}}


def classify(moment, pre_camera, reading, misread_why, verified):
    """The moment's status: other when the scene fails apart from the camera (before it, or a misread picture); a cheat
    that broke another moment when its cheats were applied together is undecided (the conflict is said)."""
    if pre_camera or reading or misread_why:
        return 'other'
    status = moment.get('status') or 'other'
    if status == 'cheat' and verified is False:
        return 'undecided'
    return status


def redraw_note(moment):
    if moment.get('status') != 'redraw':
        return None
    first = moment['as_is']
    locked = moment.get('locked_solves') or []
    if locked:
        c = locked[0]
        return (f"FRAME_NEEDS_REDRAW: only {c['kind']} {c['target']} {c['value']} solves it, and {c['why_locked']} - draw the picture "
                f"so the line's facts hold as they are ({first['code']}: {first['message'][:240]})")
    tried = len(moment.get('attempts') or [])
    return f"FRAME_NEEDS_REDRAW: no cheat within the table's limits solves it ({tried} tried) - {first['code']}: {first['message'][:360]}"


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
    now_shot, then_shot = (_read_by_author(s) for s in (lit(shot, load_world(path)), made.get('shot') or {}))
    changed = sorted(k for k in set(now_shot) | set(then_shot) if now_shot.get(k) != then_shot.get(k))
    if changed:
        out.append(f'the shot changed since ({changed})')
    return out


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


SOLVED = ('as_drawn', 'as_intended', 'cheat_recorded', 'moved', 'cheat')   # camera statuses of a moment that solves


def _verified(found):
    """Whether, with every chosen cheat applied together, each moment that solved (as it stood or with its cheat)
    still solves: True, False, or None (nothing chosen, or not checked). A moment that solved in no way is not asked."""
    rows = found.get('verify')
    if not isinstance(rows, dict) or 'error' in rows or 'skipped' in rows:
        return None if not rows else False
    solved = {str(m['at']) for m in found.get('moments') or [] if m.get('status') in SOLVED}
    return all(r['ok'] for at, r in rows.items() if at in solved)


def check(project, shot_id, apply=False, moment_s=420.0, shot_s=1200.0, solve=None):
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
        job = _job(path, project_row, shot, work, lock, moment_s, shot_s, solve)
        write_json(work / 'job.json', job)
        command = [blender_binary(), '--background', '--factory-startup', '--disable-autoexec', str(blend), '--python-exit-code', '1',
                   '--python', str(REPO / 'studio/blender_ops/frames_check.py'), '--', str(work / 'job.json')]
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
        rows.append({**m, 'status': classify(m, found.get('pre_camera'), reading, wrong, verified), 'camera_status': m.get('status'),
                     'verified': verified, 'redraw': redraw_note(m), 'flattened': flat, 'notes': list(dict.fromkeys(notes)),
                     'picture_path': next((str(p['path']) for p in _frames(path, shot_id) if p['concept_id'] == m.get('concept_id')), None)})
    result = {'shot_id': shot_id, 'checked_at': now(), 'scene': str(blend.relative_to(path)), **({'scene_remade_because': stale} if stale else {}),
              **({'scene_made': made['authored']} if made else {}),
              'complete': bool(found.get('complete')) and not failed, **({'stopped': failed} if failed else {}),
              'reading': reading, 'pre_camera': found.get('pre_camera') or [], 'error': found.get('error'), 'locked': lock,
              'moments': rows, 'verify': found.get('verify'), 'solve': found.get('solve'), 'seconds': round(time.monotonic() - started, 1),
              'blender_seconds': found.get('seconds'), 'policy': cheat_core.viewpoint_policy(path)}
    if apply:
        result['applied'] = _record(path, shot_id, rows, result['policy'])
    write_json(folder / 'frames_check.json', result)
    sheet = _sheet(path, shot_id, result)
    return {'shot_id': shot_id, 'complete': result['complete'], 'scene': result['scene'], 'solve': result['solve'],
            **({'scene_remade_because': stale} if stale else {}),
            'moments': [{k: r.get(k) for k in ('at', 'concept_id', 'status', 'verified', 'redraw', 'flattened', 'notes', 'seconds')}
                        | {'cheats': (r.get('solved_by') or {}).get('cheats'),
                           'probed': len(r.get('probes') or (r.get('search') or {}).get('probes') or []),
                           'solved_in_full': len(r.get('attempts') or (r.get('search') or {}).get('attempts') or [])} for r in rows],
            'pre_camera': result['pre_camera'], 'reading': reading, 'error': result['error'], 'seconds': result['seconds'],
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
        if row['status'] != 'cheat' or row.get('verified') is not True:
            continue
        cheats = row['solved_by']['cheats']
        if any(cheat_core.CHEATS[c['kind']]['targets'] == 'camera' for c in cheats) and not policy:
            applied.append({'concept_id': row['concept_id'], 'skipped': 'a lens or view cheat needs project.policy.viewpoint_cheat'})
            continue
        before = f"as drawn {row['as_is']['code']} ({str(row['as_is'].get('message'))[:160]})" if row['as_is'].get('code') else \
            f"as drawn it moved: {'; '.join(row.get('moved') or [])[:160]}"
        solved = row['solved_by']['row']
        why = f"frames check: {before}; with this it solves at {solved.get('lens_mm')} mm, view {solved.get('view')}"
        entries = [cheat_core.entry(c['kind'], c.get('target'), c['value'], why, 'agent', now(),
                                    'policy.viewpoint_cheat' if cheat_core.CHEATS[c['kind']]['targets'] == 'camera' else 'cheat_table')
                   for c in cheats]
        picture['cheats'] = [c for c in picture.get('cheats') or [] if (c['kind'], json.dumps(c.get('target'))) not in
                             {(e['kind'], json.dumps(e.get('target'))) for e in entries}] + entries
        applied.append({'concept_id': row['concept_id'], 'cheats': entries})
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
            detail += [f"{c['kind']} {c.get('target') or ''} {c['value']}" for c in m['solved_by']['cheats']]
            detail.append({True: '모든 순간에서 함께 확인됨', False: '함께 적용하면 다른 순간이 깨짐', None: '함께 확인 안 됨'}[m.get('verified')])
        detail += [f"기록된 치팅: {c['kind']} {c.get('target') or ''} {c['value']}" for c in m.get('recorded') or []]
        if (m.get('as_is') or {}).get('code'):
            detail.append(f"그대로: {m['as_is']['code']}")
        detail += [f"FRAME_FLATTENED: {f}" for f in m.get('flattened') or []] + list(m.get('notes') or [])
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
        job = _job(path, load_project(path), shot, work, cheat_core.locked(path, shot), 600.0, 1800.0, 'intent')
        job.update({'keep': int(keep), 'height': int(height)})
        write_json(work / 'job.json', job)
        run_command([blender_binary(), '--background', '--factory-startup', '--disable-autoexec', str(blend), '--python-exit-code', '1',
                     '--python', str(REPO / 'studio/blender_ops/frame_candidates.py'), '--', str(work / 'job.json')],
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
    entry = {'view': [float(v) for v in chosen['view']], 'lens_mm': float(chosen['lens_mm']), 'eye': chosen['eye'], 'aim': chosen['aim'],
             **({'placement': chosen['placement']} if chosen.get('placement') else {}), 'candidate': int(candidate),
             'by': 'user', 'user_words': words, 'at': now()}
    with lock(path / '.project.lock'):
        store = _store(path, shot_id)
        picture = store['pictures'].get(row['concept_id'])
        if picture is None:
            raise StudioError('INPUT_INVALID', f"{shot_id}: no reading of {row['concept_id']} (frames measure first)")
        picture['chosen'] = entry
        write_json(_store_path(path, shot_id), store)
    return {'shot_id': shot_id, 'concept_id': row['concept_id'], 'at': row['at'], 'chosen': entry,
            'next': f'studio frames apply --project {project} --shot {shot_id}'}


def _same_at(at, moment):
    try:
        return abs(float(at) - float(moment)) < 1e-6
    except (TypeError, ValueError):
        return False
