"""Repair loop policy for shots with subject specs: keep the best version, revert regressions, stop on budget.

Research basis (CADFit/VLM-loop studies): visual critique plateaus after 2-3 rounds and a later
round can make things worse, so each build is scored from its fidelity report and
- a build that scores below the best version is reverted automatically (shot.json points back to best),
- after ``limits.look_iterations_per_shot`` consecutive builds without improving the best, the next build
  is refused (REPAIR_BUDGET_EXHAUSTED) until the user decides (``repair reset --reason``). Regressions always
  count; equal scores and failed builds count only while fidelity is still failing (a camera or look change
  on a passing shot scores the same and is not a repair attempt).
A build whose subject inputs (its specs and the author script that shapes and moves it) are ones already tried since
the best - the best's own, or an earlier attempt's - is not a new repair attempt, whatever else it changed and however
it ended: camera, scene and picture work on a shot whose subject
still fails fidelity spends no repair budget (2026-10-11, rocket_landing: eight shots stopped at REPAIR_BUDGET_EXHAUSTED
after three CAMERA_MOVE_FAILED builds while applying their frame pictures; the booster had not changed).
Score order: passed fidelity, then mean silhouette IoU, then fewer failures, compared only between
versions of the same intent (the specs' declared deviations): declaring a deviation starts a new
baseline instead of counting as a regression. The ledger is
shots/<shot>/repair.json; the best version is mirrored into the latest run.json best_versions.
"""
from __future__ import annotations

from copy import deepcopy
import shutil

from .common import StudioError, check_id, file_hash, lock, now, read_json, safe_path, stable_hash, write_json
from .project import METADATA_FIELDS, load_project, load_shot, project_dir, shot_path, validate_shot


def ledger_path(project, shot_id):
    return shot_path(project, shot_id).parent / 'repair.json'


def load_ledger(project, shot_id):
    path = ledger_path(project, shot_id)
    return read_json(path) if path.is_file() else {'schema_version': 1, 'shot_id': shot_id, 'best': None, 'stale_attempts': 0, 'attempts': []}


def score(project, shot_id, version):
    """(passed, mean IoU, -failures) from the version's fidelity report; None when it has none."""
    path = shot_path(project, shot_id).parent / 'versions' / version / 'fidelity_report.json'
    if not path.is_file():
        return None
    report = read_json(path)
    summaries = [r.get('summary', {}) for r in report['subjects']]
    ious = [s['mean_silhouette_iou'] for s in summaries if s.get('mean_silhouette_iou') is not None]
    return [1 if report['passed'] else 0, round(sum(ious) / len(ious), 4) if ious else 0.0,
            -sum(s.get('failures_n', len(r['failures'])) for s, r in zip(summaries, report['subjects']))]


def subject_inputs(project, shot_id, version=None):
    """Hash of what makes the shot's subjects look and move as they do: their specs and the author script. Of a built
    version: its snapshots (subjects/*.spec.json, author.py); without one: the files the next build would read."""
    root = project_dir(project)
    if version:
        folder = shot_path(root, shot_id).parent / 'versions' / version
        specs = {p.name.removesuffix('.spec.json'): read_json(p) for p in sorted((folder / 'subjects').glob('*.spec.json'))}
        author = folder / 'author.py'
    else:
        from .subjects import spec_path
        shot = load_shot(root, shot_id)
        specs = {e['subject_id']: read_json(spec_path(root, e['subject_id'])) for e in shot.get('subjects') or []
                 if spec_path(root, e['subject_id']).is_file()}
        author = root / shot['author'] if shot.get('author') else None
    return stable_hash({'specs': specs, 'author': file_hash(author) if author is not None and author.is_file() else None})


def measured_now(project, shot_id, version):
    """True when the version's fidelity report was made by today's fidelity code: only such scores compare. A best
    scored by older measuring code (2026-10-11: before photo checks used the photo's pose, s18's best failed 19 checks
    the booster no longer fails) is no yardstick - the next scored build starts a new baseline."""
    from .fidelity import code_sha256
    path = shot_path(project, shot_id).parent / 'versions' / str(version) / 'fidelity_report.json'
    if not path.is_file():
        return False
    codes = {r.get('code_sha256') for r in read_json(path).get('subjects', [])} - {None}   # no hash recorded: nothing to say
    return not codes or codes == {code_sha256()}


