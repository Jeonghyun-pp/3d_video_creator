"""A cut's picture prompts, built from the cut as planned (the cuts layer), the reel's look line and the place chain.

- first: a new picture - the look, no text, the scene from the cut's cutaway (what is opened and how, from where, which
  parts and how many), what the first picture shows, and room left where the flows and the moving thing will be drawn
  (they are drawn, never painted). A cut at a place an earlier cut showed takes that cut's first picture as its place
  reference (the chain); the reel's first picture is every other cut's style reference.
- last: an edit of the picked first that changes only what the cut's change says (a state change).
- middle: an edit of the picked first halfway through that change, when the cut plans one.
- sprite: a moving thing drawn on its own, on a flat key colour, to be moved by the renderer (change.element drawn sprite).
A try that a check found wrong is made again with what was wrong (`reason`).
"""
from __future__ import annotations

HEAD = ('Generate an image. Vertical 9:16 portrait image (1080x1920 aspect). {look}. Absolutely no text, no letters, no numbers, '
        'no labels, no logos, no watermark anywhere in the image.')
PLACE_REF = ('Use the attached image only as a place and style reference: the same {place}, the same look and light. Draw a new '
             'picture of it as described, not a copy of the reference.')
STYLE_REF = 'Use the attached image only as a style reference (its look and light); the place and what it shows are as described.'
EDIT = ('Edit the attached image. Keep everything exactly the same - same composition, same camera, same framing, same objects in the '
        'same positions, same light. Change only one thing: {what}{halfway}. {shows} No text, no watermark. Same size and aspect as the input.')
SPRITE = ('Generate an image. A single {what}, alone, centred, filling about half of the frame, on a plain flat pure green background '
          '(#00FF00) with no shadow, no floor and nothing else. {look}. No text, no watermark.')
KEY_COLOUR = (0, 255, 0)
STEP_WORDS = {'first': '', 'wider': 'One scale step wider than before: ', 'closer': 'One scale step closer than before: ', 'same': ''}
# With a reference attached, the model copies the reference's framing unless told otherwise (Phase 0 probe, 2026-10-26:
# 6 of 9 new framings of one place came back as the reference's own view; told "not the reference's view", the ceiling
# and the worker close-ups came out close, the place kept). Said from the reference cut's scale to this cut's.
FROM_REFERENCE = {
    'closer': "This is NOT the reference's view: the camera is much closer - {what} fills the frame.",
    'wider': "This is NOT the reference's view: the camera is much further back - {what} fills the frame.",
    'same': "Frame it as described here, not as the reference is framed.",
}


def _parts(cut):
    return '; '.join((f"exactly {p['count']} " if p.get('count') else '') + p['words'] for p in cut['cutaway']['parts'])


def step_from(reference_scale, scale, table):
    """closer | wider | same: this cut's scale against the reference cut's (the grammar's scales, widest first)."""
    order = table['scales']['order']
    if reference_scale not in order or scale not in order:
        return 'same'
    a, b = order.index(reference_scale), order.index(scale)
    return 'same' if a == b else 'closer' if b > a else 'wider'


