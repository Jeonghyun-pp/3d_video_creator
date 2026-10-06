"""Repair loop policy for shots with subject specs: keep the best version, revert regressions, stop on budget.

Research basis (CADFit/VLM-loop studies): visual critique plateaus after 2-3 rounds and a later
round can make things worse, so each build is scored from its fidelity report and
- a build that scores below the best version is reverted automatically (shot.json points back to best),
- after ``limits.look_iterations_per_shot`` consecutive builds without improving the best, the next build
  is refused (REPAIR_BUDGET_EXHAUSTED) until the user decides (``repair reset --reason``). Regressions always
  count; equal scores and failed builds count only while fidelity is still failing (a camera or look change
  on a passing shot scores the same and is not a repair attempt).
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


def intent(project, shot_id, version):
    """Hash of the deviations the version's specs declare: what the shot is *meant* to look like.
    Versions are only compared against the best of the same intent; declaring a new deviation starts over."""
    folder = shot_path(project, shot_id).parent / 'versions' / version / 'subjects'
    declared = {p.name.removesuffix('.spec.json'): read_json(p).get('deviations', []) for p in sorted(folder.glob('*.spec.json'))} if folder.is_dir() else {}
    return stable_hash(declared)


def budget(project, shot_id):
    return int(load_project(project_dir(project))['limits']['look_iterations_per_shot'])


def check_budget(project, shot_id):
    """Raise before a build when the shot used its repair budget without improving."""
    ledger = load_ledger(project, shot_id)
    limit = budget(project, shot_id)
    if ledger['best'] and ledger['stale_attempts'] >= limit:
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
    current_intent = intent(path, shot_id, version) if version else None
    if best is not None and 'intent' not in best:
        best['intent'] = intent(path, shot_id, best['version'])
    new_intent = value is not None and best is not None and current_intent != best['intent']
    if value is not None and (best is None or new_intent or value > best['score']):
        entry['outcome'] = 'intent_changed' if new_intent else 'new_best'
        ledger['best'] = {'version': version, 'score': value, 'intent': current_intent}
        ledger['stale_attempts'] = 0
        _mirror_best(path, shot_id, version)
    elif value is not None and value < best['score']:
        ledger['stale_attempts'] += 1
        entry['outcome'] = 'regressed'
        select_version(path, shot_id, best['version'], reason=f'auto-revert: {version} scored {value} < {best["score"]}', replaced=version)
        entry['reverted_to'] = best['version']
        decision['reverted_to'] = best['version']
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
    ledger = load_ledger(project, shot_id)
    return {'shot_id': shot_id, 'best': ledger['best'], 'stale_attempts': ledger['stale_attempts'], 'budget': budget(project, shot_id),
            'attempts': ledger['attempts'][-10:], 'current': load_shot(project, shot_id)['scene_version']}


def register_commands(subparsers):
    parser = subparsers.add_parser('repair', help='Repair loop: best version, auto-revert, iteration budget')
    commands = parser.add_subparsers(dest='repair_command', required=True)
    p = commands.add_parser('status'); p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.set_defaults(handler=lambda a: status(a.project, a.shot))
    p = commands.add_parser('reset'); p.add_argument('--project', required=True); p.add_argument('--shot', required=True); p.add_argument('--reason', required=True)
    p.set_defaults(handler=lambda a: reset(a.project, a.shot, a.reason))
