"""camera.target_anchor on a keyed camera: what the camera is about.

- A key without `target` aims at the anchor where it is at that key's frame (resolved here, before the keys are
  applied; scene_tools.apply_camera then keys the camera as usual - the stored keys stay the render's truth).
- On every frame the anchor must be in front of the camera and inside the frame: a camera that loses what it is
  about is a build error (CAMERA_ANCHOR_OUT_OF_VIEW), not a look.
"""
import bpy
from bpy_extras.object_utils import world_to_camera_view

from scene_tools import anchor_for


def _point(ref, frame):
    bpy.context.scene.frame_set(frame + 1)
    obj, point = anchor_for(ref)
    if obj is None:
        raise ValueError(f'CAMERA_ANCHOR: target_anchor {ref!r} is not in the scene')
    return point.copy()


def fill_targets(shot):
    """Keys without a target aim at the anchor (in place on the job's shot). Returns how many were filled."""
    camera = shot['camera']
    ref, filled = camera.get('target_anchor'), 0
    for key in camera.get('keys', []):
        if 'target' not in key:
            key['target'] = list(_point(ref, key['frame']))
            filled += 1
    return filled


def check_in_view(shot, margin=0.0):
    """Frames where the anchor is behind the camera or outside the frame (with `margin` of the frame kept clear)."""
    ref, scene = shot['camera']['target_anchor'], bpy.context.scene
    out = []
    for frame in range(shot['duration_frames']):
        point = _point(ref, frame)
        view = world_to_camera_view(scene, scene.camera, point)
        if view.z <= 0 or not (margin <= view.x <= 1 - margin and margin <= view.y <= 1 - margin):
            out.append(frame)
    scene.frame_set(1)
    return out