def first(cut, body, table, place_ref=None, style_ref=None, reason=None, reference_scale=None):
    """(prompt, inputs) of a cut's first picture. reference_scale: the scale of the cut the reference picture is of."""
    from .grammar import row
    cutaway = cut['cutaway']
    place = next(p for p in body['places'] if p['id'] == cut['place'])
    opens = (row('opens', cutaway['opens'], table) or {}).get('prompt') or ''
    view = (row('view', cutaway['view'], table) or {}).get('prompt') or ''
    room = [f"leave the way of {f['what']} ({f['path']}) visible and uncluttered - it is drawn over the picture later" for f in cutaway['flow']]
    element = cut['change'].get('element')
    if element and element['drawn'] == 'picture':   # a thing of the scene: painted where it starts; the last picture moves it
        # large and clear with room ahead: a video model moves a big, plain subject and smears a small one (research
        # 2026-10-27: OpenArt, Runway; fab c04's far box blurred into the rail)
        room.append(f"{element['what']} is in the picture, where its path begins ({element['path']}), lit and placed as part of the scene, "
                    'large and clear in the frame, with open room ahead of it the way it will move')
    elif element:
        room.append(f"do not draw {element['what']} - it is drawn and moved over the picture later")
    words = [HEAD.format(look=body['look'].rstrip('.'))]
    inputs = []
    if place_ref:
        words.append(PLACE_REF.format(place=place['words']))
        inputs.append(place_ref)
    elif style_ref:
        words.append(STYLE_REF)
        inputs.append(style_ref)
    step = STEP_WORDS.get(cutaway['scale_step'], '')
    what = cutaway['what'] if step else cutaway['what'][:1].upper() + cutaway['what'][1:]
    scene = f"Scene, the place: {place['words']}. {step}{what}"
    words.append(scene.rstrip('.') + '.')
    if opens:
        words.append(f'It is {opens}.')
    if view:
        words.append(f'{view[0].upper()}{view[1:]}.')
    if place_ref or style_ref:
        words.append(FROM_REFERENCE[step_from(reference_scale, cut['scale'], table)].format(what=what[:1].lower() + what[1:]))
    if cutaway['parts']:
        words.append(f"It shows: {_parts(cut)}.")
    words.append(f"The picture: {cut['frames']['first']['shows'].rstrip('.')}.")
    moving = (row('camera.move', cutaway['camera']['move'], table) or {}).get('picture')
    if moving:   # what the camera's move needs in the picture (a ride: the way ahead open down the middle)
        room.append(moving)
    if room:
        said = '; '.join(room)
        words.append(said[:1].upper() + said[1:] + '.')
    if reason:
        words.append(f'The last try was wrong: {reason.rstrip(".")}. Correct that.')
    return ' '.join(words), inputs


def edit(cut, frame, first_picture, reason=None):
    """(prompt, inputs) of a cut's middle or last picture: the picked first, edited."""
    change = cut['change']
    shows = cut['frames'][frame]['shows'].rstrip('.')
    prompt = EDIT.format(what=change.get('what') or shows, halfway=' - halfway, not all the way' if frame == 'middle' else '',
                         shows=f'The result: {shows}.')
    if reason:
        prompt += f' The last try was wrong: {reason.rstrip(".")}. Correct that.'
    return prompt, [first_picture]


def sprite(cut, body, reason=None):
    element = cut['change']['element']
    prompt = SPRITE.format(what=element['what'], look=body['look'].rstrip('.'))
    if reason:
        prompt += f' The last try was wrong: {reason.rstrip(".")}. Correct that.'
    return prompt, []


# --- the paid video's prompt ----------------------------------------------------------------------------------------
# Image to video: the first picture already holds the place, the look and the light, so the prompt says only what moves,
# in this order - the camera (one named move from its grammar row, where it ends and a hold), the one thing that moves
# (the cut's change), what must hold while it moves said as a positive sentence (the cut's video.holds: a grip, a
# contact, a shape), and "Single continuous shot." What to avoid is nouns in the model's negative prompt, never a "no"
# sentence. Why (research of 2026-10-27, scratchpad report; Vertex video best practices and prompt guide, Runway Gen-4,
# Gigwise's camera library, Gemini Omni): several events in one clip come out "muddled or incomplete"; negative phrasing
# "may produce unpredictable or even opposite results"; a move without an end framing loses force half way (fab c03's
# "about 10% closer" pushed in and drifted back out); a camera told two moves at once drifts. fab c04 asked four moves and
# a push of one 4 s clip and got a blurred swing; every prompt carried ~50 words of look and "nothing else moves" lines.
ACTION_S = 4.0            # one subject action per this many seconds of clip (Vertex: one event per 4-6 s clip)
CONTINUOUS = 'Single continuous shot.'
NEGATIVE_BASE = ('text', 'captions', 'letters', 'labels', 'logos', 'watermark', 'camera cut', 'scene change', 'morphing', 'melting',
                 'warped shapes', 'flicker')
