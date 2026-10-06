# shot.camera.rig — fields and recipes

Read when a shot has `camera.energy: high`, follows a moving subject, or flies through structure.
Schema: `schemas/studio-v1/shot.schema.json` → `camera.rig`. Baked by `studio/blender_ops/camera_rig.py`
(math in `camera_rig_core.py`) after the author script; report in `versions/<v>/camera_rig_report.json`.

## Contract
- `movement: "rig"`, `keys: []`, `rig` object. Frames are 0-based shot frames.
- The camera must have no parent or constraints. The rig clears and re-keys it every frame
  (location, rotation_quaternion, lens; LINEAR). An author camera animation is overridden
  (`warnings: author_camera_overridden`).
- Subjects and paths are anchors resolved with `scene_tools.anchor_for` (`studio_id`, object
  name, `studio_anchors` entries or `<id>/center`). Give animated roots a `studio_id`.
- Do not create helper empties or parent the camera to subjects; preserve checks object trees.

## Fields
| field | meaning |
|---|---|
| `type` | `chase` (offset in the subject's travel frame), `follow` (offset frozen to the first-frame heading), `orbit` (circle around subject), `flythrough` (camera rides `path` at `speed_mps`), `procedural` (`script` supplies per-frame overrides) |
| `subject` / `look_target` | anchor ids. Aim = lerp(subject, look_target, `aim_keys.blend`) + `lift_m` up |
| `offset_keys[{frame, offset_m:[right, up, back], ease}]` | chase/follow/flythrough offset, eased between keys (`linear` per key optional) |
| `aim_keys`, `lens_keys` | eased keys |
| `screen_anchor {x, y, weight}` | pins the subject at screen (x, y) top-left origin; weight blends with the aim rotation (jet port: .52, .66, .72) |
| `roll {follow_bank, max_deg}` | camera roll = coordinated-turn bank of the subject trajectory × follow_bank, clamped |
| `orbit {radius_m, height_m, start_deg, deg_per_s}` | orbit only |
| `path`, `speed_mps`, `start_offset_m`, `look_ahead_m` | flythrough only; path = curve or vertex-chain object |
| `smoothing {position_s, aim_s}` | zero-phase filter time constants (no lag) |
| `pitch_limit_deg` | default 60 |
| `shake {amp_deg, freq_hz, seed}` | deterministic, small (<= 0.5 deg for chase feel) |
| `motion_blur_shutter` | sets scene motion blur |
| `guards` | `subject_margin` (.03), `look_target_visible` (true), `max_hidden_s` (.5), `min_clearance_m`, `clearance_ids` (real mesh distance), `near_field_m` (10), `min_subject_path_speed_mps` |
| `script` | `camera_rigs/<name>.py` inside the project defining `camera_state(t, ctx) -> {offset_m, blend, lift_m, lens_mm, roll_deg}` (any subset) |

## Moves, timing and motion styles (`camera.move`)
A move says what the camera is about; it compiles at build into a `flythrough` (or `orbit`) rig, which then bakes
and is guarded exactly like a hand-written rig. `camera.rig` and `camera.move` are exclusive; the shot snapshot
keeps the move, `versions/<v>/camera_move_report.json` keeps the compiled rig, resolved geometry and repairs.

The full list of what each move reads, with its defaults, is `camera_moves_core.PARAMS` (tested against the planners;
`validate_shot` refuses params a move never reads). Which rig keys a rig type reads, which timing keys a profile reads
and which move keys an orbit move ignores are `camera_keys.py` (an orbit has no `whip_in_deg`, `clearance_m`,
`look_target` or `arrive`; a timed flythrough ignores `speed_mps`; `lens_end_mm` needs `lens_mm`). A keyed camera's
`target_anchor` is what it is about: keys without `target` aim at it, and the build fails if it leaves the frame.
The table below is a guide, not the list.

| move | params (scene refs = anchors/objects) | use when the narration shows |
|---|---|---|
| `waypoints` | `points[]` (refs or [x,y,z]), `aim` (ref, point or `ahead`) | any route the named moves do not cover — the general form |
| `dive_through` | `opening` (object/empty whose box is the hole), `below`, `above_m`, `back_m`, `approach` | going from the street into what is under it |
| `pass_between` | `a`, `b` (members), `target`, `height_m`, `approach_m`, `beyond_m` | threading columns, rebar, beams (near-field parallax) |
| `descend_levels` | `section` (box), `from_z`, `to_z`, `inset_m`, `aim` | dropping floor by floor inside a cut section |
| `push_in` | `target`, `from_m`, `to_m`, `height_m`, `azimuth_deg` | closing on a detail |
| `crane` | `target`, `from_h`, `to_h`, `dist_m`, `azimuth_deg` | rising from ground level to an overview |
| `orbit_reveal` | `target`, `radius_m`, `height_m`, `start_deg`, `sweep_deg` | turning an object to show its other side |
| `turntable` | `target`, `fill` (0.8), `distance_scale`, `elevation_deg` (25), `start_deg`, `sweep_deg` (120) | turning around one object; the distance is fitted from its box and the lens, so it frames a 16 cm gearbox and a 60 m hall alike |
| `slide` | `target`, `fill` (0.8), `distance_scale`, `elevation_deg` (15), `azimuth_deg`, `span` (0.8 of its width) | trucking past an object aimed at it: parallax separates layers (ring / planets / carrier) |
| `macro_push` | `target`, `detail` (a part id) or `detail_size_m`, `fill`, `detail_fill` (0.6), `distance_scale`, `elevation_deg` (30), `azimuth_deg` | from the whole object in to one detail of it (a tooth mesh); refused when the detail is not smaller than the object |
| `section_push` | `section` (box of the structure; its -Y face is the cut), `fill` (share of the frame width, 0.45–0.6), `centre_v` (screen height of the section centre, 0.6–0.7), `back_m`, `above_m`, `into_m`, `inside_z` | the architectural cutaway: come down level in front of a section cut through the ground and push into it (marks `cam-front`, `cam-inside`); stage the cut with `section_staging.md` |

Move-level fields: `style`, `timing` (overrides the style), `lens_mm`/`lens_end_mm` (lens rides the same progress),
`whip_in_deg` (aim swings in over the first 0.25 s; turns the target-visibility guard off for that shot),
`clearance_m` (waypoints pushed out of geometry; the spline between them is still checked only by
`guards.min_clearance_m`), `motion_blur_shutter`, `look_target`, `guards`. A new kind of route is `waypoints`,
not a new move type.

**Framing (`move.framing`)** — `{horizon_v, hold_until_cue?, offset_frames?, blend_frames?}`: the rig holds the
horizon at screen height `horizon_v` (0 = top) by pitch, then (from the cue) eases over `blend_frames` (13) to the
move's own aim; look_camera's two-point trades that pitch for a lens shift, so verticals stay vertical. Without a cue
the hold lasts the shot. Gate `framing`: a held frame off by > 0.02 (`guards.framing_tolerance`). Measured on s01
v0018: aiming at the target below the road all along put the horizon at v 0.19 (reference 0.39–0.41) — the head of
the shot read as "looking down", not open. Set it from a composition style (`composition style learn` on the
reference head; `shot.render.composition_style` + `composition_span_s` checks the render, advisory): vp_v ≈ horizon,
sky_share, skyline_c. Sky the camera cannot buy (tall near buildings) comes from the street's `sightline` rule
(`environment_kits.md`).

**Dwell (`move.dwell: [{cue, seconds}]`)** — the camera lingers at a cue: the progress curve is time-warped (monotone
cubic) so it eases into a near-stop at the cue's mark for `seconds` (covering only `drift` 0.004 of the move) and
eases out; every later cue, reveal, framing release and exposure key follows the dwelled curve
(`camera_move_report.json` `dwell`: start/end frames). Take the length from a style (`motion style learn --video ref
--range a,b` measures one window and stores `dwell` beside the features: the longest interior run under 0.3 of the
shot's peak rate), never by eye. Measured 2026-10-06 on the reference 0–3.1 s: no dwell — motion rises from 9–12 to a
peak of 25 at 0.8 of the shot (burst_share 0.21); the "stop before the section" impression was not in the numbers, so
s01 got no dwell. `camera fit` searches burst_settle only (front-loaded) and cannot express that accelerating shape
yet (objective 85, LEVEL_LOW 0.29) — extending the fit to `points` curves is open.

Path invariant: the dense path never runs backwards along a waypoint chord (`MOVE_PATH_LOOP`; centripetal
Catmull-Rom). Measured: uniform Catmull-Rom looped at s01's 0.4 m thick opening (y 72.05 → 71.74, z bounce
+0.78/−1.50 m, the f46→47 lens-shift jump). `dive_through` puts `inside` ≥ 1.5 m under the slab (`inside_depth_m`).

Timing is one monotone progress curve u(t) for the whole move (`rig.timing`, also usable on a hand rig):
`burst_settle {burst_frac, burst_share, hold_frac, drift}` reaches burst_share of the path by burst_frac of the
shot, decelerates and creeps `drift` over the last hold_frac; `ease_in_out`, `linear`, `points [[t,u],...]`.
A timed flythrough covers `distance_m` (default: the whole compiled path); a timed orbit sweeps `sweep_deg`.

Styles are numbers learned from reference reels — no frames kept: `motion style learn --name X --video ref.mp4`
(cuts, per-shot envelope: burst_share, peak_t, decay_half_s, hold_frac, mean/p95 MAD, head_whip; quartiles),
`motion style show`, `motion style check --name X --video edit.mp4 --snapshot edit.snapshot.json`.
One reference sets `overfit_risk` — learn from 2+ reels before treating a style as general.
`camera fit --project P --shot S [--style X] [--apply]` probes the built shot once (screen flow vs progress)
and fits burst_frac/burst_share/hold_frac to the style medians without rendering; `--apply` revises the shot.
`LEVEL_LOW`/`LEVEL_HIGH` hints mean the path, not the timing, is wrong for the style: lengthen/shorten the move or
bring it nearer/farther from geometry. Proxy calibration (`library/motion_styles/_calibration.json`): window
Spearman 0.835 to rendered MAD, absolute level ±50 %. The style's `target_blur_px` becomes the shot's
`camera.realism.target_blur_px` (scene blur target; label anchors stay ≤ 2 px).

Reveals and camera cues: `compile_move` publishes the frame the camera passes each mark as a cue `cam-<mark>`
(`cam-wp0..n`, plus `cam-mouth`/`cam-inside` for dive_through, `cam-front`/`cam-inside` for section_push, `cam-gap` for pass_between). Any action's
`time_binding` may name them like speech cues, so a scene event finishes before the camera arrives however the
move is re-timed. The `reveal` action opens geometry progressively: a hidden mesh cutter (its faces become the
cap) keyed in location/rotation/scale at fractions `t` of the interval; `also_cut_overlapping` also cuts every
static closed mesh in the cutter's final volume (road markings), skipping animated ones (cars). The cut exists from
its first key only (the boolean is keyed off before it, so no hole opens early), and every target and the cutter
must be closed and wound outward (`REVEAL: … inside-out`, signed volume > 0): measured on s01, inside-out road
strips made the MANIFOLD cut leave a seam that opened and closed with the hole. `move.arrive:
[{cue, not_before_s}]` keeps meaning ahead of rhythm: the build reports `arrive_violations`, `camera fit` turns
them into a hard penalty and searches a slow head (`timing.head_frac`: creep `head_share` 0.06, then the burst).
`guards.clip_auto: true` sets the camera's clip_start under half the closest clearance (<= 0.1 m) and clip_end past
the farthest geometry, and fails `clip_start` if the camera gets closer than its near plane. Sensor fit VERTICAL is
honoured (the fitted sensor dimension is passed to the rig math). Every rig fails `passes_through_geometry` when a frame-to-frame camera step crosses a render-visible mesh as it
stands at that frame (closed road, a jet flying through the camera); hidden helpers are stepped over.
A target hidden by design until the reveal (under the road) needs `guards.max_hidden_s` raised for that shot.

When a move fails — in order: 1) `CAMERA_MOVE_FAILED`: fix the reference (an object/anchor that exists at frame 1);
2) `CAMERA_RIG_GUARD_FAILED` on clearance: move the waypoint params (`above_m`, `back_m`, `height_m`) or set
`clearance_m`; 3) express the route as `waypoints` with an extra point around the obstacle. Forbidden: deleting
the clearance guard or going back to hand keys to make the build pass.

