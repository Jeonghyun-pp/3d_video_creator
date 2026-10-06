"""Cycloidal disc outline (the standard parametric form): a disc with pins - 1 lobes that rolls inside a ring of
`pins` pins of radius pin_r on a circle of radius ring_r, driven at eccentricity ecc."""
import math


def outline(ring_r, pin_r, ecc, pins, points):
    out = []
    for i in range(points):
        t = 2 * math.pi * i / points
        psi = math.atan2(math.sin((1 - pins) * t), ring_r / (ecc * pins) - math.cos((1 - pins) * t))
        x = ring_r * math.cos(t) - pin_r * math.cos(t + psi) - ecc * math.cos(pins * t)
        y = -ring_r * math.sin(t) + pin_r * math.sin(t + psi) + ecc * math.sin(pins * t)
        out.append([x, y])
    return out
