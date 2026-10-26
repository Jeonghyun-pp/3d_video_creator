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
        room.append(f"{element['what']} is in the picture, where its path begins ({element['path']}), lit and placed as part of the scene")
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


VIDEO = ('{look}. The video starts exactly on the attached picture and keeps it: the same place, the same cut-open faces and '
         'layers, the same objects in the same places, the same light. {motion} Nothing else moves, nothing new appears, nothing '
         'melts or changes shape. No text, no letters, no labels, no logos.{ends}')


def video(cut, body):
    """The paid video's prompt (image to video, its first frame the approved first picture): what moves and how, said
    from the cut as planned, and where it ends (the last picture's words). The marks (labels, flows, specks) are drawn
    over the video afterwards, never asked of the model."""
    change, cam = cut['change'], cut['cutaway']['camera']
    said = []
    from .flow import focus_parts
    words = {p['id']: p['words'] for p in cut['cutaway']['parts']}
    toward = ' and '.join(words[p] for p in focus_parts(cut)[:2] if p in words) or 'the middle of the picture'
    if cam['move'] == 'push':
        said.append(f"The camera pushes in slowly and steadily toward {toward}, about {cam['amount']:.0%} closer.")
    elif cam['move'] == 'pull':
        said.append(f"The camera pulls back slowly and steadily, about {cam['amount']:.0%} further.")
    else:
        said.append('The camera holds still.')
    element = change.get('element') or {}
    if change['kind'] == 'state' and change.get('what'):
        said.append(f"Over the shot: {change['what'].rstrip('.')}.")
    elif element.get('drawn') == 'picture':
        said.append(f"{element['what'][:1].upper() + element['what'][1:]} moves {element['path'].rstrip('.')}, smoothly, as part of the scene.")
    elif change['kind'] == 'move' and change.get('what') and not element:
        said.append(f"{change['what'][:1].upper() + change['what'][1:].rstrip('.')}.")
    last = (cut['frames'].get('last') or {}).get('shows')
    ends = f" It ends on: {last.rstrip('.')}." if last and (change['kind'] == 'state' or element.get('drawn') == 'picture') else ''
    return VIDEO.format(look=body['look'].rstrip('.'), motion=' '.join(said), ends=ends)
