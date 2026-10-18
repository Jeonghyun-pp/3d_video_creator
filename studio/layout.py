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

from .blender_ops.version_order import newest
from .common import CODE, REPO, StudioError, check_id, read_json, safe_path, stable_hash
from .project import project_dir

LIST_SECTIONS = ('volumes', 'kits', 'instances', 'primitives', 'lights', 'bind', 'levels', 'links', 'characters', 'decals')
PLATE_SWEEP_DEG = 30.0      # a backdrop image is one view; an orbit past this shows it is a flat card
VIEW_TOLERANCE_DEG = 10.0   # a backdrop's perspective survives a little camera height change, not a different view
PRIMITIVE_BUDGET = 400   # boxes are the last resort: past this many, the scene should use exemplars, kits or arrays


def _checked_scene(scene, label):
    """`scene` if it is a valid shot scene (schemas/studio-v1/shot.schema.json), else INPUT_INVALID naming `label`."""
    from .project import validate_schema
    try:
        validate_schema(scene, 'shot', only='scene')
    except StudioError as error:
        raise StudioError('INPUT_INVALID', f'scene: {label} is not a valid scene: {error.message}') from None
    return scene


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
        if row.get('level_by_z') and len(row['level_by_z']) != nz:
            raise StudioError('INPUT_INVALID', f"scene: {row.get('id')} level_by_z names {len(row['level_by_z'])} levels for "
                              f'{nz} copies along z (one per copy)')
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
    version = check_id(version) if version else newest(p.name for p in folder.glob('v*'))
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


def _edit(spec, edits, label='exemplar'):
    """An instance's data edits on its pinned spec, by the shot edit grammar against the subject schema: an existing
    value, or a key the schema declares there - never a value nothing reads; parts dropped must exist; the edited spec
    must still be a valid subject spec."""
    from . import shot_edit
    from .project import validate_schema
    for edit in edits or ():
        need = ('parts',) if edit.get('op') == 'drop_parts' else ('path', 'value')
        if [key for key in need if key not in edit]:
            raise StudioError('INPUT_INVALID', f"scene: {label} edit {edit} needs {', '.join(need)}")
        if edit['op'] == 'drop_parts':
            missing = sorted(set(edit['parts']) - {b['part_id'] for b in spec['builders']})
            if missing:
                raise StudioError('INPUT_INVALID', f'scene: {label} drop_parts names parts it does not have: {missing}')
            _drop_parts(spec, edit['parts'])
        else:
            shot_edit.apply(spec, {'op': 'set', 'path': edit['path'], 'value': edit['value']}, shot_edit.schema('subject'), label=f'{label} spec')
    try:
        validate_schema(spec, 'subject')
    except StudioError as error:
        raise StudioError('INPUT_INVALID', f'scene: {label} after its edits is not a valid subject spec: {error.message}') from None
    return spec


def resolve(path, shot):
    """The concrete scene: merged, expanded, exemplars pinned and edited (project subjects read as they are). Raises on unknown ones."""
    path = project_dir(path)
    scene = shot.get('scene') or {}
    base = {}
    if scene.get('use'):   # a set is a scene too: checked by the same schema before it is merged, and merged after
        base = _checked_scene(read_json(safe_path(path, scene['use'])), scene['use'])
    merged = _merge(base, scene)
    if scene.get('use'):
        _checked_scene(merged, f"the shot's scene over {scene['use']}")
    instances, yielded_i = _expand(merged.get('instances', []), shot)
    primitives, yielded_p = _expand(merged.get('primitives', []), shot)
    lights, _ = _expand(merged.get('lights', []), shot)
    pinned, specs = [], {}
    for row in instances:
        if 'subject' in row:          # the project's own spec (subjects/<id>/spec.json), e.g. one a mechanism generator wrote
            from .subjects import load_spec, spec_path
            if not spec_path(path, row['subject']).is_file():
                raise StudioError('INPUT_INVALID', f"scene: no subject {row['subject']} in this project")
            source, spec = {'subject': row['subject']}, load_spec(path, row['subject'])
        else:
            exemplar, version, spec = _exemplar(row['exemplar'])
            source = {'exemplar': f'{exemplar}@{version}'}
        spec = _edit(deepcopy(spec), row.get('edits'), label=row['id'])
        from .blender_ops.ids_core import layout_id
        spec['subject_id'] = layout_id(row['id'])
        specs[row['id']] = stable_hash(spec)
        pinned.append({**{k: v for k, v in row.items() if k != 'edits'}, **source, 'spec': spec})
    links = []
    for row in merged.get('links', []):   # .blend assets: the path is resolved and hashed here, so the layout hash follows the file
        file = (path / row['file']).resolve()
        if not file.is_file():
            raise StudioError('INPUT_INVALID', f"scene link {row['id']}: no file {row['file']} in the project")
        from .common import file_hash
        links.append({**row, 'path': str(file), 'sha256': file_hash(file)})
    from .characters import resolve as resolve_characters   # library people: manifest (with file hashes) pinned in the layout
    characters = resolve_characters(merged.get('characters'))
    decals = []
    if merged.get('decals'):   # words on surfaces, drawn in the project font (studio/decals.py); their hashes are in the layout
        from .decals import resolve as resolve_decals
        style_file = path / 'style.json'
        decals = resolve_decals(path, merged['decals'], read_json(style_file) if style_file.is_file() else {})
    stand_ins = _stand_ins(path, merged.get('stand_ins'))
    resolved = {**merged, 'instances': pinned, 'primitives': primitives, 'lights': lights, **({'links': links} if links else {}),
                **({'stand_ins': stand_ins} if stand_ins else {}),
                **({'characters': characters} if characters else {}), **({'decals': decals} if decals else {})}
    if not stand_ins:
        resolved.pop('stand_ins', None)
    return {'scene': resolved, 'yielded': yielded_i + yielded_p, 'exemplar_specs': specs, 'layout_sha256': stable_hash(resolved)}


