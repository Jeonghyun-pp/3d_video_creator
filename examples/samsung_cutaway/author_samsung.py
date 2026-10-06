"""Samsung-station column reel: one author script for all 17 shots (dispatch on STUDIO_JOB['shot_id']).

Camera keys follow the reference reel's measured shot boundaries and start/mid/end framings
(internal analysis of the 'kenchiku cutaway' reel; reference not published). The same script builds
the blockout and the photoreal version; samsung_lib switches materials and lighting by look preset.
"""
import math
import random
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(STUDIO_JOB['script_path']).parent))   # samsung_lib.py is copied beside the author by the build
import samsung_lib as L  # noqa: E402

job = STUDIO_JOB
shot_id = job['shot_id']
N = job['shot']['duration_frames']
L.setup(job)
R90 = math.radians(90)


def show_from(obj_or_list, frame, until=None):
    for o in obj_or_list if isinstance(obj_or_list, (list, tuple)) else [obj_or_list]:
        for c in [o] + list(o.children_recursive):
            c.hide_render = True; c.keyframe_insert('hide_render', frame=1)
            c.hide_render = False; c.keyframe_insert('hide_render', frame=frame)
            if until:
                c.hide_render = True; c.keyframe_insert('hide_render', frame=until)


def label_box(name, body, loc, w, h=1.8, size=1.1, face_y=True):
    box = L.box(f'{name}.bg', (w, 0.15, h), loc, 'red')
    t = L.text(f'{name}.txt', body, (loc[0], loc[1] - 0.12, loc[2]), size, 'white_text', extrude=0.02)
    box['studio_scene_role'] = 'graphic'
    return [box, t]


MOVE = bool(job['shot']['camera'].get('move'))  # camera from shot.camera.move (rig overrides the keys below)


def s01_section():
    """Reference staging (s01 feedback 5): the ground in front of the station is cut away as a section (poché, soil,
    lit levels) and the camera comes down level in front of it and pushes in. Title: 2D (shot.titles), not 3D text."""
    import camera_moves_core
    import section
    y0 = 60.0
    depth = L.LEVELS * L.LEVEL_H
    box = ((-L.BOX_W / 2 - 0.6, y0, -depth - 1.5), (L.BOX_W / 2 + 0.6, y0 + L.BOX_L, 0.0))
    move = job['shot']['camera']['move']
    start = camera_moves_core.plan(move, {'points': {}, 'boxes': {move['params']['section']: box}})['waypoints'][0]
    framing = move.get('framing', {})
    sightline = {'camera': start, 'forward': (0.0, 1.0), 'horizon_v': framing.get('horizon_v', 0.40), 'keep_sky_v': 0.33,
                 'lens_mm': move.get('lens_mm', 24)}
    reveal = any(a['type'] == 'reveal' for a in job['shot']['actions'])
    L.city(day=False, y_range=(-320, 520), keep_clear=(y0 - 260, y0) if reveal else None, bare=(y0 - 70, y0), frames=(1, N),
           crossings=(y0 + 22, 330.0), sightline=sightline)
    L.station_box(y0=y0, cutaway=True, bright=True, skip_columns=L.brief_column_levels(job))
    if job['shot'].get('fill_brief'):   # what fills the levels is the shot's fill brief (topic, decided with the user)
        import fill_brief
        fill_brief.declare_levels(L.station_levels(y0))
    staged = section.stage('st.section', box, ceilings=[-lvl * L.LEVEL_H - 0.8 for lvl in range(L.LEVELS)][1:] + [-0.4 - 0.8])
    marker = bpy.data.objects.new(move['params']['section'], None); marker.empty_display_type = 'CUBE'; marker.empty_display_size = 1
    marker.location = tuple((box[0][i] + box[1][i]) / 2 for i in range(3)); marker.scale = tuple((box[1][i] - box[0][i]) / 2 for i in range(3))
    marker['studio_id'] = move['params']['section']; bpy.context.scene.collection.objects.link(marker)
    if reveal:   # the ground in front of the face: cut from the face toward the camera by the shot's reveal action
        section.front_cutter('ground.cutter', y0, reach_m=260.0, half_w_m=200.0, z_lo=-60.0, z_hi=0.5)
        cap = bpy.data.materials['st.section.poche'].copy(); cap.name = 'section_cap'
        road = bpy.data.objects['road.0']; road['studio_instance_id'], road['studio_part_id'] = 'road', 'slab'
    bpy.context.scene['studio_section'] = str(staged)
    L.camera([(1, (0, -150, 45), (0, 60, 10), 24), (N, (0, 85, -10), (0, 200, -10), 24)])


