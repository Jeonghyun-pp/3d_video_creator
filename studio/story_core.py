"""The story before the script: what bends where (pure; no files but the axes table).

A story is a list of beats, each with a dramatic function (hook, setback, turn, climax ...), how tense it is, and its
share of the reel. The script's lines then belong to beats, and the shots follow the beats. Why (2026-10-17): the rocket
reel was cut in the order of its sentences with nothing to say where it should hurt or peak, and came out even all the
way through; model-written stories are known to come out flat (early turns, few setbacks) unless the shape is asked for.
"""
from __future__ import annotations

from itertools import product
import random

from .common import REPO, read_json

AXES = REPO / 'library' / 'story_axes.json'
FUNCTIONS = ('hook', 'question', 'setup', 'explain', 'setback', 'turn', 'climax', 'payoff', 'close')
PEAKS = ('turn', 'climax')   # a story needs at least one place where things change

# Each rule: the value, and why it is that value. Errors stop approval; warnings are said on the sheet.
STORY_RULES = {
    'HOOK_MAX_S': (3.0, "the opening beat is over by about 3 s: platform data on short videos puts the core of the best "
                        "performers in their first 3 s, and the user's reference reels open on shots of 2.6 s at the median"),
    'SHARE_TOLERANCE': (0.05, 'beat shares are parts of one reel: they add up to 1 within this'),
    'CLIMAX_BAND': ((0.55, 0.9), "the reference reels put their consequences at 0.6-0.8 of the reel, and edited films "
                                 "peak late, before a short ending; a peak in the first half leaves the rest as an ending"),
    'TENSION_SPAN_MIN': (0.3, 'tension that never moves this much reads as one level all through: a flat story'),
    'EXPLAIN_RUN_MAX': (2, "explaining beats in a row past this read as a lecture; the reference reels repeat a role "
                           "between neighbouring shots 21 % of the time, the flat rocket reel 44 %"),
    'BEAT_WORDS_OVER': (2.0, "a beat whose lines take more than this many times its share of the spoken time crowds "
                             "the beats after it"),
}


def rule(name):
    return STORY_RULES[name][0]


def load_axes(path=AXES):
    return read_json(path)['axes']


def positions(beats):
    """Each beat's middle as a share of the reel (by length_share)."""
    out, at = [], 0.0
    total = sum(b['length_share'] for b in beats) or 1.0
    for b in beats:
        share = b['length_share'] / total
        out.append(at + share / 2)
        at += share
    return out


def lint_story(body, length_s=None, axes=None):
    """(errors, warnings) of a story body against STORY_RULES."""
    errors, warnings = [], []
    beats = body['beats']
    ids = [b['story_beat_id'] for b in beats]
    errors += [f'STORY_BEAT_DUPLICATE: beat {i} appears twice' for i in sorted(set(ids)) if ids.count(i) > 1]
    total = sum(b['length_share'] for b in beats)
    if abs(total - 1.0) > rule('SHARE_TOLERANCE'):
        errors.append(f'STORY_SHARES: beat shares add up to {total:.2f}, not 1 - they divide one reel')
    if beats[0]['function'] != 'hook':
        errors.append(f"STORY_HOOK_FIRST: the first beat is a {beats[0]['function']}, not a hook - open on a reason to keep "
                      'watching (the result, a question, a failure, a size, a belief, a clock), explain after')
    elif length_s and beats[0]['length_share'] * length_s > rule('HOOK_MAX_S') + 1e-6:
        errors.append(f"STORY_HOOK_LONG: the hook takes {beats[0]['length_share'] * length_s:.1f} s of {length_s} s - "
                      f"over {rule('HOOK_MAX_S')} s; cut it to its striking picture and move the rest into the next beat")
    if not any(b['function'] in PEAKS for b in beats):
        errors.append('STORY_NO_TURN: no beat is a turn or a climax - nothing changes, so nothing peaks; say where the '
                      'story turns (a setback overcome, the moment that must go right)')
    where = positions(beats)
    low, high = rule('CLIMAX_BAND')
    for b, at in zip(beats, where):
        if b['function'] == 'climax' and not low <= at <= high:
            warnings.append(f"STORY_CLIMAX_PLACE: the climax {b['story_beat_id']} sits at {at:.2f} of the reel, outside "
                            f"{low}-{high} - a peak this {'early leaves the rest as an ending' if at < low else 'late has no room to land'}")
    tension = [b['tension'] for b in beats]
    if max(tension) - min(tension) < rule('TENSION_SPAN_MIN'):
        warnings.append(f"STORY_TENSION_FLAT: tension runs {min(tension):.2f}-{max(tension):.2f} - under "
                        f"{rule('TENSION_SPAN_MIN')} apart, the story reads as one level; raise the turn and the climax, "
                        'let the setup breathe')
    run = 0
    for b in beats + [None]:
        if b is not None and b['function'] == 'explain':
            run += 1
            continue
        if run > rule('EXPLAIN_RUN_MAX'):
            warnings.append(f"STORY_EXPLAIN_RUN: {run} explaining beats in a row - break them with a question, a setback "
                            'or a comparison')
        run = 0
    if axes is not None and body.get('direction'):
        for axis, value in body['direction'].items():
            if axis not in axes or value not in axes[axis]['values']:
                errors.append(f'STORY_AXIS_UNKNOWN: direction {axis}={value} is not in library/story_axes.json')
    return errors, warnings


