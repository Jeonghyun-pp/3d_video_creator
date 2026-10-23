"""Camera views in the subject's own terms: which side of a subject a world azimuth looks from (from ahead, the front
quarter, the side, the back quarter, behind; on its right or left), and the azimuth that sees its broad side - from the
spec's axes (length, width, height) and, when it says one, its front (`front: +length | -length`), turned as the shot
places it (scene.instances rot_z_deg).

Why (2026-10-23, submarine r3): views are world numbers (azimuth 0: the camera on -Y looking +Y). The director framed
every cut of an 83 m hull from 25-50 deg - the stern quarter - without knowing it, and every recipe's default (20-35)
was the stern quarter too: three cuts of the same rear view in one reel.
"""
from __future__ import annotations

import math
from pathlib import Path

from .common import read_json

AXIS = {'x': (1.0, 0.0), 'y': (0.0, 1.0)}   # a horizontal spec axis on the ground plane (a length along z has no side)
BANDS = ((30.0, 'ahead', 'end-on'), (70.0, 'the front quarter', 'a quarter'), (110.0, 'the side', 'the side'),
         (150.0, 'the back quarter', 'a quarter'), (180.0, 'behind', 'end-on'))   # (up to deg off the front, with a front, without)


def subject_frame(path, shot, ref):
    """(length (x, y) on the ground, front sign or None) of the subject `ref` names in this shot, or None when its spec
    says no horizontal length."""
    subject = str(ref).split('/')[0]
    spec_file = Path(path) / 'subjects' / subject / 'spec.json'
    if not spec_file.is_file():
        spec_file = Path(path) / 'subjects' / subject.replace('-', '_') / 'spec.json'
    if not spec_file.is_file():
        return None
    spec = read_json(spec_file)
    axis = (spec.get('axes') or {}).get('length')
    if axis not in AXIS:
        return None
    turn = next((float(r.get('rot_z_deg', 0.0)) for r in ((shot.get('scene') or {}).get('instances') or [])
                 if r.get('subject') in (subject, subject.replace('-', '_'))), 0.0)
    a = math.radians(turn)
    lx, ly = AXIS[axis]
    length = (lx * math.cos(a) - ly * math.sin(a), lx * math.sin(a) + ly * math.cos(a))
    front = {'+length': 1.0, '-length': -1.0}.get(spec.get('front'))
    return length, front


def _toward_camera(azimuth_deg):
    a = math.radians(azimuth_deg)
    return math.sin(a), -math.cos(a)


def words(azimuth_deg, frame):
    """'from the back quarter, on its left' - the side of the subject a camera at this azimuth sees."""
    if frame is None:
        return None
    (lx, ly), front = frame
    cx, cy = _toward_camera(azimuth_deg)
    if front is None:   # no front: only how far off its length the camera looks (0: end-on)
        off = math.degrees(math.acos(max(-1.0, min(1.0, abs(lx * cx + ly * cy)))))
        return f"from {next(w for top, _f, w in BANDS if off <= top)}"
    fx, fy = front * lx, front * ly
    off = math.degrees(math.acos(max(-1.0, min(1.0, fx * cx + fy * cy))))   # 0: the camera ahead of it
    band = next(w for top, w, _n in BANDS if off <= top)
    right = (fy, -fx)
    hand = '' if off < 5 or off > 175 else (', on its right' if right[0] * cx + right[1] * cy > 0 else ', on its left')
    return f'from {band}{hand}'


def side_azimuth(frame):
    """The azimuth that sees the subject's broad side (square to its length; its right when it has a front), or None."""
    if frame is None:
        return None
    (lx, ly), front = frame
    sign = front or 1.0
    rx, ry = sign * ly, -sign * lx   # its right, looking along its front
    return round(math.degrees(math.atan2(rx, -ry)), 1)
