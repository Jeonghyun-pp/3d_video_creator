"""Whether a part can be seen from outside its intact subject at all - the measure behind "this part is inside, so a
picture of it must open the subject". Pure Python (host and Blender); studio/blender_ops/exposure_survey.py measures,
studio/explains.py reads.

Why (2026-10-13): the rocket's beat pictures said "the hydraulic arms tilt the engine" and drew a closed booster. Nothing
knew the arms sit inside the engine bay; a person saw it. The user: "유압 팔은 몸통 안에 있다 -> 단면이나 열린 그림이
필요하다 -> 관련 이미지를 찾아서 그린다 - 이 생각을 할 수 있을까?", and no hand list of inside parts ("단면 등
하드코딩하지 말고 규칙으로"). So inside is measured on the model: rays from every side of the closed subject.
"""
from __future__ import annotations

import math

DIRECTIONS = 64   # Fibonacci directions on the sphere, about 25 degrees apart: a gap narrower than that seen from no
                  # sampled side reads as closed - the safe side (an opening asked for that a closer look may not need)
SAMPLES = 300     # surface points a part is sampled at (camera_frame.sample)


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
