"""Picture regions, the pure part (no bpy, no numpy): one definition for the reference critique (studio/critique.py)
and the light targets (light_probe.py, studio/light_targets.py), so a region a critique names is the region a light
target is solved for. Bands top..bottom x columns left/centre/right, normalized (0,0 top-left); `class:<id>` regions
are our id-pass classes and are resolved where the id pass is."""
from __future__ import annotations

BANDS = (('top', 0.0, 0.2), ('upper', 0.2, 0.4), ('middle', 0.4, 0.6), ('lower', 0.6, 0.8), ('bottom', 0.8, 1.0))
COLUMNS = (('left', 0.0, 1 / 3), ('centre', 1 / 3, 2 / 3), ('right', 2 / 3, 1.0))


def cells():
    """{name: (x0, y0, x1, y1)} normalized, every band x column cell."""
    return {f'{band}-{column}': (x0, y0, x1, y1) for band, y0, y1 in BANDS for column, x0, x1 in COLUMNS}


def is_region(name):
    return name in cells() or str(name).startswith('class:')


def l_star_to_y(L):
    """CIE L* -> relative luminance Y (0..1)."""
    return ((L + 16) / 116) ** 3 if L > 8 else L / 903.3


def y_to_l_star(Y):
    return 116 * Y ** (1 / 3) - 16 if Y > 0.008856 else 903.3 * Y
