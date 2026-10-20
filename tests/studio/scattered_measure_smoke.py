"""Run inside Blender (headless): a must-see group whose members stand apart is read by the member shown best, and a
part too large for its probe points to land in the frame is read by the build's own measure - camera and build alike.

2026-10-20, canal clay: twelve valves along a 970 m lock were one must-see part, measured as one thin shape spread over
the frame (area over a long side running from one valve to the next) or off the frame; a 1.6 km lake and a 300 m
chamber got a handful of their 200 probe points into any frame and read 0 px. 84-97 % of those refusals were that.
- scene_index.scattered: four valves 20 m apart are members that stand apart; a chain of touching links is one thing.
- frame_probe.measure_at (the build's measure) reads the valves at the thickness of the valve shown best, the same as
  that valve alone; the chain is read whole, as before.
- camera_frame.probe reads the valves by the member shown best on their own points, and lists the lake `unread` (too
  few of its points in the frame); camera_moves.read_unread reads it as measure_at does (it was 0 px from its points).
Timings printed.
"""
from pathlib import Path
import sys
import time

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
import camera_frame  # noqa: E402
import camera_frame_core as frame_core  # noqa: E402
import frame_probe  # noqa: E402
import scene_index  # noqa: E402

SIZE, FRAME, LENS = (1080, 1920), 1, 35.0


def box(name, location, size):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.active_object
    obj.scale = size
    obj.name = obj['studio_id'] = name
    return obj


bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.frame_start, scene.frame_end = 1, 10
scene.render.resolution_x, scene.render.resolution_y = 1080, 1920
for n in range(4):                                     # four valves, 1 m each, 20 m apart along x
    box(f'valve.{n}', (20.0 * n, 0.0, 0.5), (1.0, 1.0, 1.0))
for n in range(6):                                     # six links touching end to end: one chain
    box(f'chain.{n}', (0.3 * n, 6.0, 0.15), (0.3, 0.1, 0.1))
bpy.ops.mesh.primitive_plane_add(size=1600.0, location=(0.0, 900.0, 0.0))   # a lake far larger than any frame
lake = bpy.context.active_object
lake.name = lake['studio_id'] = 'lake'
cam_data = bpy.data.cameras.new('shot_cam')
scene.camera = bpy.data.objects.new('shot_cam', cam_data)
scene.collection.objects.link(scene.camera)
scene.frame_set(FRAME)

valves = scene_index.scattered('valve')
assert sorted(o.name for o in valves) == [f'valve.{n}' for n in range(4)], [o.name for o in valves]
assert scene_index.scattered('chain') == [], 'touching links are one thing'
assert scene_index.scattered('lake') == [], 'one object is no group'

# a camera high over the row, looking down along it: three valves stacked up the tall frame, the nearest largest - the
# group reads as the valve shown best, not as one tall thin shape from the first to the last
EYE, AIM = (-5.0, 0.0, 15.0), (10.0, 0.0, 0.0)
t0 = time.perf_counter()
group = frame_probe.measure_at(['valve'], EYE, AIM, LENS, 0.0, FRAME, SIZE)['valve']
alone = max(frame_probe.measure_at([f'valve.{n}'], EYE, AIM, LENS, 0.0, FRAME, SIZE)[f'valve.{n}'] for n in range(4))
measured_s = time.perf_counter() - t0
assert group > 40 and abs(group - alone) <= 0.15 * alone, (group, alone)   # the best member, not a thin spread
chain_whole = frame_probe.measure_at(['chain'], (0.8, 3.0, 1.0), (0.8, 6.0, 0.15), LENS, 0.0, FRAME, SIZE)['chain']
link = frame_probe.measure_at(['chain.0'], (0.8, 3.0, 1.0), (0.8, 6.0, 0.15), LENS, 0.0, FRAME, SIZE)['chain.0']
assert chain_whole > 0 and link > 0, (chain_whole, link)

# the camera's probe reads them the same way: the valves by the member shown best, the lake from where it is in frame
view = frame_core.view_of(EYE, AIM)
prepared = camera_frame.probe_set(['valve'], ['valve'], ['valve'], FRAME)
assert list(prepared['members']) == ['valve'] and len(prepared['members']['valve']) == 4, list(prepared['members'])
t0 = time.perf_counter()
seen = camera_frame.probe(['valve'], ['valve'], ['valve'], EYE, view, LENS, SIZE, FRAME, prepared=prepared)
probe_s = time.perf_counter() - t0
assert abs(seen['must_see_px']['valve'] - group) <= 0.2 * group, (seen['must_see_px'], group)   # points on the grid vs the id pass
# over the lake, looking straight down: tens of metres of a 1.6 km plane fill the frame (no horizon in it)
LAKE_EYE, LAKE_AIM = (0.0, 400.0, 30.0), (0.0, 401.0, 0.0)
lake_view = frame_core.view_of(LAKE_EYE, LAKE_AIM)
lake_px = frame_probe.measure_at(['lake'], LAKE_EYE, LAKE_AIM, LENS, 0.0, FRAME, SIZE)['lake']
on_lake = camera_frame.probe_set(['lake'], ['lake'], ['lake'], FRAME)
framed = sum(1 for p in on_lake['parts']['lake'] if (lambda s: s and 0 <= s[0][0] <= 1 and 0 <= s[0][1] <= 1)(
    frame_core.project([p], LAKE_EYE, frame_core.basis(*lake_view), frame_core.tangents(LENS, SIZE[0] / SIZE[1]))[0]))
assert framed < camera_frame.PROBE_READ_MIN, framed   # the case: too few of its points land in the frame to read it
lake_seen = camera_frame.probe(['lake'], ['lake'], ['lake'], LAKE_EYE, lake_view, LENS, SIZE, FRAME, prepared=on_lake)
assert lake_seen['unread'] == ['lake'], lake_seen['unread']
import camera_moves  # noqa: E402
read = camera_moves.read_unread({}, lake_seen, ['lake'], LAKE_EYE, LAKE_AIM, LENS, SIZE, FRAME, 0.0, 'lake', {})
assert lake_px > 100 and abs(read['lake'] - lake_px) <= 1.0, (read, lake_px, framed)
print('STUDIO_SCATTERED_MEASURE_SMOKE', {'ok': True, 'valve_px': group, 'valve_alone_px': alone, 'chain_px': chain_whole,
                                         'lake_px': lake_px, 'measure_s': round(measured_s, 3), 'probe_s': round(probe_s, 3)})
