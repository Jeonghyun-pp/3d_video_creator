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
# A part a moment shows that stands in front of another part it shows (a sleeve round its pipe, both named) is never cut
# or moved by an opening - it fades to this see-through skin instead, so both show (show_scene.apply). min_share: of the
# hidden part's samples it must stand in front of to be faded (a stray edge hides nothing). Why (2026-10-17): a held
# part hid the part beside it whole and the shot failed SHOW_NOT_OPENING; the user: "반투명한것도 보이는거 아니야..?"
SHOWN_THROUGH = {'alpha': 0.45, 'rim': False, 'min_share': 0.05,
                 'draw': 'where one part named below stands in front of another named part, draw the front one half '
                         'see-through, so both show'}
# A medium the world declares (world element `medium`: water a hull floats in, glass, a haze): drawn see-through over
# the whole shot like the transparency row, and no ray stops at it (camera_frame.passes_through). Why (2026-10-20, canal):
# the chamber's water boxes, glass in look, were solid to the camera - they hid the hull and the floor the pictures drew
# through them, and 2285 refused views named them in front.
MEDIUM = {'alpha': SHOW_METHODS['transparency']['alpha'], 'rim': False}
# The shot list's earlier word for it (direction.reveal): read as the method it meant; the others show nothing inside.
LEGACY_REVEAL = {'section_cut': 'section', 'explode': 'explode', 'xray': 'xray', 'transparency': 'transparency'}

# How a shot hands over to the next. continue: the same subject and motion run on across the cut; match_action: one
# action cut in two; contrast: a deliberate change (size, side, pace); new_place: another subject or place.
HANDOVER_KINDS = ('continue', 'match_action', 'contrast', 'new_place', 'scale_through')   # scale_through: a dive into the next scale (scale_through_core)
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


def detail_min_px(shot, height):
    """{detail id: the thickness it needs} for the shot's screen.details: its own min_px, else min_px's default. The schema
    asks every detail for one; a shot read without the schema must not ask for 0 - with a see-through part counted as
    seen (SHOWN_THROUGH), 0 would let a hidden must-see pass (review 2026-10-17)."""
    return {d['id']: min_px(height, d.get('min_px')) for d in (shot.get('screen') or {}).get('details', [])}


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


def _groups(show):
    """The show rows grouped by moment: rows with the same `at` are one moment, in time order."""
    out = {}
    for row in show or ():
        out.setdefault(at_share(row['at']), []).append(row)
    return [out[k] for k in sorted(out)]


def _moment(group):
    """One moment's rows as the one row the build follows: the rows that open lead (one way of opening per moment; their
    parts joined), else the outside with every part named. The other rows' parts are what the moment also shows
    (moment_parts) - seen, never cut. Why (2026-10-26, semiconductor_fab s10): a cutaway of the wafers and two outside rows
    at `start` were read as three moments in turn, the outside one after the cutaway shut it, and the box never opened."""
    lead = [r for r in group if opens(r['how'])] or group
    first = lead[0]
    parts = list(dict.fromkeys(p for r in lead if r['how'] == first['how'] for p in r['parts']))
    return {**first, 'parts': parts}


def moment_rows(show):
    """A shot's moments in time order, one row each (_moment): what openings, the build and the briefs read."""
    return [_moment(g) for g in _groups(show)]


def split_moments(show):
    """[(at, [how, ...])] for each moment whose rows open in more than one way - one moment opens one way (_moment would
    keep only the first). SHOW_MOMENT_SPLIT in the shot list's lint."""
    return [(g[0]['at'], hows) for g in _groups(show) if len(hows := sorted({r['how'] for r in g if opens(r['how'])})) > 1]


def moment_at(show, share, tolerance=0.02):
    """The show row of the moment at `share` of the shot (a picture's `at`), or None."""
    return next((_moment(g) for g in _groups(show) if abs(at_share(g[0]['at']) - share) <= tolerance), None)


def _held_group(show, share, tolerance):
    groups = [g for g in _groups(show) if at_share(g[0]['at']) <= share + tolerance]
    return groups[-1] if groups else None


def held_at(show, share, tolerance=0.02):
    """How the shot shows at `share`: its own moment, else the last one before it - an opening stays open until a later
    moment shows the outside again, as the build holds it (openings). None before the first row.
    Why (2026-10-13): a shot that opens at the start and names no end was briefed and checked 'exterior' at its end."""
    group = _held_group(show, share, tolerance)
    return _moment(group) if group else None


