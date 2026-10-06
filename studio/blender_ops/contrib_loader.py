"""Load a contrib entry (studio/contrib.py) - new vocabulary an agent wrote: a mesh builder, a 2D profile, a coupling law,
a spec generator. Pure Python (host and Blender).

A reference is 'contrib:<name>@vNNN' (promoted, library/contrib/<kind>/<name>/vNNN) or 'contrib:<name>@draft' (the
project's own contrib/<kind>/<name>). The host resolves every reference a build uses into job['contrib'] = {ref: {dir,
sha256, kind, entry}} and the code is loaded only when its hash still matches - a changed file is never run.
"""
from __future__ import annotations

import hashlib
import types
import json
import os
from pathlib import Path

PREFIX = 'contrib:'
REF_PATTERN = r'^contrib:[a-z0-9][a-z0-9_]*@(v[0-9]{3}|draft)$'
TABLE = {}    # set from job['contrib'] by the process that builds (stage 1, stage 2, a workbench session)
_CACHE = {}


def is_ref(value):
    return isinstance(value, str) and value.startswith(PREFIX)


def parse(ref):
    """'contrib:cycloid_disc@v001' -> ('cycloid_disc', 'v001')"""
    body = ref[len(PREFIX):]
    if '@' not in body:
        raise ValueError(f'CONTRIB: {ref} must pin a version (@vNNN, or @draft for the project\'s own entry)')
    name, version = body.split('@', 1)
    return name, version


def code_sha(folder, source=None):
    source = Path(folder, 'impl.py').read_bytes() if source is None else source
    return hashlib.sha256(source + Path(folder, 'manifest.json').read_bytes()).hexdigest()


def _table():
    if TABLE:
        return TABLE
    path = os.environ.get('STUDIO_JOB_PATH')
    if not path:
        return {}
    return json.loads(Path(path).read_text()).get('contrib') or {}


def call(ref, *args, **params):
    """Run a resolved entry with its manifest defaults under the given params."""
    entry = _table().get(ref) or {}
    return load(ref)(*args, **{**entry.get('params', {}), **params})


def load(ref, table=None):
    """The entry function of a resolved reference, after checking its hash."""
    entry = (table if table is not None else _table()).get(ref)
    if entry is None:
        raise ValueError(f'CONTRIB: {ref} was not resolved for this build')
    key = (ref, entry['sha256'])
    if key not in _CACHE:
        # The bytes that were hashed are the bytes that run: read once, check, compile those (no import machinery, so
        # nothing is written beside the entry - no __pycache__ - and the file cannot change between check and use).
        path = Path(entry['dir'], 'impl.py')
        source = path.read_bytes()
        if code_sha(entry['dir'], source) != entry['sha256']:
            raise ValueError(f'CONTRIB: {ref} changed after it was resolved; build again')
        module = types.ModuleType(f"studio_contrib_{parse(ref)[0]}_{entry['sha256'][:8]}")
        module.__file__ = str(path)
        exec(compile(source, str(path), 'exec'), module.__dict__)   # noqa: S102 - checked, linted (author_lint 'pure') entry
        _CACHE[key] = getattr(module, entry['entry'])
    return _CACHE[key]
