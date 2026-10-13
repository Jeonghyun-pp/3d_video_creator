"""How a moment of a shot shows what it explains - decided in the shot list, before any picture is drawn, and followed
by the picture, the scene and the camera. Pure Python (host and Blender).

Why (2026-10-13): the camera turned 10-20 degrees from its picture in 38 of 296 moments of the rocket reel, hunting for
an angle at which an engine inside the booster showed at all. How to show a hidden part was decided last, by the camera,
and its only tool was the angle. The user: "애초에 그림 그릴 때부터 단면을 그리면 되는 거 아니냐, 순서가 반대인가?" -
so the order is: the lines -> what each moment shows and how (here) -> the picture -> the scene -> the camera.

One table, SHOW_METHODS: a new way of showing is one more row - its words for the picture (`draw`) and for reading one
(`read`), what the scene builds for it (`opens`), how long it takes to open and, for see-through skins, how see-through
they are.
"""
from __future__ import annotations

# opens: None (the outside as it is) | 'cut' (a boolean cutter: studio/blender_ops/reveal.py) | 'explode' (the parts in
# front move away) | 'see_through' (the parts in front fade to a see-through skin). extent (cuts): how much is cut away.
SHOW_METHODS = {
    'exterior': {'read': 'from outside, as it is', 'opens': None, 'open_s': 0.0,
                 'draw': 'the subject from outside, as it is; what this moment explains shows on its outer surface'},
    'section': {'read': 'cut through by a flat plane, the cut faces show the inside', 'opens': 'cut', 'extent': 'subject', 'open_s': 1.0,
                'draw': 'the subject cut through by a flat plane facing the camera: the front part is gone and the cut faces '
                        'show what is inside - the parts named below clearly visible on the cut'},
    'cutaway': {'read': 'a window cut into its skin', 'opens': 'cut', 'extent': 'parts', 'open_s': 1.0,
                'draw': 'the subject whole, with a window cut into its skin just where the parts named below sit, so they '
                        'show through the opening'},
    'interior': {'read': 'the camera inside it', 'opens': 'cut', 'extent': 'passage', 'open_s': 1.0,
                 'draw': 'the camera inside the subject, among the parts named below; the opening it came through behind it'},
    'explode': {'read': 'parts moved apart', 'opens': 'explode', 'open_s': 1.0,
                'draw': 'the subject taken apart along its axis: the parts in front moved away so the parts named below show'},
    'xray': {'read': 'the skin a faint outline, the inside solid', 'opens': 'see_through', 'open_s': 0.8, 'alpha': 0.85, 'rim': True,
             'draw': 'an x-ray view: the outer skin only as a faint glowing outline, the parts named below solid and bright inside'},
    'transparency': {'read': 'the skin half see-through', 'opens': 'see_through', 'open_s': 0.8, 'alpha': 0.55, 'rim': False,
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


def how_choices():
    """The reader's choices for `how`, from the table: 'exterior (from outside, as it is) | section (...) | ...'."""
    return ' | '.join(f"{how} ({row['read']})" for how, row in SHOW_METHODS.items())


def opening_methods():
    """The methods that show what the outside hides - every row that opens, so a new row joins without a code change."""
    return [how for how in METHODS if opens(how)]


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


# words of an earlier free-text hand-over that say its kind (English and Korean); text saying none of them is no kind
HANDOVER_WORDS = {'match_action': (r'\bmatch', '매치', '동작을 이어'), 'continue': (r'(?<!dis)continu', r'\bcarr(y|ies) on', '이어받', '이어서', '이어지', '연속'),
                  'contrast': (r'\bcontrast', '대비', '반대로'), 'new_place': (r'\bnew place', '다른 장소', '장소 이동')}


def legacy_handover(direction):
    """The hand-over of a shot list written before handover existed: its free-text direction.transition, when its words
    say a kind (HANDOVER_WORDS, first match in that order); else None - the kind is unknown, not 'new place'."""
    import re
    text = ((direction or {}).get('transition') or '').strip()
    if not text:
        return None
    for kind, words in HANDOVER_WORDS.items():
        if any(re.search(w, text.lower()) for w in words):
            return {'kind': kind, 'why': text}
    return None


def show_of(entry):
    """A shot list entry's (or a shot's) show, its own or read from an earlier shot list's direction.reveal."""
    return entry['show'] if entry.get('show') is not None else legacy_show(entry.get('direction'))


def handover_of(entry):
    return entry['handover'] if entry.get('handover') is not None else legacy_handover(entry.get('direction'))


def moment_at(show, share, tolerance=0.02):
    """The show row of the moment at `share` of the shot (a picture's `at`), or None."""
    return next((row for row in show or () if abs(at_share(row['at']) - share) <= tolerance), None)


def held_at(show, share, tolerance=0.02):
    """How the shot shows at `share`: its own row, else the last row before it - an opening stays open until a later
    moment shows the outside again, as the build holds it (openings). None before the first row.
    Why (2026-10-13): a shot that opens at the start and names no end was briefed and checked 'exterior' at its end."""
    rows = sorted((r for r in show or () if at_share(r['at']) <= share + tolerance), key=lambda r: at_share(r['at']))
    return rows[-1] if rows else None


def same_view(a, b):
    """Two ways of showing frame the same: they open the same way over the same extent (xray and transparency both see
    through the skin; a section and a cutaway open different extents). A row added to the table joins by its fields."""
    return all(SHOW_METHODS[a].get(k) == SHOW_METHODS[b].get(k) for k in ('opens', 'extent'))


# ---- the opening a moment needs (section / cutaway / interior / explode) - pure geometry, used by show_scene.py --------
CUT_MARGIN = 0.15        # a cutter reaches this share past what it must open, so no sliver of skin is left at its rim


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def corners(box):
    """The 8 corners of an axis-aligned box ((x0, y0, z0), (x1, y1, z1))."""
    (x0, y0, z0), (x1, y1, z1) = box
    return [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]


def span(points, axis):
    values = [_dot(p, axis) for p in points]
    return min(values), max(values)


def cutter(extent, parts_box, subject_box, toward, right, up, clearance_m=0.0):
    """Where a box cutter goes so a picture's view (toward: from the subject to the camera; right, up: the picture's
    screen axes) shows the parts in parts_box: its cut plane faces the camera and passes through the parts' middle -
    their front half is cut away and the cut faces show them - and it reaches past the subject towards the camera.
    extent: 'subject' (a section: the whole subject's width), 'parts' (a cutaway: a window around the parts), 'passage'
    (interior: a window the camera fits through). -> {'centre', 'half': (right, up, toward)} in world units."""
    parts, whole = corners(parts_box), corners(subject_box)
    near = _dot([(a + b) / 2 for a, b in zip(*parts_box)], toward)      # the cut plane: through the parts' middle
    far = span(whole, toward)[1]
    depth = max(far - near, 1e-3) * (1 + CUT_MARGIN)
    lateral = whole if extent == 'subject' else parts
    halves = []
    for axis in (right, up):
        lo, hi = span(lateral, axis)
        half = (hi - lo) / 2 * (1 + CUT_MARGIN)
        if extent == 'passage':
            half = max(half, clearance_m * 1.5)
        halves.append(((lo + hi) / 2, max(half, 1e-3)))
    centre_t = near + depth / 2
    centre = [right[i] * halves[0][0] + up[i] * halves[1][0] + toward[i] * centre_t for i in range(3)]
    return {'centre': centre, 'half': (halves[0][1], halves[1][1], depth / 2)}


def openings(rows):
    """Merge a shot's moments into openings: consecutive moments that open the same parts the same way are one
    opening, open from the first to the last; [{how, parts, why, first, last, closes}] where first/last index rows and
    closes says whether a later moment shows the outside again (the opening closes before it)."""
    out = []
    for n, row in enumerate(rows):
        if row is None or not opens(row['how']):   # an outside moment shuts every opening still open
            for o in out:
                if o['closes'] is None:
                    o['closes'] = n
            continue
        last = out[-1] if out else None
        if last and last['last'] == n - 1 and last['how'] == row['how'] and sorted(last['parts']) == sorted(row['parts']):
            last['last'] = n
            continue
        # another opening right after: the first stays open (the next takes over, it does not shut and reopen)
        out.append({'how': row['how'], 'parts': list(row['parts']), 'why': row['why'], 'first': n, 'last': n, 'closes': None})
    return out
