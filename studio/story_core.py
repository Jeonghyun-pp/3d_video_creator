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
CHAIN_IDEA_BEATS = 5   # two stories of one thread with the same run of links read as one only past this many beats:
                       # a short story has few link orders to choose from, and the same few say little
LINKS = ('so', 'but', 'and')   # how a beat follows the one before it: because of it, against it, or merely after it

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
    # the chain (chain_lint): measured 2026-10-19 on the study set, 10 reels of 61-153 s, beat by beat (so / but / and)
    'AND_RUN_MAX': (3, "beats joined by 'and' in a row past this are a list the viewer could reorder: the study set runs 1-4 "
                       "(median 2.5; 8 of 10 at most 3) - a journey names its next stop plainly, then a 'but' or a 'so' breaks "
                       "the run (2026-10-19, the bike reel read as 'a parade of parts')"),
    'AND_SHARE_MAX': (0.6, "links that are 'and' past this share: the study set 0.27-0.75 (median 0.43; 9 of 10 at most 0.58)"),
    'HOOK_ANSWER_MIN': (0.3, "the hook's question is first answered past this share of the reel: the study set at 0.05-0.69 "
                             '(median 0.45; 7 of 10 past 0.3) - answered early, the rest has less to wait for'),
    'BUT_EVERY_S': (30.0, "a 'but' beat (an objection, a complication, the viewer's own doubt) for every this many seconds: the "
                          'study set has 1-5 a reel, one every 19-75 s (median about 31 s)'),
    # the stake and the screen (stake_lint): measured 2026-10-20 on the study set's narrations, beat by beat
    'TOUR_RUN_MAX': (4, "beats in a row that neither raise nor answer a question past this are a tour: the study set runs 1-5 "
                        "(median 3.5; 8 of 10 at most 4) - the longest runs in the reels that follow a path part by part"),
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


def chain_unset(body):
    """A story written before the thread and the links existed (2026-10-19): no thread and no beat says how it follows.
    Its approval stands; story check and the builds say STORY_CHAIN_UNSET, a new approval needs them."""
    return not body.get('thread') and not any('link' in b or 'answers' in b or 'raises' in b for b in body['beats'])


def stake_unset(body):
    """A story written before the stake and the screen (2026-10-20): no stake and no beat says what is seen or comes back.
    Its approval stands; story check and the builds say STORY_STAKE_UNSET, a new approval needs them."""
    return not body.get('stake') and not any('on_screen' in b or 'returns_to' in b for b in body['beats'])


# Rule groups a story added over time, each with how to tell a story written before it. A settled body that predates a
# group keeps its approval and is told the group's _UNSET warning; a new or changed body answers to every group.
GROUPS = {'chain': chain_unset, 'stake': stake_unset}


def unset_groups(body):
    return {g for g, unset in GROUPS.items() if unset(body)}


SPOKEN_ONLY = ('?', '？')   # an on-screen event that ends as a question is a line heard, not a thing seen


def _seen(beat):
    """The beat's on-screen event, or None when it has none or the event is words only (a question asked)."""
    event = (beat.get('on_screen') or {}).get('event', '').strip()
    return event if event and not event.endswith(SPOKEN_ONLY) else None


