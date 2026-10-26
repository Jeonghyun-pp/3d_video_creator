"""Everything a build generates from the authored scene and the shot: fill, camera keys and actions, mechanism drives,
the camera move (and the reveals bound to its cues), simulations, the camera rig, graphics, the look, screen graphics and
the gates judged on the finished scene. build_scene.py calls it after the authored checkpoint; the workbench calls it
on its session after a shot edit, so what the agent previews is what the build makes (one chain, not two).
Raises ValueError with the same codes the build maps (MECHANISM_INTERFERENCE, CAMERA_*, LOOK_QA_FAILED, ...).
"""
import json

import bpy

from scene_tools import apply_actions, apply_camera

SINK_TOL_M = 0.02   # a subject may touch what it stands on this deep (mesh contact); measured 2026-10-14: feet 0.011 m into
                    # their support read as standing, 0.30 m after a lean as a crash


def prepare(job, output):
    """The chain up to the scene the cheats, openings and camera are built on: fill, timing and size, camera keys and
    actions, mechanism drives (reports into `output`). generate runs it first; frames check (frames_check.py) runs it
    to solve the camera without building the rest."""
    import contrib_loader   # contrib couplings run in the kinematics below
    contrib_loader.TABLE.update(job.get('contrib') or {})
    import simulate         # trajectories computed earlier for the same inputs (simulate.CACHE)
    simulate.CACHE.update({'read': job.get('simulation_cache'), 'write': str(output / 'simulation_cache')})
    scene = bpy.context.scene
    scene['studio_gate_severity'] = json.dumps(job.get('gate_severity') or {})   # gate_policy.py: which taste gates only warn
    # Fill brief: what the topic puts on the declared levels (studio/fill.py), placed before the camera is compiled so
    # clearance and pass-through see it.
    if job['shot'].get('fill_brief'):
        import fill_brief
        fill_brief.apply(job)
    scene.frame_start = 1; scene.frame_end = job['shot']['duration_frames']
    scene.render.fps = job['fps']; scene.render.fps_base = 1
    # A stand-in (layout.py, scene.stand_ins) whose id the author also gave an object of its own: the author built the
    # element, so the clay box would stand beside it as a second one.
    stood = {o.get('studio_id') for o in scene.objects if o.get('studio_element')}
    twice = sorted(i for i in stood if sum(1 for o in scene.objects if o.get('studio_id') == i and not o.get('studio_element')))
    if twice:
        raise ValueError(f"STAND_IN_DUPLICATE: the author built {twice} as well as the shot's stand-ins - drop the stand-ins "
                         '(scene.stand_ins) or the author\'s objects')
    # The environment shell (env_shell.py): the ground to the horizon and the sky the world declares, built before the
    # camera so its rays, the support check and the probe see them; sized to the solved camera in generate.
    if job.get('shell'):
        import env_shell
        shell = env_shell.build(job)
        (output / 'env_shell_report.json').write_text(json.dumps(shell, indent=1))
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
    # What the lines explain, happening (direction.change, change_scene.py): before the drives it may add, the openings and
    # the camera, so all of them see the scene changing.
    import change_scene
    change_rows = change_scene.apply(job)
    (output / 'change_report.json').write_text(json.dumps({'changes': change_rows}, indent=1))
    # Mechanisms: drive actions turn joint pivots by their couplings, baked per frame, before the camera measures the scene.
    if any(a['type'] == 'drive' for a in job['shot']['actions']):
        import kinematics
        drive_rows = kinematics.apply_drives(job['shot'], job['fps'])
        (output / 'kinematics_report.json').write_text(json.dumps({'drives': drive_rows}, indent=1))
        clashes = [c for r in drive_rows for c in r['interference']]
        if clashes:
            raise ValueError('MECHANISM_INTERFERENCE: ' + json.dumps(clashes[:5]))
    # People: the limbs on what they hold, the body bent to the stance and clear of solid things (character_contacts.py),
    # on the scene the author and the drives made - before the clearance check, the openings and the camera see it.
    if any(r.get('contacts') or (r.get('stance') or 'stand') != 'stand' for r in (job['shot'].get('scene') or {}).get('characters') or []):
        import character_contacts
        try:
            posed, job['character_held'] = character_contacts.apply(job)
        except ValueError as error:
            (output / 'character_pose.json').write_text(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False, indent=1))
            raise
        character_contacts.write(output, posed)


