"""The approval sheet: the user's first look at a reel, before anything is paid. One page (review/sheet/index.html) -
per cut its first, middle and last pictures (picked; a frame of the free preview where the cut has no such picture),
its line and what the viewer learns, the motion in words, the free preview clip, the paid video's model, length, cost
and prompt; the reel's total at the bottom, with the project's budget and what is already spent.

The user's answer is recorded as the paid approval through v1's user-words path (generative.review.check_user_words:
the user's own words only, never in a run started without its user): `sheet approve --user-words "..."` approves every
cut on the sheet, bound to the request each would send (its fingerprint: endpoint, prompt, first picture, length and
arguments); `--cuts c3` names the cuts the words ask to change - those are reopened (redrawn with the words as the
reason, then a new sheet), never approved, and words naming a cut that --cuts leaves out are refused. An approval
holds only while its request is unchanged (approved).

Why (2026-10-26, the user): "씬별로 첫, 중간, 끝 이미지랑 해당 내용, 움직임 브리핑하고 사용자가 승인".
"""
from __future__ import annotations

import html
import re
import shutil
import subprocess
from pathlib import Path

from studio.common import StudioError, file_hash, now, read_json, stable_hash, write_json

SHEET = 'review/sheet'
MODEL = {'model': 'veo-3.1', 'operation': 'image_to_video'}   # the paid video (routing.MODELS); another model's price is a
                                                              # fal_client.PRICING row, which needs the user's freeze words
PREVIEW_AT = {'first': 0.0, 'middle': 0.5, 'last': 0.98}       # where a picture the cut does not have is taken from its preview


def request_of(path, body, cut):
    """What the paid video of a cut would send and cost: {endpoint, model, prompt, first, seconds, billed_s, usd,
    fingerprint} - the fingerprint binds an approval to exactly this request (generative/clip.build_arguments with the
    files' hashes for their bytes)."""
    from studio.generative import clip as clipmod
    from studio.generative.fal_client import estimate_usd
    from studio.project import load_project
    from . import prompts
    from .frames import picked
    first = picked(path, cut, 'first')
    if first is None:
        raise StudioError('FRAMES_UNPICKED', f"{cut['cut_id']}: no picked first picture - the paid video starts on it")
    prompt, negative = prompts.video(cut, body), prompts.video_negative(cut, body)
    refused = prompts.video_lint(prompt, cut['duration_s']) + prompts.negative_lint(negative)
    if refused:   # the cuts lint refuses it at the plan; this holds the paid boundary for a plan settled before the rules
        raise StudioError('VIDEO_PROMPT_REFUSED', f"{cut['cut_id']}: {'; '.join(refused)}",
                          recovery='change the cut (cuts write): its change, camera or video.holds - studio2 cuts lint says how')
    spec = {**MODEL, 'duration_seconds': cut['duration_s'], 'negative_prompt': negative,
            'inputs': [{'kind': 'first_frame', 'path': str(Path(first).relative_to(path))}]}
    endpoint = clipmod.ENDPOINTS[(MODEL['model'], MODEL['operation'])]
    billed = clipmod._billed_output_seconds(spec)
    arguments = clipmod.build_arguments(path, load_project(path), spec, prompt, uri=lambda p: 'sha256:' + file_hash(p))
    request = {'endpoint': endpoint, 'prompt_sha256': stable_hash(prompt), 'first_sha256': file_hash(first), 'seconds': cut['duration_s'],
               'billed_s': billed, 'arguments_sha256': stable_hash(arguments), 'take': 1}
    return {**MODEL, 'endpoint': endpoint, 'prompt': prompt, 'negative_prompt': negative, 'first': str(first), 'seconds': cut['duration_s'], 'billed_s': billed,
            'usd': round(estimate_usd(endpoint, billed), 2), 'spec': spec, 'fingerprint': stable_hash(request)[:16]}


