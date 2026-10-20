"""The decision ladder: what the video says and shows is settled with the user, layer by layer, before expensive work.

  brief -> facts -> story -> script -> world -> shotlist -> look -> cut   (per shot: fill brief, studio/fill.py)

Every layer works the same way (the fill brief's pattern, generalised): the agent proposes a body, a sheet is written,
the user's words come back verbatim (`decide revise --user-words ... --ops ...`), and `decide approve --sheet rNN`
binds the user's approval to that body and to the approved bodies of its parents. Staleness is computed, never
stored: when a parent changes, every layer below it is stale until approved again. An approved layer is projected into
the contracts the tools already read (project.json brief, sources.json, the shots' narration and timeline, look
presets); a hand edit of those afterwards is drift.

A project uses the ladder once its first decision is proposed (decisions/ladder.json); projects without it keep the
older flow. Gates (require) refuse builds, renders, paid generation, final voice, candidates and delivery whose layers
are not approved and fresh.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from .common import CODE, check_id, REPO, StudioError, json_arg, lock, now, read_json, stable_hash, write_json
from .project import load_project, load_shot, project_dir, shot_path, validate_schema, validate_shot

LAYERS = {   # layer -> the layers it is bound to; order is the order of the conversation
    'brief': (),
    'facts': ('brief',),
    # where the story turns and peaks, before a line is written (studio/story_core.py, 2026-10-17): a ladder adopted
    # before it keeps its shape (parents_of), so older projects go on without one
    'story': ('facts',),
    'script': ('facts', 'story'),
    # the world, settled from a picture of every beat before shots are cut (studio/world.py, 2026-10-10)
    'world': ('script',),
    'shotlist': ('script', 'world'),
    'look': ('shotlist',),
    # the rough cut seen whole before money is spent on finals and generation (the third human checkpoint, 2026-10-09)
    'cut': ('look',),
}
GATES = {    # operation -> layers that must be approved and fresh
    'build': ('world', 'shotlist'),
    'render': ('world', 'shotlist'),
    'render_look': ('world', 'shotlist', 'look'),
    'render_final': ('world', 'shotlist', 'look', 'cut'),
    'generate': ('world', 'shotlist', 'look'),             # a generative shot's take is what its rough cut shows
    'generate_hybrid': ('world', 'shotlist', 'look', 'cut'),   # a restyle of a Blender pass the user saw in the rough cut
    'audio_final': ('script',),
    'candidate': ('facts', 'script'),
    'deliver': ('facts', 'script'),
}
CHARS_PER_S = 7.0      # provisional: Korean scratch narration speed, for the script length check (a warning)
SPEECH_SHARE = 0.9     # the share of a reel the narration fills. Measured on the user's reference (2026-10-12, its 10 reels
                       # transcribed locally - studio/speech_pace.py): words fill 91 % (87-98 %), at 7.4 characters a second,
                       # with almost no pause of 0.5 s. A first guess of 0.72 ("its music hides the pauses") was wrong. The user's
                       # "훅훅 넘어가는 느낌" came from short lines (17.6 characters against its 23), not from missing silence.
                       # Per project: audio.speech_share; with a learned reference, its median (pattern pacing learn)
SHOW_MIN_S = 0.0       # an ordinary shot needs no silence: the reference talks across its cuts
REVEAL_SHOW_S = 1.5    # a shot that opens something (direction.reveal) leaves this long with nothing said: the inside
                       # needs looking at (the user's B' choice: "꼭 필요한 곳에만 1~2초 쉼")
LINE_CHARS_MIN = 19    # a script whose median line is shorter than this splits one thought into pieces (SCRIPT_CHOPPY);
                       # the reference's sentences: median 23 characters, its shortest reel median 19. A learned
                       # reference gives its own 10th percentile (pattern pacing learn)


def spoken_seconds(texts, chars_per_s=CHARS_PER_S):
    """About how long the lines take to say (characters without spaces at the scratch voice's speed)."""
    return sum(len(t.replace(' ', '')) for t in texts) / chars_per_s


def line_spans(shots):
    """{line_id: [shot index, ...]}: the shots each line is in, in cut order. A line said across a cut is in every shot it
    runs through: it starts in the first (its last line) and continues at the start of the next ones (their first)."""
    spans = {}
    for n, s in enumerate(shots):
        for i in s['line_ids']:
            spans.setdefault(i, []).append(n)
    return spans


def span_lines(shots):
    """Errors in how lines run across cuts (line_spans): only into the shots right after, a continuing line first in each
    of them and starting at 0, and the last line of the shot it starts in. Why (2026-10-19): one line per shot made every
    shot as long as its sentence (the bike reel's 5.3 s median); the reference talks across its cuts."""
    out = []
    for i, where in line_spans(shots).items():
        if len(where) == 1:
            continue
        ids = [shots[n]['shot_id'] for n in where]
        if where != list(range(where[0], where[0] + len(where))):
            out.append(f"line {i} is in {', '.join(ids)}, not one after another - a line runs across a cut only into the next shot")
            continue
        if shots[where[0]]['line_ids'][-1] != i:
            out.append(f"line {i} runs on from {ids[0]} but is not its last line - the line said across a cut ends the shot it starts in")
        for k, n in enumerate(where[1:], 1):
            at = shots[n].get('line_at_s')
            if shots[n]['line_ids'][0] != i or (at and at[0] != 0):
                out.append(f"line {i} continues in {shots[n]['shot_id']}: it is the shot's first line, at 0 (it was already being said)")
            elif k < len(where) - 1 and len(shots[n]['line_ids']) > 1:   # it is still being said at this shot's end
                out.append(f"line {i} runs on through {shots[n]['shot_id']} into the next shot: no other line fits in {shots[n]['shot_id']}")
            elif len(shots[n]['line_ids']) > 1 and not at:   # its own lines start after the run-on ends: say where
                out.append(f"{shots[n]['shot_id']} goes on from line {i} to lines of its own: give line_at_s, each after {i} ends")
    return out


def own_lines(shots):
    """{shot_id: [(line_id, at_s or None)]}: the lines each shot starts saying (a line continuing from the shot before is
    not its own - its voice is the shot's where it started)."""
    first = {i: where[0] for i, where in line_spans(shots).items()}
    out = {}
    for n, s in enumerate(shots):
        at = s.get('line_at_s') or [None] * len(s['line_ids'])
        out[s['shot_id']] = [(i, a) for i, a in zip(s['line_ids'], at) if first[i] == n]
    return out


def continues_into(shots):
    """{shot_id: [the shots its last line runs on into]} for the shots whose voice crosses a cut."""
    spans = line_spans(shots)
    out = {}
    for n, s in enumerate(shots):
        if s['line_ids'] and len(spans[s['line_ids'][-1]]) > 1 and spans[s['line_ids'][-1]][0] == n:
            out[s['shot_id']] = [shots[k]['shot_id'] for k in spans[s['line_ids'][-1]][1:]]
    return out


def speech_occupancy(shots, texts, chars_per_s=CHARS_PER_S):
    """{shot_id: seconds of it the voice speaks over}: each line from where it starts (its line_at_s, else after the
    shot's lines before it) for as long as it takes to say, through the shots it runs into."""
    starts, t = [], 0.0
    for s in shots:
        starts.append(t); t += s['duration_s']
    out = {s['shot_id']: 0.0 for s in shots}
    spans, own = line_spans(shots), own_lines(shots)
    for n, s in enumerate(shots):
        cursor = 0.0
        for i, at in own[s['shot_id']]:
            last = spans[i][-1]   # a line is said no further than the shot it ends in (the overrun is the shot's own floor)
            begin = starts[n] + (at if at is not None else cursor)
            end = min(begin + spoken_seconds([texts.get(i, '')], chars_per_s), starts[last] + shots[last]['duration_s'])
            cursor = end - starts[n]
            for k, other in enumerate(shots):
                out[other['shot_id']] += max(0.0, min(end, starts[k] + other['duration_s']) - max(begin, starts[k]))
    return {k: round(v, 3) for k, v in out.items()}


def _reference_speech():
    from .camera_taste import PROFILE
    return (read_json(PROFILE).get('speech') or {}) if PROFILE.is_file() else {}


def speech_share(path):
    """The project's own share, else the learned reference's median, else SPEECH_SHARE."""
    own = (load_project(path).get('audio') or {}).get('speech_share')
    learned = (_reference_speech().get('speech_share') or {}).get('p50')
    return float(own or learned or SPEECH_SHARE)


def line_chars_min():
    return float((_reference_speech().get('chars_per_sentence') or {}).get('p10') or LINE_CHARS_MIN)


def choppy_lines(texts):
    """SCRIPT_CHOPPY: the script says one thought in pieces - its median line is shorter than the reference's short
    sentences, and every piece gets its own picture (2026-10-12: 17.6 characters a line against the reference's 23)."""
    import statistics
    sizes = [len(t.replace(' ', '')) for t in texts if t.strip()]
    if len(sizes) < 3:
        return []
    median, floor = statistics.median(sizes), line_chars_min()
    return [f"SCRIPT_CHOPPY: lines are {median:.0f} characters at the median, the reference's sentences seldom under {floor:.0f} - "
            'say one whole thought per line (join a line that ends in a comma to the next, unfold a bare phrase)'] if median < floor else []


def speech_band(budget_s, chars_per_s=CHARS_PER_S):
    """What a script can be to keep SCRIPT_DENSE and SCRIPT_CHOPPY both quiet, in words: whole lines of at least the
    reference's short sentence, and no more of them than the words budget holds. Why (2026-10-20): planners met the two
    on different sheets and traded one for the other - shorter lines to fit the length, then choppy."""
    floor = line_chars_min()
    total = (1 + LENGTH_TOLERANCE) * budget_s * chars_per_s
    return (f'both hold with lines of {floor:.0f}+ characters at the median and about {total:.0f} characters in all: '
            f'at most {max(1, int(total // floor))} lines; a picture with no line carries the rest')


def opens_inside(shot):
    """The shot opens its subject at some moment: a show row that opens (show_core), or the older direction.reveal."""
    from .blender_ops.show_core import opens, show_of
    return ((shot.get('direction') or {}).get('reveal') or 'none') != 'none' or any(opens(r['how']) for r in show_of(shot))


def pacing_lines(shots, lines, chars_per_s=CHARS_PER_S):
    """PACING_DENSE: shots of a shot list whose words leave too little time to look (SHOW_MIN_S; REVEAL_SHOW_S where the
    shot opens something). lines: {line_id: text}."""
    out = []
    occupied = speech_occupancy(shots, lines, chars_per_s)   # a line said across a cut speaks over each shot it runs through
    for s in shots:
        spoken = occupied[s['shot_id']]
        need = REVEAL_SHOW_S if opens_inside(s) else SHOW_MIN_S
        if need > 0 and s['duration_s'] - spoken < need:
            out.append(f"PACING_DENSE: {s['shot_id']} is {s['duration_s']:.1f} s and its words take about {spoken:.1f} s - "
                       f"{s['duration_s'] - spoken:.1f} s to look, under {need} s; lengthen it, give it fewer words, or let its last "
                       "line run on into the next shot (that line first in the next shot's line_ids: its seconds are then shared)")
    return out
LENGTH_TOLERANCE = 0.15
ROUNDS_BEFORE_ASKING = 3


def _root(path):
    return project_dir(path) / 'decisions'


LADDER_REQUIRES = ('concept_frames', 'screen_patterns', 'video_review')   # what a ladder adopted now also asks for (concept.py,
# patterns.py, video_review.py: a generated clip is looked at against the shot's lines before it is cut in, 2026-10-16);
# recorded in ladder.json


def adopted(path):
    return (_root(path) / 'ladder.json').is_file()


def ladder_requires(path, name):
    """Does this project's ladder ask for `name`? A ladder keeps the rules it was adopted with: one adopted before a
    requirement existed does not gain it mid-project (2026-10-09: concept frames and the cut layer arrived)."""
    if not adopted(path):
        return False
    return name in read_json(_root(path) / 'ladder.json').get('requires', [])


def gated(path, layer):
    """A layer gates operations if the ladder was adopted with it, or the project has started it since."""
    layers = read_json(_root(path) / 'ladder.json').get('layers', list(LAYERS))
    return layer in layers or envelope(path, layer) is not None


def ladder_evidence(path):
    """Signs a project used the ladder, for when ladder.json is missing: its project.json record or a layer file."""
    if 'decision_ladder' in load_project(path):
        return f"project.json decision_ladder adopted_at {load_project(path)['decision_ladder']['adopted_at']}"
    files = sorted(p.name for p in _root(path).glob('*.json') if p.stem in LAYERS and not _off_ladder(read_json(p))) \
        if _root(path).is_dir() else []
    return f"layer files {files}" if files else None


def _off_ladder(env):
    """A layer record written off the ladder (write: decided by the user's words or a delegated agent, or its draft)."""
    return bool((env or {}).get('off_ladder'))


def parents_of(path, layer):
    """The layers this one is bound to in this project: a layer the project's ladder was adopted without (and has not
    started) binds nothing - a ladder keeps the shape it was adopted with."""
    return [p for p in LAYERS[layer] if not adopted(path) or gated(path, p)]


def envelope(path, layer):
    file = _root(path) / f'{_layer(layer)}.json'
    return read_json(file) if file.is_file() else None


def _layer(layer):
    if layer not in LAYERS:
        raise StudioError('INPUT_INVALID', f'Unknown decision layer {layer!r} (layers: {list(LAYERS)})')
    return layer


def body_sha256(body):
    return stable_hash(body)


def validate_body(layer, body):
    schema = read_json(CODE / 'schemas' / 'studio-v1' / 'decision.schema.json')
    errors = sorted(Draft202012Validator({'$ref': f'#/$defs/{layer}', '$defs': schema['$defs']}).iter_errors(body), key=lambda e: list(e.path))
    if errors:
        raise StudioError('INPUT_INVALID', f'{layer}: ' + '; '.join(f'{list(e.path)}: {e.message}' for e in errors[:6]))


def state(path, layer):
    """{'state': missing|proposed|approved|stale, 'reason'}; stale names what changed."""
    env = envelope(path, layer)
    if env is None:
        return {'state': 'missing', 'reason': f'{layer} not proposed yet'}
    if env['status'] == 'decided':   # off the ladder: settled while its parents are, and say what they said then
        if adopted(path):   # on the ladder only the user's approval settles a layer (review 2026-10-18)
            return {'state': 'proposed', 'reason': f'{layer} was decided off the ladder - propose it and get the user\'s approval'}
        if env['decided']['body_sha256'] != body_sha256(env['body']):
            return {'state': 'stale', 'reason': f'{layer} was edited after it was decided'}
        for parent, sha in (env['decided'].get('parents') or {}).items():
            now_env = envelope(path, parent)
            if now_env is None or body_sha256(now_env['body']) != sha:
                return {'state': 'stale', 'reason': f'{parent} changed after {layer} was decided'}
            parent_state = state(path, parent)
            if parent_state['state'] not in ('decided', 'approved'):
                return {'state': 'stale', 'reason': f"{parent} is {parent_state['state']}: {parent_state['reason']}"}
        return {'state': 'decided', 'reason': f"decided by the {env['decided']['by']} off the ladder"}
    if env['status'] != 'approved':
        if _off_ladder(env):
            return {'state': 'proposed', 'reason': f"{layer} has lint errors to fix before it is decided (decide write again)"}
        return {'state': 'proposed', 'reason': f"{layer} waits for the user's approval (sheet {env['sheet']['rev']})"}
    if env['approval']['body_sha256'] != body_sha256(env['body']):
        return {'state': 'stale', 'reason': f'{layer} was edited after approval'}
    for parent in parents_of(path, layer):
        parent_state = state(path, parent)
        if parent_state['state'] != 'approved':
            return {'state': 'stale', 'reason': f"{parent} is {parent_state['state']}: {parent_state['reason']}"}
        if env['approval']['parents'].get(parent) != body_sha256(envelope(path, parent)['body']):
            return {'state': 'stale', 'reason': f'{parent} changed after {layer} was approved'}
    return {'state': 'approved', 'reason': ''}


# --- lint (errors block approval; warnings go on the sheet) -------------------------------------------------------

STAGE_NAME = r'\d+\s*단(?!위)'   # a rocket's 1단 / 2단 is the stage's name, not an amount said aloud (2026-10-13)


def _digits(text):
    """The text says a number - a claim to source. A numeral that names a part (a rocket's 1단) says no amount."""
    import re
    return any(ch.isdigit() for ch in re.sub(STAGE_NAME, '', text))


def settled_unset(path, body):
    """The rule groups (story_core.GROUPS: the chain 2026-10-19, the stake 2026-10-20) the story body predates, when it
    is the body settled before: the user's pick (story.json) or the body an earlier approval or decision bound - adopting
    the ladder or approving it again after a parent changed asks nothing new of it (STORY_<GROUP>_UNSET warns). A changed
    or new body is a re-plan: every group."""
    from .story import body_of, load as load_story
    from .story_core import unset_groups
    groups = unset_groups(body)
    if not groups:
        return set()
    sha = body_sha256(body)
    picked = body_of(load_story(path))
    env = envelope(path, 'story') or {}
    bound = [(env.get(k) or {}).get('body_sha256') for k in ('approval', 'decided')]
    return groups if (picked is not None and body_sha256(picked) == sha) or sha in bound else set()


def lint(path, layer, body):
    errors, warnings = [], []
    parent = {p: (envelope(path, p) or {}).get('body') for p in parents_of(path, layer)}
    if layer == 'facts':
        sources = {s['source_id'] for s in body['sources']}
        brief = (envelope(path, 'brief') or {}).get('body') or {}
        for claim in body['claims']:
            missing = [s for s in claim['source_ids'] if s not in sources]
            if not claim['source_ids'] or missing:
                errors.append(f"claim {claim['claim_id']}: sources {missing or 'none'} not listed")
            elif brief.get('subject_mode') == 'specific_real' and _digits(claim['text']) and len(set(claim['source_ids'])) < 2:
                errors.append(f"claim {claim['claim_id']}: a number about a real subject needs 2 independent sources")
        errors += about_lines(path, body)
        if body['claims'] and not any('about' in c for c in body['claims']):
            warnings.append('ABOUT_UNSET: no claim says what it is about, so nothing checks that what the lines explain is seen - '
                            'give each claim about (ids of world elements, subjects, subject/part, subject/feature; [] for nothing)')
    if layer == 'story':
        from .story_core import lint_story, load_axes
        brief = (envelope(path, 'brief') or {}).get('body') or {}
        kept = settled_unset(path, body)
        e, w = lint_story(body, brief.get('length_s'), load_axes(), off=kept)
        from .story import unset_lines
        errors += e; warnings += w + unset_lines(kept)
    if layer == 'script' and parent.get('story'):   # the lines tell the story's beats, in its order
        from .story_core import script_lines as story_script_lines
        e, w = story_script_lines(parent['story'], body['lines'], lambda texts: spoken_seconds(texts, body.get('chars_per_s', CHARS_PER_S)))
        errors += e; warnings += w
    if layer == 'script':
        claims = {c['claim_id'] for c in ((parent.get('facts') or {}).get('claims') or [])}
        for line in body['lines']:
            if line.get('claim_ids') and line.get('illustrative'):
                errors.append(f"line {line['line_id']}: either claims or illustrative, not both")
            elif line.get('claim_ids'):
                errors += [f"line {line['line_id']}: claim {c} is not in the approved facts" for c in line['claim_ids'] if c not in claims]
            elif line.get('illustrative'):
                if _digits(line['text']):
                    errors.append(f"line {line['line_id']}: a number said aloud is a claim - source it in facts or drop it")
            else:
                errors.append(f"line {line['line_id']}: link it to claims or mark it illustrative")
        brief = (envelope(path, 'brief') or {}).get('body') or {}
        if brief.get('length_s'):
            seconds = spoken_seconds([l['text'] for l in body['lines']], body.get('chars_per_s', CHARS_PER_S))
            share = speech_share(path)
            budget = share * brief['length_s']   # the rest of the reel is picture alone, not more words
            band = speech_band(budget, body.get('chars_per_s', CHARS_PER_S))
            if seconds > (1 + LENGTH_TOLERANCE) * budget:
                warnings.append(f"SCRIPT_DENSE: spoken about {seconds:.0f} s, the brief's {brief['length_s']} s leaves {budget:.0f} s for words "
                                f"(speech share {share}) - fewer lines, not shorter ones ({band})")
            elif seconds < (1 - LENGTH_TOLERANCE) * budget:
                warnings.append(f"spoken about {seconds:.0f} s against the {budget:.0f} s the brief's {brief['length_s']} s leaves for words")
            warnings += [f'{w} ({band})' for w in choppy_lines([l['text'] for l in body['lines']])]
        else:
            warnings += choppy_lines([l['text'] for l in body['lines']])
    if layer == 'shotlist':
        lines = [l['line_id'] for l in ((parent.get('script') or {}).get('lines') or [])]
        used = [i for s in body['shots'] for i in s['line_ids']]
        errors += [f'script line {i} is in no shot' for i in lines if i not in used]
        errors += span_lines(body['shots'])   # a line runs across a cut only into the shots right after it
        errors += [f'unknown script line {i}' for i in sorted(set(used) - set(lines))]
        ids = [s['shot_id'] for s in body['shots']]
        errors += [f'duplicate shot {i}' for i in sorted(set(ids)) if ids.count(i) > 1]
        brief = (envelope(path, 'brief') or {}).get('body') or {}
        total = sum(s['duration_s'] for s in body['shots'])
        if brief.get('length_s') and abs(total - brief['length_s']) > LENGTH_TOLERANCE * brief['length_s']:
            warnings.append(f"shots add up to {total:.1f} s against the brief's {brief['length_s']} s")
        if ladder_requires(path, 'screen_patterns'):   # advice, never an error: what each shot takes from its role's patterns
            from .patterns import CATEGORICAL, NUMERIC
            from .reel_flow import DEPART_ITEMS   # a flow finding a shot departs from on purpose (reel_flow.DEPARTS)
            known = {*NUMERIC, *CATEGORICAL, *DEPART_ITEMS}
            for s in body['shots']:
                if s.get('role') and not s.get('pattern'):
                    warnings.append(f"PATTERN_UNREAD: {s['shot_id']} ({s['role']}) records nothing from studio pattern show --role {s['role']}")
                names = [r['item'] for k in ('carried', 'departs') for r in (s.get('pattern') or {}).get(k, [])]
                warnings += [f"PATTERN_ITEM_UNKNOWN: {s['shot_id']} names {n} (items: references/screen_grammar.md)" for n in names if n not in known]
            # one record pasted on every shot is no reading (first comparison run, 2026-10-10: five shots, one record)
            from .patterns import repeated_records
            seen = set()
            for ids in repeated_records((s['shot_id'], (s.get('pattern') or {}).get('carried') or []) for s in body['shots']).values():
                if tuple(ids) not in seen:
                    seen.add(tuple(ids))
                    warnings.append(f"PATTERN_RECORD_REPEATED: {', '.join(ids)} carry the same record - read each shot's own role and "
                                    f"examples and write what that shot takes")
    if layer == 'shotlist':
        warnings += reveal_lines(body)
        script_lines = {l['line_id']: l['text'] for l in ((parent.get('script') or {}).get('lines') or [])}
        warnings += pacing_lines(body['shots'], script_lines)
        for s in body['shots']:   # where each line starts in its shot: in order, inside the shot, the first at the start
            at = s.get('line_at_s')
            if at is None or (not at and not s.get('line_ids')):   # none given, or a shot with no lines: nothing to place
                continue
            if len(at) != len(s['line_ids']) or at != sorted(at) or at[0] < 0 or at[-1] >= s['duration_s']:
                errors.append(f"{s['shot_id']}: line_at_s {at} must give each of its {len(s['line_ids'])} lines a start, in order, inside its "
                              f"{s['duration_s']} s")
    if layer == 'world':
        from . import world as world_mod
        script_body = parent.get('script') or (envelope(path, 'script') or {}).get('body')
        e, w = world_mod.lint(path, body, script_body)
        errors += e; warnings += w
        from .beat_pictures import lint as beat_lint   # what the lines explain is seen in their beat's pictures
        errors += beat_lint(path, body, script_body, (envelope(path, 'facts') or {}).get('body'))
    if layer == 'shotlist' and parent.get('world'):   # every shot shows world elements; every element is shown somewhere
        known = {e['id'] for e in parent['world']['elements']}
        beats = {b['beat_id'] for b in parent['world']['beats']}
        for s in body['shots']:
            errors += [f"{s['shot_id']}: unknown world element {e}" for e in s.get('elements', []) if e not in known]
            errors += [f"{s['shot_id']}: unknown beat {b}" for b in s.get('beat_ids', []) if b not in beats]
            if not s.get('elements'):
                warnings.append(f"{s['shot_id']}: shows no world element (elements) - what is on screen?")
        shown = {e for s in body['shots'] for e in s.get('elements', [])}
        warnings += [f'world element {e} is in no shot' for e in sorted(known - shown)]
    if layer == 'shotlist':
        script = [l['line_id'] for l in ((parent.get('script') or {}).get('lines') or [])]
        e, w = story_lines(body, script, parent.get('world'))
        errors += e + show_lines(path, body, parent.get('world')) + length_lines(body, script_lines); warnings += w
        story = (envelope(path, 'story') or {}).get('body') if not adopted(path) or gated(path, 'story') else None
        if story:
            errors += story_beat_lines(body, (parent.get('script') or {}).get('lines') or [], story)
        from .reel_flow import lines as flow_lines, plan_rows, silenced, unresolved   # how the planned pictures run (built: reel flow)
        from .maker import kind_of
        facts_body = (envelope(path, 'facts') or {}).get('body')
        kinds = {s['shot_id']: kind_of(path, s, facts_body, parent.get('script'), parent.get('world')) for s in body['shots'] if s.get('frame')}
        flow = flow_lines(plan_rows(body['shots'], {l['line_id']: l for l in (parent.get('script') or {}).get('lines') or []}, kinds), story,
                          silenced(path))
        warnings += flow + (unresolved(flow) if story else [])   # a reel told as a story answers its direction findings
        errors += explain_lines(path, body, (envelope(path, 'facts') or {}).get('body'), parent.get('script'), parent.get('world'))
        from .maker import lines as maker_lines   # who makes each cut (generative first; what a line claims of a part, Blender)
        e, w = maker_lines(path, body, (envelope(path, 'facts') or {}).get('body'), parent.get('script'), parent.get('world'))
        errors += e; warnings += w
        warnings += reuse_lines(path, body, script_lines) + view_lines(path, body)
    if layer == 'look':
        presets = set(read_json(CODE / 'studio/blender_ops/look_data/look_presets.json')['presets'])
        names = [body['preset'], *body.get('per_shot', {}).values()]
        errors += [f'unknown look preset {n}' for n in names if n not in presets]
        shots = {s['shot_id'] for s in (((envelope(path, 'shotlist') or {}).get('body') or {}).get('shots') or [])}
        errors += [f'per_shot names unknown shot {s}' for s in body.get('per_shot', {}) if shots and s not in shots]
    if layer == 'cut':
        check_id(body['candidate_id'])
        snapshot = path / 'final' / body['candidate_id'] / 'edit.snapshot.json'
        if not (path / 'final' / body['candidate_id'] / 'manifest.json').is_file() or not snapshot.is_file():
            errors.append(f"no rough cut {body['candidate_id']} (edit build --profile rough first)")
        else:
            cut_versions = cut_versions_of(path, body['candidate_id'])
            errors += [f"{k}: the cut has {cut_versions.get(k)}, the sheet says {v}" for k, v in body['versions'].items() if cut_versions.get(k) != v]
            errors += [f"{k} is in the cut but not on the sheet" for k in cut_versions if k not in body['versions']]
            qa = path / 'final' / body['candidate_id'] / 'qa.json'   # what qa collect said against the patterns (advice)
            pattern = (read_json(qa).get('visual_checks') or {}).get('patterns') if qa.is_file() else None
            if pattern and pattern.get('off'):
                warnings.append(f"patterns: {', '.join(pattern['off'])} outside what shots of their role usually do (final/{body['candidate_id']}/qa.json)")
    from .gates import severity_for, sort_lines   # a softenable finding the project's policy makes an error (all-strict, policy.gates)
    promoted, warnings = sort_lines(warnings, severity_for(path))
    return {'errors': errors + promoted, 'warnings': warnings}


SHOT_MIN_S = 1.0   # no shot is shorter: the reference rhythm's 10th percentile shot (local measure 2026-10-13: median 2.6 s)
HOOK_MIN_S = 0.5   # ... but an opening hook may cut fast (its 10th percentile: 0.4 s)


def length_lines(body, script_lines, chars_per_s=CHARS_PER_S):
    """SHOT_TOO_SHORT errors: a shot shorter than the longest of its floors - the reference rhythm's (a hook may be
    faster), its words, the openings its moments make (show_core open_s). The fix offered is never a still picture held
    longer: join it with the shot before or after under one camera (one camera, one shot) and give it their lines.
    2026-10-13, the user: 씬이 너무 짧지 않도록 - shorter than a second, a shot is a flash, not a picture."""
    from .blender_ops.show_core import SHOW_METHODS, at_share, openings, show_of
    out = []
    for n, s in enumerate(body['shots']):
        floors = {'the rhythm': HOOK_MIN_S if s.get('role') == 'hook' else SHOT_MIN_S}
        runs_on = continues_into(body['shots']).get(s['shot_id'])
        mine = [i for i, _ in own_lines(body['shots'])[s['shot_id']]]
        said = [script_lines[i] for i in (mine[:-1] if runs_on else mine) if i in script_lines]
        if said:
            # the scratch-voice estimate; the voice itself, with its tail, is held at edit build (TIMING_CONFLICT)
            floors['its words'] = spoken_seconds(said, chars_per_s)
        if runs_on:   # the line said across the cut fits the shots it runs into
            last = mine[-1]
            at = (s.get('line_at_s') or [None])[-1] if s.get('line_at_s') else spoken_seconds(said, chars_per_s)
            ends = [x for x in body['shots'] if x['shot_id'] == runs_on[-1]][0]   # the shot it ends in may start a line of its own
            then = (ends.get('line_at_s') or [0, ends['duration_s']])[1] if len(ends['line_ids']) > 1 else ends['duration_s']
            room = s['duration_s'] - at + sum(x['duration_s'] for x in body['shots'] if x['shot_id'] in runs_on[:-1]) + then
            need_s = spoken_seconds([script_lines.get(last, '')], chars_per_s)
            if need_s > room + 1e-6:
                out.append(f"LINE_OUTRUNS_SHOTS: {s['shot_id']}'s line {last} takes about {need_s:.1f} s and has {room:.1f} s to its end in "
                           f"{runs_on[-1]} - run it into one more shot, give these shots the time, or say it shorter")
        rows = sorted(show_of(s), key=lambda r: at_share(r['at']))   # moments merged as the build merges them (openings)
        opening = sum(SHOW_METHODS[o['how']]['open_s'] for o in openings(rows))
        if opening:
            floors['its openings'] = opening
        need, why = max((v, k) for k, v in floors.items())
        if s['duration_s'] + 1e-6 < need:
            neighbours = [x['shot_id'] for x in (body['shots'][n - 1] if n else None, body['shots'][n + 1] if n + 1 < len(body['shots']) else None) if x]
            out.append(f"SHOT_TOO_SHORT: {s['shot_id']} is {s['duration_s']:.2f} s, {why} need {need:.2f} s - join it with "
                       f"{' or '.join(neighbours) or 'a neighbour'} under one camera (hand-over continue, their lines together), "
                       'or give it the time; a still held longer is not the fix')
    return out


MONOTONE_S = 7.3   # shots showing the same things, cut one after another, read as one long shot past this (the reference
                   # rhythm's 90th percentile shot length, local measure 2026-10-13)


def story_lines(body, script_lines, world=None):
    """(errors, warnings) of a shot list against the story: shots follow the script's order; a shot's beats are the
    beats its lines belong to; the same things on screen shot after shot for long reads as one; what the lines name (a
    world element with line_ids: people, a place, another technique) shows in the shots of those lines. 2026-10-13, the user: the shots must
    sit in the whole storyline, and a reel that only ever shows one object is awkward (people, organisations, other
    techniques the narration names)."""
    errors, warnings = [], []
    order = {line_id: n for n, line_id in enumerate(script_lines)}
    seen = [(s['shot_id'], order[i]) for s in body['shots'] for i in s['line_ids'] if i in order]
    late = [(a, b) for (a, i), (b, j) in zip(seen, seen[1:]) if j < i]
    if late:
        errors.append(f"SHOTS_OUT_OF_ORDER: {late[0][1]} says a line the script puts before one {late[0][0]} says - shots follow the "
                      'script (reorder the shots, or the script first)')
    if world:
        beat_of = {i: b['beat_id'] for b in world['beats'] for i in b['line_ids']}
        for s in body['shots']:
            told = sorted({beat_of[i] for i in s['line_ids'] if i in beat_of})
            if s.get('beat_ids') is not None and s['line_ids'] and sorted(set(s['beat_ids'])) != told:   # a wordless shot names its own
                errors.append(f"BEAT_MISMATCH: {s['shot_id']} names beats {sorted(set(s['beat_ids']))}, its lines tell {told}")
        for element in world['elements']:   # what the lines name is seen while they are said
            shots = [s for s in body['shots'] if set(s['line_ids']) & set(element.get('line_ids') or [])]
            if shots and not any(element['id'] in (s.get('elements') or []) for s in shots):
                warnings.append(f"STORY_ELEMENT_UNSHOWN: {element['id']} is named in lines {element['line_ids']}; none of their shots "
                                f"({', '.join(s['shot_id'] for s in shots)}) shows it - give it a shot of its own or a place in one")
    run, length = [], 0.0
    for s in body['shots'] + [None]:
        same = s is not None and run and sorted(s.get('elements') or []) == sorted(run[-1].get('elements') or []) and s.get('elements')
        if same:
            run.append(s); length += s['duration_s']
            continue
        if len(run) > 1 and length >= MONOTONE_S:
            warnings.append(f"SUBJECT_MONOTONE: {', '.join(r['shot_id'] for r in run)} show the same things ({', '.join(run[0]['elements'])}) "
                            f"for {length:.1f} s - cut to what the lines also name (people, a place, another technique) or change size and side")
        run, length = ([s], s['duration_s']) if s is not None else ([], 0.0)
    return errors, warnings


def story_beat_lines(body, lines, story):
    """Errors of a shot list against the story: a shot's story beats are those its lines belong to (a wordless shot
    names its own), every named beat is the story's, and shots follow the story's order."""
    from .story_core import beats_of
    order = {b['story_beat_id']: n for n, b in enumerate(story['beats'])}
    line_beat = {l['line_id']: l.get('story_beat_id') for l in lines}
    out, last = [], None
    for s in body['shots']:
        told = beats_of(s['line_ids'], line_beat)
        named = s.get('story_beat_ids')
        if named is not None and s['line_ids'] and list(named) != told:
            out.append(f"STORY_BEAT_MISMATCH: {s['shot_id']} names story beats {list(named)}, its lines tell {told}")
        beats = list(named) if named is not None else told
        out += [f"STORY_BEAT_UNKNOWN: {s['shot_id']} names story beat {b}, which the story does not have" for b in beats if b not in order]
        known = [order[b] for b in beats if b in order]
        if known and last is not None and min(known) < last[1]:
            out.append(f"SHOTS_OUT_OF_ORDER: {s['shot_id']} tells a story beat the story puts before one {last[0]} tells - shots follow "
                       "the story's beats")
        if known:
            last = (s['shot_id'], max(known))
    return out


def about_lines(path, body, world=None):
    """CLAIM_ABOUT_MISSING / ABOUT_UNKNOWN: every claim says what it is about (studio/explains.py) - [] when nothing
    can be seen of it, so a forgotten field and a deliberate nothing differ. Without the world (facts come first) a bare
    name that is no subject is left for the world's lint, which knows the elements."""
    from .explains import unknown
    out = []
    started = any('about' in claim for claim in body['claims'])
    for claim in body['claims']:
        if 'about' not in claim:
            if started:   # facts written before 2026-10-13 say no about at all: warned (about_warnings), not refused
                out.append(f"CLAIM_ABOUT_MISSING: claim {claim['claim_id']} does not say what it is about - the world elements, "
                           "subjects, subject/part or subject/feature a line saying it must show (about: [] when nothing can be seen)")
            continue
        missing = unknown(path, claim['about'], world, bare=world is not None)
        if missing:
            out.append(f"ABOUT_UNKNOWN: claim {claim['claim_id']} is about {missing}, which no subject spec "
                       f"{'nor world element ' if world is not None else ''}is called - a subject id, subject/part_id or "
                       "subject/feature_id from subjects/<id>/spec.json, or a world element id")
    return out


def known_show_parts(path, world=None):
    """What a shot list may name as shown: each subject spec's id and its parts as subject/part, and world elements."""
    known = {e['id'] for e in (world or {}).get('elements', [])}
    for spec in sorted(Path(path, 'subjects').glob('*/spec.json')):
        subject = spec.parent.name
        known.add(subject)
        known |= {f"{subject}/{b['part_id']}" for b in read_json(spec).get('builders', [])}
    return known


def show_lines(path, body, world=None):
    """SHOW_PART_UNKNOWN errors: a moment shows a part nothing models. A key part is modelled (its subject spec); the
    rest can be a world element - a clay stand-in the generative model fills (world.DEFAULT_OWNER)."""
    from .blender_ops.show_core import show_of
    known = known_show_parts(path, world)
    out = []
    for s in body['shots']:
        for row in show_of(s):
            unknown = [p for p in row['parts'] if p not in known]
            if unknown:
                out.append(f"SHOW_PART_UNKNOWN: {s['shot_id']} shows {unknown} at {row['at']}, which nothing models - a part the "
                           'moment explains goes into its subject spec (subject/part); anything else becomes a world element '
                           '(a clay stand-in the generative model fills)')
    return out


def reuse_lines(path, body, script_lines):
    """SHOT_ID_REUSED: a shot id whose existing shot was built or staged for other words. The projection updates a shot
    in place (never deletes), so its camera move, titles, subjects and builds would carry over into a different shot.
    2026-10-13: r08 of the rocket reused s01-s25 of an older script; s25 (now the Korean demonstrator) kept the booster
    as its subject and s20 kept a title past its new end."""
    out = []
    for s in body['shots']:
        if not shot_path(path, s['shot_id']).is_file():
            continue
        shot = load_shot(path, s['shot_id'])
        staged = shot.get('scene_version') or (shot.get('camera') or {}).get('move') or any(shot.get(k) for k in ('titles', 'labels', 'graphics', 'actions'))
        said = (shot.get('narration') or {}).get('text') or ''
        text = ' '.join(script_lines[i].strip() for i, _ in own_lines(body['shots'])[s['shot_id']] if i in script_lines)
        if staged and said.strip() and said.strip() != text:
            out.append(f"SHOT_ID_REUSED: {s['shot_id']} was staged for other words ({said[:40]!r}...); its camera move, titles, subjects and "
                       "builds would carry over - give the new shot a new id, or move the old shot's folders aside first")
    return out


def explain_lines(path, body, facts, script, world=None):
    """SHOT_EXPLAINS_UNSHOWN / SHOW_INSIDE_CLOSED: what a shot's lines explain (their claims' about, studio/explains.py)
    is on screen in that shot - an element or subject among its elements or show parts, a part or feature among its show
    parts - and what sits inside its subject (the exposure survey) is shown by a moment that opens. The camera's own
    SHOW_NEEDS_OPENING finds the same after the build; this finds it while it is still words."""
    from .blender_ops.show_core import opens, show_of
    from .explains import line_things, need, open_words, problem_line
    out = [e for e in about_lines(path, facts, world) if e.startswith('ABOUT_UNKNOWN')] if facts else []
    asked = set()
    shots, spans = body['shots'], line_spans(body['shots'])

    def shown_in(s, thing):
        parts = {p for row in show_of(s) for p in row['parts']}
        if thing['kind'] in ('element', 'subject'):
            return thing['id'] in (s.get('elements') or []) or thing['id'] in parts or any(p.startswith(thing['id'] + '/') for p in parts)
        return bool(set(thing['parts']) & parts)
    for n, s in enumerate(shots):
        rows = show_of(s)
        for thing in line_things(path, s['line_ids'], facts, script, world):
            # a line said across a cut explains over every shot it runs through: one of them showing it is enough
            # (2026-10-20: asking each to show all of it pushed planners back to one line per shot)
            said_by = [i for i in s['line_ids'] if thing['id'] in {t['id'] for t in line_things(path, [i], facts, script, world)}]
            reach = {k for i in said_by for k in spans.get(i, [n])}
            if any(len(spans.get(i, [n])) == 1 for i in said_by):
                reach = {n}   # a line of this shot alone says it too: this shot shows it
            if shown_in(s, thing):
                pass
            elif len(reach) > 1 and any(shown_in(shots[k], thing) for k in reach):
                continue   # another shot of the line shows it; its checks run there
            else:
                if len(reach) > 1 and n != min(reach):
                    continue   # said once, for the line's first shot
                where = 'the shots the line runs through show' if len(reach) > 1 else 'the shot shows'
                out.append(f"SHOT_EXPLAINS_UNSHOWN: {s['shot_id']}'s lines explain {thing['id']} ({thing['words']}); {where} it "
                           f"nowhere - {'its elements or ' if thing['kind'] in ('element', 'subject') else ''}a show moment's parts "
                           f"({', '.join(thing['parts'][:3]) or thing['id']})")
                continue
            needs = need(path, thing)
            if needs['problem'] and (needs['problem'], thing['id']) not in asked:
                asked.add((needs['problem'], thing['id']))
                out.append(problem_line(path, needs, thing, s['shot_id']))
            moments = [row for row in rows if set(thing['parts']) & set(row['parts'])]
            if needs['inside'] and moments and not any(opens(row['how']) for row in moments):
                out.append(f"SHOW_INSIDE_CLOSED: {s['shot_id']} shows {thing['id']} {', '.join(row['how'] for row in moments)}, but it sits inside "
                           f"{', '.join(needs['hidden_by']) or thing['subject']} (no outside side shows it) - show it by a moment that opens "
                           f"({open_words()})")
    return out


def view_lines(path, body):
    """Warnings for what a moment must show that the subject itself keeps from showing (A3-5, 2026-10-14; the exposure
    survey, studio/explains.py):
    SHOW_VIEW_HIDES - an outside moment of a shot whose pictures already set its view (camera.move.frames, written by
      frames apply) asks for a part that view hides; it names the nearest view that shows it and the ways to open.
    MUST_SEE_NESTED - one moment shows two parts and one encloses the other (lies in front of it and behind it on more
      than explains.NESTED_SHARE of its rays from all sides; a pipe inside its own sleeve, both must-see): an opening keeps
      the shown parts whole and the engine fades the outer one (show_core.SHOWN_THROUGH), so the picture should draw it
      so; an outside moment opens nothing - frames check offers the outer one see-through (cheat see_shown).
    Warnings, not errors: the survey sees the subject as modelled, unturned; the build's camera judges the scene."""
    from .blender_ops.show_core import SHOWN_THROUGH, held_at, moment_at, opens, show_of
    from .explains import NESTED_SHARE, encloses, view_line
    out = []
    for s in body['shots']:
        rows = show_of(s)
        for row in rows:
            for inner in row['parts']:
                for outer in row['parts']:
                    share = encloses(path, outer, inner) if outer != inner else None
                    if share is not None and share > NESTED_SHARE:
                        how = (f"the {row['how']} opening keeps both whole and the engine draws {outer} half see-through "
                               f"(alpha {SHOWN_THROUGH['alpha']}) so {inner} shows through it - draw the picture that way"
                               if opens(row['how']) else
                               f"this moment shows the outside, which opens nothing: frames check offers {outer} see-through "
                               f"(cheat see_shown), or open the moment in the shot list")
                        out.append(f"MUST_SEE_NESTED: {s['shot_id']} shows {inner} and {outer} at {row['at']}, and {outer} wraps {share:.2f} of "
                                   f"{inner}'s rays from all sides: {how}")
        if not shot_path(path, s['shot_id']).is_file():
            continue
        for f in ((load_shot(path, s['shot_id']).get('camera') or {}).get('move') or {}).get('frames') or []:
            row, held = moment_at(rows, f['at']), held_at(rows, f['at'])
            if row is None or opens(held['how']) or not f.get('view'):
                continue
            out += [line for ref in row['parts'] if (line := view_line(path, f"{s['shot_id']} t={f['at']}", ref, f['view']))]
    return out


REVEAL_EXPECTED = 0.1   # the user's reference shows an inside in at least this share of its shots: a plan should say where we do


def reveal_lines(body, profile=None):
    """REVEAL_UNPLANNED: the user's reference opens its subjects (sections, see-through, exploded) and this shot list plans
    no shot that does, nor says why not. Where to go inside is the planner's judgement - the lines that say what is inside,
    or new lines when the script never does (2026-10-12, the user: "전체 내용에 내부 구조 얘기가 없으면 추가해야") - the
    check only keeps the judgement from being skipped."""
    from .camera_taste import PROFILE
    if profile is None:
        profile = read_json(PROFILE) if PROFILE.is_file() else {}
    shares = (profile.get('reveals') or {}).get('shares') or {}
    opened = 1.0 - shares.get('none', 1.0)
    if opened < REVEAL_EXPECTED:
        return []
    shots = body.get('shots') or []
    planned = [s['shot_id'] for s in shots if opens_inside(s)]
    declined = [s['shot_id'] for s in shots if any(r.get('item') == 'reveal_method' for r in (s.get('pattern') or {}).get('departs', []))]
    if planned or declined:
        return []
    return [f"REVEAL_UNPLANNED: the user's reference shows an inside in {round(opened * 100)} % of its shots and no shot here plans one "
            "(a show moment that opens) - put the camera inside where the script says what is inside (a section it pushes into), add a line "
            "that does if the script never does, or record why not (pattern.departs item reveal_method)"]


def cut_versions_of(path, candidate_id):
    """{shot_id: scene_version} of a built cut (edit.snapshot.json keeps each shot's snapshot, version inside it)."""
    rows = read_json(project_dir(path) / 'final' / check_id(candidate_id) / 'edit.snapshot.json').get('shots', [])
    return {r['shot_id']: (r.get('shot_snapshot') or {}).get('scene_version') for r in rows}


# --- the sheet the user reads -------------------------------------------------------------------------------------

def _render_body(layer, body):
    if layer == 'brief':
        return [f"- **{k}**: {v}" for k, v in body.items()]
    if layer == 'facts':
        return (['**Sources**'] + [f"- `{s['source_id']}` {s['title']} {s.get('url') or s.get('citation') or ''}" for s in body['sources']]
                + ['', '**Claims**'] + [f"- `{c['claim_id']}` {c['text']} ← {', '.join(c['source_ids']) or 'no source'}" for c in body['claims']])
    if layer == 'story':
        from .story_core import positions
        d = body.get('direction')
        out = [f"**{body['logline']}**", ''] + ([f"Drawn as: hook {d['hook']}, arc {d['arc']}, angle {d['angle']}, tone {d['tone']}", ''] if d else [])
        out += ['| at | beat | function | tension | what happens | picture |', '|---|---|---|---|---|---|']
        out += [f"| {at:.2f} | `{b['story_beat_id']}` | {b['function']} | {'█' * round(b['tension'] * 10):<10} {b['tension']:.1f} | "
                f"{b['what_happens']}{' — ' + b['why_watch'] if b.get('why_watch') else ''} | {b.get('picture_idea', '')} |"
                for b, at in zip(body['beats'], positions(body['beats']))]
        return out
    if layer == 'script':
        return [f"{i + 1}. {'[' + l['story_beat_id'] + '] ' if l.get('story_beat_id') else ''}{l['text']}  _({'claims ' + ', '.join(l['claim_ids']) if l.get('claim_ids') else 'illustrative: ' + l.get('illustrative', '?')})_"
                for i, l in enumerate(body['lines'])]
    if layer == 'shotlist':
        from .blender_ops.show_core import show_of

        def directed(s):
            d = s.get('direction') or {}
            depth = d.get('depth') or {}
            rows = [('camera', d.get('camera')), ('action', d.get('action')),
                    ('depth', ' / '.join(f"{k}: {depth[k]}" for k in ('fore', 'mid', 'back') if depth.get(k))),
                    ('size', d.get('scale_step')), ('next', d.get('transition'))]
            return ''.join(f"\n  - {name}: {value}" for name, value in rows if value)
        return [f"- **{s['shot_id']}** {s['duration_s']} s{' [' + s['role'] + ']' if s.get('role') else ''} — {s['purpose']} (lines {', '.join(s['line_ids']) or 'none'})"
                + (f" · elements {', '.join(s['elements'])}" if s.get('elements') else '')
                + (f"\n  - frame: {', '.join(s['frame']['focus'])} in focus, {s['frame']['size']}" if s.get('frame') else '\n  - frame: not described')
                + directed(s)
                + ''.join(f"\n  - shows {', '.join(r['parts']) or 'the subject'} {r['how']} at {r['at']}{' (key change)' if r.get('key') else ''}"
                          for r in show_of(s))
                + ''.join(f"\n  - takes {r['item']}{' = ' + str(r['value']) if 'value' in r else ''}: {r['why']}" for r in (s.get('pattern') or {}).get('carried', []))
                + ''.join(f"\n  - leaves {r['item']}: {r['why']}" for r in (s.get('pattern') or {}).get('departs', []))
                for s in body['shots']]
    if layer == 'world':
        light = body.get('time_light') or {}
        out = [f"- **kind**: {body['kind']}", f"- **place**: {body.get('place', '')}",
               f"- **light**: {', '.join(f'{k} {v}' for k, v in light.items())}", f"- **people**: {body.get('inhabitants', '')}"]
        if body.get('reference'):
            out.append(f"- **world reference**: ![]({'../../../../' + body['reference']['path']})")
        out += ['', '**Beats** (the minute, seen first)']
        for b in body['beats']:
            out.append(f"- `{b['beat_id']}` lines {', '.join(b['line_ids'])}: {b.get('description', '')} "
                       + ' '.join(f"![{b['beat_id']}]({'../../../../' + pic['path']})" for pic in b['pictures']))
        out += ['', '| element | does | requires | shape | look | why |', '|---|---|---|---|---|---|']
        out += [f"| {e['id']} — {e['description']} | {e['function']} | {', '.join(e.get('requirements') or []) or '-'} | {e['owner']['shape']} | "
                f"{e['owner']['look']} | {e['why']} |" for e in body['elements']]
        return out
    if layer == 'cut':
        return ([f"- rough cut **{body['candidate_id']}** (final/{body['candidate_id']}/)"] + [f"- {k}: {v}" for k, v in body['versions'].items()]
                + ([f"- note: {body['note']}"] if body.get('note') else []))
    return [f"- preset **{body['preset']}**"] + [f"- {k}: {v}" for k, v in body.get('per_shot', {}).items()]


def _sheet(path, layer, env, report, rev):
    lines = [f"# {layer.title()} — sheet {rev}", '', f"Status: {env['status']}. Bound to: {', '.join(parents_of(path, layer)) or 'nothing (first layer)'}.", '']
    lines += _render_body(layer, env['body'])
    if layer == 'shotlist':   # who makes each cut (studio/maker.py): the user reads and changes it here
        from .maker import table as maker_table
        parent = {p: (envelope(path, p) or {}).get('body') for p in ('facts', 'script', 'world')}
        rows, share = maker_table(path, env['body'], parent['facts'], parent['script'], parent['world'])
        lines += ['', '## Who makes each cut', '',
                  f"generative {share['generative']:.0%} · hybrid {share['hybrid']:.0%} · Blender {share['blender']:.0%} of the reel's seconds "
                  '(generative first; a cut whose lines claim a part, its key change or an opening stays Blender)', '',
                  '| cut | maker | why |', '|---|---|---|']
        lines += [f"| {r['shot_id']} | {r['mode']}{' (written)' if r['given'] else ''}{' · same look as ' + r['shared_reference']['from'] + ' (' + ', '.join(r['shared_reference']['ids']) + ')' if r.get('shared_reference') else ''}"
                  f"{' — kept ' + r['kept'] + ' (built; write maker to change it)' if r.get('kept') and r['kept'] != r['mode'] else ''} | {r['why']} |"
                  for r in rows]
    if layer == 'shotlist':   # the direction rules against this list (studio reel rules): open, departed or met
        from .reel_flow import DEPART_ITEMS, reel_departs, rules_table
        departs = [d['item'] for s in env['body']['shots'] for d in (s.get('pattern') or {}).get('departs', [])]
        departs += [d['code'] for d in reel_departs(path)]
        lines += ['', '**Direction (studio reel rules):**'] + rules_table(report['errors'] + report['warnings'], departs)
        said = [f"- {s['shot_id']} departs {d['item']}: {d['why']}" for s in env['body']['shots'] for d in (s.get('pattern') or {}).get('departs', [])
                if d['item'] in DEPART_ITEMS]
        said += [f"- the reel departs {d['code']} ({d.get('by')}): {d['why']}" for d in reel_departs(path)]
        if said:
            lines += ['', '**Departed on purpose (read these):**'] + said
    for kind in ('errors', 'warnings'):
        if report[kind]:
            lines += ['', f'**{kind.title()}:**'] + [f'- {x}' for x in report[kind]]
    if env.get('history'):
        lines += ['', '**What you asked → what changed:**'] + [f"- \"{h['user_words']}\" → {h['change']}" for h in env['history']]
    lines += ['', f'Approve exactly this sheet: `decide approve --layer {layer} --sheet {rev} --user-words "<the user\'s words>"`'
              if not _off_ladder(env) else 'Off the ladder: a record, not a sheet to approve (decide write settles it).']
    return '\n'.join(lines) + '\n'


def _write(path, layer, env, report, event):
    root = _root(path)
    rev = f"r{len(list((root / 'sheets' / layer).glob('r*'))) + 1:02d}"
    sheet = root / 'sheets' / layer / rev / 'sheet.md'
    sheet.parent.mkdir(parents=True, exist_ok=True)
    env['sheet'] = {'rev': rev, 'path': str(sheet.relative_to(project_dir(path)))}
    sheet.write_text(_sheet(path, layer, env, report, rev), encoding='utf-8')
    write_json(root / f'{layer}.json', env)
    with (root / 'log.jsonl').open('a', encoding='utf-8') as log:
        log.write(json.dumps({'at': now(), 'layer': layer, 'event': event, 'rev': rev, 'body_sha256': body_sha256(env['body'])}, ensure_ascii=False) + '\n')
    return {'layer': layer, 'status': env['status'], 'sheet': str(sheet), 'rev': rev, **report, 'state': state(path, layer),
            'artifacts': [str(sheet), str(root / f'{layer}.json')]}


# --- propose / revise / approve -----------------------------------------------------------------------------------

def propose(project, layer, body):
    """The agent's draft (a dict or a JSON file): validated, linted, written as a sheet; status proposed."""
    path = project_dir(project)
    body = deepcopy(json_arg(body))
    if layer == 'cut' and 'versions' not in body and body.get('candidate_id'):   # what the cut shows, not what the agent types
        body['versions'] = cut_versions_of(path, body['candidate_id'])
    validate_body(_layer(layer), body)
    root = _root(path); root.mkdir(parents=True, exist_ok=True)
    if not adopted(path):
        stamp = now()
        write_json(root / 'ladder.json', {'schema_version': 1, 'layers': list(LAYERS), 'requires': list(LADDER_REQUIRES), 'adopted_at': stamp})
        project_data = load_project(path)
        if 'decision_ladder' not in project_data:   # a second record, so deleting ladder.json cannot quietly turn the gates off
            project_data['decision_ladder'] = {'adopted_at': stamp}
            project_data['revision'] += 1
            validate_schema(project_data, 'project'); write_json(path / 'project.json', project_data)
    previous = envelope(path, layer) or {}
    env = {'schema_version': 1, 'layer': layer, 'status': 'proposed', 'body': body, 'approval': None,
           'history': previous.get('history', [])}
    return _write(path, layer, env, lint(path, layer, body), 'propose')


def settled(path, layer):
    """The layer's body once it is settled - approved on the ladder, decided off it - else None (a draft, stale, missing)."""
    return envelope(path, layer)['body'] if state(path, layer)['state'] in ('approved', 'decided') else None


def write(project, layer, body, user_words=None, agent_note=None, _picked=False):
    """A layer settled off the ladder, kept where the ladder keeps it (decisions/<layer>.json) so every tool reads one
    record: decided by the user in their words, or by a delegated run's agent with its reason (never an approval). Its
    lint errors keep it a draft (status proposed: the tools read it, nothing counts it settled), as on the ladder before
    approve; clean, it is decided against its parents as they are and projected into the contracts approve projects
    into. Why (2026-10-18): a delegated run wrote its world to world.json while the beat reader read decisions/world.json,
    and no other layer had an off-ladder record at all - the run stopped at its first beat picture."""
    from .generative.review import check_user_words
    path = project_dir(project)
    if adopted(path):
        raise StudioError('INPUT_INVALID', f'{layer}: this project is on the ladder - decide propose / approve')
    if (user_words is None) == (agent_note is None):
        raise StudioError('INPUT_INVALID', f'{layer}: write takes the user\'s words or, in a delegated run, an agent note')
    delegation = load_project(path).get('delegation')
    if layer == 'story' and not _picked:
        raise StudioError('INPUT_INVALID', "story: settled only by the user's pick from the directions sheet (story pick)")
    if user_words is not None and delegation:   # a delegated run has no user at hand to quote: words given here are the
        # run's own (review 2026-10-18: the delegation's words recorded a "user" story); the story pick is the user's answer
        if not _picked:
            raise StudioError('INPUT_INVALID', f'{layer}: in a delegated run you settle a layer with --agent-note (your decision, '
                                               "never the user's words)")
    if agent_note is not None:
        if not delegation:
            raise StudioError('INPUT_INVALID', f'{layer}: an agent writes a layer only in a delegated run (project delegate)')
        if len(agent_note.strip()) < 4:
            raise StudioError('INPUT_INVALID', f'{layer}: the agent note says why, in a few words')
        by = {'by': 'agent', 'agent_note': agent_note.strip(), 'delegation': delegation['user_words']}
    else:
        by = {'by': 'user', 'user_words': check_user_words(user_words, f'{layer} write')}
    body = deepcopy(json_arg(body))
    validate_body(_layer(layer), body)
    root = _root(path); root.mkdir(parents=True, exist_ok=True)
    with lock(path / '.project.lock', blocking=False, reentrant=True):
        report = lint(path, layer, body)
        # a parent not settled (a draft, stale) leaves nothing to decide against: this one waits as a draft too
        report['errors'] += [f"{p} is {state(path, p)['state']}: {state(path, p)['reason']} - settle it first"
                             for p in parents_of(path, layer) if envelope(path, p) and state(path, p)['state'] not in ('decided', 'approved')]
        previous = envelope(path, layer) or {}
        env = {'schema_version': 1, 'layer': layer, 'off_ladder': True, 'body': body, 'approval': None,
               'history': previous.get('history', [])}   # what each write settled is in decisions/log.jsonl (body_sha256)
        if report['errors']:
            env.update({'status': 'proposed', 'drafted': {**by, 'at': now()}})
            return {**_write(path, layer, env, report, 'draft'), 'materialized': []}
        if layer in PREFLIGHT:
            PREFLIGHT[layer](path, body)
        parents = {p: body_sha256(envelope(path, p)['body']) for p in parents_of(path, layer) if envelope(path, p)}
        env.update({'status': 'decided', 'decided': {**by, 'body_sha256': body_sha256(body), 'parents': parents, 'at': now()}})
        result = _write(path, layer, env, report, 'decide')
        result['materialized'] = MATERIALIZE[layer](path, body)
    return result


def _pointer(path_text):
    return [part.replace('~1', '/').replace('~0', '~') for part in path_text.lstrip('/').split('/')]


def apply_ops(body, ops):
    """[{op: set|add|remove, path: '/json/pointer', value}] on a copy of the body. The layer schema decides what a path
    may hold (validated after), so an edit outside the contract is refused."""
    body = deepcopy(body)
    for op in ops:
        parts = _pointer(op['path'])
        try:
            parent = body
            for part in parts[:-1]:
                parent = parent[int(part)] if isinstance(parent, list) else parent[part]
            key = parts[-1]
            if isinstance(parent, list):
                index = len(parent) if key == '-' else int(key)
                if op['op'] == 'remove':
                    parent.pop(index)
                elif op['op'] == 'add':
                    parent.insert(index, op['value'])
                else:
                    parent[index] = op['value']
            elif op['op'] == 'remove':
                parent.pop(key)
            else:
                parent[key] = op['value']
        except (KeyError, IndexError, ValueError, TypeError) as error:
            raise StudioError('INPUT_INVALID', f"edit {op.get('op')} {op['path']}: no such place ({type(error).__name__}: {error})") from None
    return body


def revise(project, layer, user_words, ops=None, body=None, agent_note=None):
    """What the user asked, in their words, applied as ops (or a whole new body); back to proposed with a new sheet."""
    from .generative.review import check_user_words
    words = check_user_words(user_words, f'{layer} revise')
    path = project_dir(project)
    env = envelope(path, _layer(layer))
    if env is None:
        raise StudioError('INPUT_INVALID', f'{layer}: nothing to revise (decide propose first)')
    if _off_ladder(env):
        raise StudioError('INPUT_INVALID', f'{layer}: an off-ladder record is written again whole (decide write), not revised')
    ops = json_arg(ops) or []
    for op in ops:
        validate_schema_op(op)
    new_body = deepcopy(json_arg(body)) if body is not None else apply_ops(env['body'], ops)
    validate_body(layer, new_body)
    rounds = env.get('rounds_since_approval', 0) + 1
    env['rounds_since_approval'] = rounds
    env['history'] = env.get('history', []) + [{'at': now(), 'user_words': words, 'ops': ops, 'agent_note': agent_note,
                                                'change': agent_note or (f'{len(ops)} edit(s): ' + '; '.join(f"{o['op']} {o['path']}" for o in ops) if ops else 'new draft'),
                                                'before_sha256': body_sha256(env['body']), 'after_sha256': body_sha256(new_body)}]
    env.update({'status': 'proposed', 'approval': None, 'body': new_body})
    report = lint(path, layer, new_body)
    if rounds > ROUNDS_BEFORE_ASKING:
        report['warnings'].append(f'DECISION_ROUNDS: {rounds} revisions of {layer} without approval - ask a framing question instead of iterating')
    return _write(path, layer, env, report, 'revise')


def validate_schema_op(op):
    schema = read_json(CODE / 'schemas' / 'studio-v1' / 'decision.schema.json')
    errors = list(Draft202012Validator({'$ref': '#/$defs/op', '$defs': schema['$defs']}).iter_errors(op))
    if errors:
        raise StudioError('INPUT_INVALID', f'edit op {op}: {errors[0].message}')


def approve(project, layer, user_words, sheet_rev):
    """Bind the user's approval to the body on the sheet they saw (the latest) and to the parents' approved bodies;
    then project it into the contracts the tools read."""
    from .generative.review import check_user_words
    words = check_user_words(user_words, f'{layer} approve')
    path = project_dir(project)
    env = envelope(path, _layer(layer))
    if env is None:
        raise StudioError('INPUT_INVALID', f'{layer}: nothing to approve')
    if not adopted(path) or _off_ladder(env):
        raise StudioError('INPUT_INVALID', f'{layer}: approval is the ladder\'s - off it a layer is settled by decide write; '
                                           'on it, propose the layer and approve the sheet the user saw')
    if sheet_rev != env['sheet']['rev']:
        raise StudioError('DECISION_STALE', f"{layer}: the latest sheet is {env['sheet']['rev']}, not {sheet_rev}; show it and ask again")
    report = lint(path, layer, env['body'])
    if report['errors']:
        raise StudioError('DECISION_INVALID', f'{layer}: ' + '; '.join(report['errors'][:6]), recovery='Fix the draft (decide revise) before approval')
    parents = {}
    for parent in parents_of(path, layer):
        parent_state = state(path, parent)
        if parent_state['state'] != 'approved':
            raise StudioError('DECISION_UNAPPROVED', f"{layer} is bound to {parent}, which is {parent_state['state']}: {parent_state['reason']}",
                              recovery=f'Approve {parent} first')
        parents[parent] = body_sha256(envelope(path, parent)['body'])
    # held over the whole approval: another process changing the project between the record and its projection would
    # leave an approval the contracts do not carry (2026-10-12 audit); BUSY here writes nothing
    with lock(path / '.project.lock', blocking=False, reentrant=True):
        if layer in PREFLIGHT:   # every contract the approval projects into must take it before the approval is written
            PREFLIGHT[layer](path, env['body'])
        env['rounds_since_approval'] = 0
        env.update({'status': 'approved', 'approval': {'user_words': words, 'body_sha256': body_sha256(env['body']), 'parents': parents,
                                                       'sheet_rev': sheet_rev, 'at': now()}})
        result = _write(path, layer, env, report, 'approve')
        result['materialized'] = MATERIALIZE[layer](path, env['body'])
    return result


def settle(project, layer, steps, undo_event, keep=(), detail=None):
    """Run steps() - the propose or revise and the approve that settle one layer - under one project hold. When any
    step raises, the layer's record, the sheets it made and the files in keep (project-relative: what the approval
    projects into) are put back as they were, the undo is logged, and the error goes on: no half-settled layer is left
    (a proposed story the script then depends on, a revised list nobody approved). Callers check what approve would
    refuse before calling, so this is the last guard, not the first."""
    import shutil
    path = project_dir(project)
    root = _root(path)
    record = root / f'{_layer(layer)}.json'
    saved = {f: (path / f).read_bytes() if (path / f).is_file() else None for f in (str(record.relative_to(path)), 'project.json', *keep)}
    ladder = root / 'ladder.json'
    had_ladder = ladder.is_file()
    sheets = set((root / 'sheets' / layer).glob('r*'))
    with lock(path / '.project.lock', blocking=False, reentrant=True):
        try:
            return steps()
        except Exception:
            for name, data in saved.items():
                if data is None:
                    (path / name).unlink(missing_ok=True)
                else:
                    (path / name).write_bytes(data)
            if not had_ladder:
                ladder.unlink(missing_ok=True)
            for made in set((root / 'sheets' / layer).glob('r*')) - sheets:
                shutil.rmtree(made, ignore_errors=True)
            root.mkdir(parents=True, exist_ok=True)
            with (root / 'log.jsonl').open('a', encoding='utf-8') as log:
                log.write(json.dumps({'at': now(), 'layer': layer, 'event': undo_event, **(detail or {})}, ensure_ascii=False) + '\n')
            raise


# --- projection into the contracts the tools read -----------------------------------------------------------------

def _project_update(path, change):
    with lock(path / '.project.lock', blocking=False, reentrant=True):   # inside approve's hold, or on its own
        project = load_project(path)
        change(project)
        project['revision'] += 1
        validate_schema(project, 'project')
        write_json(path / 'project.json', project)
    return project


def _brief_projection(body):
    return {'key_message': body['key_message'], 'subject_mode': body['subject_mode'], 'request': body['topic']}


def _materialize_brief(path, body):
    def change(project):
        project['brief'].update(_brief_projection(body))
        project['output']['target_seconds'] = body['length_s']
    _project_update(path, change)
    return ['project.json brief', 'output.target_seconds']


def _materialize_facts(path, body):
    write_json(path / 'sources.json', {'schema_version': 1, 'sources': body['sources'], 'claims': body['claims']})
    return ['sources.json']


def _sentence_links(script_lines, text):
    """One link per sentence of the shot's narration as facts.check reads it (facts.sentences of the joined text): the
    claims of every line the sentence covers, else the first covering line's illustrative note. Why (2026-10-12 audit):
    linking each line's own sentences broke when a line had no final mark and ran into the next once joined."""
    from .facts import sentences
    spans, at = [], 0
    for line in script_lines:   # where each line's text sits in the joined narration
        start = text.find(line['text'].strip(), at)
        spans.append((start, start + len(line['text'].strip()), line))
        at = start + len(line['text'].strip())
    out, at = [], 0
    for sentence in sentences(text):
        a = text.find(sentence, at); b = a + len(sentence); at = b
        covering = [line for s, e, line in spans if s < b and e > a]
        claims = list(dict.fromkeys(c for line in covering for c in line.get('claim_ids') or []))
        out.append({'claim_ids': claims} if claims else
                   {'illustrative': next((line.get('illustrative') for line in covering if line.get('illustrative')), 'framing line')})
    return out


def show_decided(path, shot_id):
    """Whether the approved shot list decided what this shot shows. A list written with show (any of its shots has one)
    decided it for every shot: one without rows shows nothing in particular, so what its pictures also show is noted,
    not made a must-see. Why (2026-10-13): s24/s25 named no parts and their pictures of the whole vehicle were refused."""
    env = envelope(path, 'shotlist')   # the list as approved, or decided off the ladder (a delegated run)
    if not env or not (env.get('status') == 'approved' or (env.get('status') == 'decided' and not adopted(path))):
        return False
    shots = env['body']['shots']
    return any(s['shot_id'] == shot_id for s in shots) and any(s.get('show') is not None for s in shots)


def _carry_show(shot, entry, world, height):
    """What the shot list decided about showing and handing over reaches the shot: show (and its parts as
    screen.details, which the build measures), handover, and the beats its lines tell."""
    from .blender_ops.show_core import handover_of, min_px, show_of
    show, handover = show_of(entry), handover_of(entry)
    before = {part for row in show_of(shot) for part in row['parts']}   # what an earlier shot list asked, read before it is replaced
    for key, value in (('show', show), ('handover', handover)):
        if value:
            shot[key] = deepcopy(value)
        else:
            shot.pop(key, None)
    beat_of = {i: b['beat_id'] for b in (world or {}).get('beats', []) for i in b['line_ids']}
    beats = entry.get('beat_ids') or sorted({beat_of[i] for i in entry['line_ids'] if i in beat_of})
    if beats:
        shot['beat_ids'] = list(beats)
    else:
        shot.pop('beat_ids', None)
    wanted = {}
    for row in show:
        for part in row['parts']:
            need = min_px(height, row.get('min_px'))
            wanted[part] = {'id': part, 'min_px': max(need, wanted.get(part, {}).get('min_px', 0)), 'why': row['why'],
                            'source': 'shotlist'}
    # the shot list's parts replace their earlier rows, and a row an earlier shot list made goes with its part (source
    # shotlist, or - written before rows said where they came from - a part the earlier show listed); details written by
    # hand for other ids stay. Why (2026-10-20): a part taken out of the shot list kept its row, and the build still
    # asked for it (canal s05 'water')
    made = lambda d: d.get('source') == 'shotlist' or ('source' not in d and d['id'] in before)  # noqa: E731
    screen = dict(shot.get('screen') or {})
    kept = [d for d in screen.get('details', []) if d['id'] not in wanted and not made(d)]
    if wanted or 'details' in screen:
        screen['details'] = kept + list(wanted.values())
        shot['screen'] = screen


def _set_aside_for_generation(path, shot, write=True):
    """A cut a model makes has no Blender scene: what only a build reads - its version pointer, subjects, scene
    instances, actions, labels, assets, graphics, its kept regions - is set aside, every value written to
    shots/<id>/set_aside_for_generation.json (the version stays on disk), so a cut made Blender again gets it back by
    hand. Nothing is thrown away. -> the keys set aside."""
    keys = [k for k in ('scene_version', 'subjects', 'actions', 'labels', 'asset_instances', 'graphics') if shot.get(k)]
    instances = (shot.get('scene') or {}).get('instances')
    kept_regions = (shot.get('screen') or {}).get('generate_only')
    if not keys and not instances and not kept_regions:
        return []
    record = shot_path(path, shot['shot_id']).parent / 'set_aside_for_generation.json'
    held = read_json(record) if record.is_file() else {'schema_version': 1, 'set_aside': []}
    held['set_aside'].append({'at': now(), **{k: deepcopy(shot[k]) for k in keys},
                              **({'scene.instances': deepcopy(instances)} if instances else {}),
                              **({'screen.generate_only': list(kept_regions)} if kept_regions else {})})
    if write:   # (approve's dry run changes the shot in memory only)
        record.parent.mkdir(parents=True, exist_ok=True)
        write_json(record, held)
    for key in keys:
        shot[key] = None if key == 'scene_version' else []
    if instances:
        shot['scene'] = {k: v for k, v in shot['scene'].items() if k != 'instances'}
        if not shot['scene']:
            shot.pop('scene')
    if kept_regions:
        shot['screen'] = {k: v for k, v in shot['screen'].items() if k != 'generate_only'}
    return keys + (['scene.instances'] if instances else []) + (['screen.generate_only'] if kept_regions else [])


def _place_subjects(shot):
    """Each subject the shot builds stands in its scene: a subject is built only as a scene instance (layout.py), so one
    the scene does not name gets one at the origin - where the author script and the camera find it. Placed instances
    stay as they are. Why (2026-10-13): r08's shots named the booster as a subject and had no scene, nothing built it."""
    scene = dict(shot.get('scene') or {})
    rows = list(scene.get('instances') or [])
    placed = {r.get('subject') for r in rows}
    rows += [{'id': x['subject_id'], 'subject': x['subject_id'], 'at': [0, 0, 0]} for x in shot.get('subjects') or []
             if x['subject_id'] not in placed]
    if rows:
        shot['scene'] = {**scene, 'instances': rows}


def _materialize_shotlist(path, body, write=True):
    """Shots and timeline from the approved shot list and script: each shot's narration is its lines, each sentence
    linked as the script linked it. Shots that already have versions are updated, never deleted. write=False only builds
    and validates every shot (approve runs it first: 2026-10-12, an approval was recorded and its projection stopped at
    the fourth shot on a schema error - three shots rewritten, the timeline not)."""
    from .routing import propose_route
    from . import world as world_mod
    project = load_project(path)
    fps = project['output']['fps']
    lines = {l['line_id']: l for l in envelope(path, 'script')['body']['lines']}
    policy = project.get('route_policy', {'allow_generative': True, 'budget_usd': 0, 'turnaround_required': True})
    timeline, offset, written, retired, cleared = [], 0, [], [], []
    facts_body = (envelope(path, 'facts') or {}).get('body')
    own, runs_on = own_lines(body['shots']), continues_into(body['shots'])
    for entry in body['shots']:
        frames = max(1, round(entry['duration_s'] * fps))
        said = [i for i, _ in own[entry['shot_id']]]   # a line continuing from the shot before is voiced there
        text = ' '.join(lines[i]['text'].strip() for i in said)
        links = _sentence_links([lines[i] for i in said], text)
        file = shot_path(path, entry['shot_id'])
        if file.is_file():
            shot = load_shot(path, entry['shot_id'])
            shot['revision'] += 1
        else:
            from .project import default_shot
            shot = default_shot(entry['shot_id'], frames, project['brief'])
            if entry.get('route_features'):
                shot['route'] = {'features': list(entry['route_features'])}
                proposed = propose_route(shot, policy)
                del shot['route']
                if proposed['mode'] == 'blender':
                    shot['route'] = proposed
        shot['duration_frames'] = frames
        shot['goal'] = entry['purpose']
        from .story_core import beats_of   # the story beats the shot tells (its lines', else its own for a wordless shot)
        told = entry.get('story_beat_ids') or beats_of(entry['line_ids'], {i: l.get('story_beat_id') for i, l in lines.items()})
        if told:
            shot['story_beat_ids'] = list(told)
        else:
            shot.pop('story_beat_ids', None)
        _carry_show(shot, entry, world_mod.load(path), project['output']['height'])
        if entry.get('frame'):   # the screen the shot list decided (studio/maker.py reads it; shared references too)
            shot['frame'] = deepcopy(entry['frame'])
        else:
            shot.pop('frame', None)
        if entry.get('direction'):   # the camera and action the user approved reach the files the build is made from
            shot['direction'] = deepcopy(entry['direction'])
        else:
            shot.pop('direction', None)
        from .patterns import role_of
        role = role_of(entry.get('role'))
        if role or entry.get('pattern'):   # the shot's job on screen and what it leaves on purpose
            screen = dict(shot.get('screen') or {})
            if role:
                screen['role'] = role
            for key in ('departs', 'carried'):   # what a later list takes out goes from the shot too (2026-10-13)
                if entry.get('pattern', {}).get(key):
                    screen[f'pattern_{key}'] = [dict(x) for x in entry['pattern'][key]]
                else:
                    screen.pop(f'pattern_{key}', None)
            shot['screen'] = screen
        world = world_mod.load(path)
        if world and entry.get('elements'):   # the world's owners decide the shot's regions and route (studio/world.py)
            style_file = Path(path, 'style.json')
            plan = world_mod.shot_plan(world, entry['elements'],
                                       world_mod.shell_for(shot, world, read_json(style_file) if style_file.is_file() else {}))
            screen = dict(shot.get('screen') or {})
            screen['elements'] = list(entry['elements'])
            # an element with a subject spec is built from it (2026-10-13: r08's new shots named the booster among their
            # elements and no subject to build, so the reader and the build had nothing to frame)
            have = {x['subject_id'] for x in shot.get('subjects') or []}
            shot['subjects'] = list(shot.get('subjects') or []) + [
                {'subject_id': e} for e in entry['elements'] if e not in have and Path(path, 'subjects', e, 'spec.json').is_file()]
            _place_subjects(shot)
            if plan['generate_only']:
                screen['generate_only'] = plan['generate_only']
            else:
                screen.pop('generate_only', None)
            shot['screen'] = screen
        else:
            plan = None
        route_now = shot.get('route') or {}
        from .maker import applies as maker_applies
        if route_now.get('status') != 'approved' and maker_applies(entry, shot):
            # who makes the cut (studio/maker.py: the shot list's maker, else the table's - generative first, Blender where
            # a line claims a part); the world's owners only say what a hybrid cut's model fills (2026-10-20)
            from .maker import maker_of
            from .routing import classify, estimate
            made = maker_of(path, entry, facts_body, envelope(path, 'script')['body'], world)
            held = classify(entry.get('route_features') or [], policy)
            if made['mode'] != 'blender' and held[1] == 'R1_no_generation':   # the project or the cut allows no generation
                made = {**made, 'mode': 'blender', 'why': f"{held[2]} (the table said {made['mode']}: {made['why']})"}
            planned = {'mode': made['mode'], 'role': 'mood' if made['mode'] == 'generative' else (plan or {}).get('role', 'explain'),
                       'generate_only': (plan or {}).get('generate_only') or []}
            shot['route'] = world_mod.planned_route(shot, planned, entry.get('route_features') or [], fps)
            shot['route'].update({'rule_id': 'S_shotlist_maker', 'reason': made['why'][:400],
                                  'confidence': 'high' if made.get('given') else 'medium'})
            if made['mode'] == 'generative':   # a cut a model makes starts from its own first picture (generative/inputs.py)
                shot['route']['generative']['operation'] = 'image_to_video'
            shot['route'].update(estimate(shot, shot['route'], path))
            if shot['route']['mode'] == 'generative':
                set_aside = _set_aside_for_generation(path, shot, write)
                if set_aside:
                    cleared.append({'shot_id': entry['shot_id'], 'set_aside': set_aside})
        elif route_now.get('status') != 'approved' and plan is not None:
            # a shot built or routed before the shot list said who makes it keeps the world's plan, as it did: the maker
            # table never takes a build away unasked (the sheet says what it would propose)
            from .routing import estimate
            shot['route'] = world_mod.planned_route(shot, plan, entry.get('route_features') or [], fps)
            shot['route'].update(estimate(shot, shot['route'], path))
            if shot['route']['mode'] == 'generative' and shot.get('scene_version'):
                retired.append({'shot_id': entry['shot_id'], 'scene_version': shot['scene_version']})
                shot['scene_version'] = None
        shot['narration'] = {**shot['narration'], 'text': text, 'sentence_claims': links}
        if entry.get('line_at_s') and said:   # each line placed in the shot; the gaps are picture alone (audio places them)
            shot['narration']['segments'] = [{'text': lines[i]['text'].strip(), 'at_s': at} for i, at in own[entry['shot_id']]]
        else:
            shot['narration'].pop('segments', None)
        if runs_on.get(entry['shot_id']):   # its last line is said on over the next shots (edit build lets the voice run there)
            shot['narration']['continues_into'] = list(runs_on[entry['shot_id']])
        else:
            shot['narration'].pop('continues_into', None)
        if not said:   # no words of its own, on purpose: its voice track is silence (audio.build_audio refuses an empty text otherwise)
            carried = next((k for k, into in runs_on.items() if entry['shot_id'] in into), None)
            shot['narration']['silent'] = f'the line said in {carried} runs on over it' if carried else 'a picture with no words of its own'
        else:
            shot['narration'].pop('silent', None)
        validate_shot(shot)
        if write:
            write_json(file, shot)
        written.append(entry['shot_id'])
        timeline.append({'shot_id': entry['shot_id'], 'start_frame': offset, 'frame_count': frames})
        offset += frames
    removed = [e['shot_id'] for e in project['shots'] if e['shot_id'] not in written]
    if not write:
        return {'shots': written, 'removed_from_timeline': removed, 'retired_versions': retired, 'cleared_for_generation': cleared}

    def change(project):
        project['shots'] = timeline
    _project_update(path, change)
    return {'shots': written, 'removed_from_timeline': removed, 'retired_versions': retired, 'cleared_for_generation': cleared}


def _materialize_look(path, body):
    from .routing import _write_shot
    for entry in load_project(path)['shots']:
        shot = load_shot(path, entry['shot_id'])
        wanted = body.get('per_shot', {}).get(entry['shot_id'], body['preset'])
        if shot['render'].get('look_preset') != wanted:
            updated = deepcopy(shot); updated['render']['look_preset'] = wanted
            _write_shot(path, entry['shot_id'], updated, shot['revision'])
    return ['shots render.look_preset']


def _decided_by(path, layer):
    """Who settled the layer as its record says: the user (approved, or decided in their words) or the delegated agent."""
    decided = (envelope(path, layer) or {}).get('decided') or {}
    return decided.get('by', 'user'), decided


def _materialize_world(path, body):
    from .world import materialize
    by, decided = _decided_by(path, 'world')
    return materialize(path, body, by, decided.get('agent_note'))


def _materialize_story(path, body):
    from .story import materialize
    by, decided = _decided_by(path, 'story')
    return materialize(path, body, by, decided.get('user_words'))


MATERIALIZE = {'brief': _materialize_brief, 'facts': _materialize_facts, 'story': _materialize_story, 'script': lambda path, body: [], 'world': _materialize_world,
               'shotlist': _materialize_shotlist, 'look': _materialize_look, 'cut': lambda path, body: []}
# a dry projection that raises where the real one would stop halfway (layers whose projection validates many files)
PREFLIGHT = {'shotlist': lambda path, body: _materialize_shotlist(path, body, write=False)}


def drift(path, layer):
    """Where the live contracts no longer say what the approved layer says (a hand edit after approval)."""
    env = envelope(path, layer)
    if env is None or env['status'] != 'approved':
        return []
    body, project = env['body'], load_project(path)
    if layer == 'brief':
        live = {k: project['brief'].get(k) for k in _brief_projection(body)}
        return [f'project.brief.{k}' for k, v in _brief_projection(body).items() if live[k] != v]
    if layer == 'story':
        from .story import load as load_story
        live = load_story(path) or {}
        return [] if {k: live.get(k) for k in body} == body else ['story.json']
    if layer == 'facts':
        sources = read_json(path / 'sources.json') if (path / 'sources.json').is_file() else {}
        return [] if (sources.get('sources'), sources.get('claims')) == (body['sources'], body['claims']) else ['sources.json']
    if layer == 'shotlist':   # the timeline, and each shot's length and direction as approved
        planned = [s['shot_id'] for s in body['shots']]
        if [e['shot_id'] for e in project['shots']] != planned:
            return ['project.shots timeline']
        fps = project['output']['fps']
        out = [f"{e['shot_id']} frame_count" for e, s in zip(project['shots'], body['shots']) if e['frame_count'] != max(1, round(s['duration_s'] * fps))]
        for s in body['shots']:
            shot = load_shot(path, s['shot_id'])
            if shot['duration_frames'] != max(1, round(s['duration_s'] * fps)):
                out.append(f"{s['shot_id']} duration_frames")
            if (shot.get('direction') or None) != (s.get('direction') or None):   # materialize leaves out an empty direction
                out.append(f"{s['shot_id']} direction")
        return out
    if layer == 'look':
        return [f"{e['shot_id']} look_preset" for e in project['shots']
                if load_shot(path, e['shot_id'])['render'].get('look_preset') != body.get('per_shot', {}).get(e['shot_id'], body['preset'])]
    if layer == 'cut':   # finals render the versions the user saw in the rough cut
        return [f"{e['shot_id']} scene_version" for e in project['shots']
                if load_shot(path, e['shot_id']).get('scene_version') != body['versions'].get(e['shot_id'])]
    return []


def require(path, operation):
    """Gate: the layers this operation stands on are approved, fresh and not drifted (only for projects on the ladder)."""
    path = project_dir(path)
    if not adopted(path):
        evidence = ladder_evidence(path)
        if evidence:
            raise StudioError('DECISION_DRIFT', f'{operation}: decisions/ladder.json is gone but the project was on the ladder ({evidence})',
                              recovery='Restore the decisions/ folder from where it was; the gates stay on once a project has used the ladder')
        return
    for layer in GATES[operation]:
        if not gated(path, layer):
            continue
        layer_state = state(path, layer)
        if layer_state['state'] in ('missing', 'proposed'):
            raise StudioError('DECISION_UNAPPROVED', f"{operation} needs the {layer} approved: {layer_state['reason']}",
                              recovery=f'decide propose / show the sheet / decide approve --layer {layer}')
        if layer_state['state'] == 'stale':
            raise StudioError('DECISION_STALE', f"{operation}: {layer} is stale - {layer_state['reason']}",
                              recovery=f'Show the {layer} sheet again and get a new approval')
        changed = drift(path, layer)
        if changed:
            raise StudioError('DECISION_DRIFT', f"{operation}: {', '.join(changed)} no longer match the approved {layer}",
                              recovery=f'Put the change through decide revise --layer {layer} (or undo the hand edit)')


def status(project):
    path = project_dir(project)
    rows = []
    for layer in LAYERS:
        env = envelope(path, layer)
        if adopted(path) and not gated(path, layer):   # a layer this ladder was adopted without: nothing waits for it
            rows.append({'layer': layer, 'state': 'not_in_ladder', 'reason': f'the ladder was adopted without {layer}', 'sheet': None, 'drift': []})
            continue
        rows.append({'layer': layer, **state(path, layer), 'sheet': (env or {}).get('sheet'), 'drift': drift(path, layer)})
    from .story import missing as story_missing   # a project made before the story, or one that skipped it (a warning)
    return {'adopted': adopted(path), 'layers': rows, **({'ladder_missing': ladder_evidence(path)} if not adopted(path) and ladder_evidence(path) else {}),
            'warnings': story_missing(path),
            'next': next((f"{r['layer']}: {r['reason']}" for r in rows if r['state'] not in ('approved', 'decided', 'not_in_ladder')),
                         'all layers settled')}


def adopt(project):
    """Draft every layer from an existing project's contracts (proposed): the user approves each before the gates
    pass. For projects made before the ladder."""
    from .facts import sentences
    path = project_dir(project)
    project_data = load_project(path)
    brief = project_data['brief']
    shots = [load_shot(path, e['shot_id']) for e in project_data['shots']]
    fps = project_data['output']['fps']
    sources = read_json(path / 'sources.json') if (path / 'sources.json').is_file() else {'sources': [], 'claims': []}
    lines, shot_rows = [], []
    for shot in shots:
        said = sentences(shot['narration'].get('text', ''))
        links = shot['narration'].get('sentence_claims') or [{} for _ in said]
        ids = []
        for k, (sentence, link) in enumerate(zip(said, links + [{}] * (len(said) - len(links)))):
            line_id = f"{shot['shot_id']}-{k + 1}"
            lines.append({'line_id': line_id, 'text': sentence, 'story_beat_id': shot['shot_id'], **({'claim_ids': link['claim_ids']} if link.get('claim_ids')
                                                                   else {'illustrative': link.get('illustrative', 'adopted: not yet linked')})})
            ids.append(line_id)
        shot_rows.append({'shot_id': shot['shot_id'], 'purpose': shot.get('goal') or 'adopted shot', 'line_ids': ids,
                          'duration_s': round(shot['duration_frames'] / fps, 3)})
    # the story: the one the user picked (story.json, story pick) when there is one - a draft must not overwrite it;
    # else drafted from the shots as they stand, one beat a shot, so the user can change it. The peak is a middle shot
    # (the one nearest 3/4 of the reel); a two-shot reel ends on it (an opening and its payoff are all there is)
    from .story import body_of, load as load_story
    picked_story = body_of(load_story(path))
    total = sum(r['duration_s'] for r in shot_rows) or 1.0
    middle = range(1, len(shot_rows) - 1) if len(shot_rows) >= 3 else range(len(shot_rows) - 1, len(shot_rows))
    peak = min(middle, key=lambda n: abs(sum(r['duration_s'] for r in shot_rows[:n + 1]) / total - 0.75)) if shot_rows else 0
    beats = [{'story_beat_id': r['shot_id'], 'what_happens': r['purpose'], 'length_share': round(r['duration_s'] / total, 4),
              'function': 'climax' if n == peak and n > 0 else 'hook' if n == 0 else 'close' if n == len(shot_rows) - 1 else 'explain',
              'tension': 0.8 if n == peak else 0.5 if n == 0 else 0.3} for n, r in enumerate(shot_rows)]
    told = brief.get('key_message') or brief.get('request') or ''
    story_draft = picked_story or ({'logline': told if len(told) >= 5 else f'{told} (to be agreed)'.strip(), 'beats': beats}
                                   if len(beats) >= 2 else None)
    if picked_story:   # the lines name the picked story's beats where a shot tells one; else they wait for the user's revise
        known = {b['story_beat_id'] for b in picked_story['beats']}
        for line in lines:
            if line['story_beat_id'] not in known:
                line.pop('story_beat_id')
    drafts = {'brief': {'topic': brief.get('request') or project_data['project_id'], 'audience': 'general', 'length_s': project_data['output']['target_seconds'],
                        'key_message': brief.get('key_message') or brief.get('request') or 'to be agreed', 'subject_mode': brief.get('subject_mode', 'schematic')},
              'facts': {'sources': [s for s in sources.get('sources', []) if s.get('source_id') and s.get('title')],
                        'claims': [c for c in sources.get('claims', []) if c.get('claim_id')]},
              **({'story': story_draft} if story_draft else {}),
              'script': {'lines': lines or [{'line_id': 'l1', 'text': '-', 'illustrative': 'no narration'}]},
              'shotlist': {'shots': shot_rows}}
    return {'adopted': [propose(path, layer, body)['layer'] for layer, body in drafts.items()],
            'note': 'every layer is proposed: show each sheet and approve in the user\'s words, in order'}


# --- delegated runs ---------------------------------------------------------------------------------------------

def delegate(project, user_words, scope=None, _from_launcher=False):
    """Record that the user handed this run's decisions to the agent (their own words, verbatim). The ladder stays
    opt-in: a delegated project opens no sheets; the agent decides what is not on SKILL's ask-only list and records
    each choice with ``note``. A later ``propose`` still starts the ladder if the user wants it back."""
    from .generative.review import check_user_words
    path = project_dir(project)
    words = check_user_words(user_words, 'delegation', _from_launcher)
    with lock(path / '.project.lock'):
        data = load_project(path)
        data['delegation'] = {'user_words': words, 'scope': list(scope or []), 'recorded_at': now()}
        data['revision'] += 1
        validate_schema(data, 'project'); write_json(path / 'project.json', data)
    return {'project': str(path), 'delegation': data['delegation']}


PENDING_DELEGATION = REPO / '.studio' / 'delegation.json'   # written by scripts/reel_agent.py --delegate (the user's words)


def claim_pending_delegation(project):
    """Record a delegation the launcher left for the project this run creates, then remove it (one launch, one project).
    Removed only once recorded: a refused or busy delegate keeps the user's words for the next try (2026-10-12 audit)."""
    if not PENDING_DELEGATION.is_file():
        return None
    given = read_json(PENDING_DELEGATION)
    # the words the launcher was given before the run began (scripts/reel_agent.py --delegate), not the run's
    recorded = delegate(project, given['user_words'], given.get('scope'), _from_launcher=True)['delegation']
    PENDING_DELEGATION.unlink(missing_ok=True)
    return recorded


NOTE_FIELDS = ('topic', 'choice', 'why')


def note(project, topic, choice, why, evidence=None):
    """The agent's own decision, recorded (decisions/agent_log.jsonl) - not an approval: no gate reads it; the report
    and the run metrics do. One line each: what was decided, the choice, why, and the evidence (a file) if any."""
    path = project_dir(project)
    values = {'topic': topic, 'choice': choice, 'why': why}
    short = [k for k, v in values.items() if not isinstance(v, str) or len(v.strip()) < 2]
    if short:
        raise StudioError('INPUT_INVALID', f'decide note needs {short} (a few words each)')
    if evidence and not (path / evidence).exists() and not Path(evidence).exists():
        raise StudioError('INPUT_INVALID', f'evidence {evidence!r} does not exist')
    log = _root(path) / 'agent_log.jsonl'
    log.parent.mkdir(parents=True, exist_ok=True)
    with lock(log):
        count = len(log.read_text().splitlines()) if log.is_file() else 0
        entry = {'n': count + 1, 'at': now(), **{k: v.strip() for k, v in values.items()}, **({'evidence': evidence} if evidence else {})}
        with log.open('a') as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + '\n')
    return entry