def generate(job, output):
    """Run the chain on the current scene; writes its reports into `output` (a Path); returns {'move_report', 'rig_report'}."""
    prepare(job, output)
    scene = bpy.context.scene
    # Declared cheats (studio/cheat_core.CHEATS, camera.move.frames[].cheats): the scene and the frame targets as they will
    # be shot, before the support check, the openings and the camera see them (cheat_scene.py)
    allowance = None
    if any(f.get('cheats') for f in ((job['shot']['camera'].get('move') or {}).get('frames') or [])):
        import cheat_scene
        allowance = sink_allowance(job)   # how deep what the cheats move stood before them
        (output / 'cheat_report.json').write_text(json.dumps(cheat_scene.apply(job), ensure_ascii=False, indent=1))
    # What the subjects stand on (scene_roles 'support'), judged on the scene the author, fill and drives made: no frame
    # puts a subject into it (SUBJECT_SINKS, broken) - before the camera and the openings are built around a wrong pose.
    sinks = _sinks(job, allowance)
    if sinks:
        (output / 'support_report.json').write_text(json.dumps(sinks, ensure_ascii=False, indent=1))
        if sinks['gate_failures']:
            raise ValueError('SUBJECT_SINKS: ' + sinks['gate_failures'][0]['why'])
    # A person's body passes through nothing solid (CHARACTER_THROUGH_OBJECT, broken): judged on the posed scene, before
    # the camera frames it (character_clearance.py; 2026-10-19, the bike reel's mechanic went through the frame).
    import character_clearance
    clear = character_clearance.check(job, job.get('character_held'))
    if clear:
        character_clearance.write(output, clear)
        if clear['gate_failures']:
            raise ValueError('CHARACTER_THROUGH_OBJECT: ' + clear['gate_failures'][0]['why'])
    # A semantic move compiles into a rig here (path + timing from the style); the snapshot keeps the move.
    # Reveals (and any action bound to camera cues) are applied once the camera's pass frames are known.
    reveals = [None]
    move_report = None
    rig_report = None
    # The shot list's show (show_core): the openings its moments need are built before the camera is solved, so the
    # camera keeps each picture's view and sees the parts because the subject is open (show_scene.py).
    import show_scene
    show_rows = show_scene.apply(job)
    medium_rows = show_scene.media(job)   # the world's media see-through from the first frame (show_core.MEDIUM)
    if medium_rows:
        (output / 'medium_report.json').write_text(json.dumps({'media': medium_rows}, ensure_ascii=False, indent=1))
    show_check = {}
    if show_rows:
        (output / 'show_report.json').write_text(json.dumps({'openings': show_rows}, ensure_ascii=False, indent=1))
    if job['shot']['camera'].get('move'):
        from camera_moves import compile_move
        import reveal

        def on_cues(cues):
            reveals[0] = reveal.apply_camera_bound(job['shot'], cues)
            if show_rows and not show_check:   # the cuts are built, the camera not yet solved: does each cut uncover its parts?
                show_check.update(show_scene.check_cuts(job, show_rows))
                (output / 'show_report.json').write_text(json.dumps({'openings': show_rows, **show_check}, ensure_ascii=False, indent=1))
                if show_check['gate_failures']:
                    raise ValueError('SHOW_CUT_MISSES: ' + show_check['gate_failures'][0]['why'])
        try:
            compiled, move_report = compile_move(job, job.get('motion_style'), on_cues=on_cues)
        except ValueError as error:
            (output / 'camera_move_report.json').write_text(json.dumps({'ok': False, 'error': str(error), **({'failure': error.report} if getattr(error, 'report', None) else {})},
                                                                       ensure_ascii=False, indent=2))
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
    if any(r.get('near') for r in (job['shot'].get('scene') or {}).get('instances') or []):
        import place_near   # subjects placed by where they show: moved now the camera is known, before anything is measured
        placed = place_near.apply(job)
        (output / 'place_report.json').write_text(json.dumps(placed, ensure_ascii=False, indent=1))
        if placed['gate_failures']:
            first = placed['gate_failures'][0]
            raise ValueError(f"PLACE_NEAR_MISSED: {first['id']} - {first['why']}")
    if job.get('shell') and (output / 'env_shell_report.json').is_file():   # the shell reaches the solved camera's horizon
        import env_shell
        fitted = env_shell.fit(job, json.loads((output / 'env_shell_report.json').read_text()))
        (output / 'env_shell_report.json').write_text(json.dumps(fitted, indent=1))
        if (fitted or {}).get('out_of_space'):   # a camera that leaves the space its world films the shot in (CAMERA_OUT_OF_SPACE)
            raise ValueError(fitted['out_of_space'])
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
    see_rows = show_scene.see_through(job)   # after the look and its materials: see-through skins wrap the final ones
    if see_rows:
        (output / 'show_report.json').write_text(json.dumps({'openings': show_rows, **show_check, 'see_through': see_rows}, ensure_ascii=False, indent=1))
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
    if move_report and job['shot']['camera'].get('move'):   # each pictured moment as the final camera sees it (A5)
        from camera_moves import measure_built
        measure_built(job, move_report)
        (output / 'camera_move_report.json').write_text(json.dumps({'ok': True, **move_report, 'reveal': reveals[0]}, ensure_ascii=False, indent=2))
    if (move_report or {}).get('scale_through'):   # a dive's carried things on the finished camera (scale_join.json)
        import scale_through
        scale_through.write(job, output, move_report['scale_through'])
    if job['shot'].get('fill_brief'):   # seen levels carry their subject or identity, the subject is not hidden, nothing off-brief
        import fill_brief
        fill_brief.check(job, output)
    return {'move_report': move_report, 'rig_report': rig_report}


