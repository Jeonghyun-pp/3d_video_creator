"""How a moment of a shot shows what it explains - decided in the shot list, before any picture is drawn, and followed
by the picture, the scene and the camera. Pure Python (host and Blender).

Why (2026-10-13): the camera turned 10-20 degrees from its picture in 38 of 296 moments of the rocket reel, hunting for
an angle at which an engine inside the booster showed at all. How to show a hidden part was decided last, by the camera,
and its only tool was the angle. The user: "애초에 그림 그릴 때부터 단면을 그리면 되는 거 아니냐, 순서가 반대인가?" -
so the order is: the lines -> what each moment shows and how (here) -> the picture -> the scene -> the camera.

One table, SHOW_METHODS: a new way of showing is one more row - its words for the picture, what the scene builds for it
(`opens`), how long it takes to open and, for see-through skins, how see-through they are.
"""
from __future__ import annotations

# opens: None (the outside as it is) | 'cut' (a boolean cutter: studio/blender_ops/reveal.py) | 'explode' (the parts in
# front move away) | 'see_through' (the parts in front fade to a see-through skin). extent (cuts): how much is cut away.
SHOW_METHODS = {
    'exterior': {'opens': None, 'open_s': 0.0,
                 'draw': 'the subject from outside, as it is; what this moment explains shows on its outer surface'},
    'section': {'opens': 'cut', 'extent': 'subject', 'open_s': 1.0,
                'draw': 'the subject cut through by a flat plane facing the camera: the front part is gone and the cut faces '
                        'show what is inside - the parts named below clearly visible on the cut'},
    'cutaway': {'opens': 'cut', 'extent': 'parts', 'open_s': 1.0,
                'draw': 'the subject whole, with a window cut into its skin just where the parts named below sit, so they '
                        'show through the opening'},
    'interior': {'opens': 'cut', 'extent': 'passage', 'open_s': 1.0,
                 'draw': 'the camera inside the subject, among the parts named below; the opening it came through behind it'},
    'explode': {'opens': 'explode', 'open_s': 1.0,
                'draw': 'the subject taken apart along its axis: the parts in front moved away so the parts named below show'},
    'xray': {'opens': 'see_through', 'open_s': 0.8, 'alpha': 0.85, 'rim': True,
             'draw': 'an x-ray view: the outer skin only as a faint glowing outline, the parts named below solid and bright inside'},
    'transparency': {'opens': 'see_through', 'open_s': 0.8, 'alpha': 0.55, 'rim': False,
                     'draw': 'the outer skin half see-through like tinted glass, the parts named below seen through it'},
}
METHODS = tuple(SHOW_METHODS)
# The shot list's earlier word for it (direction.reveal): read as the method it meant; the others show nothing inside.
LEGACY_REVEAL = {'section_cut': 'section', 'explode': 'explode', 'xray': 'xray', 'transparency': 'transparency'}

# How a shot hands over to the next. continue: the same subject and motion run on across the cut; match_action: one
# action cut in two; contrast: a deliberate change (size, side, pace); new_place: another subject or place.
HANDOVER_KINDS = ('continue', 'match_action', 'contrast', 'new_place')
CONTINUOUS = ('continue', 'match_action')

# A part a moment shows must be at least this thick on screen, as a share of the output height, unless the shot list
# names its own min_px: 1920 px tall -> 32 px, what the rocket reel needed for an engine bell to read (s15, 2026-10-12).
SHOW_MIN_SHARE = 32 / 1920


def opens(how):
    return SHOW_METHODS[how]['opens']


def min_px(height, own=None):
    """The thickness a shown part needs, in output pixels (at least the schema's 8)."""
    return max(8, int(own if own is not None else round(SHOW_MIN_SHARE * height)))


def at_share(at):
    """'start' -> 0.0, 'end' -> 1.0, a share as it is."""
    return {'start': 0.0, 'end': 1.0}.get(at, at) if isinstance(at, str) else float(at)


def legacy_show(direction):
    """The show of a shot list written before show existed: its direction.reveal at the end of the shot (no parts named)."""
    how = LEGACY_REVEAL.get(((direction or {}).get('reveal') or 'none'))
    return [{'at': 'end', 'parts': [], 'how': how, 'why': 'direction.reveal of an earlier shot list'}] if how else []


def legacy_handover(direction):
    """The hand-over of a shot list written before handover existed: its free-text direction.transition."""
    text = ((direction or {}).get('transition') or '').strip()
    if not text:
        return None
    low = text.lower()
    kind = 'match_action' if 'match' in low else 'continue' if 'continu' in low else 'new_place'
    return {'kind': kind, 'why': text}


def show_of(entry):
    """A shot list entry's show, its own or read from an earlier shot list."""
    return entry['show'] if entry.get('show') is not None else legacy_show(entry.get('direction'))


def handover_of(entry):
    return entry['handover'] if entry.get('handover') is not None else legacy_handover(entry.get('direction'))


def moment_at(show, share, tolerance=0.02):
    """The show row of the moment at `share` of the shot (a picture's `at`), or None."""
    return next((row for row in show or () if abs(at_share(row['at']) - share) <= tolerance), None)
