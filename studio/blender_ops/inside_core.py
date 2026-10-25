"""Whether a camera stands inside one closed thing (pure python): the box test that picks which things to ask and the
ray-count rule (camera_moves.INSIDE_RAYS / INSIDE_HITS) applied per object. Why (2026-10-25, semiconductor_fab): camera
poses were refused inside the framed subject only, and the s03 and s10 takes stood inside clay tool cabinets."""


def within(corners, point, margin=1e-6):
    """True when `point` lies inside the axis-aligned box of `corners` (world points)."""
    return all(min(c[i] for c in corners) - margin <= point[i] <= max(c[i] for c in corners) + margin for i in range(3))


def enclosed(hits, rays, needed):
    """The ray rule: at least `needed` of `rays` rays from the point meet the one object."""
    return rays > 0 and hits >= needed
