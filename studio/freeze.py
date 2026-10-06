"""Frozen code: the files whose change has a known, user-approved cost (docs/BUILD_REPORT.md), hashed and checked when a
build or a render starts - so an agent cannot change them by accident, or on purpose, without the user's words.

Groups (the same ones tests/freeze_check.py diffs between phases):
  render_fingerprint  render_frames / scene_tools / render_profile / render_worker: every render cache is invalidated
  look_inputs         look modules (import closure of look.py) + look_data/*.json: photoreal revisions re-apply every look pass
  control             control_pass + its imports: every control pass is regenerated
  guard               this file: the check itself is frozen
  contrib_gate        the contrib loader, checker and gate: new vocabulary cannot weaken the checks on itself
  contracts           (phase diffs only) every projects/**/shot.json and style.json
  jet_rig_samples     (phase diffs only) jet_canyon_rig chase camera samples

The code baseline lives outside the repository (~/.config/studio/frozen_code/<repo>.json): an agent sandboxed to the
workspace can read it but not rewrite it. It is recorded only with the user's own words (`studio freeze record
--user-words "..."`); a missing baseline (fresh clone) is a warning, a different one refuses the build (FROZEN_CODE_CHANGED).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

from .common import StudioError, now

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / 'studio' / 'blender_ops'
CODE_GROUPS = ('render_fingerprint', 'look_inputs', 'control', 'guard', 'contrib_gate')


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _closure(entry):
    if str(OPS) not in sys.path:
        sys.path.insert(0, str(OPS))
    from code_closure import module_closure   # the same file set the look hashes into its inputs
    return [OPS / m for m in module_closure(entry)]


def _look_files():
    files = _closure('look.py') + sorted((OPS / 'look_data').glob('*.json'))
    return sorted({f for f in files if f.is_file()})


def code_groups():
    return {
        'render_fingerprint': {p.name: _sha(p) for p in (OPS / 'render_frames.py', OPS / 'scene_tools.py', OPS / 'render_profile.py', ROOT / 'studio/render_worker.py')},
        'look_inputs': {str(p.relative_to(ROOT)): _sha(p) for p in _look_files()},
        'control': {p.name: _sha(p) for p in sorted(set(_closure('control_pass.py'))) if p.is_file()},
        'guard': {'studio/freeze.py': _sha(Path(__file__))},
        'contrib_gate': {p: _sha(ROOT / p) for p in ('studio/contrib.py', 'studio/contrib_check.py', 'studio/blender_ops/contrib_loader.py')},
    }


def groups():
    """Every group, code and contracts, for the phase diffs in tests/freeze_check.py."""
    jet = ROOT / 'tests/fixtures/jet_canyon_rig/shots/chase'
    jet_samples = {}
    if (jet / 'shot.json').is_file():
        version = json.loads((jet / 'shot.json').read_text()).get('scene_version')
        report = jet / 'versions' / str(version) / 'camera_rig_report.json'
        if report.is_file():
            jet_samples = {f'{version}/samples': hashlib.sha256(json.dumps(json.loads(report.read_text())['samples'], sort_keys=True).encode()).hexdigest()}
    return {
        **code_groups(),
        'contracts': {str(p.relative_to(ROOT)): _sha(p) for p in sorted((ROOT / 'projects').glob('**/shots/*/shot.json')) + sorted((ROOT / 'projects').glob('**/style.json'))},
        'jet_rig_samples': jet_samples,
    }


def diff(old, new):
    out = {}
    for group in sorted(set(old) | set(new)):
        a, b = old.get(group, {}), new.get(group, {})
        changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        if changed:
            out[group] = changed
    return out


def baseline_path():
    tag = hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]
    return Path.home() / '.config' / 'studio' / 'frozen_code' / f'{ROOT.name}-{tag}.json'


def check(baseline=None):
    """{'baseline', 'changed', 'warnings'} - changed lists the code groups that differ from the recorded baseline."""
    path = Path(baseline) if baseline else baseline_path()
    if not path.is_file():
        return {'baseline': None, 'changed': {}, 'warnings': [f'FROZEN_BASELINE_MISSING: no frozen-code baseline yet ({path}); '
                                                              'record one with the user\'s words: studio freeze record --user-words "..."']}
    recorded = json.loads(path.read_text())
    current = code_groups()
    new = [g for g in CODE_GROUPS if g not in recorded['groups']]   # a group added after the baseline: not frozen until the user records it
    return {'baseline': str(path), 'changed': diff({g: recorded['groups'][g] for g in CODE_GROUPS if g in recorded['groups']},
                                                   {g: current[g] for g in CODE_GROUPS if g in recorded['groups']}),
            'recorded_at': recorded.get('recorded_at'),
            'warnings': [f'FROZEN_GROUP_UNRECORDED: {new} not in the baseline yet; record it with the user\'s words'] if new else []}


def require_code_frozen(baseline=None):
    """Refuse to build or render on frozen code that changed since the user last approved it. Returns warnings."""
    result = check(baseline)
    if result['changed']:
        files = '; '.join(f"{g}: {', '.join(files[:4])}" for g, files in result['changed'].items())
        raise StudioError('FROZEN_CODE_CHANGED', f'frozen code differs from the approved baseline ({files})',
                          recovery='Undo the change, or show the user the diff and its cost (docs/BUILD_REPORT.md) and record a new '
                                   'baseline with their own words: studio freeze record --user-words "..."')
    return result['warnings']


def record(user_words, baseline=None):
    from .generative.review import check_user_words
    words = check_user_words(user_words, 'freeze record')
    path = Path(baseline) if baseline else baseline_path()
    before = check(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    history = json.loads(path.read_text()).get('history', []) if path.is_file() else []
    history.append({'recorded_at': now(), 'user_words': words, 'changed': before['changed']})
    path.write_text(json.dumps({'schema_version': 1, 'recorded_at': now(), 'user_words': words, 'groups': code_groups(),
                                'history': history[-50:]}, indent=1, sort_keys=True) + '\n')
    return {'baseline': str(path), 'changed_since_last': before['changed'], 'artifacts': [str(path)]}


def register_commands(subparsers):
    parser = subparsers.add_parser('freeze', help='Frozen code: check it, or record a new baseline with the user\'s words')
    commands = parser.add_subparsers(dest='freeze_command', required=True)
    p = commands.add_parser('check'); p.set_defaults(handler=lambda a: check())
    p = commands.add_parser('record', help="record the current frozen code as approved (the user's own words required)")
    p.add_argument('--user-words', required=True)
    p.set_defaults(handler=lambda a: record(a.user_words))
