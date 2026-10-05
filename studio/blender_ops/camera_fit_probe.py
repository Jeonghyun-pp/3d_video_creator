"""Screen flow as a function of move progress u, measured once per built shot (camera fit's only Blender step).

With a fixed path, aim target and lens-on-progress, the camera pose depends on u alone, so the screen flow
between two frames is the integral of a per-u flow profile over [u(f), u(f+1)]. The probe bakes the compiled
rig with linear progress over SAMPLES frames, measures flow_proxy between neighbours and returns the
cumulative profile G(u); the host then scores any timing curve without Blender. Moving scene objects and the
whip head are not in the profile (stated in the fit report).

blender -b <version>/scene.blend --python camera_fit_probe.py -- probe_job.json out.json
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).parent))
import camera_rig_core as core  # noqa: E402
from camera_rig import _camera, _key, _path_points, view_of  # noqa: E402
from motion_proxy import flow_proxy  # noqa: E402
from scene_tools import anchor_for  # noqa: E402

SAMPLES = 161


def probe(job):
    rig = json.loads(json.dumps(job['rig']))
    scene = bpy.context.scene
    end = job['frame_count']
    timing = {**rig.get('timing', {}), 'profile': 'linear'}
    for key in ('burst_frac', 'burst_share', 'hold_frac', 'drift', 'points', 'dwell'):   # measure the path itself, not its rhythm
        timing.pop(key, None)
    rig['timing'] = timing
    rig.pop('shake', None)
    path = subject = target = None

    def at_end(identifier):
        scene.frame_set(end)
        obj, point = anchor_for(identifier)
        if obj is None:
            raise ValueError(f'CAMERA_FIT: anchor not found: {identifier}')
        return tuple(point)
    if rig['type'] == 'flythrough':
        path = _path_points(rig['path'])
        if rig.get('look_target'):
            target = [at_end(rig['look_target'])] * SAMPLES
    else:
        subject = [at_end(rig['subject'])] * SAMPLES
        if rig.get('look_target'):
            target = [at_end(rig['look_target'])] * SAMPLES
    for keys in ('lens_keys', 'aim_keys', 'offset_keys'):  # keys on the shot's frame axis -> the probe's
        for k in rig.get(keys) or []:
            k['frame'] = k['frame'] * (SAMPLES - 1) / max(1, end - 1)
    camera = _camera(scene, [])
    view = view_of(camera, scene)
    baked = core.bake(rig, job['fps'], SAMPLES, subject=subject, target=target, path=path, view=view, default_lens=camera.data.lens)
    _key(camera, baked['frames'])
    scene.frame_start, scene.frame_end = 1, SAMPLES
    flow = flow_proxy(scene, camera, 1, SAMPLES)['per_frame']
    cumulative = [0.0]
    for v in flow:
        cumulative.append(cumulative[-1] + v)
    return {'samples': SAMPLES, 'u': [i / (SAMPLES - 1) for i in range(SAMPLES)], 'G': [round(c, 4) for c in cumulative],
            'lens_mm': [round(f['lens'], 3) for f in baked['frames'][::20]]}


if __name__ == '__main__':
    args = sys.argv[sys.argv.index('--') + 1:]
    Path(args[1]).write_text(json.dumps(probe(json.loads(Path(args[0]).read_text()))))