def stake_lint(body, length_s=None, axes=None):
    """(errors, warnings) of what the viewer has at stake and sees: the hook an event on screen, a turn that happens on
    screen, no run of beats that neither ask nor answer (a tour), a stake that is said at the start and come back to at the
    end, and the last picture back at the hook's.
    Why (2026-10-20): ten topics planned blind against the study set's reels won 6 of 10; the four that lost were judged
    'a tour of the mechanism, nothing at stake' and opened quietly, where every winner opened on something happening."""
    errors, warnings = [], []
    beats = body['beats']
    hook, last = beats[0], beats[-1]
    if not _seen(hook):
        errors.append(f"HOOK_UNSEEN: the hook {hook['story_beat_id']} {'has no on_screen event' if not hook.get('on_screen') else 'is a question asked, not an event seen'}"
                      " - open on something the viewer sees happen (on_screen {event, kind}: a belief broken, a failure, the "
                      "result first, something odd); the question comes over it, in raises")
    turns = [b for b in beats[1:] if b.get('link') == 'but']
    if turns and not any(_seen(b) for b in turns):
        warnings.append(f"TURN_SPOKEN_ONLY: the 'but' beats ({', '.join(b['story_beat_id'] for b in turns)}) all turn in words - let at "
                        'least one go wrong on screen (on_screen {event, kind}): a doubt said is weaker than a failure seen')
    longest, run, start = 0, 0, None
    for b in beats[1:] + [None]:
        if b is not None and not b.get('raises') and not b.get('answers'):
            run += 1
            start = start or b['story_beat_id']
            continue
        if run > longest:
            longest, at = run, start
        run, start = 0, None
    tour = rule('TOUR_RUN_MAX')
    if tour is not None and longest > tour:
        warnings.append(f"STORY_TOUR: {longest} beats in a row from {at} neither ask (raises) nor answer (answers) - over {tour}, "
                        'a tour of the mechanism; let each step answer what the last asked or leave a new question')
    stake = body.get('stake')
    if not stake:
        warnings.append('STAKE_MISSING: the story says nothing is at stake (stake {who, what_is_at_risk, closed_by}) - whose '
                        'day, money, safety or time turns on this, so the viewer cares how it comes out')
    elif stake.get('closed_by') != last['story_beat_id']:
        warnings.append(f"STAKE_NOT_CLOSED: the last beat {last['story_beat_id']} does not come back to the stake "
                        f"({stake['what_is_at_risk']!r}; stake.closed_by: {last['story_beat_id']}) - end on what it meant for {stake['who']}")
    if len(beats) > 2 and last.get('returns_to') != hook['story_beat_id']:
        warnings.append(f"CLOSE_NOT_RETURNING: the last beat {last['story_beat_id']} does not come back to the hook's picture "
                        f"(returns_to: {hook['story_beat_id']}) - show the opening again, now understood")
    order = {b['story_beat_id']: n for n, b in enumerate(beats)}
    for n, b in enumerate(beats):
        back = b.get('returns_to')
        if back is not None and (back not in order or order[back] >= n):
            errors.append(f"STORY_RETURNS_UNKNOWN: beat {b['story_beat_id']} returns to {back} - not a beat told before it")
    if stake and stake.get('closed_by') is not None and stake['closed_by'] not in order:
        errors.append(f"STORY_STAKE_UNKNOWN: stake.closed_by {stake['closed_by']} is not a beat of the story")
    return errors, warnings


