"""Everything a build generates from the authored scene and the shot: fill, camera keys and actions, mechanism drives,
the camera move (and the reveals bound to its cues), simulations, the camera rig, graphics, the look, screen graphics and
the gates judged on the finished scene. build_scene.py calls it after the authored checkpoint; the workbench calls it
on its session after a shot edit, so what the agent previews is what the build makes (one chain, not two).
Raises ValueError with the same codes the build maps (MECHANISM_INTERFERENCE, CAMERA_*, LOOK_QA_FAILED, ...).
"""
import json

import bpy

from scene_tools import apply_actions, apply_camera


def generate(job, output):
    """Run the chain on the current scene; writes its reports into `output` (a Path); returns {'move_report', 'rig_report'}."""
    import contrib_loader   # contrib couplings run in the kinematics below
    contrib_loader.TABLE.update(job.get('contrib') or {})
    scene = bpy.context.scene
    scene['studio_gate_severity'] = json.dumps(job.get('gate_severity') or {})   # gate_policy.py: which taste gates only warn
    # Fill brief: what the topic puts on the declared levels (studio/fill.py), placed before the camera is compiled so
    # clearance and pass-through see it.
    if job['shot'].get('fill_brief'):
        import fill_brief
        fill_brief.apply(job)
    scene.frame_start = 1; scene.frame_end = job['shot']['duration_frames']
    scene.render.fps = job['fps']; scene.render.fps_base = 1
    if job.get('output_size'):
        # Build-time framing (camera rig, anchors, screen-size checks) must use the delivery aspect, as the renderer will.
        scene.render.resolution_x, scene.render.resolution_y = job['output_size']; scene.render.resolution_percentage = 100
    anchored = bool(job['shot']['camera'].get('target_anchor')) and not job['shot']['camera'].get('rig') and not job['shot']['camera'].get('move')
    if anchored:
        import camera_aim
        camera_aim.fill_targets(job['shot'])          # keys without a target aim at the anchor
    if not scene.get('studio_authored_animation', False):
        apply_camera(job['shot']); apply_actions(job['shot'])
    if anchored:
        lost = camera_aim.check_in_view(job['shot'])
        if lost:
            raise ValueError(f"CAMERA_ANCHOR_OUT_OF_VIEW: {job['shot']['camera']['target_anchor']} leaves the frame on frames {lost[:12]}"
                             f"{' ...' if len(lost) > 12 else ''} ({len(lost)} of {job['shot']['duration_frames']})")
    # Mechanisms: drive actions turn joint pivots by their couplings, baked per frame, before the camera measures the scene.
    if any(a['type'] == 'drive' for a in job['shot']['actions']):
        import kinematics
        drive_rows = kinematics.apply_drives(job['shot'], job['fps'])
        (output / 'kinematics_report.json').write_text(json.dumps({'drives': drive_rows}, indent=1))
        clashes = [c for r in drive_rows for c in r['interference']]
        if clashes:
            raise ValueError('MECHANISM_INTERFERENCE: ' + json.dumps(clashes[:5]))
    # A semantic move compiles into a rig here (path + timing from the style); the snapshot keeps the move.
    # Reveals (and any action bound to camera cues) are applied once the camera's pass frames are known.
    reveals = [None]
    move_report = None
    rig_report = None
    if job['shot']['camera'].get('move'):
        from camera_moves import compile_move
        import reveal

        def on_cues(cues):
            reveals[0] = reveal.apply_camera_bound(job['shot'], cues)
        try:
            compiled, move_report = compile_move(job, job.get('motion_style'), on_cues=on_cues)
        except ValueError as error:
            (output / 'camera_move_report.json').write_text(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False, indent=2))
            raise
        (output / 'camera_move_report.json').write_text(json.dumps({'ok': True, **move_report, 'reveal': reveals[0]}, ensure_ascii=False, indent=2))
        job['shot']['camera']['rig'] = compiled
    elif any(a['type'] in ('reveal', 'simulate') for a in job['shot']['actions']):
        import reveal
        reveal.apply(job['shot'])
        if any(a['type'] == 'simulate' for a in job['shot']['actions']):
            import simulate
            (output / 'simulation_report.json').write_text(json.dumps({'simulations': simulate.apply(job['shot'])}, indent=2))
    # The motion style's blur target reaches every shot that names a style (move or not); an explicit realism value wins.
    if job.get('motion_style'):
        from camera_moves_core import realism_with_style
        job['shot']['camera']['realism'] = realism_with_style(job['shot']['camera'], job['motion_style'])
    # A declared camera rig owns the camera even when the author animated everything else.
    if job['shot']['camera'].get('rig'):
        from camera_rig import bake_camera_rig
        rig_report = bake_camera_rig(job)   # judged after the look below, on the scene that is saved
        (output / 'camera_rig_report.json').write_text(json.dumps(rig_report, ensure_ascii=False, indent=2))
    # A backdrop image behind everything, fixed to the camera (needs the final camera path and lens).
    if (job['shot'].get('scene') or {}).get('backdrop'):
        import backdrop
        (output / 'backdrop_report.json').write_text(json.dumps(backdrop.build(job), indent=1))
    # Explainer graphics (Grease Pencil, own render layer): built once the camera exists, hidden from every other pass.
    if job['shot'].get('graphics'):
        import graphics
        cues = (move_report or {}).get('camera_cues') if job['shot']['camera'].get('move') else None
        (output / 'graphics_report.json').write_text(json.dumps({'graphics': graphics.build(job['shot'], cues)}, indent=2))
    scene.frame_set(1)
    if move_report:   # exposure keys may name camera cues
        job['camera_cues'] = move_report.get('camera_cues')
    from look import apply_look
    look_report = apply_look(job, scene)
    (output / 'look_report.json').write_text(json.dumps(look_report, ensure_ascii=False, indent=2, default=str))
    if look_report['gate_failures']:
        raise ValueError('LOOK_QA_FAILED: ' + json.dumps(look_report['gate_failures'][:10], ensure_ascii=False))
    # Declared grade / compositor / engine settings / add-ons, composed on top of the look (expressive_core.py)
    import expressive
    expressive_report = expressive.apply(scene, job['shot']['render'])
    if expressive_report:
        (output / 'expressive_report.json').write_text(json.dumps(expressive_report, indent=1, default=str))
    # Screen-space graphics: after the look, which may key the lens shift (two-point) that places them in the frame.
    if any(g.get('space') == 'screen' for g in job['shot'].get('graphics') or []):
        import graphics
        cues = (move_report or {}).get('camera_cues') if job['shot']['camera'].get('move') else None
        world = json.loads((output / 'graphics_report.json').read_text())['graphics'] if (output / 'graphics_report.json').is_file() else []
        (output / 'graphics_report.json').write_text(json.dumps({'graphics': world + graphics.build_screen(job['shot'], cues)}, indent=2))
    # Gates on the finished scene: the look may key a lens shift (two-point), add shake and move parts (perfection), so the
    # framing guards, the fill gate and the subject measurements all read the scene that is saved, not the one before.
    if job['shot']['camera'].get('rig'):
        from camera_rig import verify_after_look
        rig_report = verify_after_look(job, rig_report)
        (output / 'camera_rig_report.json').write_text(json.dumps(rig_report, ensure_ascii=False, indent=2))
        if rig_report['gate_failures']:
            raise ValueError('CAMERA_RIG_GUARD_FAILED: ' + json.dumps(rig_report['gate_failures'][:10], ensure_ascii=False))
    if job['shot'].get('fill_brief'):   # seen levels carry their subject or identity, the subject is not hidden, nothing off-brief
        import fill_brief
        fill_brief.check(job, output)
    return {'move_report': move_report, 'rig_report': rig_report}