def s01():  # dusk drone over the road, 3D title, dive through the road into the station section
    if job['shot']['camera'].get('move', {}).get('type') == 'section_push':
        return s01_section()
    reveal = any(a['type'] == 'reveal' for a in job['shot']['actions'])
    L.city(day=False, hole=(55, 80) if MOVE and not reveal else None, keep_clear=(55, 80) if reveal else None, frames=(1, N))
    if reveal:  # closed road; the shot's reveal action grows this hidden cutter to open the hole
        L.box('road.cutter', (32, 25, 3), (0, 67.5, -0.5), 'slab_edge')
        cap = L.mat('slab_edge').copy(); cap.name = 'reveal_cap'
        road = bpy.data.objects['road.0']; road['studio_instance_id'], road['studio_part_id'] = 'road', 'slab'
    if MOVE:  # the hole the camera dives through: an empty whose display cube is the opening
        hole = bpy.data.objects.new('road.opening', None); hole.empty_display_type = 'CUBE'; hole.empty_display_size = 1
        hole.scale = (16, 12.5, 0.2); hole.location = (0, 67.5, -0.2); hole['studio_id'] = 'road.opening'
        bpy.context.scene.collection.objects.link(hole)
    L.station_box(y0=60.0, cutaway=True)
    for side, part in ((1, 'slab1_r'), (-1, 'slab1_l')):  # debris lands on the first level beside the opening
        slab = bpy.data.objects[f'st.slab1.{side}']; slab['studio_instance_id'], slab['studio_part_id'] = 'station', part
    L.text('title', '삼성역\n기둥철근', (0, 60, 40), 13.0, 'white_text', extrude=1.6)
    L.camera([(1, (0, -40, 55), (0, 120, 12), 24), (45, (0, -12, 38), (0, 90, 0), 24), (N, (0, 38, -16), (0, 90, -20), 24)])


def s02():  # frontal section of the five levels, slow push in
    L.underground((0, 100, -20), (400, 400, 200))
    L.station_box(y0=60.0, cutaway=True)
    L.photoreal_lamps([(x, 70, -LZ) for x in (-8, 8) for LZ in (8, 22)], 3000, 4)
    L.camera([(1, (0, 22, -17), (0, 100, -19), 22), (N, (0, 48, -18), (0, 100, -20), 22)])


def hall_platform(workers=True, crowd=False):
    cols = L.column_hall(n_rows=2, n_cols=9, spacing=8.0, width=1.1, height=6.5, gap=7.0)
    if workers:
        L.worker('w1', (-0.5, 52, 0), heading=math.radians(170))
        L.worker('w2', (0.6, 52.5, 0), heading=math.radians(-160))
    if crowd:  # site crew outside the column rows (the two inspectors stay real: they are the subject)
        rng = random.Random(3)
        pts = [(side * rng.uniform(6.0, 13.0), rng.uniform(8, 70), 0.0) for side in (-1, 1) for _ in range(14)]
        L.crowd('crew', pts, seed=3)
    L.photoreal_lamps([(0, y, 5.5) for y in (10, 30, 50)], 1500, 3)
    return cols


def s03():  # corridor of columns, two inspectors at the vanishing point, slow dolly
    L.underground((0, 30, 3), (400, 400, 200))
    hall_platform(crowd=True)
    L.camera([(1, (0, -4, 1.9), (0, 60, 1.6), 28), (N, (0, 16, 1.7), (0, 60, 1.5), 28)])


def s04():  # daytime aerial over the boulevard, flying toward the open excavation
    L.city(day=True, hole=(150, 190), frames=(1, N), max_height=35)
    L.station_box(y0=150.0, cutaway=False)
    L.camera([(1, (0, -20, 110), (0, 110, 0), 26), (N, (0, 60, 85), (0, 175, -8), 26)])


def s05():  # tall column from below, tilt down to its face (monitoring sensor)
    L.underground((0, 20, 8), (400, 400, 200))
    L.column_hall(n_rows=1, n_cols=4, spacing=12.0, width=1.6, height=16.0, gap=10.0, ceiling_lights=False, capitals=False, beams=False)
    L.box('sensor', (0.18, 0.08, 0.12), (0, 3 - 0.84, 3.6), 'office_white')
    for side in (-1, 1):
        L.box(f'tray{side}', (0.4, 60, 0.12), (side * 4.5, 20, 12), 'steel')
    L.photoreal_lamps([(0, -2, 10), (3, 6, 8)], 2500, 3)
    L.camera([(1, (0, -4.5, 0.4), (0, 3, 15.5), 16), (60, (0, -3.0, 1.2), (0, 3, 6.5), 20), (N, (0, -2.6, 1.6), (0, 3, 3.4), 24)])


