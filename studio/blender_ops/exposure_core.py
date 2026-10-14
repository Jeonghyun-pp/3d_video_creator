"""Whether a part can be seen from outside its intact subject at all - the measure behind "this part is inside, so a
picture of it must open the subject". Pure Python (host and Blender); studio/blender_ops/exposure_survey.py measures,
studio/explains.py reads.

Why (2026-10-13): the rocket's beat pictures said "the hydraulic arms tilt the engine" and drew a closed booster. Nothing
knew the arms sit inside the engine bay; a person saw it. The user: "유압 팔은 몸통 안에 있다 -> 단면이나 열린 그림이
필요하다 -> 관련 이미지를 찾아서 그린다 - 이 생각을 할 수 있을까?", and no hand list of inside parts ("단면 등
하드코딩하지 말고 규칙으로"). So inside is measured on the model: rays from every side of the closed subject.

Version 2 (2026-10-14) keeps, per part, the share seen from every direction (`per_direction`, in directions() order),
the share some direction reaches directly (`exposable_share`: a surface no ray from outside meets - an engine bell's top
inside its thrust section, a bulb's foot in its socket - is never the camera's to show), how often each other part
wraps it (`enclosed_by`: in front of a point and behind it on one ray) and its box in the subject's own frame (`box`).
Hidden is judged against what can be seen: a part seen from a view is not hidden when it shows at least half of what
any side reaches of it (hidden_against). A survey of another version is stale.
"""
from __future__ import annotations

import math

DIRECTIONS = 64   # Fibonacci directions on the sphere, about 25 degrees apart: a gap narrower than that seen from no
                  # sampled side reads as closed - the safe side (an opening asked for that a closer look may not need)
SAMPLES = 300     # surface points a part is sampled at (camera_frame.sample)
SCHEMA_VERSION = 2   # per_direction, exposable_share, enclosed_by and box per part (studio/explains.py reads no other)


def directions(count=DIRECTIONS):
    """Unit vectors spread evenly over the sphere (Fibonacci lattice), from the subject toward the outside."""
    golden = math.pi * (3.0 - math.sqrt(5.0))
    out = []
    for n in range(count):
        z = 1.0 - 2.0 * (n + 0.5) / count
        r = math.sqrt(max(0.0, 1.0 - z * z))
        out.append((round(math.cos(golden * n) * r, 6), round(math.sin(golden * n) * r, 6), round(z, 6)))
    return out


def inside(best_share, threshold):
    """A part is inside when, from its best outside side, more than `threshold` of it is still hidden: the share the
    camera's own diagnosis calls hidden (camera_frame.HIDDEN_SHARE), recorded with each survey."""
    return best_share < 1.0 - threshold


def hidden_against(seen_share, exposable_share):
    """How hidden a part is when `seen_share` of it shows (its points nothing else covers: behind its own near side
    counts as shown, as everywhere), judged against the share any side reaches at all: 1 - seen / exposable, never below
    0 (a view showing all that can show). A part no side reaches (exposable 0) is all hidden. Compared with the same
    threshold as an unadjusted share (camera_frame.HIDDEN_SHARE)."""
    if not exposable_share or exposable_share <= 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - seen_share / exposable_share))


def toward_camera(elevation_deg, azimuth_deg):
    """The unit direction from the subject toward a camera at (elevation, azimuth) - camera_frame_core.basis's convention
    (azimuth 0: the camera on -y; + elevation: above)."""
    a, e = math.radians(azimuth_deg), math.radians(elevation_deg)
    return (math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e))


def view_of_direction(d):
    """(elevation_deg, azimuth_deg) of a camera along direction d from the subject (toward_camera's inverse)."""
    return (round(math.degrees(math.asin(max(-1.0, min(1.0, d[2])))), 1), round(math.degrees(math.atan2(d[0], -d[1])), 1))


def nearest(dirs, d):
    """The index of the direction among dirs nearest to d."""
    return max(range(len(dirs)), key=lambda i: sum(a * b for a, b in zip(dirs[i], d)))


def reach_m(box):
    """The most a part's thinnest side can show at from any side, in metres: a box's two shortest sides a, b never
    project thicker than hypot(a, b) (on any view, a screen direction exists that crosses the longest side at right
    angles). An upper bound, so a part refused by it can never be shown thick enough."""
    lo, hi = box
    a, b, _c = sorted(abs(h - l) for l, h in zip(lo, hi))
    return math.hypot(a, b)


def most_px_per_m(box, elevation_deg, tall_px, wide_px, focal_px):
    """The most output pixels per metre any point of the subject can show at, for a picture that draws the whole
    subject tall_px tall and wide_px wide through a lens of focal_px (pixels: half the output height over tan of half
    the vertical view), from the subject's box in its own frame. Perspective: a part on the near side of the subject is
    larger than one in its middle - worst case, the near side of the box.
      Upright as modelled (the picture taller than wide when the model is, wider when it is wider): the box stands at
      least tall_m on screen (cos e * height + sin e * its narrower side - any turn about the vertical: the author may
      turn the subject, the picture's azimuth says nothing of its facing), so its far side is at least
      focal_px * tall_m / tall_px away, and its near side at most depth_m nearer (cos e * the diagonal of its footprint
      + sin e * height).
      Drawn the other way (the scene lays it down, or a misread picture): its shortest side is all it surely stands,
      and its whole diagonal all it may reach toward the camera.
    -> focal_px over the nearest distance, or inf when the camera may be inside the box's reach (nothing is refused).
    An upper bound, so a part refused with it can never show thick enough (review 2026-10-14: the bound without
    perspective could refuse a near part of a wide, low picture)."""
    lo, hi = box
    dx, dy, dz = (abs(h - l) for l, h in zip(lo, hi))
    e = math.radians(abs(elevation_deg))
    if (dz >= max(dx, dy)) == (tall_px >= wide_px):
        tall_m = math.cos(e) * dz + math.sin(e) * min(dx, dy)
        depth_m = math.cos(e) * math.hypot(dx, dy) + math.sin(e) * dz
    else:
        tall_m, depth_m = min(dx, dy, dz), math.sqrt(dx * dx + dy * dy + dz * dz)
    if tall_px <= 0 or tall_m <= 0:
        return float('inf')
    near = focal_px * tall_m / tall_px - depth_m
    return focal_px / near if near > 0 else float('inf')