# English's closed classes, not cases: sequence words that start a further step, and negators.
SEQUENCE = r'\b(then|afterwards|after that|finally|again|once more|in turn|back and forth|there and back|and back|one after another|repeatedly)\b'
NEGATION = r"\b(no|not|never|nothing|none|nobody|nowhere|neither|nor|without|cannot|can't|don't|doesn't|won't|isn't|aren't)\b"
PERCENT = r'\d+(?:\.\d+)?\s*(?:%|percent\b|per cent\b)'
FIX = {
    'VIDEO_PROMPT_ACTIONS': 'one thing moves once per {per:g} s of clip: split the steps into cuts (a leg each), or say the one step this '
                            'cut shows',
    'VIDEO_PROMPT_CAMERA': 'one camera move per clip, named once: keep the row\'s move (camera.move) and take the other out of the words',
    'VIDEO_PROMPT_PERCENT': 'name the move and where it ends (camera.ends: "a close-up of the valve"), never a share of the distance',
    'VIDEO_PROMPT_NEGATION': 'say what holds as a positive sentence ("stays clamped", "remains still"); put what to avoid as nouns in '
                             'video.avoid (the negative prompt)',
    'VIDEO_PROMPT_ORDER': 'the camera\'s sentence first, then what moves, then what holds, ending "Single continuous shot."',
    'VIDEO_NEGATIVE_NOUNS': 'the negative prompt is a list of short nouns ("swinging box, extra wafers"), never a sentence or a "no"',
}


def _sentence(text):
    text = ' '.join(text.split()).rstrip('.')
    return text[:1].upper() + text[1:] + '.' if text else ''


def said(hold):
    """A hold's words: a sentence, or the `says` of a hold that names its pair (held, holder, kind - the check measures it)."""
    return hold['says'] if isinstance(hold, dict) else hold


def _camera_row(cut, table):
    from .grammar import row
    move = cut['cutaway']['camera']['move']
    return row('camera.move', move, table) or {}


def video(cut, body, table=None):
    """The paid video's prompt (image to video, its first frame the approved first picture), in the order above. The marks
    (labels, flows, specks) are drawn over the video afterwards, never asked of the model."""
    from .flow import focus_parts
    from .grammar import load, row
    table = table or load()
    change, cam = cut['change'], cut['cutaway']['camera']
    words = {p['id']: p['words'] for p in cut['cutaway']['parts']}
    toward = ' and '.join(words[p] for p in focus_parts(cut)[:2] if p in words) or 'the middle of the picture'
    says = _camera_row(cut, table).get('video') or {}
    ends = (cam.get('ends') or says.get('ends') or '').format(toward=toward)
    camera = says.get('says', 'The locked-off camera remains perfectly still.').format(
        toward=toward, ends=ends, along=' '.join((cam.get('along') or '').split()), ending=f', ending on {ends}' if ends else '')
    lead = (row('view', cut['cutaway']['view'], table) or {}).get('video')
    sentences = [f'{lead}: {camera[:1].lower()}{camera[1:]}' if lead else camera]
    element = change.get('element') or {}
    if element.get('drawn') == 'picture':          # a thing of the scene: the model moves it (specks and sprites are ours)
        sentences.append(_sentence(f"{element['what']} moves steadily {element['path'].rstrip('.')}"))
    elif change['kind'] == 'state' and change.get('what'):
        sentences.append(_sentence(f"over the shot, {change['what'].rstrip('.')}"))
    elif change['kind'] == 'move' and change.get('what') and not element:
        sentences.append(_sentence(change['what']))
    sentences += [_sentence(said(h)) for h in (cut.get('video') or {}).get('holds') or []]
    sentences.append(CONTINUOUS)
    return ' '.join(s for s in sentences if s)


def video_negative(cut, body, table=None):
    """What the video must avoid, as nouns for the model's negative prompt: the base, the camera row's, the cut's own."""
    from .grammar import load
    table = table or load()
    said = list(NEGATIVE_BASE) + list((_camera_row(cut, table).get('video') or {}).get('avoid') or []) + list((cut.get('video') or {}).get('avoid') or [])
    return ', '.join(dict.fromkeys(' '.join(n.split()).strip(' ,.') for n in said if n.strip(' ,.')))


def _camera_terms(table):
    """Each move's words: one group per camera row (its terms), one per named move no row makes (other_terms)."""
    spec = table['fields']['camera.move']
    groups = [(name, [t.lower() for t in (r.get('video') or {}).get('terms') or []]) for name, r in spec['values'].items()]
    return [g for g in groups if g[1]], [(t, [t.lower()]) for t in spec.get('other_terms') or []]


