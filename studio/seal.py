"""The sealed engine: the copy of the studio's code the user approved, which is the only code that runs outside the
agent's sandbox.

Why (2026-10-18): the agent can edit the checkout, and the parts of the studio that run outside its sandbox - the MCP
server (its builds, its contrib checks), the broker's commands, the network workers it dispatches - ran the checkout's
code: whatever the agent had edited, planted beside it (a json.py first on the path), or compiled into __pycache__.
Reviews found four holes of that one kind. Closing them one by one left the next; so none of that code comes from the
checkout any more. `studio freeze record --user-words "..."` - the user's own words, as the frozen-code baseline always
needed - copies the engine (studio/, schemas/, templates/, scripts/reel_agent.py) to ~/.config/studio/engine/<checkout>/<sha16>/,
outside the workspace the agent may write (measured: its sandbox cannot write ~/.config), hashed before and after the
copy; current.json names it. Everything outside the sandbox starts it through its launcher (launch_argv: python -I -B,
its own pycache folder) against the checkout's data (projects, library, .studio). The agent keeps editing the checkout;
an edit reaches outside the sandbox only when the user seals again.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

from .common import CODE, REPO, StudioError, now

PARTS = ('studio', 'schemas', 'templates')         # folders sealed whole (no __pycache__, no .pyc): every top-level folder
                                                   # the engine reads under CODE (tests/test_seal: 2026-10-20, subject init
                                                   # read templates/ the seal had left out)
FILES = ('scripts/reel_agent.py', 'AGENTS.md')     # single files the sealed side runs or hands the agent
SKIP_DIRS = {'__pycache__'}
SKIP_SUFFIXES = ('.pyc', '.pyo')


def root():
    """~/.config/studio/engine/<checkout name>-<tag>/ - one per checkout, keyed by the data root (as the baseline is)."""
    tag = hashlib.sha256(str(REPO.resolve()).encode()).hexdigest()[:12]
    return Path.home() / '.config' / 'studio' / 'engine' / f'{REPO.resolve().name}-{tag}'


def engine_files(base=CODE):
    """{relative path: sha256} of the engine under `base`."""
    out = {}
    for part in PARTS:
        for path in sorted((Path(base) / part).rglob('*')):
            rel = path.relative_to(base)
            if path.is_file() and not SKIP_DIRS & set(rel.parts) and not path.name.endswith(SKIP_SUFFIXES):
                out[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in FILES:
        if (Path(base) / name).is_file():
            out[name] = hashlib.sha256((Path(base) / name).read_bytes()).hexdigest()
    return out


def digest(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def current():
    """The sealed engine folder in use, or None (never sealed)."""
    pointer = root() / 'current.json'
    if not pointer.is_file():
        return None
    folder = root() / json.loads(pointer.read_text())['sha16']
    return folder if (folder / 'seal.json').is_file() else None


def seal(user_words, expect=None):
    """Copy the checkout's engine as it is now, in the user's words (called by freeze.record, after it checked them). The
    checkout is read as data (REPO): run from the sealed engine, no checkout code runs to seal it (2026-10-18 review);
    the files that changed since the last seal are recorded with it, so what the user's words approved is on record."""
    before = current()
    previous = json.loads((before / 'seal.json').read_text())['files'] if before else {}
    files = engine_files(REPO)
    sha = digest(files)
    if expect is not None and not sha.startswith(expect):   # the checkout changed after the user saw it (seal status)
        raise StudioError('SEAL_FAILED', f'the checkout is {sha[:16]} now, not the {expect} the user saw - show seal status again',
                          recovery='studio seal status, show the user what changed, then freeze record --expect <its checkout_digest>')
    folder = root() / sha[:16]
    if not (folder / 'seal.json').is_file():
        root().mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.sealing-', dir=root()))
        try:
            for rel, want in files.items():
                target = staging / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if (REPO / rel).is_symlink():   # a link could seal a file from anywhere the agent pointed it
                    raise StudioError('SEAL_FAILED', f'{rel} is a link: the engine is sealed from real files only')
                shutil.copyfile(REPO / rel, target)
                if hashlib.sha256(target.read_bytes()).hexdigest() != want:
                    raise StudioError('SEAL_FAILED', f'{rel} changed while it was sealed: seal again')
            changed = sorted(f for f in set(files) | set(previous) if files.get(f) != previous.get(f))
            (staging / 'seal.json').write_text(json.dumps({'schema_version': 1, 'sha': sha, 'files': files, 'data_root': str(REPO.resolve()),
                                                           'user_words': user_words, 'sealed_at': now(), 'previous': (before.name if before else None),
                                                           'changed_since_previous': changed}, ensure_ascii=False, indent=1))
            shutil.rmtree(folder, ignore_errors=True)
            staging.rename(folder)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    (root() / 'current.json').write_text(json.dumps({'sha16': sha[:16], 'user_words': user_words, 'at': now()}, ensure_ascii=False))
    manifest = json.loads((folder / 'seal.json').read_text())
    return {'engine': str(folder), 'sha': sha, 'files': len(files), 'changed_since_previous': manifest.get('changed_since_previous')}


