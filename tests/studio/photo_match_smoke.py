"""Host smoke (needs Blender): match a reference photo in a workbench session, without making a version.

A "photo" of the winch is rendered (lit, studio light) at a known orbit camera; its points are the projections of
the subject's anchors. reference_fit_camera, started far from the answer, recovers that camera; reference_compare at
it scores the silhouette ~1 and finds the drum's box; after a spec edit that makes the drum fatter, the drum's box
and the silhouette show it. No version is created.
Run: .venv/bin/python tests/studio/photo_match_smoke.py
"""
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio import workbench  # noqa: E402
from studio.blender_ops.view_match_core import pose_delta, project_points  # noqa: E402
from studio.common import read_json, write_json  # noqa: E402
from studio.photo_match import add_view, view_path  # noqa: E402

SOURCE = ROOT / 'tests/fixtures/winch_spec'
report = {}


def check(name, cond, detail):
    report[name] = detail
    assert cond, f'{name}: {detail}'


(ROOT / 'projects/harness_validation').mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT / 'projects/harness_validation') as tmp:
    project = Path(tmp) / 'winch_spec'
    shutil.copytree(SOURCE, project, ignore=shutil.ignore_patterns('renders', 'workbench', 'failed_*', 'runs', 'versions'))
    versions_before = sorted(p.name for p in project.glob('shots/*/versions/*'))
    sid = workbench.start(project, subjects=['winch'])['session_id']
    try:
        call = lambda tool, args=None: workbench.call(project, sid, tool, args or {})['result']  # noqa: E731
        call('build_subject', {'subject_id': 'winch'})
        points = call('anchors', {'subject_id': 'winch'})['points']
        truth = {'target': [0.3, 0.0, 0.05], 'distance_m': 2.6, 'azimuth_deg': 210.0, 'elevation_deg': 24.0, 'roll_deg': 2.0,
                 'lens_mm': 42.0, 'width': 600, 'height': 400}
        shot = call('preview', {'views': [{**truth, 'name': 'photo', 'frame_subject': 'winch'}], 'passes': ['lit', 'id'], 'size': 600, 'subject_id': 'winch',
                                'lit_samples': 8, 'lit_light': 'studio'})
        (project / 'references/winch_photo').mkdir(parents=True)
        shutil.copy(shot['images']['photo']['lit'], project / 'references/winch_photo/photo.png')
        from PIL import Image
        ids = Image.open(shot['images']['photo']['id']).convert('RGBA')
        drum_rgb = next(tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c, key in shot['palette'].items() if key == 'winch/drum')
        r, g, b, a = ids.split()
        from PIL import ImageChops
        mask = ImageChops.multiply(ImageChops.multiply(r.point(lambda v: 255 if v == drum_rgb[0] else 0), g.point(lambda v: 255 if v == drum_rgb[1] else 0)),
                                   ImageChops.multiply(b.point(lambda v: 255 if v == drum_rgb[2] else 0), a.point(lambda v: 255 if v > 250 else 0)))
        names = sorted(n for n in points if '/corner' in n)[::3][:12]
        pixels = project_points(truth, [points[n] for n in names])
        marks = [{'anchor': n, 'px': [round(x * 600, 2), round(y * 400, 2)]} for n, (x, y) in zip(names, pixels)]
        add_view(project, 'winch_photo/three_q', 'references/winch_photo/photo.png', 'local_only', 'winch',
                 points=marks, parts={'drum': list(mask.getbbox())}, mask={'invert': False})
        call('set_spec_param', {'subject_id': 'winch', 'pointer': '/photo_views', 'value':
                                [{'id': 'three_q', 'view': 'winch_photo/three_q', 'min_iou': 0.9, 'parts_min_box_iou': 0.8}]})
        call('build_subject', {'subject_id': 'winch', 'root_location': [1.5, -0.7, 0.2]})   # moved: the camera is in the root frame
        record = read_json(view_path(project, 'winch_photo/three_q'))   # start far from the answer
        record['camera'] = {**truth, 'azimuth_deg': 250.0, 'elevation_deg': 5.0, 'distance_m': 4.0, 'lens_mm': 30.0}
        write_json(view_path(project, 'winch_photo/three_q'), record)

        t0 = time.perf_counter()
        points_only = call('reference_fit_camera', {'view': 'winch_photo/three_q', 'refine': False})
        delta = pose_delta(truth, points_only['camera'])
        check('fit_points', delta['view_deg'] < 0.1 and delta['lens_ratio'] < 0.01 and delta['eye_share'] < 0.01, delta)
        fitted = call('reference_fit_camera', {'view': 'winch_photo/three_q', 'refine': True, 'point_tolerance_px': 2.0})
        fit_s = time.perf_counter() - t0
        delta = pose_delta(truth, fitted['camera'])
        check('fit_refine_keeps_points', fitted.get('residual_px_after_refine', 0) <= 2.0 + 0.5 and fitted['cost'] <= fitted['cost_points_only']
              and delta['view_deg'] < 1.0, {**delta, **{k: v for k, v in fitted.items() if k != 'camera'}})
        check('fit_seconds', fit_s < 30, round(fit_s, 1))
        same = call('reference_compare', {'view': 'winch_photo/three_q', 'lit_samples': 8})
        check('same_iou', same['iou'] > 0.95, same['iou'])
        check('same_drum', same['parts']['drum']['box_iou'] > 0.9, same['parts']['drum'])
        check('sheet', Path(same['sheet']).is_file(), same['sheet'])
        gate = call('subject_report', {'subject_id': 'winch'})
        rows = {c['id']: c for c in gate['checks'] if c['kind'] == 'photo'}
        check('gate_photo_passes', rows.get('three_q', {}).get('passed') is True and rows.get('three_q.drum', {}).get('passed') is True, rows)

        call('set_spec_param', {'subject_id': 'winch', 'pointer': '/builders/0/params/profile', 'value':
                                [[0, -0.3], [0.21, -0.3], [0.21, 0.3], [0, 0.3]]})
        fat = call('reference_compare', {'view': 'winch_photo/three_q', 'lit_samples': 8})
        check('edit_shows_in_drum', fat['parts']['drum']['box_iou'] < same['parts']['drum']['box_iou'] - 0.1,
              [same['parts']['drum'], fat['parts']['drum']])
        check('edit_shows_in_iou', fat['iou'] < same['iou'], [same['iou'], fat['iou']])
        gate = call('subject_report', {'subject_id': 'winch'})
        rows = {c['id']: c for c in gate['checks'] if c['kind'] == 'photo'}
        check('gate_photo_sees_edit', rows['three_q.drum']['passed'] is False, rows)
    finally:
        workbench.stop(project, sid)
    check('no_version', sorted(p.name for p in project.glob('shots/*/versions/*')) == versions_before, versions_before)

print('PHOTO MATCH SMOKE OK', report)