## Recipes
- Fast chase (jet canyon port, `projects/harness_validation/jet_canyon_rig`): chase, offset (5-13, 8, 22) → high moments (2.5, 15-17, 11), lens 24→27 / 18 when high, aim blend .15→.32, screen_anchor (.52, .66, .72), roll .16/16°, clearance guard on walls.
- Architectural intro fly-through: flythrough on a path 2-10 m from repeating members, 20-25 m/s, lens 20 mm, look_ahead 10-25 m, 2-4 s.
- Product/structure reveal: orbit radius 3-6× object size, 15-30°/s, lens 35-50 mm, energy medium.
- Mechanism explanation: no rig; `keys` with calm energy.

## Verification
- Graphics in the scene (role `graphic`: titles, 3D text) are classified per frame against the frame edge; cut by the
  edge for > 2 frames fails `graphic_in_frame` (s01 v0018's 3D title was partly above the frame for frames 1–37).
  Words belong in 2D `shot.titles` (`titles.md`), never 3D text.
- Build fails with `CAMERA_RIG_GUARD_FAILED` and lists frames; the failed version is kept as `failed_*`.
- `studio qa motion` measures screen motion; `qa collect` adds per-shot energy warnings
  (high: mean < 2.5 or > 10 % near-still frames; calm: p95 > 6).

## Rules moved from SKILL.md (2026-10-04, verbatim)

<CRITICAL>
**Decide `camera.energy` (calm | medium | high) per shot before authoring, and make a high-energy camera with `camera.rig`, never with a camera loop inside the author script.** Speed feel comes from the camera following the subject every frame, near-field geometry sweeping past (parallax), a wide lens and banking — not from render quality. Measured at 448 px: Otis explainer shots 0.06-0.64 (calm keys), rig-style jet chase 2.86, its Blender reference 3.62. A hand-written camera loop bypasses the rig's guards (subject in frame, no wall penetration, no reversing subject, pitch limit) and must be rewritten for every shot. Instead declare `camera.rig` (`chase | follow | orbit | flythrough`; `procedural` + `camera_rigs/<name>.py` returning `camera_state(t, ctx)` only when keys cannot express it) and let the build bake and verify it.
</CRITICAL>

Bad: explainer label shot made `energy: high` with a 20 mm orbit — labels smear; or an intro fly-through made with two static `keys` 6 m apart at 50 mm — reads as a slow push.
Good: label/mechanism shot `energy: calm`, `keys`, 35-85 mm; intro/transition/scale shot `energy: high`, `rig.type: flythrough` along a path 2-10 m from repeating structure, 18-28 mm, 2-4 s.

| high-energy rule | value | checked by |
|---|---|---|
| near-field structure | within 2-10 m of camera, `near_field_ratio` >= 0.5 | `camera_rig_report.json` summary (needs `guards.clearance_ids`) |
| clearance | `guards.min_clearance_m` >= 1.5 | build gate `CAMERA_RIG_GUARD_FAILED` |
| lens | 18-28 mm | `lens_range_mm` |
| roll | `roll.follow_bank` 0.1-0.3, `max_deg` <= 20 | `max_abs_roll_deg` |
| pitch | <= 60 deg (top-down views lose the subject) | build gate |
| shot length | 2-4 s | shot `duration_frames` |
| motion blur | `motion_blur_shutter` 0.5 unless labels must stay sharp | render |
| screen motion | mean >= 2.5, or >= 70 % of a reference | `qa motion` / `qa collect` warnings |

When a rig guard fails — in order: 1) widen `offset_m` back/up or adjust `screen_anchor` for the failing frames shown in `camera_rig_report.json`; 2) change lens keys; 3) add `smoothing`; 4) change the subject choreography in the author script. Forbidden: deleting or loosening a guard to make the build pass without a recorded reason.

After rendering a high-energy shot run `.venv/bin/python -m studio qa motion --video <clip> [--reference <ref> --reference-start N --frames N]` and report mean, window ratios and still ratio. → Full rig fields and recipes: `references/camera_rig.md`.

