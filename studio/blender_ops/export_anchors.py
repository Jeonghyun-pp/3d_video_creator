import json
from pathlib import Path
import sys
import bpy
sys.path.insert(0,str(Path(__file__).parent))
from scene_tools import anchors_for_frame
job=json.loads(Path(sys.argv[sys.argv.index('--')+1]).read_text())
# Project the way the renderer frames the shot: the saved scene's own resolution may differ (aspect changes u,v).
if job.get('output_size'):
    scene=bpy.context.scene
    scene.render.resolution_x,scene.render.resolution_y=job['output_size']; scene.render.resolution_percentage=100
rows=[]
for frame in range(job['duration_frames']):
    bpy.context.scene.frame_set(frame+1)
    rows.extend(anchors_for_frame(job['labels'],frame))
Path(job['output_path']).write_text(json.dumps({'schema_version':1,'frames':rows},ensure_ascii=False))
