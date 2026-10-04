"""Factory spec validation and standard-table lookup (host side, stdlib only).

A spec names an asset by standard + designation; every dimension the CAD
worker uses comes from ``tables/*.json`` rows, never from the caller.
"""
from pathlib import Path

from ..common import StudioError, check_id, file_hash, read_json, stable_hash

TABLES = Path(__file__).parent / 'tables'
# Bump when the meaning of a spec or of factory.json changes without a code-file change.
FACTORY_SCHEMA = 1
# Builder source per kind: its bytes are part of the spec hash, so a geometry
# change in the builder can never be served from a stale library version.
BUILDERS = {'bolt_set': 'cad_worker.py', 'hbeam': 'cad_worker.py', 'rebar_cage': 'blender/rebar_cage.py'}
# Allowed fields per kind (default-deny: unknown fields are rejected, not ignored).
FIELDS = {
    'bolt_set': {'length_mm', 'grip_mm', 'washers', 'nut', 'nut_standard', 'washer_standard'},
    'hbeam': {'length_mm'},
    'rebar_cage': {'tie_designation', 'column_mm', 'cover_mm', 'length_mm', 'tie_spacing_mm', 'longitudinal_count'},
}
COMMON = {'asset_id', 'kind', 'standard', 'designation', 'name', 'tags'}
STANDARD_OF_KIND = {'bolt_set': 'ISO 4014', 'hbeam': 'EN 10365', 'rebar_cage': 'KS D 3504'}


def load_tables(root=None):
    """Return {standard: table} for every tables/*.json; each row must cite its source."""
    tables = {}
    for path in sorted(Path(root or TABLES).glob('*.json')):
        table = read_json(path)
        if not str(table.get('_verification', '')).strip():
            raise StudioError('INPUT_INVALID', f'Table {path.name} lacks the _verification notice')
        for designation, row in table.get('rows', {}).items():
            if not str(row.get('source', '')).strip():
                raise StudioError('INPUT_INVALID', f'Table {path.name} row {designation} lacks a source')
        tables[table['standard']] = {**table, 'file': path.name}
    return tables


def _key(text):
    return ''.join(str(text).split()).upper()


def lookup(tables, standard, designation):
    table = tables.get(standard)
    if table is None:
        raise StudioError('SPEC_UNKNOWN_STANDARD', f'No table for standard {standard!r}', recovery=f'Known: {sorted(tables)}')
    for name, row in table['rows'].items():
        if _key(name) == _key(designation):
            return name, row
    raise StudioError('SPEC_UNKNOWN_DESIGNATION', f'{standard} has no row {designation!r}',
                      recovery=f'Known designations: {sorted(table["rows"])}; add a sourced row to {table["file"]}')


def _row(tables, standard, designation):
    """Table row plus the provenance and gate tolerances the worker needs."""
    name, row = lookup(tables, standard, designation)
    table = tables[standard]
    extra = {key: table[key] for key in ('gate_tolerance_mm', 'area_tolerance_rel') if key in table}
    return name, {'standard': standard, 'designation': name, **row, **extra}


def _number(spec, key, default=None, positive=True):
    value = spec.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (positive and value <= 0):
        raise StudioError('INPUT_INVALID', f'{key} must be a positive number, got {value!r}')
    return value


