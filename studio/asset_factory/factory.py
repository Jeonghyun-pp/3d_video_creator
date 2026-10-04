"""Host side of the CAD asset factory (stdlib only).

generate_asset: spec -> (reuse | CAD worker in a staging dir -> gates) ->
fetch_trusted into the library -> optional two-stage Blender preparation.
Nothing reaches the library unless every required gate passed.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from .. import assets
from ..common import REPO, StudioError, file_hash, lock, read_json, write_json
from .spec import resolve_spec, spec_hash

WORKER = Path(__file__).parent / 'cad_worker.py'
DXF_WORKER = Path(__file__).parent / 'dxf_worker.py'
REQUIREMENTS = Path(__file__).parent / 'requirements-cad.txt'
DEFAULT_CAD_PYTHON = REPO / '.venvs' / 'cad' / 'bin' / 'python'
WORKER_TIMEOUT_S = 600
# Every one of these must be present and passed; a missing gate counts as failed.
REQUIRED_GATES = ('bbox', 'table', 'interference')
SUPPORTED_KINDS = {'bolt_set', 'hbeam'}


def cad_python():
    return Path(os.environ.get('STUDIO_CAD_PYTHON') or DEFAULT_CAD_PYTHON)


def _pins():
    pins = {}
    for line in REQUIREMENTS.read_text(encoding='utf-8').splitlines():
        line = line.split('#', 1)[0].strip()
        if '==' in line:
            name, version = line.split('==', 1)
            pins[name.strip()] = version.strip()
    return pins


def doctor_check():
    """{'available', 'python', 'versions', ...}; available only when installed versions equal the pins."""
    python = cad_python()
    result = {'available': False, 'python': str(python), 'versions': {}, 'pinned': _pins()}
    if not python.is_file():
        result['error'] = 'CAD interpreter missing; create it with uv (see requirements-cad.txt) or set STUDIO_CAD_PYTHON'
        return result
    try:
        for worker in (WORKER, DXF_WORKER):  # every worker reports the packages it imports
            run = subprocess.run([str(python), '-I', str(worker), '--versions'], capture_output=True, text=True, timeout=120)
            if run.returncode != 0:
                result['versions'] = {}
                break
            result['versions'].update(json.loads(run.stdout))
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        result['error'] = str(exc)
        return result
    if not result['versions']:
        result['error'] = (run.stderr or run.stdout)[-1000:]
        return result
    result['mismatches'] = {name: {'pinned': pin, 'installed': result['versions'].get(name)}
                            for name, pin in result['pinned'].items() if result['versions'].get(name) != pin}
    result['available'] = not result['mismatches']
    return result


def _asset_root(library_root):
    return Path(library_root or REPO / 'library').resolve() / 'assets'


def _versions(asset_root, asset_id):
    return sorted(asset_root.glob(f'{asset_id}/v*/asset.json'), key=lambda path: int(path.parent.name[1:]) if re.fullmatch(r'v\d+', path.parent.name) else -1)


def _find_existing(asset_root, asset_id, digest):
    for manifest_path in _versions(asset_root, asset_id):
        manifest = read_json(manifest_path)
        if manifest.get('source', {}).get('factory_spec_sha256') != digest:
            continue
        if not all(Path(item['path']).is_file() and file_hash(item['path']) == item['sha256'] for item in manifest.get('files', [])):
            raise StudioError('MISSING_DEPENDENCY', f'Factory asset {manifest_path} has missing or changed files',
                              recovery='Restore its recorded bytes or remove that version so it can be regenerated')
        return manifest_path, manifest
    return None, None


def _next_version(asset_root, asset_id):
    numbers = [int(path.parent.name[1:]) for path in _versions(asset_root, asset_id) if re.fullmatch(r'v\d+', path.parent.name)]
    return f'v{max(numbers, default=0) + 1:04d}'


def run_worker(job, staging, python=None, timeout=WORKER_TIMEOUT_S):
    """Run cad_worker.py in the CAD interpreter; return its factory.json (gates checked by the caller)."""
    python = Path(python or cad_python())
    if not python.is_file():
        raise StudioError('ENVIRONMENT_MISSING', f'CAD interpreter not found: {python}',
                          recovery='uv venv -p python3.12 .venvs/cad && uv pip install --python .venvs/cad/bin/python -r studio/asset_factory/requirements-cad.txt, or set STUDIO_CAD_PYTHON')
    job_path = Path(staging) / 'job.json'
    out_dir = Path(staging) / 'out'
    write_json(job_path, job)
    try:
        # -I: no user site / PYTHON* env and no script-dir shadowing of library modules.
        run = subprocess.run([str(python), '-I', str(WORKER), str(job_path), str(out_dir)], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise StudioError('TIMEOUT', f'CAD worker exceeded {timeout}s', retryable=True) from exc
    report_path = out_dir / 'factory.json'
    if run.returncode not in (0, 1) or not report_path.is_file():
        raise StudioError('FACTORY_WORKER_FAILED', f'CAD worker exited {run.returncode}: {(run.stderr + run.stdout)[-2000:]}')
    return read_json(report_path), out_dir, run.returncode


def check_gates(report, digest):
    failed = [name for name in REQUIRED_GATES if not report.get('gates', {}).get(name, {}).get('passed')]
    if report.get('spec_sha256') != digest:
        failed.append('spec_identity')
    if failed:
        raise StudioError('FACTORY_GATE_FAILED', f'Factory gates failed: {failed}; details: '
                          f'{json.dumps({name: report.get("gates", {}).get(name) for name in failed}, default=str)[:2000]}',
                          recovery='Fix the spec/table row or the builder; nothing was written to the library', affected_ids=failed)


def build_mapping(report, inventory, asset_id=None):
    """Semantic mapping whose part objects are exactly the GLB nodes named after part ids.

    Objects are matched by name both ways: a part without its node, or an
    imported object no part claims, is FACTORY_MAPPING_INCOMPLETE.
    """
    rows = {row['name']: row for row in inventory.get('objects', [])}
    expected = [part['node_name'] for part in report['parts']]
    missing = [name for name in expected if name not in rows or rows[name].get('type') != 'MESH' or not rows[name].get('object_id')]
    unexpected = sorted(set(rows) - set(expected))
    if missing or unexpected:
        raise StudioError('FACTORY_MAPPING_INCOMPLETE', f'GLB nodes missing as mesh objects: {missing}; unclaimed objects: {unexpected}',
                          affected_ids=missing + unexpected)
    parts, anchors = [], []
    for part in report['parts']:
        object_id = rows[part['node_name']]['object_id']
        anchor_ids = []
        for name, point_mm in sorted(part['anchors_mm'].items()):
            anchor_id = f"{part['part_id']}_{name}"
            anchor_ids.append(anchor_id)
            # GLB vertices are baked in the asset frame, so object-local == asset frame.
            anchors.append({'anchor_id': anchor_id, 'object_id': object_id, 'point_local_m': [value / 1000.0 for value in point_mm]})
        parts.append({'part_id': part['part_id'], 'kind': 'leaf', 'object_ids': [object_id], 'root_object_id': object_id,
                      'explode_vector': part['explode_vector'], 'anchor_ids': anchor_ids})
    return {'parts': parts, 'anchors': anchors}


def _prepare(manifest_path, report, blender=None):
    manifest = read_json(manifest_path)
    if manifest.get('status') == 'prepared':
        return assets.prepare_asset(manifest_path, None, blender)
    # Stage 1 (no mapping) leaves status 'needs_mapping' and only records the
    # Blender inventory; stage 2 applies the mapping and pins the version.
    first = assets.prepare_asset(manifest_path, None, blender)
    mapping = build_mapping(report, first['inventory'])
    second = assets.prepare_asset(manifest_path, mapping, blender)
    if second['status'] != 'prepared':
        raise StudioError('FACTORY_PREPARE_FAILED', f'Preparation ended as {second["status"]}', recovery=f'Inspect {manifest_path}')
    return second


def generate_asset(spec, prepare=False, library_root=None, *, python=None, blender=None, timeout=WORKER_TIMEOUT_S):
    if isinstance(spec, (str, Path)):
        spec = read_json(spec)
    resolved = resolve_spec(spec)
    kind = resolved['spec']['kind']
    if kind not in SUPPORTED_KINDS:
        raise StudioError('FACTORY_KIND_UNSUPPORTED', f'{kind} is not built by the CAD worker',
                          recovery='Build rebar cages in Blender with studio/asset_factory/blender/rebar_cage.py and spec.rebar_cage_params')
    digest = spec_hash(spec)
    asset_id = resolved['spec']['asset_id']
    asset_root = _asset_root(library_root)
    manifest_path, manifest = _find_existing(asset_root, asset_id, digest)
    reused = manifest is not None
    if not reused:
        with tempfile.TemporaryDirectory(prefix='studio-factory-') as staging:
            report, out_dir, _ = run_worker({**resolved, 'spec_sha256': digest}, staging, python, timeout)
            check_gates(report, digest)
            with lock(asset_root / '.factory.lock'):
                manifest_path, manifest = _find_existing(asset_root, asset_id, digest)
                reused = manifest is not None
                if not reused:
                    candidate = {'asset_id': asset_id, 'version': _next_version(asset_root, asset_id), 'provider': 'factory',
                                 'name': resolved['spec'].get('name', asset_id), 'tags': resolved['spec'].get('tags', []),
                                 'type': 'models', 'source_units': 'meters', 'path': str(out_dir / report['glb']),
                                 'files': [{'local_path': str(out_dir / 'factory.json'), 'relative_path': 'factory.json', 'role': 'dependency'}]}
                    gates = report['gates']
                    trusted = {'license_id': 'original-generated', 'use_status': 'cleared', 'factory_spec_sha256': digest,
                               'standards': resolved['standards'], 'factory_versions': report['versions'],
                               'factory_gates': {'bbox': {'passed': True, 'limit_mm': gates['bbox']['limit_mm'],
                                                          'max_error_mm': max(p['max_error_mm'] for p in gates['bbox']['parts'])},
                                                 'table': {'passed': True, 'checks': len(gates['table']['checks'])},
                                                 'interference': {'passed': True, 'limit_mm3': gates['interference']['limit_mm3'],
                                                                  'max_overlap_mm3': gates['interference']['max_overlap_mm3']}}}
                    fetched = assets.fetch_trusted(candidate, trusted, asset_root)
                    manifest_path, manifest = Path(fetched['manifest_path']), read_json(fetched['manifest_path'])
    report = read_json(next(item['path'] for item in manifest['files'] if item['relative_path'] == 'factory.json'))
    result = {**manifest, 'manifest_path': str(manifest_path), 'reused': reused, 'factory_spec_sha256': digest,
              'artifacts': [str(manifest_path)]}
    if prepare:
        prepared = _prepare(manifest_path, report, blender)
        result.update({**prepared['asset'], 'status': prepared['status'], 'artifacts': prepared['artifacts'],
                       'warnings': prepared.get('warnings', [])})
    return result


def register_generate(commands):
    """Add `asset generate` to the `asset` subcommand group created in studio/assets.py."""
    generate = commands.add_parser('generate', help='Build a standards-based CAD asset from a factory spec (gated, then registered)')
    generate.add_argument('--spec', required=True)
    generate.add_argument('--prepare', action='store_true')
    generate.add_argument('--library', help='Library root containing assets/ (default: repo library/)')
    generate.add_argument('--blender')
    generate.set_defaults(handler=lambda args: generate_asset(args.spec, args.prepare, args.library, blender=args.blender))


if __name__ == '__main__':  # python -m studio.asset_factory.factory --doctor
    if sys.argv[1:] == ['--doctor']:
        print(json.dumps(doctor_check(), indent=2))