def script_lines(story, lines, spoken=None):
    """(errors, warnings) of script lines against the story: each line names a beat of it, in the story's order; every
    beat is said or left to the picture on purpose; no beat's words crowd far past its share. spoken(texts) -> seconds."""
    errors, warnings = [], []
    order = {b['story_beat_id']: n for n, b in enumerate(story['beats'])}
    seen = []
    for line in lines:
        beat = line.get('story_beat_id')
        if beat is None:
            errors.append(f"STORY_LINE_UNPLACED: line {line['line_id']} names no story beat (story_beat_id)")
        elif beat not in order:
            errors.append(f"STORY_BEAT_UNKNOWN: line {line['line_id']} names beat {beat}, which the story does not have")
        else:
            seen.append((line['line_id'], order[beat]))
    late = [(a, b) for (a, i), (b, j) in zip(seen, seen[1:]) if j < i]
    if late:
        errors.append(f'STORY_ORDER: line {late[0][1]} belongs to a beat the story tells before the beat of line {late[0][0]} - '
                      "the script follows the story's beats")
    said = {line.get('story_beat_id') for line in lines}
    warnings += [f"STORY_BEAT_UNSAID: beat {b['story_beat_id']} ({b['function']}) has no line - fine if the picture tells it; "
                 'its shot needs story_beat_ids then' for b in story['beats'] if b['story_beat_id'] not in said]
    if spoken is not None:
        texts = {b['story_beat_id']: [l['text'] for l in lines if l.get('story_beat_id') == b['story_beat_id']] for b in story['beats']}
        whole = spoken([l['text'] for l in lines]) or 1.0
        for b in story['beats']:
            share = spoken(texts[b['story_beat_id']]) / whole
            if b['length_share'] > 0 and share > rule('BEAT_WORDS_OVER') * b['length_share']:
                warnings.append(f"STORY_BEAT_CROWDED: beat {b['story_beat_id']} has {share:.0%} of the words for {b['length_share']:.0%} "
                                'of the reel - fewer words there, or a larger share')
    return errors, warnings


def beats_of(line_ids, line_beat):
    """The story beats a shot tells, in story order of first appearance."""
    out = []
    for i in line_ids:
        b = line_beat.get(i)
        if b and b not in out:
            out.append(b)
    return out


# --- directions: drawn by the code, written by the agent ---------------------------------------------------------

def combos(axes):
    names = sorted(axes)
    return [dict(zip(names, values)) for values in product(*(sorted(axes[n]['values']) for n in names))]


def distance(a, b):
    return sum(a.get(k) != b.get(k) for k in set(a) | set(b))


def preference_score(combo, preference):
    return sum((preference.get(axis) or {}).get(value, 0.0) for axis, value in combo.items())


REJECTED_SCORE = -0.5   # a value the user's picks pass over by at least half a pick (taste_picks.MIN_SCORE): the contrast
                        # slot moves away from their taste, not toward what they already said no to


def pick_directions(axes, n=3, preference=None, used=(), seed=0):
    """n directions as far apart on the axes as possible: [{slot, role, axes}]. Slot 1 (taste) is the combination the
    user's picks favour most; slot 2 (contrast) the furthest from it among values they have not passed over; the rest
    (explore) the furthest from all drawn. Every slot moves away from earlier rounds (used): no combination is drawn
    twice while the axes have others left, and within a slot the one on the most values no direction used before, then
    the furthest from every earlier draw, wins. The explore slots never read the taste: a taste must not narrow every video to one
    kind (studies of writing with a model: each piece better, all pieces alike). preference: {axis: {value: score}};
    used: axis dicts of earlier directions. Ties are broken by seed, so a fresh round draws afresh."""
    preference = preference or {}
    pool = combos(axes)
    rng = random.Random(seed)
    rng.shuffle(pool)
    used = [dict(u) for u in used]
    used_values = {(k, v) for u in used for k, v in u.items()}

    def fresh(c):   # values no earlier direction was drawn with
        return sum((k, v) not in used_values for k, v in c.items())

    def away(c):    # how far from the nearest earlier draw
        return min((distance(c, u) for u in used), default=len(c))

    def new(c):     # not drawn before (a repeat only once the axes run out)
        return c not in used

    def rejected(c):
        return sum((preference.get(a) or {}).get(v, 0.0) <= REJECTED_SCORE for a, v in c.items())

    first = max(pool, key=lambda c: (new(c), preference_score(c, preference), fresh(c), away(c)))
    chosen = [{'slot': 1, 'role': 'taste', 'axes': first}]
    if n >= 2:
        second = max((c for c in pool if c != first),
                     key=lambda c: (new(c), distance(c, first), -rejected(c), away(c)))
        chosen.append({'slot': 2, 'role': 'contrast', 'axes': second})
    while len(chosen) < n:
        drawn = [c['axes'] for c in chosen]
        nxt = max((c for c in pool if c not in drawn),
                  key=lambda c: (new(c), min(distance(c, d) for d in drawn), fresh(c), away(c)))
        chosen.append({'slot': len(chosen) + 1, 'role': 'explore', 'axes': nxt})
    return chosen


def stories_one_idea(directions):
    """STORIES_ONE_IDEA: two written directions with the same opening kind and the same run of beat functions are one
    story told twice. directions: [{slot, body}]."""
    out = []
    for i, a in enumerate(directions):
        for b in directions[i + 1:]:
            same_shape = [x['function'] for x in a['body']['beats']] == [x['function'] for x in b['body']['beats']]
            same_hook = (a['body'].get('direction') or {}).get('hook') == (b['body'].get('direction') or {}).get('hook')
            if same_shape and same_hook:
                out.append(f"STORIES_ONE_IDEA: directions {a['slot']} and {b['slot']} open the same way and run the same "
                           'beats - rewrite one along its drawn axes')
    return out