def video_lint(text, seconds, table=None):
    """What the paid video's prompt is refused for (findings 'CODE: what - how to fix'): more subject actions than the
    clip holds (one per ACTION_S, steps counted by sequence words), more than one camera move, a percentage, a negative
    sentence, the camera not first or the end not "Single continuous shot."."""
    import re
    from .grammar import load
    table = table or load()
    low = ' '.join(text.split()).lower()
    out = []
    allowed = max(1, int(seconds / ACTION_S + 1e-9))
    steps = 1 + len(re.findall(SEQUENCE, low))
    if steps > allowed:
        out.append(f"VIDEO_PROMPT_ACTIONS: {steps} steps ({', '.join(m.group(0) for m in re.finditer(SEQUENCE, low))}) in a {seconds:g} s "
                   f"clip that holds {allowed} - " + FIX['VIDEO_PROMPT_ACTIONS'].format(per=ACTION_S))
    rows, others = _camera_terms(table)
    taken = _named(low, rows + others)
    moves = list(dict.fromkeys(name for _a, _b, name in sorted(taken)))
    if len(moves) > 1:
        out.append(f"VIDEO_PROMPT_CAMERA: {len(moves)} camera moves ({', '.join(moves)}) - " + FIX['VIDEO_PROMPT_CAMERA'])
    found = re.findall(PERCENT, low)
    if found:
        out.append(f"VIDEO_PROMPT_PERCENT: a camera distance as {', '.join(found)} - " + FIX['VIDEO_PROMPT_PERCENT'])
    denied = [s.strip() for s in re.split(r'(?<=[.;:!?])\s+', ' '.join(text.split())) if re.search(NEGATION, s.lower())]
    if denied:
        out.append(f"VIDEO_PROMPT_NEGATION: {' | '.join(denied)} - " + FIX['VIDEO_PROMPT_NEGATION'])
    flat = ' '.join(text.split())
    first_end = len(re.split(r'(?<=[.;!?])\s+', flat)[0])
    row_names = {name for name, _ in rows}
    wrong = ([] if any(a < first_end and name in row_names for a, _b, name in taken) else ['the first sentence names no camera move']) \
        + ([] if flat.endswith(CONTINUOUS) else [f'it does not end "{CONTINUOUS}"'])
    if wrong:
        out.append(f"VIDEO_PROMPT_ORDER: {'; '.join(wrong)} - " + FIX['VIDEO_PROMPT_ORDER'])
    return out


def _named(low, groups):
    """[(start, end, move)] of each move term in lowered text, a longer term first taking its words."""
    import re
    taken = []
    for name, terms in groups:
        for term in terms:
            for m in re.finditer(rf'\b{re.escape(term)}\b', low):
                if not any(a < m.end() and m.start() < b for a, b, _ in taken):
                    taken.append((m.start(), m.end(), name))
    return taken


def camera_said(text, table=None):
    """The camera a prompt names, read back from its words: {move} when exactly one camera row's terms are in it (no
    move phrase of another), with its `amount` from a percentage where the row's amount is the picture's growth
    (measured.amount); {} when it names none or more than one - unknown, never guessed. For a clip made before what
    it was asked was kept with it (video.asked_from_request)."""
    import re
    from .grammar import load
    table = table or load()
    low = ' '.join(text.split()).lower()
    rows, others = _camera_terms(table)
    moves = list(dict.fromkeys(name for _a, _b, name in sorted(_named(low, rows + others))))
    row_names = {name for name, _ in rows}
    if len(moves) != 1 or moves[0] not in row_names:
        return {}
    out = {'move': moves[0]}
    measured = table['fields']['camera.move']['values'][moves[0]].get('measured') or {}
    found = re.findall(r'(\d+(?:\.\d+)?)\s*(?:%|percent\b|per cent\b)', low)
    if measured.get('amount') and len(found) == 1:
        out['amount'] = round(float(found[0]) / 100, 4)
    return out


def negative_lint(negative):
    """The negative prompt is nouns: an item with a negator or more than four words is a sentence."""
    import re
    bad = [n.strip() for n in negative.split(',') if n.strip() and (re.search(NEGATION, n.lower()) or len(n.split()) > 4)]
    return [f"VIDEO_NEGATIVE_NOUNS: {' | '.join(bad)} - " + FIX['VIDEO_NEGATIVE_NOUNS']] if bad else []
