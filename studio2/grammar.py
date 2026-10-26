"""The cutaway grammar: what a cut may say about itself, as rows (library/patterns/cutaway_grammar.json), and what the
reference does with each, read from the reference library through studio.patterns - never remembered here.

A field either names its values (opens, camera.move, change.kind, view, scale_step, graphics: a new kind is a row) and the
reference item its values are read against, or names the numbers it reads ('section.item.stat'). A number the reference
has no strong evidence for reads None with why (patterns.reading), and nothing is checked against it.
"""
from __future__ import annotations

from studio.common import REPO, StudioError, read_json

TABLE = REPO / 'library' / 'patterns' / 'cutaway_grammar.json'


def load():
    return read_json(TABLE)


def values(field, table=None):
    """The values a field takes (its rows), e.g. values('opens') -> ['section_cut', ...]."""
    row = (table or load())['fields'].get(field)
    if row is None or 'values' not in row:
        raise StudioError('INPUT_INVALID', f'cutaway grammar: no field {field!r} with values')
    return list(row['values'])


def row(field, value, table=None):
    return (table or load())['fields'][field]['values'].get(value)


def scales(table=None):
    return list((table or load())['scales']['order'])


def colour(name, table=None):
    """sRGB 0-255 of a colour row, or None."""
    got = (table or load())['colours']['rows'].get(name)
    return tuple(got['srgb']) if got else None


def _library(name):
    from studio.patterns import load as load_library
    return load_library(name)


def read(field, table=None, library=None):
    """What the reference does with a field: for a field with values, each value's share (patterns.reading of its item);
    for a field of numbers, each number it reads. Every entry is {reads, value, n?, why?}."""
    table = table or load()
    spec = table['fields'][field]
    from studio.patterns import reading
    lib = library or _library(spec.get('library') or table['library'])
    if 'values' in spec:
        return {v: reading(lib, f"all.{spec['item']}.{(r or {}).get('item_value', v)}") for v, r in spec['values'].items()}
    return {reads: {**reading(lib, reads), **shown(lib, reads)} for reads in spec['reads']}


def shown(library, reads):
    """The number as measured even when it is no evidence (weak): {measured, strength} - shown beside the reading, never
    used as a target (patterns.py: 'Weak items are shown, never used as targets')."""
    where, item, stat = reads.split('.')
    part = library.get('reel') if where == 'reel' else (library.get('sections') or {}).get(where)
    data = ((part or {}).get('items') or {}).get(item) or {}
    return {'measured': data.get(stat), 'strength': data.get('strength')} if data.get(stat) is not None else {}


def number(field, stat, table=None):
    """One number a field reads ('label_text', 'median') -> the value or None (no strong evidence)."""
    table = table or load()
    spec = table['fields'][field]
    reads = next((r for r in spec['reads'] if r.endswith('.' + stat)), None)
    if reads is None:
        raise StudioError('INPUT_INVALID', f'cutaway grammar: {field} reads no {stat}')
    from studio.patterns import load as load_library, reading
    try:
        lib = load_library(spec.get('library') or table['library'])
    except StudioError:
        return None
    return reading(lib, reads)['value']


def show(project=None):
    """Every field, its values or numbers and what the reference does with each."""
    table = load()
    out = {}
    for field, spec in table['fields'].items():
        try:
            out[field] = {'means': spec.get('means'), 'reads': read(field, table)}
        except StudioError as error:   # a library not built yet (a measure that has not run): said, not hidden
            out[field] = {'means': spec.get('means'), 'reads': None, 'why': error.message}
    return {'library': table['library'], 'fields': out, 'scales': table['scales']['order'], 'colours': list(table['colours']['rows'])}


def register_commands(subparsers):
    parser = subparsers.add_parser('grammar', help='The cutaway grammar: its rows and what the reference does with each')
    commands = parser.add_subparsers(dest='grammar_command', required=True)
    p = commands.add_parser('show'); p.add_argument('--project')
    p.set_defaults(handler=lambda a: show(a.project))
    from .label_measure import register
    register(commands)
    return commands