def notes(project):
    log = _root(project_dir(project)) / 'agent_log.jsonl'
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()] if log.is_file() else []


def register_commands(subparsers):
    parser = subparsers.add_parser('decide', help='The decision ladder: brief, facts, script, shot list, look - settled with the user')
    commands = parser.add_subparsers(dest='decide_command', required=True)
    p = commands.add_parser('status'); p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: status(a.project))
    p = commands.add_parser('propose'); p.add_argument('--project', required=True); p.add_argument('--layer', required=True, choices=list(LAYERS))
    p.add_argument('--body', required=True, help='JSON (text or a .json file) draft of the layer body (schemas/studio-v1/decision.schema.json)')
    p.set_defaults(handler=lambda a: propose(a.project, a.layer, a.body))
    p = commands.add_parser('revise'); p.add_argument('--project', required=True); p.add_argument('--layer', required=True, choices=list(LAYERS))
    p.add_argument('--user-words', required=True); p.add_argument('--ops', help='JSON (text or a .json file) list of {op, path, value}'); p.add_argument('--body')
    p.add_argument('--agent-note')
    p.set_defaults(handler=lambda a: revise(a.project, a.layer, a.user_words, a.ops, a.body, a.agent_note))
    p = commands.add_parser('approve'); p.add_argument('--project', required=True); p.add_argument('--layer', required=True, choices=list(LAYERS))
    p.add_argument('--user-words', required=True); p.add_argument('--sheet', required=True, help='The sheet revision the user saw (rNN)')
    p.set_defaults(handler=lambda a: approve(a.project, a.layer, a.user_words, a.sheet))
    p = commands.add_parser('write', help="Off the ladder: a layer settled in the user's words, or by a delegated run's agent "
                                          '(--agent-note); lint errors keep it a draft')
    p.add_argument('--project', required=True); p.add_argument('--layer', required=True, choices=list(LAYERS))
    p.add_argument('--body', required=True, help='JSON (text or a .json file) layer body'); p.add_argument('--user-words'); p.add_argument('--agent-note')
    p.set_defaults(handler=lambda a: write(a.project, a.layer, a.body, a.user_words, a.agent_note))
    p = commands.add_parser('show'); p.add_argument('--project', required=True); p.add_argument('--layer', required=True, choices=list(LAYERS))
    p.set_defaults(handler=lambda a: {**(envelope(a.project, a.layer) or {}), 'state': state(a.project, a.layer)})
    p = commands.add_parser('adopt'); p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: adopt(a.project))
    p = commands.add_parser('note', help="Record a decision you made yourself (not an approval): topic, choice, why")
    p.add_argument('--project', required=True); p.add_argument('--topic', required=True); p.add_argument('--choice', required=True)
    p.add_argument('--why', required=True); p.add_argument('--evidence')
    p.set_defaults(handler=lambda a: note(a.project, a.topic, a.choice, a.why, a.evidence))
    p = commands.add_parser('notes'); p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: {'notes': notes(a.project)})
