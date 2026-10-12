"""Load a contrib entry (studio/contrib.py) - new vocabulary an agent wrote: a mesh builder, a 2D profile, a coupling law,
a spec generator. Pure Python (host and Blender).

A reference is 'contrib:<name>@vNNN' (promoted, library/contrib/<kind>/<name>/vNNN) or 'contrib:<name>@draft' (the
project's own contrib/<kind>/<name>). The host resolves every reference a build uses into job['contrib'] = {ref: {dir,
sha256, kind, entry, params, builtins, imports}} and the code is loaded only when its hash still matches - a changed
file is never run.

One way to run an entry, wherever it runs (a build's stages, a workbench session, the host's contract and probe
children): run() - the bytes that were hashed, with only the builtins and imports the 'pure' profile allows (carried in
the entry by studio/contrib.resolve, from studio/author_lint.py), under the pure rule of blender_ops/sandbox.py.
"""
from __future__ import annotations

import hashlib
import types
import json
import os
from pathlib import Path
import sys

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
        _CACHE[key] = run(entry, parse(ref)[0])
    return _CACHE[key]


def _sandbox():
    try:
        from . import sandbox
    except ImportError:   # Blender and the host children put blender_ops on the path
        import sandbox
    return sandbox


def _builtins(names, imports):
    """The builtins an entry's module sees: the 'pure' profile's, an import that only reaches its modules, and the class
    statement's own helper."""
    import builtins

    def pure_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level or name.split('.')[0] not in imports:
            sys.audit(_sandbox().IMPORT_REFUSED, name)   # refused and recorded by the pure rule, even if the entry catches it
            raise ImportError(f'CONTRIB: import {name} - an entry imports only what the pure profile lists (studio/author_lint.py)')
        return builtins.__import__(name, globals, locals, fromlist, 0)
    allowed = {name: getattr(builtins, name) for name in names if hasattr(builtins, name)}
    return {**allowed, '__import__': pure_import, '__build_class__': builtins.__build_class__}


def run(entry, name='entry'):
    """The entry function of one resolved entry {dir, sha256, entry, builtins, imports}."""
    if not (isinstance(entry.get('builtins'), list) and isinstance(entry.get('imports'), list)):
        raise ValueError(f"CONTRIB: {name} was resolved without its run policy (builtins, imports): resolve it again")
    # The bytes that were hashed are the bytes that run: read once, check, compile those (no import machinery, so
    # nothing is written beside the entry - no __pycache__ - and the file cannot change between check and use).
    path = Path(entry['dir'], 'impl.py')
    source = path.read_bytes()
    if code_sha(entry['dir'], source) != entry['sha256']:
        raise ValueError(f'CONTRIB: {name} changed after it was resolved; build again')
    imports = frozenset(entry['imports'])
    _sandbox().add_pure(entry['dir'], imports)   # before a line of it runs
    module = types.ModuleType(f"studio_contrib_{name}_{entry['sha256'][:8]}")
    module.__file__ = str(path)
    module.__builtins__ = _builtins(entry['builtins'], imports)
    sys.modules[module.__name__] = module   # library helpers find a class's module there (dataclasses); unique per hash
    exec(compile(source, str(path), 'exec'), module.__dict__)   # noqa: S102 - checked, linted (author_lint 'pure'), pure rule
    return getattr(module, entry['entry'])
