"""Find manifest params a contrib entry ignores (SKILL #8: a `rib` parameter that changes nothing is a placeholder).

The entry runs in studio/contrib_probe_child.py - a separate isolated interpreter, like the contract tests
(studio/contrib_check.py) - on the hash-checked bytes. Results are cached by the entry's hash (an entry never changes
under the same hash), so a build pays for each entry once.
"""
from __future__ import annotations

import json
import subprocess
import sys

from .common import REPO, blender_env, read_json, write_json

CACHE = REPO / '.studio' / 'contrib_probe_cache.json'
TIMEOUT_S = 60


def probe(entry):
    """{unused: [param, ...], not_probed: [...]} for one resolved entry ({dir, sha256, ...}); {error} if it could not run."""
    cache = read_json(CACHE) if CACHE.is_file() else {}
    if entry['sha256'] in cache:
        return cache[entry['sha256']]
    try:
        run = subprocess.run([sys.executable, '-I', str(REPO / 'studio/contrib_probe_child.py')], input=json.dumps(
            {'dir': entry['dir'], 'sha256': entry['sha256']}), capture_output=True, text=True, timeout=TIMEOUT_S, env=blender_env())
    except subprocess.TimeoutExpired:
        return {'error': f'probe timed out ({TIMEOUT_S} s)'}
    line = next((l for l in run.stdout.splitlines() if l.startswith('CONTRIB_PROBE ')), None)
    if line is None:
        return {'error': f'probe crashed: {run.stderr[-400:]}'}
    result = json.loads(line[len('CONTRIB_PROBE '):])
    if 'error' not in result:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        write_json(CACHE, {**cache, entry['sha256']: result})
    return result