INTENT_V = 3   # 2: the approved contract is part of the intent (2026-10-12); 3: its policy too
CONTRACT_FIELDS = ('duration_frames', 'direction', 'goal', 'policy')   # the user's decisions on a shot (shot list, policy)


def intent(project, shot_id, version):
    """Hash of what the version was *meant* to be: the deviations its specs declare and the contract the user approved for
    the shot when it was built (its length, direction and goal, from the version's shot snapshot). Versions are only
    compared against the best of the same intent: a new deviation, or a newly approved plan, starts over.
    Why the contract (2026-10-12): a shot re-planned from 1.8 s to 5.4 s going inside scored below its old best, was
    reverted, and the old shot - length and direction - came back over the user's approval."""
    folder = shot_path(project, shot_id).parent / 'versions' / version
    declared = {p.name.removesuffix('.spec.json'): read_json(p).get('deviations', []) for p in sorted((folder / 'subjects').glob('*.spec.json'))} \
        if (folder / 'subjects').is_dir() else {}
    snapshot = read_json(folder / 'shot.snapshot.json') if (folder / 'shot.snapshot.json').is_file() else {}
    return stable_hash({'deviations': declared, 'contract': {k: snapshot.get(k) for k in CONTRACT_FIELDS}})


def budget(project, shot_id):
    return int(load_project(project_dir(project))['limits']['look_iterations_per_shot'])


APPEARANCE_DEFAULT = 6   # appearance builds per shot when the project names none (limits.appearance_iterations_per_shot)


def appearance(project, shot_id, version):
    """Count a finished build of the shot: the first is its construction, every later one an appearance iteration.
    {'used', 'budget', 'remaining'}."""
    path = project_dir(project)
    ledger = load_ledger(path, shot_id)
    built = ledger.setdefault('built_versions', [])
    if version not in built:
        built.append(version)
        write_json(ledger_path(path, shot_id), ledger)
    limit = int(load_project(path)['limits'].get('appearance_iterations_per_shot', APPEARANCE_DEFAULT))
    used = max(0, len(built) - 1)
    return {'used': used, 'budget': limit, 'remaining': max(0, limit - used)}


def recount(project, shot_id, ledger):
    """Ledgers written before subject_inputs: the failed or equal attempts since the best did not record what subject
    they built. Those on the best's subject were camera or scene work and leave the stale count; on a changed subject
    they count once (one change tried, however many builds it took)."""
    best = ledger.get('best')
    if not best or 'subject_inputs' in best or not ledger.get('stale_attempts'):
        return ledger
    best['subject_inputs'] = subject_inputs(project, shot_id, best['version'])
    tail = []
    for entry in reversed(ledger['attempts']):
        if entry.get('version') == best['version']:
            break
        tail.append(entry)
    moved = [e for e in reversed(tail) if e.get('outcome') in ('failed', 'equal') and 'subject_inputs' not in e]
    changed = best['subject_inputs'] != subject_inputs(project, shot_id)
    for e in moved[1 if changed else 0:]:
        e['outcome'], e['recounted'] = 'not_a_repair', now()
    ledger['stale_attempts'] = max(0, ledger['stale_attempts'] - len(moved) + (1 if changed and moved else 0))
    write_json(ledger_path(project, shot_id), ledger)
    return ledger