def _stand_ins(path, rows):
    """Each world element a shot stands in (scene.stand_ins) with its blockout (world.json): the shape, its real size,
    its id (the element's; copies '<element>.n', which answer to the element as a group - world.element_ids), the clay
    colour of a stand-in. Raises on an element the world does not have, one with no blockout, and one linked to scene ids
    (the author builds it: a stand-in would stand beside it - build_scene refuses the same id twice after the author)."""
    if not rows:
        return []
    from .world import load as load_world
    elements = {e['id']: e for e in (load_world(path) or {}).get('elements', [])}
    out, seen = [], {}
    for row in rows:
        element = elements.get(row['element'])
        if element is None or not element.get('blockout'):
            raise StudioError('INPUT_INVALID', f"scene stand_in {row['element']}: " + ('no such world element' if element is None else
                              'the element has no blockout (world.json elements[].blockout: kind, size_m)'))
        if element.get('ids'):
            raise StudioError('INPUT_INVALID', f"scene stand_in {row['element']}: the element is linked to scene ids "
                              f"{element['ids']} (world link: the author builds it) - drop the stand-in or unlink the element")
        n = seen.get(row['element'], 0)
        seen[row['element']] = n + 1
        block = element['blockout']
        out.append({'id': row['element'] if n == 0 else f"{row['element']}.{n}", 'element': row['element'],
                    'shape': block['kind'], 'size': list(block['size_m']), 'at': list(row['at']), 'rot_deg': list(row.get('rot_deg') or (0, 0, 0)),
                    'scale_ref': bool(block.get('scale_ref')), 'function': element['function']})
    return out


def _signature(module, function, exclude=()):
    """Keyword names a Blender-side function takes, read from its source (no bpy import): what data passed as **kwargs
    may name."""
    tree = ast.parse((CODE / 'studio/blender_ops' / module).read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function)
    return {a.arg for a in fn.args.args + fn.args.kwonlyargs} - set(exclude)


KIT_SOURCES = {'street': ('env_kits.py', 'street'), 'hall': ('env_kits_space.py', 'hall'), 'strata': ('env_kits_space.py', 'strata'),
               'vegetation': ('env_kits_space.py', 'vegetation'), 'water': ('env_kits_space.py', 'water')}   # blender_ops/layout.py calls these


def _kit_params(kit):
    module, function = KIT_SOURCES[kit]
    return _signature(module, function, ('name', 'library_root'))


def _kit_required(kit):
    """Arguments a kit cannot do without (no default in its signature)."""
    module, function = KIT_SOURCES[kit]
    tree = ast.parse((CODE / 'studio/blender_ops' / module).read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function)
    positional = [a.arg for a in fn.args.args]
    required = positional[:len(positional) - len(fn.args.defaults)]
    required += [a.arg for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults) if d is None]
    return [r for r in required if r not in ('name', 'library_root')]


KITS = {kit: (lambda k=kit: _kit_params(k)) for kit in KIT_SOURCES}
SECTION_OPTIONS = lambda: _signature('section.py', 'stage', ('name', 'box', 'ceilings'))   # noqa: E731  (ceilings is its own key)


def _street_nested():
    """Keys a street's `overrides` (merged over a density preset) and `intersections[].cross` (over its cross_street)
    may name: the keys the presets define."""
    presets = {k: v for k, v in read_json(CODE / 'studio/blender_ops/env_kits_data/presets.json').items() if not k.startswith('_')}
    return set().union(*(p.keys() for p in presets.values())), set().union(*((p.get('cross_street') or {}).keys() for p in presets.values()))


