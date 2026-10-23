import json
from pathlib import Path
import sys
import bpy
sys.path.insert(0,str(Path(__file__).parent))
from bpy_extras.object_utils import world_to_camera_view
import ids_core
from scene_index import AnchorIndex, resolve_ref
from scene_roles import first_blocking_hit
job=ids_core.from_job(json.loads(Path(sys.argv[sys.argv.index('--')+1]).read_text()))
# Project the way the renderer frames the shot: the saved scene's own resolution may differ (aspect changes u,v).
if job.get('output_size'):
    scene=bpy.context.scene
    scene.render.resolution_x,scene.render.resolution_y=job['output_size']; scene.render.resolution_percentage=100


def rows_for_frame(labels, frame, index):
    """Where each label's anchor shows at this frame. The anchor is read as every camera and show path reads an id
    (scene_index.resolve_ref: an object, a declared anchor, '<id>/center', a group or a part in any of its spellings) -
    2026-10-23, r3: a label on 'submarine_hull/pressure_hull' was never placed, the scene names it 'submarine-hull/...'.
    A label is hidden by anything but its own part (hidden helpers and volumes never hide it: scene_roles 'raycast')."""
    scene = bpy.context.scene
    depsgraph = bpy.context.evaluated_depsgraph_get()
    out = []
    for label in labels:
        if not label['start_frame'] <= frame < label['end_frame']:
            continue
        found = resolve_ref(label['anchor'], index)
        if found is None:
            out.append({'frame': frame, 'label_id': label['label_id'], 'anchor_id': label['anchor'], 'u': 0, 'v': 0, 'depth': -1, 'visible': False, 'occluded': None})
            continue
        point = found['point']
        own = set(found['objects']) | set(found['roots'])
        ndc = world_to_camera_view(scene, scene.camera, point)
        visible = ndc.z > 0 and 0 <= ndc.x <= 1 and 0 <= ndc.y <= 1
        occluded = None
        if visible:
            origin = scene.camera.matrix_world.translation
            ray = point - origin
            distance = ray.length
            hit, hitpoint, _, _, hitobj, _ = first_blocking_hit(scene, depsgraph, origin, ray.normalized(), max(0, distance - .005))
            occluded = bool(hit and hitobj not in own and (hitpoint - origin).length < distance - .005)
        out.append({'frame': frame, 'label_id': label['label_id'], 'anchor_id': label['anchor'], 'u': ndc.x, 'v': 1 - ndc.y, 'depth': ndc.z, 'visible': visible, 'occluded': occluded})
    return out


rows=[]
index=AnchorIndex()
for frame in range(job['duration_frames']):
    bpy.context.scene.frame_set(frame+1)
    rows.extend(rows_for_frame(job['labels'],frame,index))
Path(job['output_path']).write_text(json.dumps({'schema_version':1,'frames':rows},ensure_ascii=False))
