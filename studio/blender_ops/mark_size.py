"""How thick a drawn mark is (pure: no bpy). A mark's line is a share of the frame's height where it stands
(graphics.STROKE_SHARE), whatever the scale of the scene; a size in metres is used only where nothing places it.
Why (2026-10-25, semiconductor_fab s11): a floor of 4 cm (DEFAULT_RADIUS_M, set 2026-10-23 for an 84 m boat's arrows)
drew the outline of a 3 mm speck in a 5 cm macro frame as a red disc wider than the frame."""


def stroke_radius(seen_m, default_m):
    """The line's radius: what shows the frame share at the mark (seen_m), else default_m when nothing places it."""
    return seen_m if seen_m and seen_m > 0 else default_m


def stands_at(point, box):
    """Where a mark on a thing stands: the middle of the thing's box when it has one, else the point it was named by. An
    object's origin need not lie on it (2026-10-26, fab s26: a chip's origin was the world's middle, a metre off)."""
    if box:
        low, high = box
        return tuple(round((a + b) / 2, 9) for a, b in zip(low, high))
    return tuple(point)
