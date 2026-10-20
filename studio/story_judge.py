"""Blind pairwise judging of story directions against reference reels, before the user sees them (rule E1).

Each written direction is paired with a few reference narrations; the pair is shown as A and B in a drawn order, with
nothing saying which is ours; judges (the host's agents, never told which is ours) answer which one they would watch
to the end and why. A direction that loses most of its pairs is told STORY_LOSES with the judges' reasons, rewritten
once from them (story add, the same slot), and judged again; the sheet shows the user the directions that won, the
others folded. The user still picks any of them.

Why (2026-10-20): ten topics planned blind won 6 of 10 pairs against the study set, and every loss had one reason the
lint never saw ("a tour of the mechanism, nothing at stake"); a judge reads a story as a viewer would, the lint cannot.
The reference narrations are the user's local study set: read from a file outside the repository (REFS), never
copied into it.
"""
from __future__ import annotations

import hashlib
import random
from pathlib import Path

from .common import StudioError, now, read_json, write_json

REFS = Path.home() / '.config' / 'studio' / 'story_refs.json'   # {refs: [{ref, narration}]}: local, never committed
JUDGES = 3      # judges per pair: an odd number, so every pair has a majority; one judge's taste is noise
REF_PAIRS = 3   # references each direction meets: one pair says more about the reference drawn than about the story
JUDGE_DIR = 'judge'
KEY = 'judge_key.json'   # which side is ours: in the round folder, never where the judges read
QUESTION = ("Two short vertical explainer videos, told as their stories. Which one would you watch to the end, and why? "
            "Judge as a viewer scrolling past, not as an expert: the opening, whether you want to know what happens next, "
            "whether it ends on something. The why says what the one you did not pick lacks, never what the other one is "
            "about (it goes back to the writer of the one that lost, who must not copy the other). Answer as JSON: "
            "{\"pair\": \"<id>\", \"winner\": \"A\" | \"B\", \"why\": \"<one sentence>\"}.")


def story_text(record):
    """The story as a viewer would be told it, in running prose like the reference narrations (a field list would say
    which side is ours): the written direction's prose, else its beats' events in order."""
    if record.get('prose'):
        return record['prose']
    return ' '.join(b['what_happens'].rstrip('.') + '.' for b in record['body']['beats'])


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def _refs(path):
    file = Path(path) if path else REFS
    if not file.is_file():
        raise StudioError('INPUT_INVALID', f'no reference narrations at {file}',
                          recovery='write {refs: [{ref, narration}]} there (the local study set; never in the repository)')
    refs = [{'ref': r.get('ref') or f'ref{n + 1}', **r} for n, r in enumerate(read_json(file).get('refs', [])) if r.get('narration')]
    if len(refs) < REF_PAIRS:
        raise StudioError('INPUT_INVALID', f'{file} has {len(refs)} narrations, fewer than {REF_PAIRS}')
    return refs


def pack(folder, written, refs_path=None, pairs=REF_PAIRS, seed=None):
    """Write the blind pairs for a round's written directions: judge/pair_NN.md (A and B, nothing saying which is ours),
    judge/brief.md (the question, for each judge) and judge/key.json (which side is ours - never shown to a judge)."""
    refs = _refs(refs_path)
    rng = random.Random(f'{folder.parent.parent.parent.name}:{folder.name}:{seed}')   # the project's own draw: two projects' r01 meet different references
    out = folder / JUDGE_DIR
    key, n, files = {}, 0, []
    for d in written:
        text = story_text(d)
        for ref in rng.sample(refs, min(pairs, len(refs))):
            n += 1
            pair = f'pair_{n:02d}'
            ours = rng.choice('AB')
            files.append((pair, *((text, ref['narration']) if ours == 'A' else (ref['narration'], text))))
            key[pair] = {'slot': d['slot'], 'ref': ref['ref'], 'ours': ours, 'story_sha': _sha(text)}
    write_pairs(out, files, QUESTION)
    write_json(folder / KEY, {'schema_version': 1, 'packed_at': now(), 'judges': JUDGES, 'pairs': key})   # beside, not with the pairs
    return {'pairs': sorted(key), 'brief': str(out / 'brief.md'), 'judges': JUDGES,
            'next': f'give brief.md and the pair files to {JUDGES} judges who know nothing of the stories, gather their answers '
                    '[{judge, pair, winner, why}] in one file, then: studio story judged --verdicts <file>'}


def votes(key, verdicts):
    """{pair: [verdict]} - every packed pair answered once by each of JUDGES different judges, each naming A or B.
    verdicts: one list, or one list per judge."""
    flat = [v for item in verdicts for v in (item if isinstance(item, list) else [item])]
    if not all(isinstance(v, dict) for v in flat):
        raise StudioError('INPUT_INVALID', 'verdicts are [{judge, pair, winner, why}] (or one such list per judge)')
    by_pair = {}
    for v in flat:
        if v.get('pair') not in key or v.get('winner') not in ('A', 'B'):
            raise StudioError('INPUT_INVALID', f'a verdict names no packed pair or no winner A/B: {v}')
        by_pair.setdefault(v['pair'], []).append(v)
    short = sorted(p for p in key if len({str(v.get('judge')) for v in by_pair.get(p, [])}) != JUDGES or len(by_pair[p]) != JUDGES)
    if short:
        raise StudioError('INPUT_INVALID', f'pairs {short} do not have one verdict from each of {JUDGES} different judges')
    return by_pair


def write_pairs(out, pairs, question):
    """judge/pair_NN.md for [(pair, a_text, b_text)] and judge/brief.md with the question; older pairs are removed."""
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob('pair_*.md'):
        old.unlink()
    for pair, a, b in pairs:
        (out / f'{pair}.md').write_text(f'# {pair}\n\n## A\n\n{a}\n\n## B\n\n{b}\n', encoding='utf-8')
    (out / 'brief.md').write_text(f'{question}\n\nRead every pair_NN.md in this folder and answer once per pair, as a JSON list.\n',
                                  encoding='utf-8')


def judged(folder, written, verdicts):
    """Record the judges' verdicts on each written direction: {pairs, wins, won, why} - won when it takes most of its
    pairs, each pair settled by the majority of its judges; why, the reasons judges gave against it. The pairs must be
    of the stories as written now (story_sha), every pair answered by JUDGES judges."""
    key = read_json(folder / KEY)['pairs']
    texts = {d['slot']: _sha(story_text(d)) for d in written}
    stale = sorted({k['slot'] for k in key.values() if texts.get(k['slot']) != k['story_sha']})
    if stale:
        raise StudioError('DECISION_STALE', f'directions {stale} changed since the pairs were packed',
                          recovery='studio story judge-pack again, then judge the new pairs')
    by_pair = votes(key, verdicts)
    out = {}
    for pair, k in key.items():
        cast = by_pair[pair]
        ours = sum(v['winner'] == k['ours'] for v in cast)
        row = out.setdefault(k['slot'], {'pairs': 0, 'wins': 0, 'why': []})
        row['pairs'] += 1
        if ours * 2 > len(cast):
            row['wins'] += 1
        row['why'] += [v.get('why', '') for v in cast if v['winner'] != k['ours'] and v.get('why')]
    for slot, row in out.items():
        row['won'] = row['wins'] * 2 > row['pairs']
        row['at'] = now()
    return out


def loses_line(row):
    return (f"STORY_LOSES: it lost {row['pairs'] - row['wins']} of {row['pairs']} blind pairs against the reference reels - "
            f"rewrite it once from what the judges said ({'; '.join(row['why'][:3])}), or leave it folded on the sheet")
