"""A cut's picture checked before it is picked: a reader (screen_grammar.codex_json) answers questions built from the cut
as planned - its opening shown, its parts there, their counts, room for its flows, no text, whole anatomy, the same place
as its chain, an edit that changed only the change - and a blind viewer says what changed between the first and the
last picture, matched against the change (v1's matcher, studio.reel_see). The questions are rows of
library/picture_checks.json; a row is a floor only where calibration bore it out (calibrate), the rest is advice.

A row with a `measure` is not a question to the general reader but a number: `separate` (count_separate) finds the parts
the plan says are many separate things (many_parts), locates them on the try, cuts each out and asks a narrow count
reader three ways how many items stand apart in it (video_parts.count_read, the middle answer kept), over the planned
number. Its limit and floor are calibration's too (labelled pictures, `expect` {row: true good | false bad}).

A failure is made again with its reason (frames make --reason, at most frames.RETRIES times), then written up.
"""
from __future__ import annotations

import re
from pathlib import Path

from studio.common import REPO, StudioError, file_hash, now, read_json, stable_hash, write_json
from studio.project import project_dir

TABLE = REPO / 'library' / 'picture_checks.json'
BLIND = """Two pictures of one short moment of a video, first then second. You know nothing else about it. Answer only from the
pictures. seen: what is on screen, plainly, in a sentence. changes: what is different in the second picture, or "nothing".
learns: what a viewer who knows nothing learns from the two, in one sentence."""


def table():
    return read_json(TABLE)


def questions(cut, frame, rows, has_reference):
    """[{id, row, ask, expect?}] for one frame of a cut (the table's rows that apply to it)."""
    from .grammar import row as grammar_row
    cutaway, change = cut['cutaway'], cut['change']
    out = []
    for rid, r in rows.items():
        if frame not in r['frames'] or r.get('blind') or r.get('measure'):
            continue
        if r.get('needs') == 'place_ref' and not has_reference:
            continue
        per = r.get('per')
        if per == 'part':
            out += [{'id': f"{rid}:{p['id']}", 'row': rid, 'ask': r['asks'].format(part=p['words'])} for p in cutaway['parts']]
        elif per == 'counted part':
            out += [{'id': f"{rid}:{p['id']}", 'row': rid, 'ask': r['asks'].format(part=p['words']), 'expect': p['count']}
                    for p in cutaway['parts'] if p.get('count')]
        elif per == 'flow':
            out += [{'id': f'{rid}:{n}', 'row': rid, 'ask': r['asks'].format(flow=f"of {f['what']} ({f['path']})")}
                    for n, f in enumerate(cutaway['flow'])]
        elif rid == 'opens_shown':
            if cutaway['opens'] != 'none':
                means = (grammar_row('opens', cutaway['opens']) or {}).get('means', cutaway['opens'])
                out.append({'id': rid, 'row': rid, 'ask': r['asks'].format(opens_means=means, what=cutaway['what'])})
        elif '{change}' in r['asks']:
            if change.get('what'):
                out.append({'id': rid, 'row': rid, 'ask': r['asks'].format(change=change['what'])})
        else:
            out.append({'id': rid, 'row': rid, 'ask': r['asks']})
    return out


