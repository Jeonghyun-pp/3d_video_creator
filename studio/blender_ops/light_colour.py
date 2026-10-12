"""Light colour, the pure part (no bpy): a lamp's colour as a correlated colour temperature, and which lamps will tint
the picture against the camera's white balance.

Why (2026-10-10): a floor cutaway's section faces rendered blue (blue minus red +42..+61 of 255). The look pinned the
white balance at 4000 K and the author lit the cut faces with a cool fill (linear RGB 0.82, 0.89, 1 - about 7400 K, 115 mired from the balance); warm
practicals lit the rooms. One white balance cannot make both neutral, so the fix is at the lamp: report every kept lamp
whose colour sits far from the white balance, with the direction it pushes the picture.
"""
from __future__ import annotations

CAST_MIRED = 100.0   # a lamp more than this many mired from the white balance tints what it lights visibly
                     # (4000 K vs 6500 K is 96 mired: a daylight lamp under a warm interior balance just reads cool)


def rgb_to_cct(rgb):
    """Correlated colour temperature (K) of a linear-sRGB colour (McCamy's approximation on CIE xy); None for black or
    for a colour on McCamy's singular line (y = 0.1858, a saturated blue-violet far from any lamp; 2026-10-12 audit)."""
    r, g, b = (max(0.0, float(c)) for c in rgb[:3])
    x_ = 0.4124 * r + 0.3576 * g + 0.1805 * b
    y_ = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z_ = 0.0193 * r + 0.1192 * g + 0.9505 * b
    total = x_ + y_ + z_
    if total <= 0:
        return None
    x, y = x_ / total, y_ / total
    if abs(0.1858 - y) < 1e-9:
        return None
    n = (x - 0.3320) / (0.1858 - y)
    cct = 449.0 * n ** 3 + 3525.0 * n ** 2 + 6823.3 * n + 5520.33
    return max(1000.0, min(40000.0, cct))


def casts(lamps, white_balance_k):
    """lamps: [(name, kelvin)] -> warnings for those more than CAST_MIRED from the white balance."""
    out = []
    wb = 1e6 / white_balance_k
    for name, kelvin in lamps:
        if not kelvin:
            continue
        delta = 1e6 / kelvin - wb
        if abs(delta) > CAST_MIRED:
            tint = 'blue' if delta < 0 else 'orange'
            out.append(f"LIGHT_CAST: kept light {name} is about {round(kelvin, -2):.0f} K against the {white_balance_k:.0f} K white "
                       f"balance - what it lights renders {tint}; give it a colour temperature near the white balance "
                       f"(use_temperature) or set the white balance on purpose (style light_rig.white_balance_k)")
    return out