def s06():  # whip pan, then design (2 rows) vs as-built (1 row) main bars side by side
    L.underground((0, 0, 3), (400, 400, 200))
    L.column_hall(n_rows=2, n_cols=4, spacing=9.0, width=1.2, height=7.0, gap=12.0, origin=(0, 6, 0))
    L.rebar_column('design', (-1.4, 0, 0), size=1.2, height=6.0, rows=2)
    L.rebar_column('asbuilt', (1.4, 0, 0), size=1.2, height=6.0, rows=1)
    t = L.text('rows', '2줄 → 1줄', (0, -1.0, 4.6), 0.95, 'red', extrude=0.06)
    show_from(t, 24)
    L.photoreal_lamps([(0, -3, 6), (-3, -2, 3)], 1200, 2)
    L.camera([(1, (-7, -5, 3), (6, 4, 3), 24), (14, (1.2, -6.5, 4.2), (0, 0.5, 2.8), 24), (N, (0.9, -5.4, 4.0), (0, 0.5, 2.8), 24)])


def s07():  # column in the hall, then its cage exposed and compression arrows pressing down
    L.underground((0, 0, 3), (400, 400, 200))
    L.column_hall(n_rows=2, n_cols=4, spacing=9.0, width=1.2, height=9.0, gap=12.0, origin=(0, 6, 0))
    L.box('c7.low', (1.2, 1.2, 2.4), (0, 0, 1.2), 'concrete')
    L.box('c7.high', (1.2, 1.2, 0.8), (0, 0, 4.9), 'concrete')
    L.rebar_column('c7', (0, 0, 2.2), size=1.2, height=2.3, rows=1, cut=False, spiral=True)
    bars = [o for o in bpy.data.objects if o.name.startswith('c7.') and o.name != 'c7.conc' and not o.name.startswith(('c7.low', 'c7.high'))]
    conc = bpy.data.objects.get('c7.conc')
    if conc:
        bpy.data.objects.remove(conc, do_unlink=True)  # the middle third is shown as bare cage
    arrows = []
    for x in (-0.4, 0, 0.4):
        arrows += list(L.arrow(f'arr{x}', (x, -0.2, 5.5), 1.0, 'red'))
    show_from(arrows, 45)
    L.photoreal_lamps([(2, -3, 6), (-2, -2, 2)], 1200, 2)
    L.camera([(1, (3.5, -6.5, 1.0), (0, 0, 3.6), 24), (N, (0.8, -4.0, 2.6), (0, 0, 4.3), 26)])


def s08():  # through two columns into the excavation; then the pier with the weak zone ringed in red
    L.underground((100, 10, 0), (700, 700, 300))
    L.box('slot_l', (1.6, 1.6, 14), (-2.2, 0, 4), 'concrete'); L.box('slot_r', (1.6, 1.6, 14), (2.2, 0, 4), 'concrete')
    L.excavation(origin=(0, 6, 0), depth=36, w=20, l=30)
    L.box('pier', (4.0, 1.6, 10), (200, 0, 5), 'concrete')
    for side in (-1, 1):
        L.box(f'pier.wing{side}', (2.0, 1.6, 10), (200 + side * 4, 1.2, 5), 'concrete')
    bpy.ops.mesh.primitive_torus_add(major_radius=0.9, minor_radius=0.06, location=(200, -0.85, 3.6), rotation=(R90, 0, 0))
    ring = bpy.context.object; ring.name = 'ring'; ring.data.materials.append(L.mat('red'))
    show_from(ring, 90)
    L.photoreal_lamps([(200, -4, 6), (0, 10, -6)], 2500, 3)
    L.camera([(1, (0, -9, 6), (0, 30, -6), 26), (55, (0, -0.5, 3.5), (0, 30, -10), 26), (56, (200, -11, 3.6), (200, 0, 3.6), 26),
              (N, (200, -8.5, 3.6), (200, 0, 3.6), 26)])