def scene_unread(scene):
    """Values of a scene (as written, or resolved) that nothing reads: kit arguments outside the kit's signature or
    presets, section options outside section.stage's, catalog overrides the material kind does not define (or that
    no catalog material reads)."""
    out = []
    preset_keys, cross_keys = None, None
    for kit in scene.get('kits', []):
        args = kit.get('args', {})
        out += [f"scene/kits/{kit['id']}/args/{k}" for k in sorted(set(args) - KITS[kit['kit']]())]
        if kit['kit'] == 'street' and (args.get('overrides') or args.get('intersections')):
            preset_keys, cross_keys = _street_nested() if preset_keys is None else (preset_keys, cross_keys)
            out += [f"scene/kits/{kit['id']}/args/overrides/{k}" for k in sorted(set(args.get('overrides') or {}) - preset_keys)]
            for i, x in enumerate(args.get('intersections') or []):
                out += [f"scene/kits/{kit['id']}/args/intersections/{i}/cross/{k}" for k in sorted(set(x.get('cross') or {}) - cross_keys)]
    options = (scene.get('section') or {}).get('options') or {}
    if options:
        out += [f'scene/section/options/{k}' for k in sorted(set(options) - SECTION_OPTIONS())]
    kinds = None
    for name, material in (scene.get('materials') or {}).items():
        overrides = material.get('catalog_overrides') or {}
        if not overrides:
            continue
        if not material.get('catalog') or material.get('emission'):
            out.append(f'scene/materials/{name}/catalog_overrides (read only by a catalog material without emission)')
            continue
        kinds = kinds or read_json(CODE / 'studio/blender_ops/look_data/material_catalog.json')['kinds']
        entry = kinds.get(material['catalog'], {})
        out += [f'scene/materials/{name}/catalog_overrides/{k}' for k in sorted(set(overrides) - set(entry))]
    return out


def _project_file(path, relative):
    try:
        return safe_path(project_dir(path), relative).is_file()
    except StudioError:
        return False


def lint(path, shot, author=False):
    """{'errors', 'warnings'} for the shot's scene. `author`: an author script also runs (targets it makes are unknown)."""
    errors, warnings = [], []
    try:
        result = resolve(path, shot)
    except StudioError as error:
        return {'errors': [error.message], 'warnings': []}
    scene = result['scene']
    made = [r['id'] for key in ('volumes', 'kits', 'instances', 'primitives', 'lights', 'links', 'characters', 'decals', 'stand_ins')
            for r in scene.get(key, [])]
    made += [scene['section']['id']] if scene.get('section') else []
    errors += [f'id {i} is used {made.count(i)} times' for i in sorted(set(made)) if made.count(i) > 1]
    errors += [f'nothing reads {p}' for p in scene_unread(scene)]
    errors += [f"kit {kit['id']}: {kit['kit']} needs args.{need}" for kit in scene.get('kits', []) for need in _kit_required(kit['kit'])
               if need not in kit.get('args', {})]
    used = {r['material'] for r in scene.get('primitives', []) if r.get('material')}
    used |= {m for m in [(((scene.get('backdrop') or {}).get('relation') or {}).get('support') or {}).get('material')] if m}
    defined = set(scene.get('materials') or {})
    errors += [f'primitive material {m} is not defined in scene.materials (it would fall back to grey)' for m in sorted(used - defined)]
    backdrop = scene.get('backdrop') or {}   # as merged: a set's backdrop is the shot's too
    if backdrop.get('image') and not _project_file(path, backdrop['image']):
        errors.append(f"backdrop image {backdrop['image']} is not in the project")
    view = (backdrop.get('relation') or {}).get('view')
    move = shot['camera'].get('move') or {}
    if backdrop.get('image') and move:
        from .blender_ops.camera_keys import ORBIT_MOVES
        from .blender_ops.camera_moves_core import PARAMS
        sweep = (move.get('params') or {}).get('sweep_deg', PARAMS.get(move['type'], {}).get('sweep_deg'))
        if move['type'] in ORBIT_MOVES and isinstance(sweep, (int, float)) and sweep > PLATE_SWEEP_DEG:
            warnings.append(f"{move['type']} turns {sweep} deg around the subject but a backdrop image is one view: past "
                            f'{PLATE_SWEEP_DEG:.0f} deg its perspective visibly stops matching (keep the turn small, or the backdrop will need a panorama)')
    if view and move:
        from .blender_ops.camera_moves_core import PARAMS
        default = PARAMS.get(move['type'], {}).get('elevation_deg')
        elevation = (move.get('params') or {}).get('elevation_deg', default if isinstance(default, (int, float)) else None)
        if elevation is not None and abs(elevation - view['elevation_deg']) > VIEW_TOLERANCE_DEG:
            warnings.append(f"camera looks down {elevation} deg but the backdrop was made for {view['elevation_deg']} deg: "
                            'the support and the background perspective will disagree (set one to the other)')
    warnings += [f'scene.materials {m} is used by no primitive' for m in sorted(defined - used)]
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
    unpinned = [i['exemplar'] for i in (shot.get('scene') or {}).get('instances', []) if 'exemplar' in i and '@' not in i['exemplar']]
    if unpinned:
        warnings.append(f'exemplars pinned to their latest version at build: {sorted(set(unpinned))} (write name@vNNN to keep it)')
    return {'errors': errors, 'warnings': warnings, 'counts': {k: len(scene.get(k, [])) for k in ('instances', 'primitives', 'kits', 'lights', 'volumes')},
            'yielded': result['yielded']}
