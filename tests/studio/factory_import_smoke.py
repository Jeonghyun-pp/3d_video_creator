"""Factory -> library -> Blender import smoke (needs .venvs/cad and Blender).

Run with the host interpreter:
    ../.venv/bin/python tests/studio/factory_import_smoke.py
Host part: generate + prepare the M20 bolt set into a temporary library and
check status, part count and bounds. It then re-runs this file inside
Blender, which imports the prepared asset with a transform, checks every
anchor against factory.json and against the mesh extremes, and builds the
D22x8 + D10 tie rebar cage twice to compare geometry hashes.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
# Bolt M20 x 80 (ISO 4014 length l) + head height k 12.5 mm -> 92.5 mm along Z.
EXPECTED_EXTENT_M, EXTENT_TOL_M, ANCHOR_TOL_M = 0.0925, 0.0005, 1e-4
TRANSFORM = {'location': [0.4, -0.25, 0.1], 'rotation_euler': [0.0, 0.0, 0.3], 'scale': [1, 1, 1]}


def host():
    from studio.asset_factory.factory import doctor_check, generate_asset
    from studio.common import blender_binary
    if not doctor_check()['available']:
        print(json.dumps({'ok': False, 'skipped': 'CAD interpreter unavailable'}))
        return 0
    with tempfile.TemporaryDirectory(prefix='studio-factory-smoke-') as temporary:
        result = generate_asset(REPO / 'examples/factory/m20_bolt_set.json', prepare=True, library_root=temporary)
        assert result['status'] == 'prepared', result['status']
        assert len(result['parts']) == 4, [p['part_id'] for p in result['parts']]
        bounds = result['inspection']['bounds_m']
        extent = max(b - a for a, b in zip(bounds['minimum'], bounds['maximum']))
        assert abs(extent - EXPECTED_EXTENT_M) <= EXTENT_TOL_M, extent
        out = Path(temporary) / 'blender_check.json'
        run = subprocess.run([blender_binary(), '--background', '--factory-startup', '--python-exit-code', '1',
                              '--python', __file__, '--', result['manifest_path'], str(out)], capture_output=True, text=True, timeout=900)
        if run.returncode or not out.is_file():
            print(run.stdout[-3000:], run.stderr[-3000:])
            raise SystemExit('Blender part failed')
        check = json.loads(out.read_text())
    print(json.dumps({'ok': True, 'extent_m': extent, **check}, indent=1))
    return 0


def blender(manifest_path, out_path):
    import bpy
    from mathutils import Vector
    sys.path.insert(0, str(REPO / 'studio/blender_ops'))
    sys.path.insert(0, str(REPO / 'studio/asset_factory/blender'))
    from assets import import_prepared_asset
    from rebar_cage import build_rebar_cage, geometry_hash
    from studio.asset_factory.spec import rebar_cage_params

    bpy.ops.wm.read_factory_settings(use_empty=True)  # own process; the builders themselves never reset
    manifest = json.loads(Path(manifest_path).read_text())
    factory = json.loads(Path(next(f['path'] for f in manifest['files'] if f['relative_path'] == 'factory.json')).read_text())
    ids = import_prepared_asset(manifest_path, 'bolt_a', TRANSFORM)
    bpy.context.view_layer.update()
    root = bpy.data.objects['bolt_a']
    errors = {}
    for part in factory['parts']:
        obj = bpy.data.objects[ids['part_ids'][part['part_id']]]
        stored = json.loads(obj['studio_anchors'])
        world_z = [(obj.matrix_world @ v.co).z for v in obj.data.vertices]
        extremes = {'top': max(world_z), 'head_top': max(world_z), 'bottom': min(world_z), 'tip': min(world_z)}
        for name, point_mm in part['anchors_mm'].items():
            anchor_id = f"{part['part_id']}_{name}"
            actual = obj.matrix_world @ Vector(stored[ids['anchor_ids'][anchor_id]])
            expected = root.matrix_world @ Vector([v / 1000.0 for v in point_mm])
            errors[anchor_id] = (actual - expected).length
            if name in extremes:  # anchor must sit on the real mesh extreme, not just echo its input
                errors[anchor_id + ':mesh'] = abs(actual.z - extremes[name])
    worst = max(errors.values())
    assert worst <= ANCHOR_TOL_M, {k: v for k, v in errors.items() if v > ANCHOR_TOL_M}

    params = rebar_cage_params({'asset_id': 'cage_smoke', 'kind': 'rebar_cage', 'designation': 'D22', 'tie_designation': 'D10',
                                'longitudinal_count': 8, 'column_mm': 400, 'cover_mm': 40, 'length_mm': 1200, 'tie_spacing_mm': 150})
    hashes, counts = [], []
    for run in range(2):
        collection = bpy.data.collections.new(f'cage_{run}')
        bpy.context.scene.collection.children.link(collection)
        objects = build_rebar_cage(params, collection)
        counts.append({role: sum(o['studio_rebar_role'] == role for o in objects) for role in ('longitudinal', 'tie')})
        hashes.append(geometry_hash(objects))
    assert counts[0] == {'longitudinal': 8, 'tie': 8}, counts
    assert hashes[0] == hashes[1], hashes
    Path(out_path).write_text(json.dumps({'anchors_checked': len(errors), 'max_anchor_error_m': worst,
                                          'rebar_counts': counts[0], 'rebar_hash': hashes[0]}))


if __name__ == '__main__':
    if '--' in sys.argv:
        blender(*sys.argv[sys.argv.index('--') + 1:][:2])
    else:
        raise SystemExit(host())
