"""Contrib: vocabulary an agent adds without touching the engine - and the library learns it.

Kinds (KINDS): mesh (a builder: params -> verts, faces), profile (a 2D section: params -> points, used by the profile and
revolve builders), coupling (a joint law: driver value -> driven value), spec (a subject spec generator). An entry is a
folder with impl.py (one pure function: no bpy, no files, no processes - author_lint 'pure') and manifest.json
{kind, name, entry, params: {name: default}, lengths: [params that are lengths], words: [what a user would say],
description}.

  draft     projects/<p>/contrib/<kind>/<name>/      used as 'contrib:<name>@draft'
  promoted  library/contrib/<kind>/<name>/vNNN/      used as 'contrib:<name>@vNNN' (pinned: a later version never
                                                     reaches a project that did not ask for it)

Lines (structural): the lint and the contract tests run before any use (the contract tests in a separate isolated
interpreter, studio/contrib_check.py); the loader runs code only when its hash matches what was resolved; promotion is
automatic but only after the entry passed its contracts AND a project build used it and passed fidelity and the
mechanism checks; provenance is recorded; `contrib deprecate` stops new use. This file, the checker and the loader are a
frozen code group (studio/freeze.py), so the checks cannot be weakened by the code they check.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from .blender_ops.contrib_loader import PREFIX, code_sha, is_ref, parse
from .common import REPO, StudioError, blender_env, check_id, lock, now, read_json, write_json
from .project import project_dir

LIBRARY = REPO / 'library' / 'contrib'
KINDS = ('mesh', 'profile', 'coupling', 'spec')


def _draft_dir(path, name):
    hits = [d for kind in KINDS for d in [project_dir(path) / 'contrib' / kind / check_id(name)] if d.is_dir()]
    if not hits:
        raise StudioError('INPUT_INVALID', f'no draft contrib {name} in this project (contrib/<kind>/{name}/)')
    return hits[0]


def _index():
    path = LIBRARY / 'index.json'
    return read_json(path) if path.is_file() else {'schema_version': 1, 'entries': []}


def _promoted_dir(name, version):
    row = next((e for e in _index()['entries'] if e['name'] == name and e['version'] == version), None)
    if row is None:
        raise StudioError('INPUT_INVALID', f'no promoted contrib {name}@{version} (see library/contrib/index.json)')
    return LIBRARY / row['path'], row


def words(ref):
    """The words a promoted entry is described with (its manifest), or [] for a draft or unknown ref."""
    try:
        name, version = parse(ref)
    except ValueError:
        return []
    row = next((e for e in _index()['entries'] if e['name'] == name and e['version'] == version), None)
    return list(row['words']) if row else []


def check(folder):
    """{'ok', 'problems', 'lint', 'contract'} for one entry folder - lint first, then the contracts in a separate process."""
    from .author_lint import lint
    folder = Path(folder)
    problems = []
    try:
        manifest = read_json(folder / 'manifest.json')
    except (OSError, ValueError) as error:
        return {'ok': False, 'problems': [f'manifest.json: {error}']}
    for key in ('kind', 'name', 'entry', 'params', 'words'):
        if key not in manifest:
            problems.append(f'manifest.json needs {key}')
    if manifest.get('kind') not in KINDS:
        problems.append(f"kind {manifest.get('kind')!r} (known: {KINDS})")
    if problems:
        return {'ok': False, 'problems': problems}
    linted = lint(folder / 'impl.py', 'pure')
    problems += [f"impl.py:{e['line']} {e['rule']} - {e['hint']}" for e in linted['errors']]
    if len(linted['modules']) > 1:
        problems.append('an entry is one self-contained file (no companion modules)')
    if problems:
        return {'ok': False, 'problems': problems, 'lint': linted}
    try:
        run = subprocess.run([sys.executable, '-I', str(REPO / 'studio/contrib_check.py'), str(folder)], capture_output=True, text=True,
                             timeout=120, env=blender_env())
    except subprocess.TimeoutExpired:
        return {'ok': False, 'problems': ['contract tests timed out (120 s)']}
    line = next((l for l in run.stdout.splitlines() if l.startswith('CONTRIB_CHECK ')), None)
    if line is None:
        return {'ok': False, 'problems': [f'contract tests crashed: {run.stderr[-600:]}']}
    contract = json.loads(line[len('CONTRIB_CHECK '):])
    problems += contract['problems']
    if manifest['kind'] == 'spec' and not problems:
        from .project import validate_schema
        from .subjects import lint_spec
        try:
            validate_schema(contract['spec'], 'subject')
            problems += [f'generated spec: {e}' for e in lint_spec(contract['spec'])['errors']]
        except StudioError as error:
            problems.append(f'generated spec: {error.message}')
    return {'ok': not problems, 'problems': problems, 'kind': manifest['kind'], 'sha256': code_sha(folder)}


def refs_in(document):
    """Every 'contrib:...' string anywhere in a spec or shot."""
    out = set()
    stack = [document]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack += list(node.values())
        elif isinstance(node, list):
            stack += node
        elif is_ref(node):
            out.add(node)
    return out


def resolve(path, refs):
    """{ref: {dir, sha256, kind, entry, draft}} for a build, each entry checked; refuses deprecated or failing entries."""
    table = {}
    for ref in sorted(refs):
        name, version = parse(ref)
        if version == 'draft':
            folder, row = _draft_dir(path, name), None
        else:
            folder, row = _promoted_dir(name, version)
            if row.get('deprecated'):
                raise StudioError('CONTRIB_DEPRECATED', f"{ref} is deprecated ({row['deprecated']}); pin a newer version",
                                  recovery='See library/contrib/index.json for the versions of this entry')
        result = check(folder)
        if not result['ok']:
            raise StudioError('CONTRIB_INVALID', f'{ref}: ' + '; '.join(result['problems'][:6]),
                              recovery='Fix the entry (studio/contrib.py lists the contracts); run: studio contrib check')
        manifest = read_json(folder / 'manifest.json')
        table[ref] = {'dir': str(folder), 'sha256': result['sha256'], 'kind': manifest['kind'], 'entry': manifest['entry'],
                      'draft': version == 'draft', 'params': manifest['params']}
    return table


def promote(path, name, used_by):
    """Copy a draft into the library as the next version, with provenance. Called by auto_promote, never on a failing build."""
    folder = _draft_dir(path, name)
    sha = code_sha(folder)
    with lock(LIBRARY.parent / '.contrib.lock'):
        index = _index()
        existing = [e for e in index['entries'] if e['name'] == name]
        same = next((e for e in existing if e['sha256'] == sha), None)
        if same:
            return same
        number = max([int(e['version'][1:]) for e in existing] or [0]) + 1
        manifest = read_json(folder / 'manifest.json')
        target = LIBRARY / manifest['kind'] / check_id(name) / f'v{number:03d}'
        target.mkdir(parents=True)
        for file in ('impl.py', 'manifest.json'):
            shutil.copy2(folder / file, target / file)
        row = {'name': name, 'version': f'v{number:03d}', 'kind': manifest['kind'], 'path': str(target.relative_to(LIBRARY)), 'sha256': sha,
               'words': manifest['words'], 'promoted_at': now(), 'provenance': used_by}
        write_json(target / 'provenance.json', row)
        index['entries'].append(row)
        write_json(LIBRARY / 'index.json', index)
        return row


def auto_promote(path, table, build):
    """After a passing build: every draft it used whose contracts passed goes into the library (provenance: the build)."""
    fidelity = build.get('fidelity') or {}
    if fidelity and not fidelity.get('passed', True):
        return []
    promoted = []
    for ref, entry in table.items():
        if entry['draft']:
            row = promote(path, parse(ref)[0], {'project': str(project_dir(path)), 'shot_id': build.get('shot_id'),
                                                 'version': build.get('scene_version')})
            promoted.append(f"contrib:{row['name']}@{row['version']}")
    return promoted


def deprecate(name, version, reason):
    with lock(LIBRARY.parent / '.contrib.lock'):
        index = _index()
        row = next((e for e in index['entries'] if e['name'] == name and e['version'] == version), None)
        if row is None:
            raise StudioError('INPUT_INVALID', f'no promoted contrib {name}@{version}')
        row['deprecated'] = reason
        write_json(LIBRARY / 'index.json', index)
    return {'deprecated': f'{PREFIX}{name}@{version}', 'reason': reason}


def register_commands(subparsers):
    parser = subparsers.add_parser('contrib', help='New builders / profiles / couplings / spec generators as checked, versioned entries')
    commands = parser.add_subparsers(dest='contrib_command', required=True)
    p = commands.add_parser('check'); p.add_argument('--project', required=True); p.add_argument('--name', required=True)
    p.set_defaults(handler=lambda a: check(_draft_dir(a.project, a.name)))
    p = commands.add_parser('list')
    p.set_defaults(handler=lambda a: _index())
    p = commands.add_parser('deprecate'); p.add_argument('--name', required=True); p.add_argument('--version', required=True)
    p.add_argument('--reason', required=True)
    p.set_defaults(handler=lambda a: deprecate(a.name, a.version, a.reason))