def motion_words(cut):
    """The cut's motion in words: the camera, what changes and when, what moves, what flows."""
    cam, change = cut['cutaway']['camera'], cut['change']
    out = ['camera: ' + cam['move'] + (f" (the picture {cam['amount']:.0%})" if cam.get('amount') else '')
           + (f" along {cam['along']}" if cam.get('along') else '') + (f", ending on {cam['ends']}" if cam.get('ends') else '')]
    if change['kind'] != 'none':
        at = change.get('at') or [0, 1]
        out.append(f"{change.get('what', change['kind'])} ({change['kind']}{', the key change' if change.get('key') else ''}; "
                   f"{at[0]:.0%}-{at[1]:.0%} of the cut)")
    element = change.get('element')
    if element:
        how = {'picture': 'painted in the pictures; the video moves it', 'speck': 'a glowing mark drawn over', 'sprite': 'a small picture drawn over'}
        out.append(f"{element['what']} moves {element['path']} ({how[element['drawn']]})")
    out += [f"{f['what']} flows {f['path']} (drawn {f['colour']})" for f in cut['cutaway']['flow']]
    if cut['cutaway']['labels']:
        out.append('labels: ' + ', '.join(l['word'] for l in cut['cutaway']['labels']))
    return out


def _frame(clip, share, frames, target):
    if not target.is_file():
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{share * frames / 30:.3f}', '-i', str(clip), '-frames:v', '1', '-vf', 'scale=270:-2',
                        str(target)], check=True)
    return target


def _thumb(png, target):
    from PIL import Image
    if not target.is_file():
        with Image.open(png) as im:
            im = im.convert('RGB')
            im.thumbnail((270, 480))
            im.save(target, quality=88)
    return target


def preview_reel(path, clips):
    """The newest preview reel v1's edit cut from exactly these clips ({cut: clip}), in this order, or None: the user's
    first look is at the reel as a viewer meets it - its voice and captions over the clips on the sheet, none other."""
    want = [(cid, file_hash(clip)) for cid, clip in clips.items()]
    found = []
    for snapshot in (path / 'final').glob('*/edit.snapshot.json'):
        video = next((v for v in (snapshot.parent / n for n in ('candidate.mp4', 'scratch_candidate.mp4')) if v.is_file()), None)
        if video is not None and [(s['shot_id'], s.get('clip_sha256')) for s in read_json(snapshot).get('shots') or []] == want:
            found.append((snapshot.stat().st_mtime, video))
    return max(found)[1] if found else None


