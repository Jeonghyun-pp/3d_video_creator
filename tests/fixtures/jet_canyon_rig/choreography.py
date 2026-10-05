"""Shared phase schedule: weight of the two oblique high-camera moments (0..1)."""
import math


def high(t):
    bump = lambda mid, width: math.exp(-((t - mid) / width) ** 4)
    return max(bump(3.8, .72), .92 * bump(6.05, .58))
