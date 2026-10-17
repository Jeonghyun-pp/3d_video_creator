"""What state a part of a built subject is in at one moment, from geometry alone (pure python: Blender measures the
points, studio/blender_ops/picture_passes.py; the host compares with what the narration claims, studio/picture3d.py).

The vocabulary is generic (STATES), so a landing leg, a grid fin, a drawbridge, a laptop lid and a robot's gripper
are judged by the same rules, never by their names:
  extended / retracted  how far the part reaches out from its owner's body, per length of the part (EXTENDED_SHARE,
                        RETRACTED_SHARE): a leg folded along a body reaches out by its thickness, one swung out by most
                        of its length
  contact / clear       how far the part's lowest point stands above the support under it (CONTACT_M)
  on / off              an element that renders at the moment (a flame, a light, a jet), none of it renders at all,
                        or unknown (it renders, out of frame); a part of a feature is on when something rendering
                        outside its subject is attached to it (a flame at a bell, a beam at a lamp: attach,
                        ATTACH_SHARE) or it glows itself - whether it is in frame or not (lit is the 3D's state, not
                        the camera's)
  open / closed         in the vocabulary (a reader may say it), not measured yet: a claim of it is never judged
Why (2026-10-16 pilot): pictures drawn from text disagreed with the 3D on 35 % of what was asked (fins deployed in the
picture and folded in the build, a foot on the deck in the picture and 3.7 m above it in the build); the camera can
never fix that, so the 3D's own state is read and compared with the narration's claim before a picture is painted.
"""
from __future__ import annotations

import math

STATES = ('extended', 'retracted', 'contact', 'clear', 'on', 'off', 'open', 'closed', 'unknown')
EXTENDED_SHARE = 0.35    # reaching out from the body by over a third of its own length: swung out (a deployed leg ~0.5+)
RETRACTED_SHARE = 0.15   # under a seventh: lying along the body (a folded leg's thickness over its length)
CONTACT_M = 0.02         # a lowest point within 2 cm of the support top touches it (SUBJECT_SINKS' tolerance, generate.py)
OPPOSITE = {'extended': 'retracted', 'retracted': 'extended', 'contact': 'clear', 'clear': 'contact', 'on': 'off', 'off': 'on',
            'open': 'closed', 'closed': 'open'}
KIND = {'extended': 'pose', 'retracted': 'pose', 'contact': 'contact', 'clear': 'contact', 'on': 'render', 'off': 'render',
        'open': 'open', 'closed': 'open'}   # a state is judged only against a measure of its own kind
MEASURE = {'pose': 'extension', 'contact': 'contact', 'render': 'render', 'open': 'open'}   # the state.json field of each kind
ATTACH_SHARE = 0.1      # something rendering within a tenth of a part's own length of it is attached to it (a plume
                        # starts at its bell's exit plane); each such thing to the one part whose centre is nearest
ATTACH_SIZE_MAX = 40.0  # ... and no longer than this many times the part's own length: a landing plume runs ~20 bells
                        # long; a glow that fills a room or a sky is no one part's light (review 2026-10-17)
ALIASES = {'deployed': 'extended', 'out': 'extended', 'unfolded': 'extended', 'folded': 'retracted', 'stowed': 'retracted',
           'in': 'retracted', 'touching': 'contact', 'landed': 'contact', 'above': 'clear', 'firing': 'on', 'lit': 'on',
           'burning': 'on', 'none': 'off', 'unlit': 'off'}   # words a reader or a person may use for the same states


def normal(word):
    """A state word in the vocabulary (an alias mapped), or 'unknown'."""
    w = str(word or '').strip().lower()
    w = ALIASES.get(w, w)
    return w if w in STATES else 'unknown'


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    return math.sqrt(_dot(a, a))


def _radial(p, origin, axis):
    d = _sub(p, origin)
    along = _dot(d, axis)
    return _norm((d[0] - axis[0] * along, d[1] - axis[1] * along, d[2] - axis[2] * along))


def principal_axis(points):
    """The direction of the points' longest spread (power iteration on their covariance), unit length; z when they are
    fewer than two."""
    if len(points) < 2:
        return (0.0, 0.0, 1.0)
    n = len(points)
    c = [sum(p[i] for p in points) / n for i in range(3)]
    cov = [[sum((p[i] - c[i]) * (p[j] - c[j]) for p in points) / n for j in range(3)] for i in range(3)]
    v = (0.0, 0.0, 1.0) if cov[2][2] >= max(cov[0][0], cov[1][1]) else ((1.0, 0.0, 0.0) if cov[0][0] >= cov[1][1] else (0.0, 1.0, 0.0))
    for _ in range(50):
        w = tuple(sum(cov[i][j] * v[j] for j in range(3)) for i in range(3))
        length = _norm(w)
        if length < 1e-12:
            break
        v = (w[0] / length, w[1] / length, w[2] / length)
    return v