def build(project):
    """The sheet of the reel as it stands: review/sheet/<rev>/ (index.html, sheet.json, its pictures and clips) and
    review/sheet/index.html (the latest). Every cut must be made; a cut failing the calibrated style floor is not put to
    the user (STYLE_OFF: redrawn from its plan first)."""
    from studio import decisions
    from studio.edit import latest_composite
    from studio.generative.fal_client import ledger_total
    from studio.project import load_project, load_shot, project_dir
    from .frames import picked
    from .style import failing
    path = project_dir(project)
    decisions.require(path, 'cut_preview')
    body = decisions.envelope(path, 'cuts')['body']
    lines = {l['line_id']: l['text'] for l in decisions.envelope(path, 'script')['body']['lines']}
    off = [(c['cut_id'], failing(path, c)) for c in body['cuts']]
    off = [(c, row) for c, row in off if row]
    if off:
        raise StudioError('STYLE_OFF', '; '.join(f"{c} reads {row['belongs']} against the reference ({row.get('further', '')})" for c, row in off),
                          recovery='redraw each from its plan - what it opens, its scale step (cuts write, frames make) - then the sheet')
    rows, made = [], {}
    for cut in body['cuts']:
        shot = load_shot(path, cut['cut_id'])
        made[cut['cut_id']] = latest_composite(path, shot, shot['duration_frames'])
        if made[cut['cut_id']] is None:
            raise StudioError('RENDER_REQUIRED', f"{cut['cut_id']}: no free preview clip made", recovery=f"studio2 motion render --cut {cut['cut_id']}")
        rows.append((cut, shot, request_of(path, body, cut)))
    rev = stable_hash([[c['cut_id'], r['fingerprint'], made[c['cut_id']]['key']] for c, _s, r in rows])[:12]
    folder = path / SHEET / rev
    folder.mkdir(parents=True, exist_ok=True)
    out_rows = []
    for cut, shot, request in rows:
        cid, clip = cut['cut_id'], made[cut['cut_id']]
        pictures = {}
        for name in ('first', 'middle', 'last'):
            png = picked(path, cut, name)
            if png is not None:
                pictures[name] = {'file': _thumb(png, folder / f'{cid}_{name}.jpg').name, 'from': 'picture'}
            else:
                pictures[name] = {'file': _frame(clip['clip'], PREVIEW_AT[name], shot['duration_frames'], folder / f'{cid}_{name}_preview.jpg').name,
                                  'from': 'preview'}
        clip_copy = folder / f'{cid}.mp4'
        if not clip_copy.is_file():
            shutil.copy2(clip['clip'], clip_copy)
        own = [lines[i] for i in cut['line_ids'] if i in lines]
        out_rows.append({'cut': cid, 'seconds': cut['duration_s'], 'lines': own, 'line_ids': list(cut['line_ids']),
                         'learns': cut['show_how']['point'], 'motion': motion_words(cut), 'pictures': pictures, 'clip': clip_copy.name,
                         'clip_key': clip['key'], 'model': request['model'], 'endpoint': request['endpoint'], 'billed_s': request['billed_s'],
                         'usd': request['usd'], 'prompt': request['prompt'], 'negative_prompt': request['negative_prompt'],
                         'fingerprint': request['fingerprint']})
    data = load_project(path)
    budget = (data.get('route_policy') or {}).get('budget_usd')
    total = round(sum(r['usd'] for r in out_rows), 2)
    warnings = []
    reel = preview_reel(path, {c['cut_id']: made[c['cut_id']]['clip'] for c, _s, _r in rows})
    if reel is not None:
        shutil.copy2(reel, folder / 'reel.mp4')
    else:
        warnings.append('PREVIEW_UNMADE: no preview reel was cut from these clips - studio2 reel preview, then the sheet again')
    sheet = {'schema_version': 1, 'rev': rev, 'at': now(), 'cuts': out_rows, 'total_usd': total, 'budget_usd': budget,
             'spent_usd': round(ledger_total(data['project_id']), 2), 'reel': 'reel.mp4' if reel is not None else None}
    write_json(folder / 'sheet.json', sheet)
    page = folder / 'index.html'
    page.write_text(_html(sheet, data), encoding='utf-8')
    latest = path / SHEET / 'index.html'
    latest.write_text(_html(sheet, data, base=f'{rev}/'), encoding='utf-8')
    write_json(path / SHEET / 'latest.json', {'rev': rev, 'at': sheet['at']})
    return {'page': str(page), 'latest': str(latest), 'sheet': str(folder / 'sheet.json'), 'rev': rev, 'total_usd': total,
            'reel': str(folder / 'reel.mp4') if reel is not None else None, 'warnings': warnings, 'artifacts': [str(page), str(latest)],
            'next': ["show the page to the user; their answer: studio2 sheet approve --user-words \"<their words>\" [--cuts <cuts they ask to change>]"]}