def groups_of(engine):
    """The frozen-code groups of a sealed engine, as that engine computes them (studio freeze groups): the baseline the
    user's words record is the sealed copy's, whatever engine recorded it."""
    import subprocess
    done = subprocess.run(launch_argv('studio', 'freeze', 'groups', engine=engine), capture_output=True, text=True, timeout=300,
                          env={**__import__('os').environ, 'STUDIO_DATA_ROOT': str(REPO)})
    lines = [l for l in done.stdout.splitlines() if l.startswith('{')]
    result = json.loads(lines[-1]) if lines else {}
    if not result.get('ok'):
        raise StudioError('SEAL_FAILED', f'the sealed engine could not list its frozen code: {(done.stderr or done.stdout)[-400:]}')
    return result['groups']


def launch_argv(what, *args, engine=None):
    """The command that starts `what` (studio | mcp | reel_agent) from an engine - the sealed one by default - isolated
    from the checkout (studio/_launch.py)."""
    engine = Path(engine) if engine else current()
    if engine is None:
        raise StudioError('ENGINE_NOT_SEALED', 'no sealed engine for this checkout: nothing outside the sandbox may run the checkout\'s code',
                          recovery='show the user the change and record it in their words: studio freeze record --user-words "..."')
    return [sys.executable, '-I', '-B', '-X', f'pycache_prefix={engine / ".pyc"}', str(engine / 'studio' / '_launch.py'), what, *map(str, args)]


def running_sealed():
    """This process runs a sealed engine (its code is under the seal root)."""
    return CODE.resolve().is_relative_to(root().resolve())


def status():
    """The sealed engine, and how the checkout differs from it (files changed, added, gone)."""
    folder = current()
    if folder is None:
        return {'sealed': None, 'behind': None, 'checkout_digest': digest(engine_files(REPO))[:16]}
    sealed = json.loads((folder / 'seal.json').read_text())
    here = engine_files(REPO)
    changed = sorted(f for f in set(here) | set(sealed['files']) if here.get(f) != sealed['files'].get(f))
    return {'sealed': str(folder), 'sha': sealed['sha'], 'sealed_at': sealed['sealed_at'], 'user_words': sealed['user_words'],
            'behind': changed, 'running_sealed': running_sealed(), 'code': str(CODE), 'checkout_digest': digest(here)[:16],
            'launch_astra': ' '.join(launch_argv('reel_agent', engine=folder)) + ' <request> [options]'}


def behind_warning():
    """ENGINE_SEAL_BEHIND for a result made by the sealed engine while the checkout has moved on, else None."""
    try:
        state = status()
    except (OSError, ValueError, KeyError):
        return None
    if state['sealed'] and state['behind']:
        return (f"ENGINE_SEAL_BEHIND: outside the sandbox the sealed engine ({state['sha'][:12]}, {state['sealed_at']}) runs; "
                f"the checkout differs in {len(state['behind'])} files ({state['behind'][:4]}) - they reach it when the user "
                'records them: studio seal status, studio freeze record --user-words "..."')
    return None


def register_commands(subparsers):
    parser = subparsers.add_parser('seal', help="The sealed engine: the user's approved copy that runs outside the agent's sandbox")
    commands = parser.add_subparsers(dest='seal_command', required=True)
    commands.add_parser('status').set_defaults(handler=lambda a: status())