def moment_parts(show, share, tolerance=0.02):
    """Every part the moment held at `share` shows - its leading row's and the rows beside it at the same `at`."""
    group = _held_group(show, share, tolerance)
    return list(dict.fromkeys(p for r in group or () for p in r['parts']))


def applied_at(rows, frame):
    """The opening a built scene shows at `frame`, from show_scene.apply's report rows ({how, parts, open: [start, on],
    close: [shut_from, shut] | None, opened}; a row with nothing in front of its parts carries open [on, on] and opens
    nothing): the last row fully open at that frame (on <= frame, before it starts to shut) -> {how, parts, opened},
    else None (the outside)."""
    found = None
    for row in sorted(rows or (), key=lambda r: r['open'][1]):
        close = row.get('close')
        if row['open'][1] <= frame and (not close or frame < close[0]):
            found = {'how': row['how'], 'parts': list(row.get('parts') or []), 'opened': list(row.get('opened', row.get('parts')) or [])}
    return found


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


def cutter(extent, part_boxes, subject_box, toward, right, up, clearance_m=0.0):
    """Where a box cutter goes so a picture's view (toward: from the subject to the camera; right, up: the picture's
    screen axes) shows the parts (part_boxes: one world box per opened part): its cut plane faces the camera and passes
    through the middle of the part whose middle lies deepest along the view - every opened part has its front half cut
    free and the cut faces show them - and it reaches past the subject towards the camera.
    Why the deepest part (A3-2, 2026-10-14): through the middle of the parts' joint box, a slanted view left the farther
    of two stacked tanks 94 % behind skin the cut never reached.
    extent: 'subject' (a section: the whole subject's width), 'parts' (a cutaway: a window around the parts), 'passage'
    (interior: a window the camera fits through). -> {'centre', 'half': (right, up, toward)} in world units."""
    parts, whole = [c for box in part_boxes for c in corners(box)], corners(subject_box)
    near = min(_dot([(a + b) / 2 for a, b in zip(*box)], toward) for box in part_boxes)   # the deepest part's middle
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


def held_parts(opening, shown):
    """What an opening must never cut, fade or move: the parts the moments it stays open through show (`shown`: per
    moment in order, the parts its show row names and its must-see) - from its first moment until the outside shows
    again (`closes`), or to the end when it is held open. A moment before it is pictured before the cut grows (the
    opening starts one frame after it), so what only that moment shows is the skin the opening is there to cut.
    Why (2026-10-17): every part any moment showed was kept, so a section after a closed exterior start never cut the
    body the start showed and left the tanks 95-100 % hidden; a later moment the opening is held open through still
    keeps its parts (review 2026-10-14: a held section cut the pipe the end moment shows)."""
    end = opening['closes'] if opening['closes'] is not None else len(shown)
    return sorted({p for n in range(opening['first'], end) for p in shown[n]})


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

FACING_MIN = 0.5   # an opened part must read on screen in at least this share of its opening's probed frames


def faces_away(frame_report, show_report, output_size):
    """[{code, text}] OPENING_FACES_AWAY: an opening whose parts read (at SHOW_MIN_SHARE of the height, as change and
    detail reads count) in under FACING_MIN of the probed frames of its window - the cut faces one way and the camera
    looks from another most of the shot. A part never read at all is KEY_PART_INVISIBLE's. Why (2026-10-23, r3 s13): a
    slide swept 160 deg round a hull cut open toward one side; its tanks showed edge on behind the hull most of the shot."""
    width, height = output_size
    frames = sorted((frame_report or {}).get('frames') or [], key=lambda r: r['frame'])
    last = max([r['frame'] for r in frames] or [0])
    out = []
    for row in (show_report or {}).get('openings') or []:
        if not opens(row.get('how')):
            continue
        start = (row.get('open') or [0, 0])[1]
        end = (row.get('close') or [last + 1])[0] if row.get('close') else last
        window = [r for r in frames if start <= r['frame'] <= end]
        if len(window) < 3:
            continue
        for part in row.get('parts') or []:
            def reads(r):
                box = (((r.get('shapes') or {}).get(f'key:{part}')) or {}).get('bbox')
                return bool(box) and min((box[2] - box[0]) * width, (box[3] - box[1]) * height) >= SHOW_MIN_SHARE * height
            read = sum(reads(r) for r in window)
            if 0 < read < FACING_MIN * len(window):
                out.append({'code': 'OPENING_FACES_AWAY', 'text': f"{part} reads in {read} of the {len(window)} probed frames its "
                            f"{row['how']} is open (frames {start}-{end}) - the camera looks from where the cut does not face most "
                            'of the time: sweep less round it, cut it open as a section, or split the shot into moments'})
    return out
