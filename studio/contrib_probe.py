"""Find manifest params a contrib entry ignores (SKILL #8: a `rib` parameter that changes nothing is a placeholder).

The entry runs in studio/contrib_probe_child.py - a separate isolated interpreter, like the contract tests
(studio/contrib_check.py), inside the OS sandbox where there is one (isolated) - on the hash-checked bytes, by the one
run path (blender_ops/contrib_loader.run). Results are cached by the entry's hash (an entry never changes
under the same hash), so a build pays for each entry once.
"""
from __future__ import annotations

import json
from pathlib import Path
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


def child(script, job, timeout):
    """Run a child script (contrib_check.py, contrib_probe_child.py) on one entry job (JSON on stdin): (completed
    process, isolation). The child works in the entry folder, named absolutely: inside the OS sandbox it cannot read
    the folders above it to resolve a relative one."""
    folder = Path(job['dir']).resolve()
    command, isolation = isolated([sys.executable, '-I', REPO / 'studio' / script],
                                  [REPO / 'studio' / script, REPO / 'studio' / 'blender_ops', folder])
    return subprocess.run(command, input=json.dumps({**job, 'dir': str(folder)}), capture_output=True, text=True, timeout=timeout,
                          env=blender_env(), cwd=folder), isolation


def probe(entry):
    """{unused: [param, ...], not_probed: [...]} for one resolved entry ({dir, sha256, ...}); {error} if it could not run."""
    cache = read_json(CACHE) if CACHE.is_file() else {}
    if entry['sha256'] in cache:
        return cache[entry['sha256']]
    try:
        run, _ = child('contrib_probe_child.py', {key: entry.get(key) for key in ('dir', 'sha256', 'entry', 'builtins', 'imports')}, TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {'error': f'probe timed out ({TIMEOUT_S} s)'}
    except OSError as error:   # the entry folder is gone, or the child could not start
        return {'error': f'probe could not start: {error}'}
    line = next((l for l in run.stdout.splitlines() if l.startswith('CONTRIB_PROBE ')), None)
    if line is None:
        return {'error': f'probe crashed: {run.stderr[-400:]}'}
    result = json.loads(line[len('CONTRIB_PROBE '):])
    if 'error' not in result:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        write_json(CACHE, {**cache, entry['sha256']: result})
    return result
