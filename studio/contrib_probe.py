"""Find manifest params a contrib entry ignores (SKILL #8: a `rib` parameter that changes nothing is a placeholder).

The entry runs in studio/contrib_probe_child.py - a separate isolated interpreter, like the contract tests
(studio/contrib_check.py), inside the OS sandbox where there is one (isolated) - on the hash-checked bytes, by the one
run path (blender_ops/contrib_loader.run). Results are cached by the entry's hash (an entry never changes
under the same hash), so a build pays for each entry once.
"""
from __future__ import annotations

import json
from pathlib import Path
import secrets
import subprocess
import sys

from .common import REPO, blender_env, read_json, write_json

CACHE = REPO / '.studio' / 'contrib_probe_cache.json'
TIMEOUT_S = 60


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
    token = secrets.token_hex(16)
    command, isolation = isolated([sys.executable, '-I', REPO / 'studio' / script],
                                  [REPO / 'studio' / script, REPO / 'studio' / 'blender_ops', folder])
    run = subprocess.run(command, input=json.dumps({**job, 'dir': str(folder), 'token': token}), capture_output=True, text=True,
                         timeout=timeout, env=blender_env(), cwd=folder)
    lines = [line for line in run.stdout.splitlines() if line.startswith(f'{tag} {token} ')]
    if len(lines) != 1:
        return {'crashed': (run.stderr or run.stdout)[-600:], 'lines': len(lines)}, isolation
    return json.loads(lines[0][len(f'{tag} {token} '):]), isolation


def probe(entry):
    """{unused: [param, ...], not_probed: [...]} for one resolved entry ({dir, sha256, ...}); {error} if it could not run."""
    cache = read_json(CACHE) if CACHE.is_file() else {}
    if entry['sha256'] in cache:
        return cache[entry['sha256']]
    try:
        result, _ = child('contrib_probe_child.py', {key: entry.get(key) for key in ('dir', 'sha256', 'entry', 'builtins', 'imports')},
                          TIMEOUT_S, 'CONTRIB_PROBE')
    except subprocess.TimeoutExpired:
        return {'error': f'probe timed out ({TIMEOUT_S} s)'}
    except OSError as error:   # the entry folder is gone, or the child could not start
        return {'error': f'probe could not start: {error}'}
    if 'crashed' in result:
        return {'error': f"probe crashed: {result['crashed'][-400:]}"}
    if 'error' not in result:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        write_json(CACHE, {**cache, entry['sha256']: result})
    return result
