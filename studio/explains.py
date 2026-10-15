"""What is said must be seen: what each script line explains, and whether that can be seen from outside.

A claim in facts says what it is about (`about`: a world element, a subject, subject/part or subject/feature). A line
explains what its claims are about; the beat pictures and the shots of that line must show it, and what sits inside its
subject (measured: studio/blender_ops/exposure_core.py, `studio subject exposure`) must be shown opened - by any row of
show_core.SHOW_METHODS that opens, never a method named here.

Why (2026-10-13): the rocket's beat pictures drew a closed booster for "유압 팔이 엔진을 기울여"; nothing knew what the
line was about nor that the arms sit inside. The user: "단면 등 하드코딩하지 말고 규칙으로 적용할 방법 고민해봐".
"""
from __future__ import annotations

from pathlib import Path

from .common import file_hash, read_json

EXPOSURE = 'exposure.json'   # subjects/<id>/exposure.json, written by `studio subject exposure`


def _specs(path):
    return {spec.parent.name: read_json(spec) for spec in sorted(Path(path, 'subjects').glob('*/spec.json'))}


def _part_words(spec, part_id):
    said = [f['description'] for f in spec.get('features', []) if part_id in f.get('part_ids', [])]
    return f"{part_id.replace('_', ' ')}" + (f" ({'; '.join(said[:2])})" if said else '')


def resolve(path, about_id, world=None, specs=None):
    """{id, kind: element|subject|part|feature, subject, parts: [subject/part], words}, or None when nothing is called that."""
    specs = _specs(path) if specs is None else specs
    if '/' in about_id:
        sid, rest = about_id.split('/', 1)
        spec = specs.get(sid)
        if spec is None:
            return None
        if rest in {b['part_id'] for b in spec.get('builders', [])}:
            return {'id': about_id, 'kind': 'part', 'subject': sid, 'parts': [about_id], 'words': f"{sid}: {_part_words(spec, rest)}"}
        feature = next((f for f in spec.get('features', []) if f['id'] == rest), None)
        if feature is None:
            return None
        return {'id': about_id, 'kind': 'feature', 'subject': sid, 'parts': [f'{sid}/{p}' for p in feature['part_ids']],
                'words': f"{sid}: {feature['description']}"}
    if about_id in specs:
        return {'id': about_id, 'kind': 'subject', 'subject': about_id, 'parts': [], 'words': str(specs[about_id].get('identity') or about_id)}
    element = next((e for e in (world or {}).get('elements', []) if e['id'] == about_id), None)
    if element is not None:
        return {'id': about_id, 'kind': 'element', 'subject': None, 'parts': [], 'words': element['description']}
    return None


def unknown(path, ids, world=None, bare=False):
    """The ids nothing is called. Before the world (facts come first) a bare name may still become a world element, and
    subject/x of a subject whose spec is not written yet may still be modelled: only `bare=True` (the world known) counts
    those - the world and shot-list lints then name them."""
    specs = _specs(path)
    return [i for i in ids if resolve(path, i, world, specs) is None and (bare or i.split('/', 1)[0] in specs)]


def things(path, ids, world=None):
    specs, out, seen = _specs(path), [], set()
    for i in ids:
        thing = resolve(path, i, world, specs)
        if thing is not None and thing['id'] not in seen:
            seen.add(thing['id']); out.append(thing)
    return out


def line_about(facts, script, line_ids):
    """The about ids of the claims the lines say, in the script's order. A claim with no `about` (facts approved before
    2026-10-13) explains nothing checkable."""
    claims = {c['claim_id']: c for c in (facts or {}).get('claims', [])}
    lines = {l['line_id']: l for l in (script or {}).get('lines', [])}
    out = []
    for line_id in line_ids:
        for claim_id in lines.get(line_id, {}).get('claim_ids') or []:
            out += [a for a in claims.get(claim_id, {}).get('about') or [] if a not in out]
    return out


def line_things(path, line_ids, facts, script, world=None):
    return things(path, line_about(facts, script, line_ids), world)


def exposure(path, subject_id):
    """The subject's survey and its state: (record or None, None | 'EXPOSURE_MISSING' | 'EXPOSURE_STALE')."""
    record, why = _survey(path, subject_id)
    return record, (None if not why else 'EXPOSURE_MISSING' if record is None else 'EXPOSURE_STALE')


def exposure_why(path, subject_id):
    """Why the subject's survey cannot be used, in words - '' when it can."""
    return '; '.join(_survey(path, subject_id)[1])