def _schema(ids):
    answer = {'type': 'object', 'additionalProperties': False, 'required': ['id', 'ok', 'count', 'why'],
              'properties': {'id': {'enum': ids}, 'ok': {'type': 'boolean'}, 'count': {'type': ['integer', 'null']}, 'why': {'type': 'string'}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['answers', 'note'],
            'properties': {'answers': {'type': 'array', 'items': answer, 'minItems': len(ids), 'maxItems': len(ids)}, 'note': {'type': 'string'}}}


def prompt_of(qs, reference):
    head = ('You check one picture made for a short vertical explainer video. The first image is the picture'
            + (', the last image is ' + reference + '.' if reference else '.') + ' Answer each question only from what you see; '
            'for a count, count what is there and give the number. Write why in one short sentence.')
    return head + '\n' + '\n'.join(f"- {q['id']}: {q['ask']}" for q in qs)


def _verdicts(qs, answer):
    got = {a['id']: a for a in answer['answers']}
    out = {}
    for q in qs:
        a = got.get(q['id']) or {'ok': False, 'count': None, 'why': 'not answered'}
        ok = (a.get('count') == q['expect']) if 'expect' in q else bool(a['ok'])
        out[q['id']] = {'row': q['row'], 'ok': ok, 'why': a.get('why', ''), **({'count': a.get('count'), 'expect': q['expect']} if 'expect' in q else {})}
    return out


def judge(image, cut, frame, rows, reference=None, reference_kind=None, target=None, reader=None, reuse=False):
    """{answers: {id: {row, ok, why}}, failures, warnings} of one picture. reuse: a reading already at `target` for the same
    pictures and prompt is taken (a calibration resumed), not asked again."""
    from studio.screen_grammar import codex_json
    qs = questions(cut, frame, rows, reference is not None and reference_kind == 'place')
    if not qs:
        return {'answers': {}, 'failures': [], 'warnings': [], 'note': 'nothing to ask'}
    images = [image] + ([reference] if reference else [])
    words = {'place': 'the place reference', 'original': 'the original picture this one was edited from'}.get(reference_kind)
    prompt = prompt_of(qs, words)
    asked = stable_hash({'prompt': prompt, 'images': [file_hash(i) for i in images]})
    if not (reuse and Path(target).is_file() and read_json(target).get('asked_sha') == asked):
        for attempt in (1, 2):   # a transient failure of the reader is tried once more
            try:
                (reader or codex_json)(images, prompt, _schema([q['id'] for q in qs]), target)
                break
            except StudioError as error:
                if not error.retryable or attempt == 2:
                    raise
        write_json(target, {**read_json(target), 'asked_sha': asked})
    got = read_json(target)
    answers = _verdicts(qs, got['answer'])
    wrong = [i for i, a in answers.items() if not a['ok']]
    return {'answers': answers, 'failures': [i for i in wrong if rows[answers[i]['row']].get('floor')],
            'warnings': [i for i in wrong if not rows[answers[i]['row']].get('floor')], 'note': got['answer'].get('note', ''),
            'reader': got.get('reader') or 'codex'}


# --- many separate things (a measured row) ---------------------------------------------------------------------------

SEPARATE_HEAD = """The image is one part cut out of a picture made for a short explainer video ({n} crop). {parts}
{ask}
Give one count for the crop, from what it shows only."""


def _number(token):
    """A written number of 2 or more ("25", "1,400"), or None."""
    return int(token.replace(',', '')) if re.fullmatch(r'\d{1,3}(,\d{3})+|\d+', token) else None


def _tokens(text):
    return [t.strip(".,;:!?'\"()[]").lower() for t in str(text or '').split()]


def many_parts(cut, row):
    """[{part, n, from}] - the cut's parts the plan says are many separate things: a `count` of 2 or more; a number
    standing before one of the part's own plural nouns (within 4 words, a unit word between them excluded: "300 mm
    wafers" is a size) in its words or the cut's show rows (purpose, show_how.point, cutaway.what, frames' shows); else a
    word of the row's `many` in its words (n None: the row's unknown_n stands for it). A number in a label is not read:
    a label's number may count something else (fab c04b's aisle "최대 1,400단계" counts steps)."""
    texts = [cut.get('purpose'), (cut.get('show_how') or {}).get('point'), cut['cutaway'].get('what')]
    texts += [f.get('shows') for f in (cut.get('frames') or {}).values() if isinstance(f, dict)]
    units = set(row.get('units') or [])
    out = []
    for part in cut['cutaway']['parts']:
        if (part.get('count') or 0) >= 2:
            out.append({'part': part['id'], 'n': part['count'], 'from': 'count'})
            continue
        nouns = {t for t in _tokens(part['words']) if len(t) > 3 and t.endswith('s')}
        found = None
        for text in [part['words']] + texts:
            toks = _tokens(text)
            for i, t in enumerate(toks):
                n = _number(t)
                after = toks[i + 1:i + 5]
                if n and n >= 2 and (not after or after[0] not in units) and any(a in nouns for a in after):
                    found = {'part': part['id'], 'n': n, 'from': text}
                    break
            if found:
                break
        if not found:
            words = ' ' + ' '.join(_tokens(part['words'])) + ' '
            hit = next((m for m in row.get('many') or [] if f' {m} ' in words), None)
            found = {'part': part['id'], 'n': None, 'from': f'"{hit}" in its words'} if hit else None
        if found:
            out.append(found)
    return out


def separate(png, cut, row, folder, placer=None, counter=None):
    """{value (the lowest over the parts), parts: {id: {n, n_from, counts (the three asks), median, value}}} for the
    many-parts of a picture, or None when the cut has none: each part located on it (locate.PROMPT, its box alone), cut
    out with the row's margin, and counted (the middle of three answers); value = min(median, n) / n. Kept in `folder`,
    read again only for another picture, part or wording."""
    from PIL import Image
    from studio.screen_grammar import codex_json
    from . import locate as loc, video_parts as vp
    many = many_parts(cut, row)
    if not many:
        return None
    folder = Path(folder)
    words = {p['id']: p['words'] for p in cut['cutaway']['parts']}
    ids = [m['part'] for m in many]
    key = stable_hash({'image': file_hash(png), 'many': many, 'words': {i: words[i] for i in ids},
                       'row': {k: row.get(k) for k in ('asks', 'tile', 'margin', 'unknown_n')}})[:16]
    kept = folder / 'separate.json'
    if kept.is_file() and read_json(kept).get('key') == key:
        return read_json(kept)['result']
    items = 'parts:\n' + '\n'.join(f'  {i}: {words[i]}' for i in ids)
    (placer or codex_json)([png], loc.PROMPT.format(items=items, reason=''), loc._schema(ids, [], None), folder / 'locate.json')
    located = {p['id']: p for p in read_json(folder / 'locate.json')['answer'].get('parts') or []}
    image = Image.open(png).convert('RGB')
    w, h = image.size
    m = row.get('margin', 0)
    crops = {}
    for i in ids:
        p = located.get(i)
        if not p or not loc.shown(p):
            continue
        x0, y0, x1, y1 = p['box']
        box = (max(0, round((min(x0, x1) - m) * w)), max(0, round((min(y0, y1) - m) * h)),
               min(w, round((max(x0, x1) + m) * w)), min(h, round((max(y0, y1) + m) * h)))
        if box[2] - box[0] >= vp.MIN_PX and box[3] - box[1] >= vp.MIN_PX:
            crops[i] = [(0.0, image.crop(box))]
    files = vp.strips(crops, folder, tile=row.get('tile', vp.TILE))
    counted = vp.count_read(files, words, folder, counter, head=SEPARATE_HEAD, asks=tuple(row['asks'])) if files else {'parts': {}, 'asks': []}
    parts = {}
    for got in many:
        i = got['part']
        n = got['n'] or row['unknown_n']
        median = (counted['parts'].get(i) or [0])[0]   # not located, or too small to read: nothing stands apart
        parts[i] = {'n': n, 'n_from': got['from'] if got['n'] else f"no number planned ({got['from']}): {n} stands for many",
                    'counts': [a.get(i, [None])[0] for a in counted['asks']], 'median': median, 'value': round(min(median, n) / n, 4),
                    'box': (located.get(i) or {}).get('box')}
    result = {'value': min(v['value'] for v in parts.values()), 'parts': parts}
    write_json(kept, {'key': key, 'result': result})
    return result


def separate_answers(result, rid, row):
    """{qid: answer} of a separate() result against the row's limit (a value under it fails; no limit: not calibrated,
    never a pass)."""
    out = {}
    limit = row.get('limit')
    for pid, v in (result or {}).get('parts', {}).items():
        ok = None if limit is None else v['value'] >= limit
        why = (('' if ok else f"{row.get('code', rid)}: ") + f"{v['median']} items read apart (asks {v['counts']}) of {v['n']}, {v['value']}"
               + (f' under the limit {limit}' if ok is False else '') + ('; the check is not calibrated' if ok is None else '')
               + f" - {v['n_from']}")
        out[f'{rid}:{pid}'] = {'row': rid, 'ok': bool(ok), 'calibrated': ok is not None, 'why': why, 'value': v['value'],
                               'count': v['median'], 'expect': v['n']}
    return out


def blind_change(first, last, change_what, point, target, reader=None, matcher=None):
    """What a viewer who knows nothing sees change between the first and last picture, matched against the change (v1's
    matcher): {account, seen: yes|partly|no, why}."""
    from studio.reel_see import SEEN, match_prompt
    from studio.screen_grammar import codex_json
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['seen', 'changes', 'learns'],
              'properties': {'seen': {'type': 'string'}, 'changes': {'type': 'string'}, 'learns': {'type': 'string'}}}
    (reader or codex_json)([first, last], BLIND, schema, target)
    account = read_json(target)['answer']
    mprompt = match_prompt([('cut', account, f'{change_what} ({point})')], account['learns'], point)
    verdict = {'seen': {'enum': list(SEEN)}, 'why': {'type': 'string'}}
    mschema = {'type': 'object', 'additionalProperties': False, 'required': ['cuts', 'reel'],
               'properties': {'cuts': {'type': 'array', 'minItems': 1, 'maxItems': 1, 'items': {
                   'type': 'object', 'additionalProperties': False, 'required': ['shot_id', 'seen', 'why'],
                   'properties': {'shot_id': {'enum': ['cut']}, **verdict}}},
                   'reel': {'type': 'object', 'additionalProperties': False, 'required': ['seen', 'why'], 'properties': verdict}}}
    mtarget = Path(target).with_name(Path(target).stem + '_match.json')
    (matcher or codex_json)([], mprompt, mschema, mtarget)
    cut = read_json(mtarget)['answer']['cuts'][0]
    return {'account': account, 'seen': cut['seen'], 'why': cut['why']}