def resolve_spec(spec, tables=None):
    """Validate a spec and attach the exact table rows it uses.

    Returns {'spec': spec with defaults filled, 'standards': [...], 'rows': {role: row}}.
    """
    if not isinstance(spec, dict):
        raise StudioError('INPUT_INVALID', 'Factory spec must be a JSON object')
    tables = tables or load_tables()
    check_id(spec.get('asset_id'))
    kind = spec.get('kind')
    if kind not in FIELDS:
        raise StudioError('INPUT_INVALID', f'Unknown factory kind {kind!r}', recovery=f'Known kinds: {sorted(FIELDS)}')
    unknown = set(spec) - COMMON - FIELDS[kind]
    if unknown:
        raise StudioError('INPUT_INVALID', f'Unknown {kind} spec fields: {sorted(unknown)}')
    standard = spec.get('standard', STANDARD_OF_KIND[kind])
    if standard != STANDARD_OF_KIND[kind]:
        raise StudioError('INPUT_INVALID', f'{kind} is built to {STANDARD_OF_KIND[kind]}, not {standard!r}')
    out = {**spec, 'standard': standard}
    name, row = _row(tables, standard, spec.get('designation'))
    out['designation'] = name
    rows = {'main': row}
    if kind == 'bolt_set':
        out['nut'] = spec.get('nut', True)
        out['washers'] = spec.get('washers', 2)
        out['nut_standard'] = spec.get('nut_standard', 'ISO 4032')
        out['washer_standard'] = spec.get('washer_standard', 'ISO 7089')
        if not isinstance(out['nut'], bool) or out['washers'] not in (0, 1, 2):
            raise StudioError('INPUT_INVALID', 'nut must be true/false and washers 0, 1 or 2')
        length = _number(spec, 'length_mm')
        if length not in tables[standard]['nominal_lengths_mm'] or not row['l_min'] <= length <= row['l_max']:
            raise StudioError('INPUT_INVALID', f'{standard} {name} length {length} is not a listed nominal length in [{row["l_min"]}, {row["l_max"]}]')
        grip = _number(spec, 'grip_mm')
        stack = grip
        if out['washers']:
            _, wrow = _row(tables, out['washer_standard'], name)
            rows['washer'] = wrow
            stack += out['washers'] * wrow['h']
        if out['nut']:
            _, nrow = _row(tables, out['nut_standard'], name)
            rows['nut'] = nrow
            stack += nrow['m']
        if stack > length:
            raise StudioError('INPUT_INVALID', f'Grip {grip} + washers + nut ({stack:.2f} mm) exceeds bolt length {length}')
    elif kind == 'hbeam':
        _number(spec, 'length_mm')
    else:
        out.update({'tie_designation': spec.get('tie_designation', 'D10'), 'column_mm': _number(spec, 'column_mm', 400),
                    'cover_mm': _number(spec, 'cover_mm', 40), 'length_mm': _number(spec, 'length_mm', 1200),
                    'tie_spacing_mm': _number(spec, 'tie_spacing_mm', 150), 'longitudinal_count': spec.get('longitudinal_count', 8)})
        if out['longitudinal_count'] not in (4, 8):
            raise StudioError('INPUT_INVALID', 'longitudinal_count must be 4 (corners) or 8 (corners + mid-faces)')
        out['tie_designation'], rows['tie'] = _row(tables, standard, out['tie_designation'])
    standards = sorted({item['standard'] for item in rows.values()})
    return {'spec': out, 'standards': standards, 'rows': rows}


def spec_hash(spec, tables=None):
    """Identity of a generated asset: resolved spec + table rows + builder source bytes."""
    resolved = resolve_spec(spec, tables)
    builder = Path(__file__).parent / BUILDERS[resolved['spec']['kind']]
    return stable_hash({'factory_schema': FACTORY_SCHEMA, 'resolved': resolved, 'builder_sha256': file_hash(builder)})


def rebar_cage_params(spec, tables=None):
    """Parameters for blender/rebar_cage.build_rebar_cage from a rebar_cage spec."""
    resolved = resolve_spec(spec, tables)
    if resolved['spec']['kind'] != 'rebar_cage':
        raise StudioError('INPUT_INVALID', 'rebar_cage_params needs a rebar_cage spec')
    s, rows = resolved['spec'], resolved['rows']

    def bar(row):
        # Rib height: mid of the tabulated min..max, as in the prototype.
        return {'designation': row['designation'], 'd': row['d'], 'rib_spacing': row['rib_spacing_max'],
                'rib_height': round((row['rib_height_min'] + row['rib_height_max']) / 2, 4)}
    return {'name_prefix': s['asset_id'], 'column_mm': s['column_mm'], 'cover_mm': s['cover_mm'], 'length_mm': s['length_mm'],
            'tie_spacing_mm': s['tie_spacing_mm'], 'longitudinal_count': s['longitudinal_count'],
            'longitudinal': bar(rows['main']), 'tie': bar(rows['tie'])}
