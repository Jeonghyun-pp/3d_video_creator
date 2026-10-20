"""What the user picked, what they passed over and why, as taste the next plan reads (.studio/taste.json, picks).

Every choice the user makes between options - a story direction, a concept picture, a camera take, a redone shot -
is kept as the axis values of the option chosen and of those passed over, with their words. summary() turns the
picks into a few lines a prompt can carry ("User taste:"), assembled by code, not written by a model. Why (2026-10-17):
picks were stored as the chosen id and the user's words only, so nothing learned from them; a recorded recipe was
never read. A person's taste stays on this machine, never in the repo.

The taste is read by what should follow it - the picture variants' taste slot (story_core.pick_directions, never its
explore slot: following every pick would narrow every video to one kind) and, as a reference only, the story pitch
brief (story_pitch.brief); a story pick records how it opens and what it follows (story.taste_axes).
"""
from __future__ import annotations

from .common import StudioError, now, read_json, write_json
from .patterns import TASTE

KINDS = ('story', 'concept', 'take', 'redo')
SUMMARY_LEAN = 3       # how many leanings and passings-over the summary names
SUMMARY_WORDS = 3      # how many of the user's latest reasons it quotes
MIN_SCORE = 0.5        # a leaning weaker than half a pick is not said


def _data():
    return read_json(TASTE) if TASTE.is_file() else {'schema_version': 1, 'entries': []}


def picks(kind=None):
    rows = _data().get('picks', [])
    return [p for p in rows if kind is None or p['kind'] == kind]


def _axes(values, what):
    if not isinstance(values, dict) or not values or not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
        raise StudioError('INPUT_INVALID', f'{what} must be axis values {{axis: value}}')
    return dict(values)


def record(kind, chosen, rejected, user_words, project=None, ref=None):
    """One pick in the user's words: the chosen option's axis values and each passed-over option's."""
    from .generative.review import check_user_words
    if kind not in KINDS:
        raise StudioError('INPUT_INVALID', f'unknown pick kind {kind} (kinds: {list(KINDS)})')
    words = check_user_words(user_words, f'{kind} pick')
    row = {'at': now(), 'kind': kind, 'chosen': _axes(chosen, 'chosen'), 'rejected': [_axes(r, 'rejected') for r in rejected or []],
           'user_words': words, **({'project': str(project)} if project else {}), **({'ref': ref} if ref else {})}
    data = _data()
    data['picks'] = data.get('picks', []) + [row]
    TASTE.parent.mkdir(parents=True, exist_ok=True)
    write_json(TASTE, data)
    return row


def preference(kind=None, rows=None):
    """{axis: {value: score}}: a chosen value +1, each passed-over value -1 shared among the options passed over."""
    out = {}
    for p in (rows if rows is not None else picks(kind)):
        for axis, value in p['chosen'].items():
            out.setdefault(axis, {})[value] = out.get(axis, {}).get(value, 0.0) + 1.0
        share = 1.0 / len(p['rejected']) if p['rejected'] else 0.0
        for other in p['rejected']:
            for axis, value in other.items():
                if p['chosen'].get(axis) != value:
                    out.setdefault(axis, {})[value] = out.get(axis, {}).get(value, 0.0) - share
    return out


def summary(kinds=None):
    """{text, preference, picks}: at most five lines - how often the user picked, what they lean to, what they pass
    over, their latest reasons in their words, and their pattern taste entries (patterns.taste_entries)."""
    from .patterns import taste_entries
    rows = [p for p in picks() if kinds is None or p['kind'] in kinds]
    pref = preference(rows=rows)
    flat = sorted(((s, a, v) for a, values in pref.items() for v, s in values.items()), reverse=True)
    lean = [f'{a} {v}' for s, a, v in flat if s >= MIN_SCORE][:SUMMARY_LEAN]
    avoid = [f'{a} {v}' for s, a, v in reversed(flat) if s <= -MIN_SCORE][:SUMMARY_LEAN]
    lines = []
    if rows:
        counts = {k: sum(p['kind'] == k for p in rows) for k in KINDS}
        lines.append('Picked ' + ', '.join(f'{k} {n}' for k, n in counts.items() if n) + ' times so far.')
    if lean:
        lines.append('Leans to: ' + ', '.join(lean) + '.')
    if avoid:
        lines.append('Passes over: ' + ', '.join(avoid) + '.')
    said = [p['user_words'] for p in rows[-SUMMARY_WORDS:]]
    if said:
        lines.append('In their words: ' + ' / '.join(f'"{w}"' for w in said))
    entries = taste_entries()
    if entries:
        lines.append('Shot taste: ' + '; '.join(f"{', '.join(e['sections'])} {e['item']} {e['prefer'] if isinstance(e['prefer'], str) else '/'.join(e['prefer'])} ({e['why']})"
                                                for e in entries))
    return {'text': '\n'.join(lines), 'preference': pref, 'picks': len(rows)}


def prompt_block(kinds=None):
    """The 'User taste:' paragraph a brief carries, or '' when nothing is known yet."""
    text = summary(kinds)['text']
    return f'User taste (from their earlier picks; follow it unless this brief says to explore):\n{text}' if text else ''