def s09():  # platform dolly; workers wrapping a column with steel plates, 'steel plate' label
    L.underground((0, 30, 3), (400, 400, 200))
    L.column_hall(n_rows=2, n_cols=7, spacing=8.0, width=1.1, height=5.5, gap=8.0, floor='floor_tile', origin=(0, 0, 0))
    for side in (-1, 1):
        L.box(f'p9.line{side}', (0.3, 60, 0.02), (side * 5.5, 30, 0.01), 'safety_line')
        L.box(f'p9.pit{side}', (4, 60, 1.2), (side * 8.0, 30, -0.6), 'concrete_dark')
    L.worker('pw1', (-0.4, 34, 0), math.radians(180)); L.worker('pw2', (0.5, 35, 0), math.radians(160)); L.worker('pw3', (3.2, 40, 0))
    col = (4.0, 27.0)  # the column being strengthened: plates halfway round it, crew around
    for k, ang in enumerate((0, 90, 180)):
        a = math.radians(ang)
        L.box(f'plate{k}', (1.4, 0.04, 2.6), (col[0] + 0.62 * math.sin(a), col[1] - 0.62 * math.cos(a), 1.4), 'steel', rot=(0, 0, a))
    crew = [L.worker(f'cw{k}', (col[0] + 1.2 * math.cos(t), col[1] + 1.2 * math.sin(t), 0), heading=t + math.pi / 2)
            for k, t in enumerate((math.radians(200), math.radians(250), math.radians(300), math.radians(340)))]
    lab = label_box('lab9', '강판 보강', (col[0], col[1] - 0.8, 3.6), 2.6, 0.8, 0.55)
    show_from(lab, 85)
    L.photoreal_lamps([(0, y, 4.8) for y in (8, 24, 40)], 1500, 3)
    L.camera([(1, (0, -6, 1.7), (0, 40, 1.6), 24), (75, (0.6, 14, 1.7), (2, 40, 1.5), 24), (N, (2.4, 21.5, 2.0), (4, 27, 1.6), 26)])


def s10():  # steel-jacketed round column in a shaft, hologram measurement lines, crane up
    L.underground((0, 0, 10), (400, 400, 200))
    L.cyl('jacket', 1.25, 16, (0, 0, 8), 'steel', verts=48)
    L.photoreal_lamps([(0, -5, 14), (4, -4, 4), (-4, -4, 9)], 3500, 3)
    for k in range(9):
        L.cyl(f'band{k}', 1.33, 0.12, (0, 0, 0.8 + k * 1.8), 'steel', verts=48)
    for k in range(16):
        a = 2 * math.pi * k / 16
        L.box(f'rib{k}', (0.05, 0.06, 16), (1.28 * math.cos(a), 1.28 * math.sin(a), 8), 'steel', rot=(0, 0, a))
    L.cyl('base', 2.0, 0.3, (0, 0, 0.15), 'steel', verts=48)
    for k in range(12):
        a = 2 * math.pi * k / 12
        L.box(f'gusset{k}', (0.05, 0.5, 0.9), (1.55 * math.cos(a), 1.55 * math.sin(a), 0.75), 'steel', rot=(0, 0, a + R90))
    bpy.ops.mesh.primitive_cylinder_add(radius=9.0, depth=24, location=(0, 0, 10), vertices=64)
    shaft = bpy.context.object; shaft.name = 'shaft'; shaft.data.materials.append(L.mat('concrete_dark'))
    for p in shaft.data.polygons:
        p.flip()
    for z in (7.0, 14.0):
        bpy.ops.mesh.primitive_torus_add(major_radius=8.0, minor_radius=1.0, location=(0, 0, z), major_segments=64)
        b = bpy.context.object; b.name = f'balcony{z}'; b.scale = (1, 1, 0.25); b.data.materials.append(L.mat('concrete'))
    L.box('floor10', (20, 20, 0.2), (0, 0, -0.1), 'concrete_dark')
    holo = [L.box('h1', (0.02, 0.02, 10), (0, -1.35, 5), 'holo'), L.box('h2', (8, 0.02, 0.02), (2.5, -1.4, 5.0), 'holo', rot=(0, math.radians(-8), 0)),
            L.box('h3', (0.02, 0.02, 6), (-6, -2, 3), 'holo'), L.box('h4', (0.02, 0.02, 6), (6, -2, 3), 'holo')]
    L.photoreal_lamps([(0, -6, 12), (5, -4, 3)], 3000, 3)
    L.camera([(1, (0, -6.5, 1.2), (0, 0, 4), 24), (N, (0, -7.5, 7.5), (0, 0, 9), 24)])


def s11():  # plunge into the cutaway levels, then aerial of the excavation with the 'decision request' label
    L.city(day=True, hole=(60, 220), frames=(1, N), max_height=35)
    L.station_box(y0=60.0, cutaway=True)
    lab = label_box('lab11', '결정 요청', (0, 120, 14), 12, 3.0, 2.0)
    show_from(lab, 40)
    L.camera([(1, (0, 64, -2), (0, 90, -34), 20), (28, (0, 70, -16), (0, 100, -36), 20), (29, (0, 20, 120), (0, 120, 0), 26),
              (N, (0, 70, 80), (0, 130, -4), 26)])