def _target(path, cut, frame, try_n):
    from .frames import folder
    return folder(path, cut) / 'checks' / f'{frame}_{try_n}.json'


def result_of(path, cut, frame, try_n):
    file = _target(path, cut, frame, try_n)
    if not file.is_file():
        return None
    row = read_json(file)
    from .frames import folder
    png = folder(path, cut) / f'{frame}_{try_n}.png'
    return row if png.is_file() and row.get('image_sha256') == file_hash(png) and row.get('table_sha') == stable_hash(table()['rows']) else None


def floor_failures(result):
    return list(result.get('failures') or [])


def check(project, cut_id, frame=None, try_n=None, reader=None, matcher=None, placer=None, counter=None):
    """Check the cut's latest try of each frame (or the one named) not checked yet. -> {results, next}."""
    from . import frames as frames_mod
    path = project_dir(project)
    body = frames_mod.settled(path)
    cut = frames_mod.cut_of(body, cut_id)
    rows = table()['rows']
    out = []
    for name in ([frame] if frame else frames_mod.needed(cut)):
        have = frames_mod.tries(path, cut, name)
        chosen = [t for t in have if t[0] == try_n] if try_n else have[-1:]
        for n, png, _record in chosen:
            if result_of(path, cut, name, n) is not None:
                out.append({'frame': name, 'try': n, **result_of(path, cut, name, n), 'cached': True})
                continue
            reference, kind = None, None
            if name == 'first':
                place_ref, _style = frames_mod.chain_ref(path, body, cut)
                reference, kind = (place_ref, 'place') if place_ref else (None, None)
            elif name in frames_mod.EDITS:
                reference, kind = frames_mod.picked(path, cut, 'first'), 'original'
            target = _target(path, cut, name, n)
            got = judge(png, cut, name, rows, reference, kind, target.with_name(f'{name}_{n}_read.json'), reader)
            for rid, r in rows.items():
                if r.get('measure') != 'separate' or name not in r['frames']:
                    continue
                answers = separate_answers(separate(png, cut, r, target.parent / f'{name}_{n}_separate', placer, counter), rid, r)
                got['answers'].update(answers)
                for qid, a in answers.items():
                    if not a['ok']:
                        (got['failures'] if a['calibrated'] and r.get('floor') else got['warnings']).append(qid)
            if name == 'last' and cut['change'].get('what') and frames_mod.picked(path, cut, 'first') and 'change_read' in rows:
                read = blind_change(frames_mod.picked(path, cut, 'first'), png, cut['change']['what'], cut['show_how']['point'],
                                    target.with_name(f'{name}_{n}_blind.json'), reader, matcher)
                got['answers']['change_read'] = {'row': 'change_read', 'ok': read['seen'] != 'no', 'why': read['why'], 'seen': read['seen'],
                                                 'account': read['account']}
                if read['seen'] == 'no':
                    (got['failures'] if rows['change_read'].get('floor') else got['warnings']).append('change_read')
            row = {'cut': cut_id, 'frame': name, 'try': n, 'image_sha256': file_hash(png), 'table_sha': stable_hash(rows), 'at': now(), **got}
            write_json(target, row)
            out.append(row)
    nexts = []
    for r in out:
        wrong = r['failures'] + r['warnings']
        if wrong:
            why = '; '.join(f"{i}: {r['answers'][i]['why']}" for i in wrong)
            nexts.append(f"{r['frame']}_{r['try']}: {why} - frames make --cut {cut_id} --frame {r['frame']} --reason \"...\" "
                         + ('(a floor: it cannot be picked)' if r['failures'] else '(advice: pick it with why, or make it again)'))
    return {'cut': cut_id, 'results': out, 'next': nexts or [f'frames pick --cut {cut_id} --frame <frame> --try N --why ...']}


