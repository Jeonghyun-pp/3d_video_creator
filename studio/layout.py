"""Declarative scenes, host side: shot.scene (schema $defs/scene) resolved into the concrete list blender_ops/layout.py
builds, and checked before Blender runs.

resolve: merge the project set the scene uses (entries replaced by id), expand repeat / mirror_x into copies, leave out
copies that yield to the fill brief (an entry with yields_to_fill 'column' is not placed on a level where the brief
puts a subject column - a principle, not a list of levels), pin every exemplar to a version and apply its data edits.
lint: ids unique, exemplars exist, kit arguments match the kit's signature, section boxes and camera references exist,
every action target is something the scene makes, fill levels are declared, primitives stay a last resort.
"""
from __future__ import annotations

import ast
from copy import deepcopy

from .common import REPO, StudioError, read_json, stable_hash
from .project import project_dir

LIST_SECTIONS = ('volumes', 'kits', 'instances', 'primitives', 'lights', 'bind', 'levels')
PRIMITIVE_BUDGET = 400   # boxes are the last resort: past this many, the scene should use exemplars, kits or arrays


def _merge(base, scene):
    out = deepcopy(base)
    for key, value in scene.items():
        if key == 'use':
            continue
        if key in LIST_SECTIONS:
            id_key = 'level_id' if key == 'levels' else 'select' if key == 'bind' else 'id'
            rows = {row[id_key]: row for row in out.get(key, [])}
            order = [row[id_key] for row in out.get(key, [])]
            for row in value:
                if row[id_key] not in rows:
                    order.append(row[id_key])
                rows[row[id_key]] = deepcopy(row)
            out[key] = [rows[i] for i in order]
        elif isinstance(value, dict) and isinstance(out.get(key), dict) and key != 'section':
            out[key] = {**out[key], **deepcopy(value)}
        else:
            out[key] = deepcopy(value)
    return out


def _fill_subject_words(shot):
    brief = shot.get('fill_brief') or {}
    return {lv['level_id']: [i['element'] for i in lv.get('items', []) if i['role'] == 'subject'] for lv in brief.get('levels', [])}


def _expand(rows, shot):
    """Copies of each entry: repeat (i, j, k) then mirror_x; copies yielding to the fill brief are left out."""
    subjects = _fill_subject_words(shot)
    out, yielded = [], []
    for row in rows:
        repeat = row.get('repeat') or {'counts': [1, 1, 1], 'pitch_m': [0, 0, 0]}
        nx, ny, nz = repeat['counts']
        px, py, pz = repeat['pitch_m']
        for k in range(nz):
            level = (row.get('level_by_z') or [row.get('level')] * nz)[k] if row.get('level_by_z') or row.get('level') else None
            if row.get('yields_to_fill') and level and any(row['yields_to_fill'] in e for e in subjects.get(level, [])):
                yielded.append(f"{row['id']}@{level}")
                continue
            for j in range(ny):
                for i in range(nx):
                    suffix = ''.join(f'.{n}' for n, c in zip((i, j, k), (nx, ny, nz)) if c > 1)
                    at = [row['at'][0] + i * px, row['at'][1] + j * py, row['at'][2] + k * pz]
                    copy = {key: deepcopy(v) for key, v in row.items() if key not in ('repeat', 'mirror_x', 'level_by_z', 'yields_to_fill')}
                    copy.update(id=row['id'] + suffix, at=at, **({'level': level} if level else {}))
                    out.append(copy)
                    if row.get('mirror_x'):
                        mirrored = deepcopy(copy)
                        mirrored.update(id=copy['id'] + '.m', at=[-at[0], at[1], at[2]])
                        if 'rot_z_deg' in mirrored:
                            mirrored['rot_z_deg'] = -mirrored['rot_z_deg']
                        out.append(mirrored)
    return out, yielded


def _exemplar(name):
    exemplar, _, version = name.partition('@')
    folder = REPO / 'library' / 'exemplars' / exemplar
    if not folder.is_dir():
        raise StudioError('INPUT_INVALID', f'scene: no exemplar {exemplar} in the library')
    version = version or sorted(p.name for p in folder.glob('v*'))[-1]
    spec_file = folder / version / 'spec.json'
    if not spec_file.is_file():
        raise StudioError('INPUT_INVALID', f'scene: exemplar {exemplar} has no version {version}')
    return exemplar, version, read_json(spec_file)


def _drop_parts(spec, ids):
    spec['builders'] = [b for b in spec['builders'] if b['part_id'] not in ids]
    for key in ('materials', 'features', 'dimensions'):
        rows = []
        for row in spec.get(key, []):
            row = dict(row, part_ids=[p for p in row.get('part_ids', []) if p not in ids])
            if row['part_ids'] or (key == 'dimensions' and 'part_ids' not in row):
                rows.append(row)
        spec[key] = rows


