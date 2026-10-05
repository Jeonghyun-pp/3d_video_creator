"""Exemplar library: subject specs that passed fidelity, reusable as starting points for new subjects.

Retrieval of verified examples beat generation from scratch in the CAD-agent studies; here an exemplar
is only ever a spec whose current version passed every fidelity check against that exact spec
(spec_sha256 match), copied with its report and reference images. ``subject init --from-exemplar``
starts a new spec from it with the request/trace reset, so the new request is still decomposed.
"""
from __future__ import annotations

import re
from pathlib import Path
import shutil

from .common import REPO, StudioError, check_id, lock, now, read_json, write_json
from .fidelity import spec_sha256
from .project import load_shot, project_dir
from .subjects import load_spec, spec_path

LIBRARY = REPO / 'library' / 'exemplars'


def _passing_report(project, subject_id, spec):
    """(shot_id, version, subject report) of a current shot version that passed this exact spec."""
    path = project_dir(project)
    for shot_file in sorted(path.glob('shots/*/shot.json')):
        shot = load_shot(path, shot_file.parent.name)
        if not any(s['subject_id'] == subject_id for s in shot.get('subjects', [])) or not shot.get('scene_version'):
            continue
        report_path = shot_file.parent / 'versions' / shot['scene_version'] / 'fidelity_report.json'
        if not report_path.is_file():
            continue
        report = next((r for r in read_json(report_path)['subjects'] if r['subject_id'] == subject_id), None)
        if report and report['passed'] and report.get('spec_sha256') == spec_sha256(spec):
            return shot['shot_id'], shot['scene_version'], report, report_path.parent
    return None


def _repo_relative(path):
    path = Path(path).resolve()
    return str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path)


def _tokens(text):
    return {t for t in re.split(r'[^0-9a-z가-힣]+', text.lower()) if len(t) > 1}


def promote(project, subject_id, library=None):
    library = library or LIBRARY
    spec = load_spec(project, subject_id)
    found = _passing_report(project, subject_id, spec)
    if not found:
        raise StudioError('FIDELITY_FAILED', f'{subject_id}: no current shot version passed fidelity for this exact spec',
                          recovery='Build the shot with the spec and make fidelity pass before promoting it')
    shot_id, version, report, version_dir = found
    with lock(library / '.index.lock'):
        index_path = library / 'index.json'
        index = read_json(index_path) if index_path.is_file() else {'schema_version': 1, 'exemplars': []}
        previous = [e for e in index['exemplars'] if e['exemplar_id'] == subject_id]
        number = max([e['version'] for e in previous] or [0]) + 1
        target = library / check_id(subject_id) / f'v{number:03d}'
        target.mkdir(parents=True)
        write_json(target / 'spec.json', spec)
        write_json(target / 'fidelity_report.json', report)
        for png in version_dir.glob('silhouette_*.png'):
            shutil.copy2(png, target / png.name)
        for silhouette in spec.get('silhouettes', []):
            image = project_dir(project) / silhouette['image']
            if image.is_file():
                (target / 'refs').mkdir(exist_ok=True)
                shutil.copy2(image, target / 'refs' / image.name)
        builders = sorted({b['builder'] for b in spec['builders']} | {b['params']['item']['builder'] for b in spec['builders'] if b['builder'] == 'array'})
        entry = {'exemplar_id': subject_id, 'version': number, 'path': str(target.relative_to(library)), 'identity': spec['identity'],
                 'subject_mode': spec['subject_mode'], 'request': spec['request'], 'builders': builders,
                 'parts': [b['part_id'] for b in spec['builders']], 'relations': len(spec.get('relations', [])),
                 'assembly_claims': len(spec.get('assembly_claims', [])), 'summary': report.get('summary'),
                 'source': {'project': _repo_relative(project_dir(project)), 'shot_id': shot_id, 'version': version}, 'spec_sha256': spec_sha256(spec),
                 'promoted_at': now()}
        index['exemplars'] = [e for e in index['exemplars'] if e['exemplar_id'] != subject_id] + [entry]
        write_json(index_path, index)
    return {'exemplar': entry, 'artifacts': [str(target / 'spec.json')]}


def search(query, limit=5, library=None):
    library = library or LIBRARY
    index_path = library / 'index.json'
    entries = read_json(index_path)['exemplars'] if index_path.is_file() else []
    wanted = _tokens(query)
    rows = []
    for entry in entries:
        haystack = _tokens(' '.join([entry['identity'], entry['request'], ' '.join(entry['builders']), ' '.join(entry['parts'])]))
        partial = sum(1 for w in wanted for h in haystack if w != h and (w in h or h in w))
        score = 2 * len(wanted & haystack) + partial
        if score:
            rows.append({**entry, 'search_score': score})
    rows.sort(key=lambda e: (-e['search_score'], e['exemplar_id']))
    return {'query': query, 'results': rows[:limit]}


def init_from_exemplar(project, subject_id, exemplar_id, identity, request, library=None):
    library = library or LIBRARY
    index = read_json(library / 'index.json')
    entry = next((e for e in index['exemplars'] if e['exemplar_id'] == exemplar_id), None)
    if entry is None:
        raise StudioError('INPUT_INVALID', f'No exemplar {exemplar_id}')
    path = spec_path(project, subject_id)
    if path.exists():
        raise StudioError('REVISION_CONFLICT', f'Spec exists: {path}')
    spec = read_json(library / entry['path'] / 'spec.json')
    spec.update({'subject_id': subject_id, 'identity': identity, 'request': request,
                 'request_trace': [{'phrase': request, 'items': [], 'note': 'decompose the new request; copied structure only'}]})
    for silhouette in spec.get('silhouettes', []):
        silhouette['image'] = f'subjects/{subject_id}/refs/{silhouette["image"].rsplit("/", 1)[-1]}'
    refs = library / entry['path'] / 'refs'
    if refs.is_dir():
        shutil.copytree(refs, path.parent / 'refs')
    write_json(path, spec)
    return {'status': 'created', 'spec_path': str(path), 'from_exemplar': f"{exemplar_id} v{entry['version']}",
            'note': 'Structure, builders and relations copied; numbers are the exemplar\'s - replace them with this subject\'s sources (trace/fit), then subject lint'}