# --- calibration ----------------------------------------------------------------------------------------------------

def calibrate(set_file, out, runs=2, reader=None, write=False, placer=None, counter=None, only=None):
    """Run the checks on a set of pictures whose right answers are known: [{name, image, cut, frame, reference?,
    reference_kind?, expect: {row: true|false}, why}]. A row becomes a floor only when every expected pass passed and every
    expected fail failed, on every run, with at least one of each. A measured row (`measure`) is measured on every item
    that labels it, located and read afresh each run; its limit is the middle of the gap between the labelled goods and
    bads over all runs (video._limit), a floor only where that gap is clean. `only`: the rows to calibrate (the rest keep
    theirs). -> per row the counts; write: the table's floors, limits and calibration block are set from them."""
    from .video import _limit
    items = read_json(set_file)['items']
    rows = {k: v for k, v in table()['rows'].items() if not only or k in only}
    out = Path(out)
    tally = {}
    readings = []
    measured = {rid: r for rid, r in rows.items() if r.get('measure') == 'separate'}
    values = {rid: {'good': [], 'bad': [], 'good_items': set(), 'bad_items': set(), 'per': []} for rid in measured}
    for run in range(runs):
        for item in items:
            for rid, r in measured.items():
                want = item['expect'].get(rid)
                if want is None:
                    continue
                got = separate(item['image'], item['cut'], r, out / item['name'] / f'run{run}', placer, counter)
                if got is None:
                    continue
                v = values[rid]
                v['good' if want else 'bad'].append(got['value'])
                v['good_items' if want else 'bad_items'].add(item['name'])
                v['per'].append({'item': item['name'], 'run': run, 'want': want, 'value': got['value'], 'parts': got['parts']})
            if not any(rid not in measured for rid in rows):
                continue
            target = out / f"{item['name']}_run{run}.json"
            got = judge(item['image'], item['cut'], item['frame'], {k: {**v, 'floor': False} for k, v in rows.items()},
                        item.get('reference'), item.get('reference_kind'), target, reader, reuse=True)
            for qid, a in got['answers'].items():
                want = item['expect'].get(qid, item['expect'].get(a['row']))
                if want is None:
                    continue
                t = tally.setdefault(a['row'], {'pass_expected': 0, 'pass_got': 0, 'fail_expected': 0, 'fail_got': 0, 'misses': []})
                if want:
                    t['pass_expected'] += 1; t['pass_got'] += a['ok']
                else:
                    t['fail_expected'] += 1; t['fail_got'] += not a['ok']
                if a['ok'] != want:
                    t['misses'].append({'item': item['name'], 'id': qid, 'run': run, 'why': a['why']})
            readings.append({'item': item['name'], 'run': run, 'answers': got['answers']})
    for row, t in tally.items():
        t['floor'] = bool(t['pass_expected'] and t['fail_expected'] and t['pass_got'] == t['pass_expected'] and t['fail_got'] == t['fail_expected'])
    for rid, v in values.items():
        limit, separates, missed = _limit(v['good'], v['bad'], measured[rid]['flag'])
        tally[rid] = {'limit': limit, 'floor': separates, 'misses': missed, 'good': sorted(v['good']), 'bad': sorted(v['bad']),
                      'good_items': sorted(v['good_items']), 'bad_items': sorted(v['bad_items']),
                      'gap': round(min(v['good']) - max(v['bad']), 4) if v['good'] and v['bad'] else None, 'readings': v['per']}
    result = {'at': now(), 'runs': runs, 'items': len(items), 'rows': tally}
    write_json(out / 'calibration.json', {**result, 'readings': readings})
    if write:
        data = table()
        for row, t in tally.items():
            data['rows'][row]['floor'] = t['floor']
            if row in measured:
                data['rows'][row]['limit'] = t['limit']
        keep = (data.get('calibration') or {}).get('rows') or {}
        data['calibration'] = {**(data.get('calibration') or {}), 'rows': {**keep, **{
            r: {k: v for k, v in t.items() if k not in ('misses', 'readings')} | ({'at': result['at'], 'runs': runs} if r in measured else {})
            for r, t in tally.items()}}}
        if any(r not in measured for r in tally):
            data['calibration'].update(at=result['at'], runs=runs, items=len(items))
        write_json(TABLE, data)
    return {**result, 'artifacts': [str(out / 'calibration.json')]}


def register_commands(subparsers):
    parser = subparsers.add_parser('checks', help="A cut's pictures checked before they are picked (library/picture_checks.json)")
    commands = parser.add_subparsers(dest='checks_command', required=True)
    p = commands.add_parser('run', help="check the cut's latest try of each frame (or --frame/--try)")
    p.add_argument('--project', required=True); p.add_argument('--cut', required=True); p.add_argument('--frame')
    p.add_argument('--try', dest='try_n', type=int)
    p.set_defaults(handler=lambda a: check(a.project, a.cut, a.frame, a.try_n))
    p = commands.add_parser('calibrate', help='the checks on pictures whose answers are known: which rows may be floors')
    p.add_argument('--set', required=True); p.add_argument('--out', required=True); p.add_argument('--runs', type=int, default=2)
    p.add_argument('--write', action='store_true', help="set the table's floors and calibration from the result")
    p.add_argument('--only', nargs='+', help='the rows to calibrate (the rest keep theirs)')
    p.set_defaults(handler=lambda a: calibrate(a.set, a.out, a.runs, write=a.write, only=a.only))
    return commands