def check_budget(project, shot_id):
    """Raise before a build when the shot used its repair budget without improving."""
    ledger = recount(project, shot_id, load_ledger(project, shot_id))
    limit = budget(project, shot_id)
    if ledger['best'] and ledger['stale_attempts'] >= limit and not measured_now(project, shot_id, ledger['best']['version']):
        return (f"REPAIR_BEST_REMEASURE: {ledger['best']['version']} was scored by older fidelity code; this build is "
                'scored afresh and becomes the new baseline')
    if ledger['best'] and ledger['stale_attempts'] >= limit:
        from .gates import is_error, severity_for
        if not is_error('REPAIR_BUDGET_EXHAUSTED', severity_for(project)):   # look-first: say it, keep building
            return f"REPAIR_BUDGET_EXHAUSTED (warning by policy): {ledger['stale_attempts']} builds since {ledger['best']['version']} without improving"
        raise StudioError('REPAIR_BUDGET_EXHAUSTED',
                          f"{shot_id}: {ledger['stale_attempts']} builds since {ledger['best']['version']} without improving its fidelity "
                          f"(limit {limit}); best stays {ledger['best']['version']}",
                          recovery='Change strategy (split parts, add stations, new reference) and ask the user; '
                                   'record their decision with repair reset --reason')


def select_version(project, shot_id, version, reason='select', replaced=None):
    """Point shot.json back at an existing immutable version: the version's scene fields (its shot snapshot) with the
    current metadata (labels, titles, narration, route, fill brief - project.METADATA_FIELDS) kept, revision bumped.
    The version's own subject specs are restored, so fidelity is not stale; the specs they replace are kept beside them."""
    path = project_dir(project)
    version = check_id(version)
    directory = safe_path(shot_path(path, shot_id).parent, f'versions/{version}')
    dependencies = read_json(directory / 'dependencies.json')
    if file_hash(directory / 'scene.blend') != dependencies['scene_sha256']:
        raise StudioError('REVISION_CONFLICT', f'{version} snapshot was modified; it cannot be selected')
    restored = []
    with lock(path / '.project.lock', blocking=False):
        current = load_shot(path, shot_id)
        selected = deepcopy(read_json(directory / 'shot.snapshot.json'))
        # a version built for another approved plan would bring its length and direction back over the approval
        # (2026-10-12 audit; the user chose to refuse it rather than warn)
        differs = [k for k in CONTRACT_FIELDS if selected.get(k) != current.get(k)]
        if differs:
            raise StudioError('VERSION_CONTRACT_DIFFERS', f"{shot_id}: {version} was built for another plan ({', '.join(differs)} differ from the "
                              'approved shot)', recovery='build the shot again for the approved plan, or revise the shot list (decide revise)')
        for key in METADATA_FIELDS:
            if key in current:
                selected[key] = deepcopy(current[key])
            else:
                selected.pop(key, None)
        selected['scene_version'] = version
        selected['revision'] = current['revision'] + 1
        validate_shot(selected)
        for copy in sorted((directory / 'subjects').glob('*.spec.json')):
            live = path / 'subjects' / copy.name[:-len('.spec.json')] / 'spec.json'
            if live.is_file() and file_hash(live) != file_hash(copy):
                kept = live.with_name(f"spec.replaced-{replaced or 'select'}-{file_hash(live)[:8]}.json")
                shutil.copyfile(live, kept)
                shutil.copyfile(copy, live)
                restored.append({'subject_id': live.parent.name, 'replaced_spec': str(kept.relative_to(path))})
        write_json(shot_path(path, shot_id), selected)
    _mirror_best(path, shot_id, version)
    return {'shot_id': shot_id, 'scene_version': version, 'revision': selected['revision'], 'reason': reason, 'restored_specs': restored}


def _mirror_best(path, shot_id, version):
    runs = sorted(path.glob('runs/*/run.json'), key=lambda p: read_json(p).get('created_at', ''))
    if not runs:
        return
    run_path = runs[-1]
    with lock(run_path.parent / '.run.lock'):
        run = read_json(run_path)
        run.setdefault('best_versions', {})[shot_id] = version
        run['updated_at'] = now()
        write_json(run_path, run)


