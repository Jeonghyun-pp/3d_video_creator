"""Subjects placed by where they show, not where they stand: a scene instance with `near {at, screen, size}` is moved,
once the shot's camera is baked, so that at that moment of the shot it shows at that place on the frame (x, y from the
top left) and that tall (share of the frame's height) - a 0.4 m stone near the lens with an 83 m hull behind it.

Why (2026-10-23, submarine r3): the hook compared a falling stone with the boat that does not sink, and no world
position could make both read - at the boat's distance the stone was a dot, and nobody knew where the camera would
stand until it was solved. The director says how the stone shows; the engine finds where that is.

apply(job) runs in generate after the camera rig is baked and before anything is measured; the camera rig's own guards
(run after the look) and the frame probe then judge the scene with the subject where it was put.
"""
import bpy
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view

from scene_index import resolve_ref

PLACE_TOL = 0.05     # the subject's middle within this of the asked place on the frame ...
SIZE_TOL = 0.25      # ... and its height within this share of the asked size, else PLACE_NEAR_MISSED
REFINE_STEPS = 3     # put it, read where and how big it shows, put it again


def _box(objects):
    pts = [o.matrix_world @ Vector(c) for o in objects if o.type == 'MESH' and not o.hide_render for c in o.bound_box]
    return pts


def _screen(scene, cam, points):
    """(centre [x, y] from the top left, height share) of points on the frame, or None when all lie behind the camera."""
    seen = [world_to_camera_view(scene, cam, p) for p in points]
    seen = [s for s in seen if s.z > 0]
    if not seen:
        return None
    xs, ys = [s.x for s in seen], [1.0 - s.y for s in seen]
    return [round((min(xs) + max(xs)) / 2, 4), round((min(ys) + max(ys)) / 2, 4)], round(max(ys) - min(ys), 4)


def _shift(root, delta):
    """Move a root by a world delta: its location keys with it when it is keyed, else its place."""
    local = (root.parent.matrix_world.inverted().to_3x3() @ delta) if root.parent else delta
    curves = []
    action = getattr(root.animation_data, 'action', None) if root.animation_data else None
    if action is not None:
        from scene_tools import curves as all_curves
        curves = [c for c in all_curves(action) if c.data_path == 'location']
    if curves:
        for c in curves:
            for point in c.keyframe_points:
                point.co.y += local[c.array_index]
                point.handle_left.y += local[c.array_index]
                point.handle_right.y += local[c.array_index]
    else:
        root.location += local


def apply(job):
    """Move every instance with `near` to show as asked; -> report {placed: [...], gate_failures: [...]}."""
    scene = bpy.context.scene
    cam = scene.camera
    count = job['shot']['duration_frames']
    rows = [r for r in ((job['shot'].get('scene') or {}).get('instances') or []) if r.get('near')]
    report = {'placed': [], 'gate_failures': []}
    for row in rows:
        near = row['near']
        found = resolve_ref(row['id'])
        if not found or not found['roots']:
            report['gate_failures'].append({'code': 'PLACE_NEAR_MISSED', 'id': row['id'], 'why': 'nothing of it is built'})
            continue
        frame = round(float(near['at']) * (count - 1)) + 1
        scene.frame_set(frame)
        objects = found['objects']
        points = _box(objects)
        if not points:
            report['gate_failures'].append({'code': 'PLACE_NEAR_MISSED', 'id': row['id'], 'why': 'nothing of it renders'})
            continue
        matrix = cam.matrix_world
        origin = matrix.translation.copy()
        forward = (matrix.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()
        up = (matrix.to_3x3() @ Vector((0.0, 1.0, 0.0))).normalized()
        tr, br, bl, tl = cam.data.view_frame(scene=scene)   # camera local, on the plane z = tr.z
        x, y = (float(v) for v in near['screen'])
        frame_h = (tl.y - bl.y) / -tl.z   # the frame's height at a depth of one metre
        heights = [p.dot(up) for p in points]
        extent = max(heights) - min(heights)
        depth = extent / max(1e-6, float(near['size']) * frame_h)
        start = sum(points, Vector()) / len(points)
        ax, ay, got = x, y, None
        for _ in range(REFINE_STEPS):   # put, look, and put again by what showed (a near box's own depth makes it look larger)
            local = (tl.lerp(tr, ax)).lerp(bl.lerp(br, ax), ay)
            ray = ((matrix @ local) - origin).normalized()
            target = origin + ray * (depth / max(1e-6, ray.dot(forward)))
            centre = sum(_box(objects), Vector()) / len(points)
            for root in found['roots']:
                _shift(root, target - centre)
            bpy.context.view_layer.update()
            scene.frame_set(frame)
            got = _screen(scene, cam, _box(objects))
            if got is None or got[1] <= 0:
                break
            depth *= got[1] / float(near['size'])
            ax, ay = ax + (x - got[0][0]), ay + (y - got[0][1])
        target = sum(_box(objects), Vector()) / len(points)
        row_report = {'id': row['id'], 'frame': frame - 1, 'asked': {'screen': [x, y], 'size': near['size']},
                      'moved_m': [round(v, 3) for v in (target - start)], 'depth_m': round((target - origin).dot(forward), 3)}
        if got is None:
            row_report['shown'] = None
            report['gate_failures'].append({'code': 'PLACE_NEAR_MISSED', 'id': row['id'], 'why': 'it ended behind the camera'})
        else:
            row_report['shown'] = {'screen': got[0], 'size': got[1]}
            off = max(abs(got[0][0] - x), abs(got[0][1] - y))
            if off > PLACE_TOL or abs(got[1] - near['size']) > SIZE_TOL * near['size']:
                report['gate_failures'].append({'code': 'PLACE_NEAR_MISSED', 'id': row['id'],
                                                'why': f"asked at {[x, y]} {near['size']} tall, shows at {got[0]} {got[1]} tall"})
        report['placed'].append(row_report)
    scene.frame_set(1)
    return report