def reach(part_points, owner_points):
    """How far the part reaches out from its owner's body, per the part's own length: (max radial distance of the part
    from the owner's long axis - the owner's median radius) / the part's longest extent. None without points."""
    if not part_points or not owner_points:
        return None
    axis = principal_axis(owner_points)
    n = len(owner_points)
    origin = tuple(sum(p[i] for p in owner_points) / n for i in range(3))
    radii = sorted(_radial(p, origin, axis) for p in owner_points)
    radius = radii[len(radii) // 2]
    out = max(_radial(p, origin, axis) for p in part_points) - radius
    lo = [min(p[i] for p in part_points) for i in range(3)]
    hi = [max(p[i] for p in part_points) for i in range(3)]
    length = max(hi[i] - lo[i] for i in range(3))
    return max(0.0, out) / length if length > 1e-9 else None


def extension(part_points, owner_points):
    """'extended' | 'retracted' | 'unknown' (between the two shares, or nothing to measure) and the measured share."""
    share = reach(part_points, owner_points)
    if share is None:
        return 'unknown', None
    if share >= EXTENDED_SHARE:
        return 'extended', round(share, 3)
    if share <= RETRACTED_SHARE:
        return 'retracted', round(share, 3)
    return 'unknown', round(share, 3)


def contact(lowest_z, support_top):
    """'contact' | 'clear' | 'unknown' (no support under it) and the gap in metres (negative: into the support)."""
    if support_top is None or lowest_z is None:
        return 'unknown', None
    gap = lowest_z - support_top
    return ('contact' if gap <= CONTACT_M else 'clear'), round(gap, 3)


def kind(state):
    """'pose' | 'contact' | 'render' | 'open', or None for 'unknown'."""
    return KIND.get(normal(state))


def differs(claimed, measured):
    """Whether a measured state contradicts the claimed one: both known, of the same kind, and opposite. A measure of
    another kind, or none, never contradicts (a claimed contact is not judged by how far a part reaches out)."""
    c, m = normal(claimed), normal(measured)
    return c != 'unknown' and m != 'unknown' and KIND[c] == KIND[m] and OPPOSITE[c] == m


def box_gap(a, b):
    """The distance between two boxes ((x0, y0, z0), (x1, y1, z1)): 0 when they touch or overlap."""
    return math.sqrt(sum(max(0.0, a[0][i] - b[1][i], b[0][i] - a[1][i]) ** 2 for i in range(3)))


def attach(parts, others, share=ATTACH_SHARE, size_max=ATTACH_SIZE_MAX):
    """{part: [other names]}: each rendering thing outside the subject (`others` {name: box}) given to the one part
    (`parts` {id: box}) it touches - within `share` of that part's longest extent, and at most `size_max` times that
    extent long itself (a glow filling the room is no part's light) - whose centre is nearest its own (a plume as wide
    as the gap to the next bell still belongs to its own bell). Parts with nothing attached are []."""
    out = {pid: [] for pid in parts}

    def mid(box):
        return tuple((box[0][i] + box[1][i]) / 2 for i in range(3))

    def longest(box):
        return max(box[1][i] - box[0][i] for i in range(3))

    for name, box in others.items():
        near = [pid for pid, pb in parts.items()
                if box_gap(pb, box) <= share * longest(pb) and longest(box) <= size_max * max(longest(pb), 1e-6)]
        if near:
            c = mid(box)
            out[min(near, key=lambda pid: _norm(_sub(mid(parts[pid]), c)))].append(name)
    return out


def in_state(state, parts):
    """How many of a feature's parts (part_state_core rows {extension, contact, render, ...}) are in `state` at the
    moment, by the state's own kind (KIND/MEASURE) - or None when the state is unknown or no part has that measure."""
    want = normal(state)
    field = MEASURE.get(KIND.get(want))
    measured = [normal(r.get(field)) for r in parts.values() if field and normal(r.get(field)) != 'unknown']
    return sum(1 for m in measured if m == want) if measured else None