def _survey(path, subject_id):
    """(record or None, [why it is missing or out of date])."""
    spec = Path(path, 'subjects', subject_id, 'spec.json')
    file = spec.parent / EXPOSURE
    if not file.is_file():
        return None, ['it has no exposure survey']
    record = read_json(file)
    from .blender_ops.contrib_loader import code_sha
    from .blender_ops.exposure_core import SCHEMA_VERSION
    why = []
    # a survey of another schema lacks what the view checks read (v1: no per-direction shares, 2026-10-14): measured again
    if record.get('schema_version') != SCHEMA_VERSION:
        why.append(f"its survey is schema {record.get('schema_version')}, not {SCHEMA_VERSION}")
    if record.get('spec_sha256') != file_hash(spec):
        why.append('its spec changed since the survey')
    if not all(Path(row['dir']).is_dir() and code_sha(row['dir']) == row['sha256'] for row in (record.get('contrib') or {}).values()):
        why.append('its builders changed since the survey')
    return record, why


def need(path, thing):
    """{inside: True|False|None, hidden_by: [words], problem}. None: not measured (a world element, or no survey yet -
    problem says which). A feature is inside only when every part of it is: one part outside can show it."""
    if thing['kind'] in ('element', 'subject'):
        return {'inside': None if thing['kind'] == 'element' else False, 'hidden_by': [], 'problem': None}
    record, problem = exposure(path, thing['subject'])
    if problem:
        return {'inside': None, 'hidden_by': [], 'problem': problem}
    from .blender_ops.exposure_core import inside
    built = {b['part_id'] for b in _specs(path)[thing['subject']].get('builders', [])}
    unbuilt = [p for p in thing['parts'] if p.split('/', 1)[1] not in built]
    if unbuilt:   # a feature naming a part no builder makes: the spec's error, which no survey clears
        return {'inside': None, 'hidden_by': [], 'problem': 'SPEC_PART_UNBUILT', 'parts': unbuilt}
    rows = [record['parts'].get(p.split('/', 1)[1]) for p in thing['parts']]
    if not rows or any(r is None for r in rows):
        return {'inside': None, 'hidden_by': [], 'problem': 'EXPOSURE_STALE'}
    rows = [r for r in rows if r['best_share'] is not None]   # a part with nothing that renders shows nothing either way
    if not rows:
        return {'inside': None, 'hidden_by': [], 'problem': None}
    hidden = all(inside(r['best_share'], record['threshold']) for r in rows)
    by = []
    for r in rows:
        by += [h for h in r.get('hidden_by', []) if h not in by]
    return {'inside': hidden, 'hidden_by': by[:3] if hidden else [], 'problem': None}


def exposure_command(subject_id, project='<project>'):
    return f'studio subject exposure --project {project} --subject {subject_id}'


def open_words():
    """How the lints and the briefs name the ways to show an inside: every opening row of the table."""
    from .blender_ops.show_core import opening_methods
    return ', '.join(opening_methods())


def problem_line(path, n, thing, where):
    """The lint line for a thing whose inside could not be judged: the survey to run, or the spec to fix."""
    if n['problem'] == 'SPEC_PART_UNBUILT':
        return (f"SPEC_PART_UNBUILT: {where} explains {thing['id']}, whose parts {n['parts']} no builder of subjects/"
                f"{thing['subject']}/spec.json makes - fix the feature's part_ids or add the builder")
    return (f"{n['problem']}: {where} explains {thing['id']}; whether it sits inside {thing['subject']} is not measured - "
            f"{exposure_command(thing['subject'], path)}")


def look_words(path, thing):
    """What sets a subject apart, from its own spec: its features and sourced sizes (a 12.2 m demonstrator with one
    engine is not a 41 m booster with nine). A drawn picture follows the words it is given (2026-10-13: world r06's
    demonstrator came out a copy of the booster)."""
    if thing['kind'] != 'subject':
        return ''
    spec = _specs(path).get(thing['id']) or {}
    said = [f['description'] for f in spec.get('features', [])][:5]
    axis = {'z': 'height', 'x': 'width', 'y': 'depth'}
    sizes = list(dict.fromkeys(f"{axis.get(d.get('measure'), d.get('measure', 'size'))} {d['value_m']} m" for d in spec.get('dimensions', [])
                               if d.get('value_m')))[:3]
    return '; '.join(said + sizes)


