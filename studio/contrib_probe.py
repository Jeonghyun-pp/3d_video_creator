"""Find manifest params a contrib entry ignores (SKILL #8: a `rib` parameter that changes nothing is a placeholder).

The entry runs in studio/contrib_probe_child.py - a separate isolated interpreter, like the contract tests
(studio/contrib_check.py), inside the OS sandbox where there is one (isolated) - on the hash-checked bytes, by the one
run path (blender_ops/contrib_loader.run). Results are cached by the entry's hash (an entry never changes
under the same hash), so a build pays for each entry once.

Verdicts (2026-10-18, the user: "contrib 검사 broker로 돌리게 동결 해제해 -> 이거 승인할게"): the OS sandbox the child runs
in cannot be made inside another - macOS refuses sandbox_apply in a sandboxed process (measured: exit 71 even for an
allow-all profile) - so inside an agent's sandbox, and inside the broker's, no entry could be checked, and a build using
one failed. A child's result run under the OS sandbox is now kept as a verdict outside the repository
(~/.config/studio/contrib_verdicts/, beside the frozen-code baseline: an agent sandboxed to the workspace can read it but
not write it - measured the same day), keyed by the entry's code, the checker's own code and the run policy, so a
changed entry or checker is checked again. A confined process (CODEX_SANDBOX, STUDIO_BROKERED) reads verdicts only and
never runs entry code: with none it stops with CONTRIB_NEEDS_BROKER, and the broker checks the entry itself, outside any
sandbox, in the sealed engine (studio/seal.py: the user's approved copy of this code, which the agent cannot write), its
child started with -I -S (no site-packages, no .pth). No path runs entry code without the OS sandbox where there is one. A verdict is kept only for a result about the code it was asked about (the entry's bytes
unchanged by the end of the run) and with no error: a passing moment's glitch never stands for that code.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

from .common import CODE, REPO, StudioError, blender_env, file_hash, lock, now, read_json, stable_hash, write_json

TIMEOUT_S = 60
# the code a verdict stands on besides the entry's: the checker, both children, the loader and the rules they run under
CHECKER_FILES = ('studio/contrib.py', 'studio/contrib_check.py', 'studio/contrib_probe.py', 'studio/contrib_probe_child.py',
                 'studio/blender_ops/sandbox.py', 'studio/blender_ops/contrib_loader.py', 'studio/author_lint.py')
CONFINED = ('CODEX_SANDBOX', 'STUDIO_BROKERED')   # set in a process that already runs inside a sandbox (common.blender_binary)



def verdicts_path():
    """Outside the repository, as the frozen-code baseline is (studio/freeze.baseline_path): one file per checkout."""
    tag = hashlib.sha256(str(REPO.resolve()).encode()).hexdigest()[:12]
    return Path.home() / '.config' / 'studio' / 'contrib_verdicts' / f'{REPO.resolve().name}-{tag}.json'


def checker_sha():
    """The checker a verdict stands on: the code that runs it (the sealed engine's in the broker)."""
    return stable_hash({f: file_hash(CODE / f) for f in CHECKER_FILES})


def verdict_key(script, job):
    """A verdict stands for this child on this entry code, under this checker and this run policy."""
    return stable_hash({'script': script, 'job': {k: v for k, v in job.items() if k not in ('dir', 'token')}, 'checker': checker_sha()})


def confined():
    """This process already runs inside a sandbox: the child's OS sandbox cannot be made here (macOS)."""
    return sys.platform == 'darwin' and any(os.environ.get(name) for name in CONFINED)


def _verdict(key):
    path = verdicts_path()
    return (read_json(path).get('verdicts') or {}).get(key) if path.is_file() else None


def _keep(key, result, isolation, script):
    path = verdicts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock(path.with_suffix('.lock')):
        data = read_json(path) if path.is_file() else {'schema_version': 1, 'verdicts': {}}
        data['verdicts'][key] = {'result': result, 'isolation': isolation, 'script': script, 'at': now()}
        write_json(path, data)


def _sb_quote(path):
    return '"' + str(path).replace('\\', '\\\\').replace('"', '\\"') + '"'


def isolated(command, readable):
    """(command, isolation) for a child that runs entry code. On macOS the child runs inside sandbox-exec: no network, no
    writes, no new processes, and nothing in the home folder read but Python itself and `readable` (the child's own
    scripts and the entry folder) - the hard wall around the pure rule of blender_ops/sandbox.py. Elsewhere the pure rule
    is the only line, and the result says so ('hook')."""
    tool = Path('/usr/bin/sandbox-exec')
    if sys.platform != 'darwin' or not tool.is_file():
        return list(command), 'hook'
    places = {Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(), *(Path(p).resolve() for p in readable)}
    allow = ' '.join(f"({'subpath' if p.is_dir() else 'literal'} {_sb_quote(p)})" for p in sorted(places))
    profile = ('(version 1)(allow default)(deny network*)(deny file-write*)(deny process-fork)'
               f'(deny file-read-data (subpath {_sb_quote(Path.home().resolve())}))(allow file-read-data {allow})')
    return [str(tool), '-p', profile, *map(str, command)], 'os+hook'


def child(script, job, timeout, tag):
    """Run a child script (contrib_check.py, contrib_probe_child.py) on one entry job (JSON on stdin): (its result,
    isolation) - the result is the one stdout line '<tag> <token> {json}', or None. The token is new for every run and
    the child drops it before entry code runs: an entry that prints a result line of its own cannot know it (2026-10-13
    re-audit: an entry printed 'CONTRIB_CHECK {"problems": []}' first and passed). The child works in the entry
    folder, named absolutely: inside the OS sandbox it cannot read the folders above it to resolve a relative one."""
    folder = Path(job['dir']).resolve()
    key = verdict_key(script, job)
    kept = _verdict(key)
    if kept is not None:   # this entry's code was run under the OS sandbox by this checker before
        return kept['result'], kept['isolation']
    if confined():   # never run entry code here: no OS sandbox can be made inside this one
        raise StudioError('CONTRIB_NEEDS_BROKER', f'{folder.name}: a contrib entry is checked under an OS sandbox, which cannot be '
                                                  'made inside this one (an agent sandbox or the broker), and no verdict for this code is kept',
                          recovery='Call the MCP tool studio_run with {"args": ["contrib", "check", "--project", "<project>", "--name", '
                                   f'"{folder.name}"]}}, then run this command again')
    token = secrets.token_hex(16)
    studio = CODE / 'studio'   # -S: no site-packages (.pth files) - the pure run needs the standard library only
    command, isolation = isolated([sys.executable, '-I', '-S', studio / script], [studio / script, studio / 'blender_ops', folder])
    run = subprocess.run(command, input=json.dumps({**job, 'dir': str(folder), 'token': token}), capture_output=True, text=True,
                         timeout=timeout, env=blender_env(), cwd=folder)
    lines = [line for line in run.stdout.splitlines() if line.startswith(f'{tag} {token} ')]
    if len(lines) != 1:
        return {'crashed': (run.stderr or run.stdout)[-600:], 'lines': len(lines)}, isolation
    result = json.loads(lines[0][len(f'{tag} {token} '):])
    from .blender_ops.contrib_loader import code_sha
    if isolation == 'os+hook' and 'error' not in result and code_sha(folder) == job.get('sha256'):
        _keep(key, result, isolation, script)   # ran under the OS sandbox, on the code it was asked about, without a glitch
    return result, isolation


def probe(entry):
    """{unused: [param, ...], not_probed: [...]} for one resolved entry ({dir, sha256, ...}); {error} if it could not run."""
    try:
        result, _ = child('contrib_probe_child.py', {key: entry.get(key) for key in ('dir', 'sha256', 'entry', 'builtins', 'imports')},
                          TIMEOUT_S, 'CONTRIB_PROBE')
    except subprocess.TimeoutExpired:
        return {'error': f'probe timed out ({TIMEOUT_S} s)'}
    # CONTRIB_NEEDS_BROKER goes on: a probe that cannot run is said, not passed
    except OSError as error:   # the entry folder is gone, or the child could not start
        return {'error': f'probe could not start: {error}'}
    if 'crashed' in result:
        return {'error': f"probe crashed: {result['crashed'][-400:]}"}
    return result   # kept as a verdict by child() (the code it ran, the checker, the policy)
