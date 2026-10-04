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

## Recipes
- Fast chase (jet canyon port, `projects/harness_validation/jet_canyon_rig`): chase, offset (5-13, 8, 22) → high moments (2.5, 15-17, 11), lens 24→27 / 18 when high, aim blend .15→.32, screen_anchor (.52, .66, .72), roll .16/16°, clearance guard on walls.
- Architectural intro fly-through: flythrough on a path 2-10 m from repeating members, 20-25 m/s, lens 20 mm, look_ahead 10-25 m, 2-4 s.
- Product/structure reveal: orbit radius 3-6× object size, 15-30°/s, lens 35-50 mm, energy medium.
- Mechanism explanation: no rig; `keys` with calm energy.

## Verification
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

After rendering a high-energy shot run `../.venv/bin/python -m studio qa motion --video <clip> [--reference <ref> --reference-start N --frames N]` and report mean, window ratios and still ratio. → Full rig fields and recipes: `references/camera_rig.md`.

