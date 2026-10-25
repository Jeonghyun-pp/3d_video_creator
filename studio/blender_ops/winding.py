"""Whether a closed mesh winds inward, from its world-space signed volume (pure: no bpy).
Why (2026-10-25, semiconductor_fab s04): the reveal rounded each target's volume to 3 decimals of a cubic metre before
judging its sign, so a 0.4 m x 12 mm x 6 mm rib on a wafer pod (2.9e-5 m3) read 0.0 and was refused as inside-out:
every part smaller than half a litre failed. The sign is judged on the volume itself; only the report is rounded."""


def winds_inward(volume_m3):
    """True when the faces wind inward (negative volume) or enclose nothing at all (zero)."""
    return not volume_m3 > 0.0


def shown(volume_m3):
    """The volume as the report says it: four significant figures, at any size."""
    return float(f'{volume_m3:.4g}')