def _edit(spec, edits):
    from .decisions import apply_ops
    for edit in edits or ():
        if edit['op'] == 'drop_parts':
            _drop_parts(spec, edit['parts'])
        else:
            spec = apply_ops(spec, [{'op': 'set', 'path': edit['path'], 'value': edit['value']}])
    return spec


def resolve(path, shot):
    """The concrete scene: merged, expanded, exemplars pinned and edited. Raises on unknown exemplars."""
    path = project_dir(path)
    scene = shot.get('scene') or {}
    base = read_json(path / scene['use']) if scene.get('use') else {}
    merged = _merge(base, scene)
    instances, yielded_i = _expand(merged.get('instances', []), shot)
    primitives, yielded_p = _expand(merged.get('primitives', []), shot)
    lights, _ = _expand(merged.get('lights', []), shot)
    pinned, specs = [], {}
    for row in instances:
        exemplar, version, spec = _exemplar(row['exemplar'])
        spec = _edit(deepcopy(spec), row.get('edits'))
        spec['subject_id'] = row['id'].lower().replace('_', '-').replace('.', '-')
        specs[row['id']] = stable_hash(spec)
        pinned.append({**{k: v for k, v in row.items() if k != 'edits'}, 'exemplar': f'{exemplar}@{version}', 'spec': spec})
    resolved = {**merged, 'instances': pinned, 'primitives': primitives, 'lights': lights}
    return {'scene': resolved, 'yielded': yielded_i + yielded_p, 'exemplar_specs': specs, 'layout_sha256': stable_hash(resolved)}


def _street_params():
    tree = ast.parse((REPO / 'studio/blender_ops/env_kits.py').read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'street')
    return {a.arg for a in fn.args.args + fn.args.kwonlyargs} - {'name', 'library_root'}   # path comes in args


KITS = {'street': _street_params}


def lint(path, shot, author=False):
    """{'errors', 'warnings'} for the shot's scene. `author`: an author script also runs (targets it makes are unknown)."""
    errors, warnings = [], []
    try:
        result = resolve(path, shot)
    except StudioError as error:
        return {'errors': [error.message], 'warnings': []}
    scene = result['scene']
    made = [r['id'] for key in ('volumes', 'kits', 'instances', 'primitives', 'lights') for r in scene.get(key, [])]
    made += [scene['section']['id']] if scene.get('section') else []
    errors += [f'id {i} is used {made.count(i)} times' for i in sorted(set(made)) if made.count(i) > 1]
    for kit in scene.get('kits', []):
        unknown = sorted(set(kit.get('args', {})) - KITS[kit['kit']]())
        if unknown:
            errors.append(f"kit {kit['id']}: {kit['kit']} takes no {unknown}")
    volumes = {v['id'] for v in scene.get('volumes', [])}
    if scene.get('section') and scene['section']['box'] not in volumes:
        errors.append(f"section box {scene['section']['box']} is not a volume")
    bound = {(b['instance_id'], b['part_id']) for b in scene.get('bind', [])}
    built = {(i['id'], b['part_id']) for i in scene['instances'] for b in i['spec']['builders']}
    for action in shot.get('actions', []):
        for target in action.get('targets', []):
            if (target['instance_id'], target['part_id']) not in bound | built:
                (warnings if author else errors).append(f"action {action['action_id']} targets {target['instance_id']}/{target['part_id']}, which the scene does not make")
    refs = [v for v in ((shot['camera'].get('move') or {}).get('params') or {}).values() if isinstance(v, str)]
    known = set(made) | {b['select'] for b in scene.get('bind', [])}
    for ref in refs:
        if ref not in known and not author:
            warnings.append(f'camera move refers to {ref}, which the scene does not name (an anchor id or a kit object?)')
    declared = {lv['level_id'] for lv in scene.get('levels', [])}
    brief_levels = {lv['level_id'] for lv in (shot.get('fill_brief') or {}).get('levels', [])}
    if declared and brief_levels - declared:
        errors.append(f'fill brief levels {sorted(brief_levels - declared)} are not declared by the scene')
    if len(scene['primitives']) > PRIMITIVE_BUDGET:
        warnings.append(f"{len(scene['primitives'])} primitives (> {PRIMITIVE_BUDGET}): use exemplars, kits or scatter for repeated parts")
    unpinned = [i['exemplar'] for i in (shot.get('scene') or {}).get('instances', []) if '@' not in i['exemplar']]
    if unpinned:
        warnings.append(f'exemplars pinned to their latest version at build: {sorted(set(unpinned))} (write name@vNNN to keep it)')
    return {'errors': errors, 'warnings': warnings, 'counts': {k: len(scene.get(k, [])) for k in ('instances', 'primitives', 'kits', 'lights', 'volumes')},
            'yielded': result['yielded']}
