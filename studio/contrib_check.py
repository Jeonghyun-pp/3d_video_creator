"""Contract tests for one contrib entry, run in a separate isolated interpreter (python -I, scrubbed environment, the OS
sandbox where there is one: studio/contrib_probe.isolated) by studio/contrib.py: the agent's code runs here, never inside
the studio process, and only by the one run path (blender_ops/contrib_loader.run: the hash-checked bytes, the pure
builtins and imports, the pure rule of blender_ops/sandbox.py). Prints one JSON line.

Contracts by kind (every kind: the function's parameters equal the manifest's params, two calls give the same result,
no NaN / inf):
  mesh      fn(**params) -> (verts, faces): indices valid, closed (every edge in exactly two faces), consistently
            oriented (each directed edge once), length params scale the bounding box
  profile   fn(**params) -> [[x, y], ...]: >= 3 points, a simple closed loop (no self-crossing), length params scale it
  coupling  fn(x, **params) -> y: finite over x in [-720, 720]
  spec      fn(**params) -> a subject spec dict (the host validates it against the schema and the spec lint)
Usage: python -I contrib_check.py, with {dir, sha256, builtins, imports, token} as JSON on stdin; prints CONTRIB_CHECK <token> {...}
"""
import ast
import json
import math
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.append(str(Path(__file__).resolve().parent / 'blender_ops'))   # appended: the standard library still comes first


def _finite(values):
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


def _bbox(points):
    return [max(p[i] for p in points) - min(p[i] for p in points) for i in range(len(points[0]))]


def _segments_cross(a, b, c, d):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0) and min(abs(o1), abs(o2), abs(o3), abs(o4)) > 1e-15


def check_mesh(result):
    verts, faces = result
    problems = []
    if not verts or not faces:
        return ['empty mesh']
    if not _finite([c for v in verts for c in v]):
        problems.append('non-finite vertex')
    if any(i < 0 or i >= len(verts) for f in faces for i in f):
        problems.append('face index out of range')
    directed, undirected = {}, {}
    for f in faces:
        for a, b in zip(f, list(f[1:]) + [f[0]]):
            directed[(a, b)] = directed.get((a, b), 0) + 1
            key = (min(a, b), max(a, b))
            undirected[key] = undirected.get(key, 0) + 1
    open_edges = sum(1 for n in undirected.values() if n != 2)
    if open_edges:
        problems.append(f'not closed: {open_edges} edges not shared by exactly two faces')
    if any(n > 1 for n in directed.values()):
        problems.append('faces not consistently oriented')
    return problems


def check_profile(points):
    problems = []
    if len(points) < 3:
        return ['fewer than 3 points']
    if not _finite([c for p in points for c in p]):
        problems.append('non-finite point')
    n = len(points)
    edges = [(points[i], points[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if _segments_cross(*edges[i], *edges[j]):
                problems.append(f'self-crossing between edges {i} and {j}')
                return problems
    return problems


def main(job):
    import contrib_loader
    import sandbox
    folder = Path(job['dir'])
    if contrib_loader.code_sha(folder) != job['sha256']:
        return {'problems': ['impl.py or manifest.json changed while it was checked: check again']}
    manifest = json.loads((folder / 'manifest.json').read_text())
    source = (folder / 'impl.py').read_text()
    fn_node = next((n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == manifest['entry']), None)
    out = {'kind': manifest['kind'], 'problems': []}
    if fn_node is None:
        out['problems'].append(f"no function {manifest['entry']} in impl.py")
        return out
    args = [a.arg for a in fn_node.args.args + fn_node.args.kwonlyargs]
    declared = list(manifest.get('params', {}))
    expected = (['x'] if manifest['kind'] == 'coupling' else []) + declared
    if sorted(args) != sorted(expected):
        out['problems'].append(f'function parameters {args} != manifest params {expected} (the manifest is the declared-reads table)')
    if not manifest.get('words'):
        out['problems'].append('manifest words: the words a user would say for this (prompt and storyboard vocabulary)')
    params = dict(manifest.get('params', {}))
    kind = manifest['kind']
    try:
        _contracts(contrib_loader.run({**job, 'entry': manifest['entry']}, 'check'), manifest, out)
    except Exception as error:   # noqa: BLE001 - the agent's code failing is a contract result, not a crash
        out['problems'].append(f'{type(error).__name__}: {error}')
    # refused at run time, even where the entry caught the error itself
    out['problems'] += [f"refused at run time: {v['reason']}" for v in sandbox.violations()[:5]]
    return out


def _contracts(fn, manifest, out):
    params = dict(manifest.get('params', {}))
    kind = manifest['kind']
    if kind == 'coupling':
        values = [fn(x, **params) for x in range(-720, 721, 15)]
        if not _finite(values):
            out['problems'].append('non-finite output')
        if values != [fn(x, **params) for x in range(-720, 721, 15)]:
            out['problems'].append('not deterministic')
        return
    first, second = fn(**params), fn(**params)
    if json.dumps(first, default=str) != json.dumps(second, default=str):
        out['problems'].append('not deterministic')
    if kind == 'mesh':
        out['problems'] += check_mesh(first)
        points = first[0]
    elif kind == 'profile':
        out['problems'] += check_profile(first)
        points = first
    elif kind == 'spec':
        out['spec'] = first
        return
    lengths = manifest.get('lengths', [])
    if lengths and points:
        scaled = fn(**{**params, **{k: params[k] * 2 for k in lengths}})
        scaled_points = scaled[0] if kind == 'mesh' else scaled
        ratio = [b / a for a, b in zip(_bbox(points), _bbox(scaled_points)) if a > 1e-12]
        if any(abs(r - 2) > 1e-6 for r in ratio):
            out['problems'].append(f'doubling the length params {lengths} scaled the size by {ratio}, not 2')
    elif not lengths:
        out['problems'].append('manifest lengths: name the params that are lengths (checked: doubling them doubles the shape)')


if __name__ == '__main__':
    job = json.loads(sys.stdin.read())
    token = job.pop('token')   # never in what the entry code is handed (studio/contrib_probe.child)
    out = json.dumps(main(job))
    print(f'CONTRIB_CHECK {token} {out}')
