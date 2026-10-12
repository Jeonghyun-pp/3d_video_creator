"""Which manifest params of one contrib entry change its output - run in a separate isolated interpreter (python -I,
scrubbed environment) by studio/contrib_probe.py: the agent's code runs here, never inside the studio process.
No studio package imports: the entry runs by the one run path (blender_ops/contrib_loader.run, under the pure rule of
blender_ops/sandbox.py). Reads {dir, sha256, entry, builtins, imports} as JSON on stdin; prints one line
'CONTRIB_PROBE {...}'.

Each param is nudged from its manifest default (number x1.1, or 0.01 / 1 from zero; int +1; bool flipped; other
types are not probed) and the output compared with the default output. A call that raises with the nudged value
counts as "the param matters" (it is read). Outputs by kind: mesh (verts, faces) and profile points and spec dicts
as returned; coupling y over x = -720..720 step 45.
"""
import json
import math
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.append(str(Path(__file__).resolve().parent / 'blender_ops'))   # appended: the standard library still comes first
COUPLING_XS = [x * 45.0 for x in range(-16, 17)]


def _canonical(value):
    """Rounded, JSON-stable form of an output (tuples as lists, floats to 9 digits)."""
    if isinstance(value, float):
        return round(value, 9) if math.isfinite(value) else str(value)
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    return value


def _nudge(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value * 1.1 if value else 0.01
    return None


def main():
    import contrib_loader
    import sandbox
    job = json.loads(sys.stdin.read())
    folder = Path(job['dir'])
    manifest_bytes = (folder / 'manifest.json').read_bytes()
    if contrib_loader.code_sha(folder) != job['sha256']:
        print('CONTRIB_PROBE ' + json.dumps({'error': 'the entry changed after it was resolved'}))
        return
    manifest = json.loads(manifest_bytes)
    try:
        fn = contrib_loader.run({**job, 'entry': manifest['entry']}, 'probe')
    except Exception as error:   # noqa: BLE001 - the entry refused or failing to load is the probe's answer
        print('CONTRIB_PROBE ' + json.dumps({'error': f'{type(error).__name__}: {error}'}))
        return
    kind, defaults = manifest['kind'], dict(manifest['params'])

    def output(params):
        if kind == 'coupling':
            return _canonical([fn(x, **params) for x in COUPLING_XS])
        return _canonical(fn(**params))

    base = json.dumps(output(defaults), sort_keys=True)
    unused, skipped = [], []
    for name, value in defaults.items():
        nudged = _nudge(value)
        if nudged is None:
            skipped.append(name)
            continue
        try:
            changed = json.dumps(output({**defaults, name: nudged}), sort_keys=True) != base
        except Exception:   # noqa: BLE001 - the nudged value is refused: the param is read
            changed = True
        if not changed:
            unused.append(name)
    refused = sandbox.violations()
    if refused:   # refused at run time, even where the entry caught the error
        print('CONTRIB_PROBE ' + json.dumps({'error': 'refused at run time: ' + '; '.join(v['reason'] for v in refused[:3])}))
        return
    print('CONTRIB_PROBE ' + json.dumps({'unused': unused, 'not_probed': skipped}))


if __name__ == '__main__':
    main()
