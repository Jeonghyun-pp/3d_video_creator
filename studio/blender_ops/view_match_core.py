"""A camera described as an orbit around a point, and where it puts 3D points on the image - pure Python (no bpy).

Used to match a reference photo's view: the host solves the orbit from 2D<->3D correspondences (studio/photo_match.py),
the workbench renders it (workbench_tools.view_render) and a shot takes it through its own camera grammar. One camera
model everywhere, the same as camera_rig_core (Blender's sensor-fit rules, track -Z / up Y):

    camera = {target: [x, y, z] (m), distance_m, azimuth_deg (0 = +X, 90 = +Y), elevation_deg, roll_deg (0),
              lens_mm, sensor_mm (36), sensor_fit ('AUTO'), shift_x (0), shift_y (0), width, height (px)}

Screen points are normalised, top-left origin: (0, 0) top-left, (1, 1) bottom-right - pixels / (width, height).
Shift follows Blender: a fraction of the larger image side, +x moves the view right (the subject left), +y up.
"""
from __future__ import annotations

import math

try:   # host: package import; Blender: studio/blender_ops is on sys.path
    from .camera_rig_core import look_rotation, project, view_tangents
except ImportError:
    from camera_rig_core import look_rotation, project, view_tangents

CAMERA_KEYS = ('target', 'distance_m', 'azimuth_deg', 'elevation_deg', 'roll_deg', 'lens_mm', 'sensor_mm', 'sensor_fit',
               'shift_x', 'shift_y', 'width', 'height')
DEFAULTS = {'roll_deg': 0.0, 'sensor_mm': 36.0, 'sensor_fit': 'AUTO', 'shift_x': 0.0, 'shift_y': 0.0}


def full(camera):
    """The camera with defaults filled; unknown keys refused (a typo would silently change nothing)."""
    unknown = sorted(set(camera) - set(CAMERA_KEYS))
    if unknown:
        raise ValueError(f'camera keys {unknown} are not read (reads {list(CAMERA_KEYS)})')
    out = {**DEFAULTS, **camera}
    missing = [k for k in CAMERA_KEYS if k not in out]
    if missing:
        raise ValueError(f'camera needs {missing}')
    if not (out['distance_m'] > 0 and out['lens_mm'] > 0 and out['width'] > 0 and out['height'] > 0):
        raise ValueError('distance_m, lens_mm, width and height must be positive')
    return out


def eye(camera):
    c = full(camera)
    az, el = math.radians(c['azimuth_deg']), math.radians(c['elevation_deg'])
    t, d = c['target'], c['distance_m']
    return (t[0] + d * math.cos(el) * math.cos(az), t[1] + d * math.cos(el) * math.sin(az), t[2] + d * math.sin(el))


def pose(camera):
    """(eye, rotation quaternion w,x,y,z, tan_x, tan_y) - what Blender's camera object and data take."""
    c = full(camera)
    e = eye(c)
    if abs(abs(c['elevation_deg']) - 90.0) < 1e-6:
        raise ValueError('elevation_deg of +-90 has no defined roll; use +-89.9')
    q = look_rotation(e, tuple(c['target']), math.radians(c['roll_deg']))
    tan_x, tan_y = view_tangents(c['lens_mm'], c['sensor_mm'], c['sensor_fit'], c['width'], c['height'])
    return e, q, tan_x, tan_y


def project_points(camera, points, depth=False):
    """[(x, y) normalised, top-left] per world point (``depth``: (x, y, distance along the view)); None behind the camera."""
    c = full(camera)
    e, q, tan_x, tan_y = pose(c)
    side = max(c['width'], c['height'])
    sx, sy = c['shift_x'] * side / c['width'], c['shift_y'] * side / c['height']
    out = []
    for p in points:
        hit = project(q, e, tuple(p), tan_x, tan_y)
        out.append(None if hit is None else ((hit[0] - sx, hit[1] + sy, hit[2]) if depth else (hit[0] - sx, hit[1] + sy)))
    return out


def residual_px(camera, points3d, points2d):
    """RMS reprojection error in pixels (points2d normalised); a point behind the camera counts one image diagonal."""
    c = full(camera)
    diag = math.hypot(c['width'], c['height'])
    total = 0.0
    for got, want in zip(project_points(c, points3d), points2d):
        total += diag ** 2 if got is None else ((got[0] - want[0]) * c['width']) ** 2 + ((got[1] - want[1]) * c['height']) ** 2
    return math.sqrt(total / max(1, len(points2d)))


def pose_delta(a, b):
    """How far camera b is from camera a: view direction (deg), eye distance (m) and its share of a's distance,
    lens ratio - the quantities storyboard.TOLERANCE judges."""
    ea, eb = eye(a), eye(b)
    da = [t - e for t, e in zip(full(a)['target'], ea)]
    db = [t - e for t, e in zip(full(b)['target'], eb)]
    cos = sum(x * y for x, y in zip(da, db)) / (math.hypot(*da) * math.hypot(*db))
    moved = math.dist(ea, eb)
    return {'view_deg': math.degrees(math.acos(max(-1.0, min(1.0, cos)))), 'eye_m': moved,
            'eye_share': moved / full(a)['distance_m'], 'lens_ratio': abs(full(b)['lens_mm'] / full(a)['lens_mm'] - 1)}