def chain_lint(body, length_s=None, axes=None):
    """(errors, warnings) of how the story's beats hold together: one thread the viewer keeps hold of, and every beat after
    the hook following the one before it - because of it (so) or against it (but), never a list of 'and's - and the
    questions the beats leave (raises) closed by later ones (answers); the hook's question carried late and answered, the ending back at the opening.
    Why (2026-10-19): the study set's reels are chains a viewer cannot reorder; ours read as a parade of parts. The link
    is the writer's claim; the order of questions and answers is what the code checks."""
    errors, warnings = [], []
    beats = body['beats']
    thread = body.get('thread')
    kinds = ((axes or {}).get('thread') or {}).get('values')
    if not thread:
        errors.append('STORY_THREAD_MISSING: the story names no thread (thread {kind, carrier, carrier_ids}) - the one thing '
                      'the viewer keeps hold of from beat to beat (what moves, the clock, the question, the change, the failure, '
                      'the scale); without it the beats are a list')
    else:
        if kinds is not None and thread['kind'] not in kinds:
            errors.append(f"STORY_THREAD_UNKNOWN: thread kind {thread['kind']} is not in library/story_axes.json thread "
                          f"({', '.join(sorted(kinds))}) - add it there with what orders it and why")
        drawn = (body.get('direction') or {}).get('thread')
        if drawn and drawn != thread['kind']:
            errors.append(f"STORY_THREAD_DIFFERS: the direction was drawn with thread {drawn}, the story holds {thread['kind']}")
    order = {b['story_beat_id']: n for n, b in enumerate(beats)}
    for n, b in enumerate(beats[1:], 1):
        link, answers = b.get('link'), b.get('answers') or []
        if link is None:   # a link says how it follows the last beat; answers, which questions it closes (often none yet)
            errors.append(f"STORY_LINK_MISSING: beat {b['story_beat_id']} does not say how it follows {beats[n - 1]['story_beat_id']} "
                          "(link: so - because of it, but - against it, and - merely after it)")
        late = [a for a in answers if a not in order or order[a] >= n]
        if late:
            errors.append(f"STORY_ANSWERS_UNKNOWN: beat {b['story_beat_id']} answers {late} - not beats told before it")
    links = [b.get('link') for b in beats[1:]]
    run, worst = 0, 0
    for link in links:
        run = run + 1 if link == 'and' else 0
        worst = max(worst, run)
    ands = links.count('and')
    if worst > rule('AND_RUN_MAX') or (links and ands / len(links) > rule('AND_SHARE_MAX') + 1e-9):
        errors.append(f"STORY_LIST: {ands} of {len(links)} beats follow by 'and' ({worst} in a row) - a list the viewer could "
                      "reorder; make each beat the consequence of the last (so) or its objection (but): what does the viewer "
                      'now wonder, and which beat answers it?')
    hook = beats[0]
    answering = [n for n, b in enumerate(beats) if n and hook['story_beat_id'] in (b.get('answers') or [])]
    if not hook.get('raises') or not answering:
        errors.append(f"HOOK_UNANSWERED: the hook {'raises no question (raises)' if not hook.get('raises') else 'asks ' + repr(hook['raises'])}"
                      f"{'' if answering else ' and no beat answers it (answers: [' + hook['story_beat_id'] + '])'} - the question "
                      'the reel opens is the one it closes')
    else:
        at = positions(beats)[answering[0]]
        if at < rule('HOOK_ANSWER_MIN'):
            warnings.append(f"HOOK_ANSWERED_EARLY: the hook's question is answered at {at:.2f} of the reel (beat "
                            f"{beats[answering[0]]['story_beat_id']}) - under {rule('HOOK_ANSWER_MIN')}, the rest has nothing to "
                            'wait for; answer a smaller question there and keep the hook\'s for the climax')
    answered = {a for b in beats for a in (b.get('answers') or [])}
    for b in beats[1:-1]:
        if b.get('raises') and b['story_beat_id'] not in answered:
            warnings.append(f"QUESTION_DANGLING: beat {b['story_beat_id']} raises {b['raises']!r} and no later beat answers it")
    if len(beats) > 2 and hook['story_beat_id'] not in (beats[-1].get('answers') or []):
        warnings.append(f"STORY_CLOSE_OPEN: the last beat {beats[-1]['story_beat_id']} does not come back to the hook (answers: "
                        f"[{hook['story_beat_id']}]) - end where the reel began, the question answered whole")
    if length_s:
        need = int(length_s // rule('BUT_EVERY_S'))
        if links.count('but') < need:
            warnings.append(f"STORY_NO_BUT: {links.count('but')} 'but' beats in {length_s} s - at least {need} (one every "
                            f"{rule('BUT_EVERY_S'):.0f} s): the viewer's doubt, a complication, what goes wrong")
    return errors, warnings


GROUP_LINTS = {'chain': chain_lint, 'stake': stake_lint}


def lint_story(body, length_s=None, axes=None, off=frozenset()):
    """(errors, warnings) of a story body against STORY_RULES and every rule group (GROUP_LINTS) but those in off - the
    groups a settled story predates (unset_groups; the caller tells it STORY_<GROUP>_UNSET)."""
    errors, warnings = [], []
    for group, check in GROUP_LINTS.items():
        if group not in off:
            e, w = check(body, length_s, axes)
            errors += e; warnings += w
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
    for b in story['beats']:
        if b['story_beat_id'] in said:
            continue
        if b.get('link') == 'but':   # the turn against what came before is what pulls the viewer on: unheard, it is easily lost
            warnings.append(f"STORY_LINK_UNSAID: beat {b['story_beat_id']} turns against the one before (but) and has no line - "
                            "say the doubt or the complication, or give it a shot of its own that shows it (story_beat_ids)")
        else:
            warnings.append(f"STORY_BEAT_UNSAID: beat {b['story_beat_id']} ({b['function']}) has no line - fine if the picture "
                            'tells it; its shot needs story_beat_ids then')
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

def drawable(axis):
    """An axis's values a draw may use: a retired value (library/story_axes.json 'retired') still reads, never draws."""
    return sorted(v for v, spec in axis['values'].items() if not (isinstance(spec, dict) and spec.get('retired')))


def combos(axes):
    names = sorted(axes)
    return [dict(zip(names, values)) for values in product(*(drawable(axes[n]) for n in names))]


def distance(a, b):
    return sum(a.get(k) != b.get(k) for k in set(a) | set(b))


def preference_score(combo, preference):
    return sum((preference.get(axis) or {}).get(value, 0.0) for axis, value in combo.items())


REJECTED_SCORE = -0.5   # a value the user's picks pass over by at least half a pick (taste_picks.MIN_SCORE): the contrast
                        # slot moves away from their taste, not toward what they already said no to


def pick_directions(axes, n=3, preference=None, used=(), seed=0, fixed=None):
    """n directions as far apart on the axes as possible: [{slot, role, axes}]. Slot 1 (taste) is the combination the
    user's picks favour most; slot 2 (contrast) the furthest from it among values they have not passed over; the rest
    (explore) the furthest from all drawn. Every slot moves away from earlier rounds (used): no combination is drawn
    twice while the axes have others left, and within a slot the one on the most values no direction used before, then
    the furthest from every earlier draw, wins. The explore slots never read the taste: a taste must not narrow every video to one
    kind (studies of writing with a model: each piece better, all pieces alike). preference: {axis: {value: score}};
    used: axis dicts of earlier directions. Ties are broken by seed, so a fresh round draws afresh. fixed: {axis: value}
    the user's words settle (2026-10-18: "레버 클로즈업" redone as wide, medium and medium options) - every direction holds
    them and differs on the other axes; a list of values holds one of them ("더 가깝게": medium or close, not wide); at most
    as many directions as those leave."""
    preference = preference or {}
    fixed = {k: set(v) if isinstance(v, (list, tuple, set)) else {v} for k, v in (fixed or {}).items()}
    pool = [c for c in combos(axes) if all(c.get(k) in v for k, v in fixed.items())]
    n = min(n, len(pool))
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
            # the same thread joined the same way (so, but, and in one order) is one chain told twice, whatever the functions
            same_chain = (len(a['body']['beats']) >= CHAIN_IDEA_BEATS and bool(a['body'].get('thread')) and (a['body'].get('thread') or {}).get('kind') == (b['body'].get('thread') or {}).get('kind')
                          and [x.get('link') for x in a['body']['beats']] == [x.get('link') for x in b['body']['beats']])
            if (same_shape and same_hook) or same_chain:
                out.append(f"STORIES_ONE_IDEA: directions {a['slot']} and {b['slot']} "
                           f"{'hold one thread joined the same way' if same_chain and not (same_shape and same_hook) else 'open the same way and run the same beats'}"
                           ' - rewrite one along its drawn axes')
    return out
