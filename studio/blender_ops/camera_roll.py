"""The camera's own roll between two frames, from its orientation (pure python).
Why (2026-10-25, semiconductor_fab s03): the comfort check's 'turn' (camera_comfort: the roll, summed over its fastest
half second) was the rotation term of a 2D similarity fit over scene points held still. A camera sliding along a rail
under a ceiling close overhead and a floor far below sees the near points slide faster than the far ones; the fit
explained that parallax as a roll of 19.7 deg in half a second while the camera's own orientation never changed
(yaw -20 deg, pitch -5 deg on every frame). In a built scene the camera is known: its roll is read from it."""
import math


def roll_rad(before, after):
    """The rotation about the view axis taking orientation `before` to `after` (3x3 world-from-camera matrices, the
    camera looking down its local -Z), in radians, signed as the picture turns counter-clockwise."""
    rel = [[sum(before[k][i] * after[k][j] for k in range(3)) for j in range(3)] for i in range(3)]   # before^T after: the change in the camera's own axes
    return math.atan2(rel[1][0] - rel[0][1], rel[0][0] + rel[1][1])
