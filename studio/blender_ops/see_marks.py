"""What a see-through skin hides from a mark (an outline, a label, a dimension's value): nothing it is faded to show.
Read by the graphics layer (render_graphics.py) and the label anchors (export_anchors.py); show_scene keys SEE_PROP."""
from scene_roles import first_blocking_hit

SEE_PROP = 'studio_see_through'          # keyed 0..1 on each see-through object (its materials read it)
SEEN_THROUGH_FROM = 0.5   # half way into its fade (as its window opens, show_scene.apply): it no longer hides what it shows


def faded(obj):
    """True while the object is drawn see-through at the current frame. It is faded to show what is behind it, so it
    never hides that thing's marks - an outline, a label, a dimension's value (2026-10-23, r3 s05: a tank seen through
    its 45% faded hull had its outline held out by the hull in the graphics layer, and never showed)."""
    return float(obj.get(SEE_PROP, 0.0)) >= SEEN_THROUGH_FROM


def mark_hit(scene, depsgraph, origin, direction, distance):
    """scene_roles.first_blocking_hit for a mark: steps over faded objects too. Same return."""
    start, left = origin.copy(), distance
    while left > 0:
        hit = first_blocking_hit(scene, depsgraph, start, direction, left)
        if not hit[0] or not faded(hit[4]):
            return hit
        step = (hit[1] - start).length + 1e-4
        start, left = hit[1] + direction * 1e-4, left - step
    return False, None, None, None, None, None