# ---- what a picture's view shows of a part, before anything is built (A3-5, 2026-10-14) ----------------------------
# Why: side views asked for an engine inside a cluster and for parts only one side shows; the shot list and the
# picture passed, and the build stopped. The survey knows each part's share from every
# direction, so the picture's view is judged while it is still words. The subject's own frame is taken as the scene's
# (the survey builds it at the origin, unturned): a subject an author turns is judged as modelled - the build's camera
# diagnosis (camera_frame.diagnose_part) has the last word.

NESTED_SHARE = 0.5   # one part encloses another when it wraps more than this share of the other's rays (front and back)


def part_row(path, ref):
    """(the survey record, its row for subject/part) when a fresh survey of version 2 has it, else (None, None)."""
    if not isinstance(ref, str) or '/' not in ref:
        return None, None
    subject, part = ref.split('/', 1)
    if not Path(path, 'subjects', subject, 'spec.json').is_file():
        return None, None
    record, problem = exposure(path, subject)
    row = (record or {}).get('parts', {}).get(part) if not problem else None
    return (record, row) if row and row.get('per_direction') else (None, None)


def view_hides(path, ref, view):
    """None when the picture's view (elevation_deg, azimuth_deg) shows the part as well as any side does, or when no
    survey says; else {share, exposable, hidden, best} - the share seen from the survey direction nearest the view,
    judged against the part's exposable share (exposure_core.hidden_against, the survey's threshold), and `best` the
    passing direction nearest the view ({elevation_deg, azimuth_deg, share}, None when no side passes)."""
    from .blender_ops import exposure_core as core
    record, row = part_row(path, ref)
    if row is None:
        return None
    dirs = core.directions(len(row['per_direction']))
    want = core.toward_camera(float(view.get('elevation_deg', 0.0)), float(view.get('azimuth_deg', 0.0)))
    share = row['per_direction'][core.nearest(dirs, want)]
    exposable = row.get('exposable_share') or 0.0
    hidden = core.hidden_against(share, exposable)
    if hidden < record['threshold']:
        return None
    passing = [i for i, s in enumerate(row['per_direction']) if core.hidden_against(s, exposable) < record['threshold']]
    best = max(passing, key=lambda i: sum(a * b for a, b in zip(dirs[i], want))) if passing else None
    return {'share': share, 'exposable': exposable, 'hidden': round(hidden, 3),
            'best': None if best is None else dict(zip(('elevation_deg', 'azimuth_deg'), core.view_of_direction(dirs[best])),
                                                   share=row['per_direction'][best])}


def view_line(path, where, ref, view):
    """SHOW_VIEW_HIDES: the line for a part an outside moment must show that its picture's view hides, or None."""
    hides = view_hides(path, ref, view)
    if hides is None:
        return None
    best = hides['best']
    turn = (f"a view from elevation {best['elevation_deg']} deg, azimuth {best['azimuth_deg']} deg shows {best['share']:.2f} of it"
            if best else 'no side shows it from outside')
    return (f"SHOW_VIEW_HIDES: {where} shows {ref} from outside, and from the picture's view (elevation {view.get('elevation_deg', 0.0)}, "
            f"azimuth {view.get('azimuth_deg', 0.0)}) {hides['share']:.2f} of it shows of the {hides['exposable']:.2f} any side shows - "
            f"{turn}; draw that view, or open the moment ({open_words()})")


def encloses(path, outer, inner):
    """The share of inner's rays, from all sides, on which outer lies both in front of it and behind it (the survey's
    enclosed_by: wrapped round it), when both are parts of one surveyed subject; None when no survey says."""
    if outer.split('/', 1)[0] != inner.split('/', 1)[0]:
        return None
    _record, row = part_row(path, inner)
    if row is None or '/' not in outer:
        return None
    return (row.get('enclosed_by') or {}).get(outer.split('/', 1)[1], 0.0)


def part_reach_m(path, ref):
    """The most the part's thinnest side can show at from any side, in metres (the survey's reach_m, on the part's own
    axes), or None when no survey says."""
    _record, row = part_row(path, ref)
    return row.get('reach_m') if row else None


def subject_box(path, subject):
    """The surveyed subject's box in its own frame (the union of its parts' boxes), or None."""
    if not Path(path, 'subjects', subject, 'spec.json').is_file():
        return None
    record, problem = exposure(path, subject)
    boxes = [r['box'] for r in ((record or {}).get('parts') or {}).values() if r.get('box')] if not problem else []
    if not boxes:
        return None
    return ([min(b[0][i] for b in boxes) for i in range(3)], [max(b[1][i] for b in boxes) for i in range(3)])
