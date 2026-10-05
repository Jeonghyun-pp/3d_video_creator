"""Variant A: the v0003 hand-written camera_state(t), expressed as a procedural rig hook."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from choreography import high


def ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def camera_state(t, ctx):
    h = high(t)
    reveal = ease((t - 5.0) / 2.0)
    return {'offset_m': ((9 + 4.0 * math.sin(t * 1.25)) * (1 - h) + 2.5 * h, 8 + 9 * h, 22 - 11 * h),
            'blend': .15 + .17 * reveal, 'lift_m': 1.0 * (1 - h),
            'lens_mm': (24 + 3 * reveal) * (1 - h) + 18 * h}