def _has_support(scene):
    from scene_roles import counts
    return any(o.type == 'MESH' and counts(o, 'floor') for o in scene.objects)


def sink_allowance(job, cheats=None):
    """{object: m} how deep each object the scene cheats are about to move, scale or turn (`cheats`, else every one the
    shot's frames declare) reaches into a support as the scene stands now (camera_frame.sink_depths) - run before they
    are applied; {} without a support or such a cheat."""
    import camera_frame
    import cheat_scene
    scene = bpy.context.scene
    if cheats is None:
        cheats = [c for f in ((job['shot']['camera'].get('move') or {}).get('frames') or []) for c in f.get('cheats') or []]
    names = cheat_scene.moved_by(cheats)
    if not names or not _has_support(scene):
        return {}
    return camera_frame.sink_depths(scene, names, range(job['shot']['duration_frames']))


def _sinks(job, allowance=None, only=None, among=()):
    """The deepest any subject the shot moves (job.probe.subjects: the camera's target, shot.subjects, scene instances,
    screen.subject; collection instances included) or any thing a cheat moved, scaled or turned (cheat_scene.TAG, even
    held still over the shot) reaches into a support over the shot (camera_frame.sinks), judged against SINK_TOL_M past
    how deep it stood before the cheat (`allowance`, sink_allowance) under the project's gate policy: {deepest, moved,
    tolerance_m, gate_failures, warnings}; None when the scene has no support or nothing to judge. A subject placed once
    and never moved is not judged (a basement below the ground is built that way) - see camera_frame.sinks for the limit.
    only: judge these objects alone, as moved (frames check: what one trial's cheats move, not what sank before it);
    among: the rest of the subjects they belong to, read for where the support is but not judged - a flat part taken apart
    wholly into the ground has its own top under the ground's top too, so read alone the ground is its ceiling."""
    import camera_frame
    import cheat_scene
    import gate_policy
    from scene_index import resolve_ref
    scene = bpy.context.scene
    if not _has_support(scene):
        return None
    if only is not None:
        cheated = set(only)
        names = cheated | set(among)
    else:
        cheated = cheat_scene.moved_objects()
        names = {o.name for ident in (job.get('probe') or {}).get('subjects', []) for o in ((resolve_ref(ident) or {}).get('objects') or [])}
        names |= cheated
    if not names:
        return None
    deepest, moved = camera_frame.sinks(scene, names, range(job['shot']['duration_frames']), moved=cheated, allowance=allowance)
    failures = []
    if deepest and deepest['depth_m'] - deepest.get('allowance_m', 0.0) > SINK_TOL_M:
        before = f" ({deepest['allowance_m']:.3f} m before a cheat moved it)" if deepest.get('allowance_m') else ''
        failures.append({'gate': 'SUBJECT_SINKS', **deepest,
                         'why': f"{deepest['object']} reaches {deepest['depth_m']:.3f} m into {deepest['support']} at shot frame "
                                f"{deepest['frame']}{before} (at {deepest['at']}; allowed {SINK_TOL_M} m) - pose the subject on its "
                                'support at that moment (the author script, its drives, its lean or its cheat), or move the support'})
    errors, softened = gate_policy.split(failures, 'gate')
    return {'deepest': deepest, 'moved': moved, 'tolerance_m': SINK_TOL_M, 'gate_failures': errors,
            'warnings': [f"SUBJECT_SINKS (warning by policy): {f['why']}" for f in softened]}