def _html(sheet, project, base=''):
    e = html.escape
    rows = []
    for r in sheet['cuts']:
        pics = ''.join(f"<figure><img src='{base}{p['file']}'><figcaption>{name}{' (from the preview)' if p['from'] == 'preview' else ''}"
                       f"</figcaption></figure>" for name, p in r['pictures'].items())
        rows.append(f"""<section class='cut'><h2>{e(r['cut'])} <span>{r['seconds']:g} s</span></h2>
<div class='pics'>{pics}<figure><video src='{base}{r['clip']}' controls loop muted playsinline></video><figcaption>free preview</figcaption></figure></div>
<p class='line'>{e(' '.join(r['lines'])) or '(the line before runs on)'}</p>
<p><b>The viewer learns:</b> {e(r['learns'])}</p>
<p><b>Motion:</b> {e('; '.join(r['motion']))}</p>
<p><b>Paid video:</b> {e(r['model'])} image-to-video, {r['billed_s']} s billed, <b>${r['usd']:.2f}</b></p>
<details><summary>what the video model is asked</summary><p class='prompt'>{e(r['prompt'])}</p>
<p class='prompt'><b>Avoid (negative prompt):</b> {e(r.get('negative_prompt') or '')}</p></details></section>""")
    budget = (f"budget ${sheet['budget_usd']:.2f}, already spent ${sheet['spent_usd']:.2f}" if sheet.get('budget_usd') is not None
              else f"no budget set; already spent ${sheet['spent_usd']:.2f}")
    reel = (f"<section class='cut'><h2>The free preview reel <span>scratch voice and captions</span></h2>"
            f"<video src='{base}{sheet['reel']}' controls playsinline></video></section>" if sheet.get('reel')
            else "<p><b>No preview reel was cut from these clips yet.</b></p>")
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Approval sheet</title><style>
:root{{--bg:#fff;--fg:#1d1d1f;--muted:#666;--card:#f4f4f6}}
@media (prefers-color-scheme: dark){{:root{{--bg:#151517;--fg:#eee;--muted:#aaa;--card:#222226}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,system-ui,sans-serif;margin:0 auto;max-width:1200px;padding:16px}}
.cut{{background:var(--card);border-radius:10px;padding:12px 16px;margin:14px 0}} h2 span{{color:var(--muted);font-weight:400;font-size:.8em}}
.pics{{display:flex;gap:8px;overflow-x:auto}} figure{{margin:0}} img,video{{height:300px;border-radius:6px;display:block}}
figcaption{{color:var(--muted);font-size:.85em}} .line{{font-size:1.1em}} .prompt{{color:var(--muted)}}
.total{{font-size:1.2em;border-top:2px solid var(--muted);padding-top:10px}}</style></head><body>
<h1>{e(project['brief'].get('request', project['project_id']))[:120]}</h1>
<p>Sheet {e(sheet['rev'])} - {len(sheet['cuts'])} cuts. Approve all, or name the cuts to change and what to change.</p>
{reel}
{''.join(rows)}
<p class='total'>Reel total: <b>${sheet['total_usd']:.2f}</b> ({budget})</p></body></html>"""


def latest(path):
    file = Path(path) / SHEET / 'latest.json'
    if not file.is_file():
        raise StudioError('SHEET_MISSING', 'no approval sheet was made', recovery='studio2 sheet build, then show it to the user')
    return read_json(Path(path) / SHEET / read_json(file)['rev'] / 'sheet.json')


def named_cuts(words, cut_ids):
    """The cuts the words name (their ids, as the sheet shows them)."""
    return [c for c in cut_ids if re.search(rf'(?<![A-Za-z0-9_]){re.escape(c)}(?![0-9])', words, flags=re.IGNORECASE)]


def approve(project, user_words, cuts=None, only=None, budget_usd=None):
    """Record the user's words on the latest sheet: every cut approved but those the words ask to change (cuts), which
    are reopened - or, when the words pay for some cuts only (`only`), those cuts approved and the rest left as they are
    (neither paid for nor sent back: their free clips stay). `budget_usd` is the total the words allow: the project's
    budget becomes it (route_policy.budget_usd, the cap the paid call's ledger holds), refused - nothing written - when
    what is already spent and the approved cuts' price pass it. Refused in a run without its user, on a sheet older than
    the cuts, or when the words name a cut that `cuts` leaves out.
    Why `only` (2026-10-27): the user paid for the first 12 s of a 15-cut reel within $3.40; leaving the other cuts out of
    `cuts` would have approved them all, and sending them back would have said the user asked them changed."""
    from studio import decisions
    from studio.generative.review import check_user_words
    from studio.project import project_dir
    words = check_user_words(user_words, 'sheet approve')
    path = project_dir(project)
    sheet = latest(path)
    body = decisions.envelope(path, 'cuts')['body']
    by = {c['cut_id']: c for c in body['cuts']}
    stale = [r['cut'] for r in sheet['cuts'] if r['cut'] not in by or request_of(path, body, by[r['cut']])['fingerprint'] != r['fingerprint']]
    if stale or [c['cut_id'] for c in body['cuts']] != [r['cut'] for r in sheet['cuts']]:
        raise StudioError('SHEET_STALE', f"the cuts changed after sheet {sheet['rev']} was made ({', '.join(stale) or 'the cuts themselves'})",
                          recovery='studio2 sheet build, and show the new sheet to the user')
    change = list(dict.fromkeys(cuts or []))
    unknown = [c for c in change if c not in by]
    if unknown:
        raise StudioError('INPUT_INVALID', f'sheet approve --cuts: no cut {unknown} on the sheet')
    named = named_cuts(words, [r['cut'] for r in sheet['cuts']])
    if set(named) - set(change):
        raise StudioError('INPUT_INVALID', f"the user's words name {', '.join(sorted(set(named) - set(change)))}: a cut they speak of is "
                          'reopened with --cuts, never approved by leaving it out - or ask the user which they meant')
    paid = list(dict.fromkeys(only or [])) if only is not None else None
    if paid is not None:
        unknown = [c for c in paid if c not in by]
        if unknown or not paid or set(paid) & set(change):
            raise StudioError('INPUT_INVALID', f"sheet approve --only: {'no cut ' + str(unknown) + ' on the sheet' if unknown else 'no cut named' if not paid else 'a cut both paid for and sent back: ' + ', '.join(sorted(set(paid) & set(change)))}")
    approved = [r for r in sheet['cuts'] if r['cut'] not in change and (paid is None or r['cut'] in paid)]
    left = [r['cut'] for r in sheet['cuts'] if r['cut'] not in change and r not in approved]
    project_data = None
    if budget_usd is not None:   # checked first: a refused approval writes nothing
        from studio.generative.fal_client import ledger_total
        from studio.project import load_project
        project_data = load_project(path)
        spent, asked = ledger_total(project_data['project_id']), round(sum(r['usd'] for r in approved), 2)
        if spent + asked > float(budget_usd) + 1e-9:
            raise StudioError('BUDGET_EXCEEDED', f"the approved cuts cost ${asked} and ${spent} is spent: more than the ${float(budget_usd):.2f} "
                              'the words allow', recovery='fewer paid cuts or shorter ones (sheet build again), or ask the user')
    file = path / SHEET / 'approvals.json'
    data = read_json(file) if file.is_file() else {'schema_version': 1, 'approvals': [], 'reopened': []}
    at = now()
    if project_data is not None:
        from studio.common import lock
        from studio.project import load_project
        from studio.project import validate_schema
        with lock(path / '.project.lock', blocking=False):
            stored = load_project(path)
            stored['route_policy'] = {**(stored.get('route_policy') or {}), 'budget_usd': float(budget_usd)}
            stored['revision'] += 1
            validate_schema(stored, 'project'); write_json(path / 'project.json', stored)
    data['approvals'] += [{'cut': r['cut'], 'fingerprint': r['fingerprint'], 'usd': r['usd'], 'endpoint': r['endpoint'], 'sheet': sheet['rev'],
                           'user_words': words, 'at': at} for r in approved]
    data['reopened'] += [{'cut': c, 'sheet': sheet['rev'], 'user_words': words, 'at': at} for c in change]
    write_json(file, data)
    return {'approved': [r['cut'] for r in approved], 'reopened': change, 'left': left, 'approved_usd': round(sum(r['usd'] for r in approved), 2),
            'artifacts': [str(file)],
            'next': [f"frames make --cut {c} --frame first --reason \"<what the user asked: {words}>\" (or cuts write if the plan changes), "
                     'then motion render, then sheet build and show it again' for c in change]
            + (['studio2 video make --cut <cut> for the approved cuts (paid, within the approved total)'] if approved else [])}


def approved(path, cut_id):
    """The user's approval of a cut's paid video as it would be sent now, or None (none, reopened since, or the
    request changed after the words)."""
    from studio import decisions
    file = Path(path) / SHEET / 'approvals.json'
    if not file.is_file():
        return None
    data = read_json(file)
    body = decisions.envelope(path, 'cuts')['body']
    cut = next((c for c in body['cuts'] if c['cut_id'] == cut_id), None)
    if cut is None:
        return None
    try:
        current = request_of(Path(path), body, cut)['fingerprint']
    except StudioError:
        return None
    rows = [a for a in data['approvals'] if a['cut'] == cut_id and a['fingerprint'] == current]
    reopened = [r for r in data['reopened'] if r['cut'] == cut_id]
    if not rows or (reopened and reopened[-1]['at'] > rows[-1]['at']):
        return None
    return rows[-1]


def register_commands(subparsers):
    parser = subparsers.add_parser('sheet', help="The approval sheet: the user's first look, and their words as the paid approval")
    commands = parser.add_subparsers(dest='sheet_command', required=True)
    p = commands.add_parser('build', help='one page for the reel: per cut its pictures, line, motion, preview, paid model and cost')
    p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: build(a.project))
    p = commands.add_parser('approve', help="the user's words on the latest sheet (never in a run without its user)")
    p.add_argument('--project', required=True); p.add_argument('--user-words', required=True)
    p.add_argument('--cuts', help='comma-separated: the cuts the words ask to change (reopened, not approved)')
    p.add_argument('--only', help='comma-separated: the only cuts the words pay for (the rest left as they are)')
    p.add_argument('--budget-usd', type=float, help='the total the words allow: the project budget the paid calls are held to')
    p.set_defaults(handler=lambda a: approve(a.project, a.user_words, [c for c in (a.cuts or '').split(',') if c],
                                             [c for c in a.only.split(',') if c] if a.only else None, a.budget_usd))
    return commands