def s12():  # shield tunnel, rail-level dolly
    L.tunnel(length=180, r=3.6)
    L.photoreal_lamps([(0, y, 5.5) for y in (6, 20, 34, 48)], 800, 1.5)
    L.camera([(1, (0, 0, 2.2), (0, 60, 2.3), 20), (N, (0, 22, 2.2), (0, 80, 2.3), 20)])


def s13():  # platform with skylight beams, train arriving on the right track
    L.underground((0, 60, 3), (400, 400, 200))
    L.box('p13.floor', (8, 120, 1.0), (-1, 60, -0.5), 'floor_tile')
    L.box('p13.pit', (5, 120, 1.4), (5.5, 60, -1.2), 'concrete_dark')
    for xr in (-0.75, 0.75):
        L.box(f'p13.rail{xr}', (0.08, 120, 0.16), (5.5 + xr, 60, -0.4), 'rail')
    L.box('p13.edge', (0.3, 120, 0.02), (2.6, 60, 0.01), 'safety_line')
    L.box('p13.wall', (0.6, 120, 8), (8.4, 60, 3), 'concrete_dark')
    for k in range(14):
        L.box(f'p13.col{k}', (0.8, 0.8, 6.0), (-2.6, 4 + k * 8, 3), 'concrete')
        L.box(f'p13.beam{k}', (12, 0.7, 0.8), (2.5, 4 + k * 8, 6.4), 'concrete')
    L.box('p13.roof_l', (5, 120, 0.4), (-3, 60, 7.0), 'concrete')
    train = L.box('train', (3.0, 40, 3.6), (5.5, 220, 1.6), 'train')
    L.box('train.front', (2.6, 0.1, 1.0), (5.5, 200 - 0.1, 2.2), 'headlight').parent = train
    train.location.y = 160; train.keyframe_insert('location', frame=1)
    train.location.y = 32; train.keyframe_insert('location', frame=N)
    L._linear(train)
    L.photoreal_lamps([(0, y, 6) for y in (10, 30, 50)], 1500, 3)
    L.camera([(1, (0, -2, 1.7), (2, 60, 1.6), 24), (N, (0.4, 14, 1.7), (3, 70, 1.4), 24)])


def s14():  # site office, fast push in
    L.underground((0, 6, 2), (400, 400, 200))
    L.office()
    L.camera([(1, (0, -2.5, 1.6), (0, 15, 1.4), 22), (N, (0, 5.0, 1.4), (0, 15, 1.2), 22)])


def s15():  # desk close-up: laptop with chart, binder
    L.underground((0, 6, 2), (400, 400, 200))
    L.office()
    L.camera([(1, (2.5, 0.2, 1.5), (2.5, 3.3, 0.95), 28), (N, (2.45, 1.7, 1.25), (2.45, 3.35, 0.95), 28)])


def s16():  # aerial, then the station under the road as a hologram, opening schedule
    L.city(day=True, hole=(80, 240), frames=(1, N), max_height=60)
    L.station_box(y0=80.0, cutaway=True, holo=True)
    t = L.text('y2028', '개통 일정', (0, 70, 20), 10.0, 'red', extrude=0.8)
    show_from(t, 45)
    L.camera([(1, (0, -60, 40), (0, 150, 0), 24), (N, (0, 40, 24), (0, 140, -12), 24)])


def s17():  # cathedral-like hall of giant round columns, seen from the balcony, slow descent
    L.underground((0, 30, 10), (400, 400, 200))
    L.column_hall(n_rows=4, n_cols=8, spacing=10.0, width=2.6, height=24.0, gap=10.0, round_cols=True, capitals=False,
                  beams=True, floor='concrete_dark', ceiling_lights=False)
    L.box('balcony', (40, 4, 0.6), (0, -9, 18), 'concrete'); L.box('upper_wall', (40, 1, 30), (0, -11.5, 15), 'concrete')
    for k in range(10):
        L.worker(f'fw{k}', ((k % 5 - 2) * 4.5, 12 + (k // 5) * 18, 0))
    L.photoreal_lamps([(x, 30, 22) for x in (-10, 10)], 8000, 6)
    L.camera([(1, (0, -4.5, 22), (0, 40, 2), 26), (N, (0, -3.0, 15), (0, 40, 4), 26)])


{'s01': s01, 's02': s02, 's03': s03, 's04': s04, 's05': s05, 's06': s06, 's07': s07, 's08': s08, 's09': s09,
 's10': s10, 's11': s11, 's12': s12, 's13': s13, 's14': s14, 's15': s15, 's16': s16, 's17': s17}[shot_id]()
bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, N