def record(project, shot_id, version, base=None, diagnosis=None, error=None, extra=None):
    """Score a finished (or failed) build, keep/replace the best, revert a regression. Returns the decision."""
    path = project_dir(project)
    ledger = load_ledger(path, shot_id)
    value = score(path, shot_id, version) if version and not error else None
    best = ledger['best']
    entry = {'version': version, 'base': base, 'score': value, 'diagnosis': diagnosis, 'error': error, 'at': now(), **(extra or {})}
    decision = {'score': value, 'best_before': best}
    repairing = best is None or not best['score'][0]  # the budget is for fidelity repair, not for normal work on a passing shot
    inputs = subject_inputs(path, shot_id, version if version and not error else None)
    entry['subject_inputs'] = inputs
    if best is not None and 'subject_inputs' not in best:
        best['subject_inputs'] = subject_inputs(path, shot_id, best['version'])
    since = []
    for earlier in reversed(ledger['attempts']):
        if best is not None and earlier.get('version') == best['version']:
            break
        since.append(earlier.get('subject_inputs'))
    # the best's subject, or one already tried since it: camera / scene / picture work, not a new repair attempt
    same_subject = best is not None and (best.get('subject_inputs') == inputs or inputs in since)
    current_intent = intent(path, shot_id, version) if version else None
    if best is not None and best.get('intent_v') != INTENT_V:   # recorded by an older rule: recomputed, not a new intent
        best['intent'], best['intent_v'] = intent(path, shot_id, best['version']), INTENT_V
    new_intent = value is not None and best is not None and (current_intent != best['intent'] or not measured_now(path, shot_id, best['version']))
    if value is not None and (best is None or new_intent or value > best['score']):
        entry['outcome'] = 'intent_changed' if new_intent else 'new_best'
        ledger['best'] = {'version': version, 'score': value, 'intent': current_intent, 'intent_v': INTENT_V, 'subject_inputs': inputs}
        ledger['stale_attempts'] = 0
        _mirror_best(path, shot_id, version)
    elif value is not None and value < best['score'] and value[0] and best['score'][0]:
        # both pass fidelity: a lower mean IoU is a design choice within the spec, not a regression to revert
        entry['outcome'] = 'passing_lower'
    elif value is not None and value < best['score']:
        ledger['stale_attempts'] += 1
        entry['outcome'] = 'regressed'
        select_version(path, shot_id, best['version'], reason=f'auto-revert: {version} scored {value} < {best["score"]}', replaced=version)
        entry['reverted_to'] = best['version']
        decision['reverted_to'] = best['version']
    elif same_subject:
        entry['outcome'] = 'not_a_repair'
    else:
        entry['outcome'] = 'equal' if value is not None else 'failed'
        if repairing:
            ledger['stale_attempts'] += 1
    ledger['attempts'].append(entry)
    write_json(ledger_path(path, shot_id), ledger)
    decision.update({'outcome': entry['outcome'], 'best': ledger['best'], 'stale_attempts': ledger['stale_attempts'],
                     'budget': budget(path, shot_id)})
    return decision


def reset(project, shot_id, reason):
    if not reason or len(reason.strip()) < 8:
        raise StudioError('INPUT_INVALID', "repair reset needs the user's decision in --reason")
    path = project_dir(project)
    ledger = load_ledger(path, shot_id)
    ledger['stale_attempts'] = 0
    ledger.setdefault('resets', []).append({'reason': reason, 'at': now()})
    write_json(ledger_path(path, shot_id), ledger)
    return {'shot_id': shot_id, 'stale_attempts': 0, 'best': ledger['best']}


def status(project, shot_id):
    ledger = recount(project, shot_id, load_ledger(project, shot_id))
    return {'shot_id': shot_id, 'best': ledger['best'], 'stale_attempts': ledger['stale_attempts'], 'budget': budget(project, shot_id),
            'attempts': ledger['attempts'][-10:], 'current': load_shot(project, shot_id)['scene_version']}


def register_commands(subparsers):
    parser = subparsers.add_parser('repair', help='Repair loop: best version, auto-revert, iteration budget')
    commands = parser.add_subparsers(dest='repair_command', required=True)
    p = commands.add_parser('status'); p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.set_defaults(handler=lambda a: status(a.project, a.shot))
    p = commands.add_parser('reset'); p.add_argument('--project', required=True); p.add_argument('--shot', required=True); p.add_argument('--reason', required=True)
    p.set_defaults(handler=lambda a: reset(a.project, a.shot, a.reason))
