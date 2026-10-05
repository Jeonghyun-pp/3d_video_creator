"""Spec-built subjects of a built scene and when they are on screen (versions/<v>/subjects_index.json).

The generation prompt (generative.clip.assemble_prompt) maps clay shapes to these, so a scene that never declared
shot.subjects (building elements placed from exemplars) still describes itself. Kept out of scene_geometry, whose
hash is part of the control-pass and look fingerprints.
"""
from __future__ import annotations


def subjects_on_screen(scene, samples=5):
    """Every spec-built subject root (studio_subject_summary) with the sampled frames where any of its meshes'
    bounding-box centre is inside the camera frame."""
    import json as _json
    from bpy_extras.object_utils import world_to_camera_view
    from mathutils import Vector
    roots = [o for o in scene.objects if o.get('studio_subject_summary')]
    if not roots or scene.camera is None:
        return []
    start, end = scene.frame_start, scene.frame_end
    frames = sorted({round(start + (end - start) * i / max(1, samples - 1)) for i in range(samples)})
    current = scene.frame_current
    seen = {o.name: [] for o in roots}
    meshes = {o.name: [m for m in o.children_recursive if m.type == 'MESH' and not m.hide_render] for o in roots}
    for frame in frames:
        scene.frame_set(frame)
        for root in roots:
            for mesh in meshes[root.name]:
                centre = mesh.matrix_world @ (sum((Vector(c) for c in mesh.bound_box), Vector()) / 8)
                u = world_to_camera_view(scene, scene.camera, centre)
                if u.z > 0 and 0 <= u.x <= 1 and 0 <= u.y <= 1:
                    seen[root.name].append(frame - 1)   # 0-based shot frame
                    break
    scene.frame_set(current)
    return [{'subject_id': r['studio_subject_id'], **_json.loads(r['studio_subject_summary']), 'on_screen_frames': seen[r.name]} for r in roots]
