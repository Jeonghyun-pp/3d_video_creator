---
name: reel-production
description: Create and revise technical cutaway, exploded-view and architectural reels with this repository's studio CLI — Blender scenes, per-shot routing to Blender / generative / hybrid, photoreal look presets, camera rigs, reusable 3D assets and reference-frame review. Use for scene creation, reel production, part or camera changes, look and asset work, and production recovery.
---

Work from `ai_technical_visualization_starter`. Run `../.venv/bin/python -m studio --help` for the installed interface. Read `docs/AGENT_BUILD_PLAN.md` selectively for contracts and `docs/BUILD_REPORT.md` for verified capability when present. The design isn't proof of implementation.

The requested orchestrator is `gpt-6-astra`; the launcher `scripts/reel_agent.py` requests it explicitly. Never claim a different active model is Astra. Use authorized independent subagents for research, implementation and read-only review. Keep one writer per scene. Astra handles shot design, visual decisions and orchestration; Sol handles bounded implementation; Luna may handle checkable inventories. Report actual model availability.

Turn the request into brief/project/shot JSON. Preserve explicit intent and reference identity. An illustrative model must not be represented as a surveyed real building; invented internals aren't documented fact.

<HARD-GATE>
Paid generation: do not run `generate clip`, `asset image3d` or any fal/Google call until the user approved that shot and its cost in this conversation AND `route approve --evidence "<their words>"` recorded it. No exceptions. Never write, paraphrase into, or infer an approval yourself.
</HARD-GATE>

<HARD-GATE>
Turnaround: do not render `final` for a shot that uses an AI-generated or not-cleared asset, or a specific real subject, until a human approved the turnaround (`asset approve` / `review record` with `turnaround_approved`). Show front/back/side previews and wait. No exceptions.
</HARD-GATE>

## Non-Negotiable Rules

<CRITICAL>
**#1 — Route before authoring**: run `route plan` and decide each shot's route (blender | generative | hybrid) from declared features, never from how the goal text sounds. Exact geometry/motion/identity/anchored labels stay in Blender; generation only owns phenomena, real-place atmosphere or beyond-asset realism. Instead of guessing, declare `route_features` and let the rules decide; ask the user when the rule is R0 (low confidence).
**#2 — No text in generated frames**: generated video garbles letters. Every generation prompt says "no text, no letters, no captions" and contains no quoted strings; titles, numbers and labels are added in edit from 3D anchors or measured 2D anchors.
**#3 — GPU by default**: render with `STUDIO_RENDER_DEVICE=GPU` (Metal, ~11× faster outside the Codex sandbox). Only when the user limits computer load use CPU, one worker, one small frame first — and disclose it.
**#4 — Real scale before look**: run the scale audit (look_report.json `passes.scale`) and fix scale before judging lighting, DOF or materials; a 8.6× oversized scene makes every physical look setting wrong. Author-called `rescale_scene` refuses scenes with baked camera keys — rescale first, then bake the camera.
**#5 — Never manufacture approval**: approvals, budgets and human verdicts come only from the user's own words. Candidate ≠ approved master.
**#6 — Camera energy, explored not guessed**: decide `camera.energy` per shot; make high-energy cameras with `camera.rig`, never with a camera loop in the author script. For high-energy or hero-motion shots explore 2–3 alternatives in the workbench (`set_camera_rig` → `variant_save` each) and pick one from `workbench compare` at the same frames, then `workbench commit --chosen-variant X --why "..."` — the first idea is usually safe and flat (the stiff jet chase). Exploration costs no version and no repair budget. Bad: render the first orbit you typed. Good: low chase / side sweep / close pass compared at frames 1, 45, 90; side sweep chosen because its guards pass and near_field_ratio is highest. → `references/camera_rig.md`, `references/workbench.md`
**#7 — Spec before shape**: decompose every subject the request names into a subject spec (`subject init/lint`): trace every request phrase to a requirement, source real dimensions (≥ 2 sources for a real subject), list identity features with a verify method, and build geometry from spec builders (loft/wing/revolve/sweep/profile/wall/array), never from numbers typed into the author script. The shape decides everything downstream: a hybrid restyle copies the blockout silhouette, so a toy-proportioned model stays a toy. Polish camera and look only after `fidelity_report.json` passes. → `references/subject_fidelity.md`
**#8 — You decide structure, code decides numbers**: you choose parts, builders, relations, claims and `free` bounds; numbers come from sources, `subject trace` (drawing pixels at `px_per_m` through a datum), `subject fit` (Nelder-Mead on silhouette IoU with sourced dimensions held) or `subject from-dxf`. LLM-picked numbers are where the toy jet and the 0.62 m drawing offset came from. Place parts with `relations` (attach/align/through/on_surface/symmetric) and state connections as `assembly_claims` (contact/no_interference/through/cover/clearance/no_floating), not typed coordinates — a typed coordinate has no meaning to check. Bad: wing `"location": [0, -0.48, -0.48]` guessed from the picture. Good: `subject trace --register --part wing` → candidate with pixel evidence → `subject fit` → `relations: attach fin/-z → fuselage/+z`. → `references/subject_fidelity.md`
**#9 — One diagnosis per build, best version kept, 3 rounds**: every subject-shot rebuild names the single failure it addresses (`shot build --diagnosis`, `workbench commit --diagnosis`). A build that scores below the best version is reverted automatically; after `limits.look_iterations_per_shot` (3) non-improving builds the next one is refused (`REPAIR_BUDGET_EXHAUSTED`) — critique loops plateau after 2-3 rounds and a later round can be worse. When stuck — in order: 1) re-trace or add stations where `silhouette_*.png` diverges; 2) widen `free` bounds or add free parameters; 3) split the part; 4) new or better reference (CAD, second view); 5) ask the user (they reset the budget with `repair reset --reason`). Forbidden: lowering `min_iou`/tolerances, deleting a claim, or editing a spec number by hand to pass. → `references/workbench.md`
**#10 — Freedom by declaration**: when the shot needs a subject to differ from its sources on purpose (a bolt enlarged to be legible, an exploded view where parts float, a stylised silhouette), declare it in the spec's `deviations` (`check`, `factor` / `min_iou` / `waive`, `reason`; real subjects with a large change also `user_evidence` in the user's words). The check then judges the declared target and the report keeps the original expectation, so a deliberate change is visible and a mistake is still caught. Instead of loosening tolerances or deleting checks (still forbidden), declare. Unused deviations are reported; remove them.
</CRITICAL>

→ Module index: `references/index.md` (read it, then only the modules for your current phase).

## Phase 0 — State and route
1. `doctor`, `project status`; resume useful results rather than restarting.
2. Inspect reference frames; write visible acceptance criteria (exact times, framing, motion numbers) before authoring.
3. `route plan` → show the user `route_plan.md` (route, reason, cost, minutes per shot). Paid shots wait for approval (HARD-GATE).

Bad: "Shot 3 shows the DDP facade at dusk, so generate it." Good: "Shot 3 features: exact_geometry (panel joints), photoreal_beyond_assets → R2 hybrid: Blender motion pass + video-to-video restyle; $3.3 estimate; awaiting approval."
Bad: routing a mechanism close-up to Veo because the reference reel used Veo. Good: mechanism → blender (R3); the reference's look is matched with a photoreal look preset, not by giving up exact motion.
Never decide a new case by matching the keywords of the closest example; apply the feature definitions in `references/routing.md`.

## Phase 1 — Assets
Order: local library → Poly Haven / ambientCG (CC0, studio-cleared) → CAD factory (standard dimensions) → image-to-3D (paid, review-only) → hand modelling. Details: `references/asset_ladder.md`.
Forbidden: marking a caller-supplied file `cleared`, using Hunyuan 3D (licence excludes Korea), or using an image-to-3D mesh's invented back side in a close-up without turnaround approval.

## Phase 1b — Subject spec (before any modelling)
Bad: "WWII-style fighter" → six boxes and a slab wing with hand-typed sizes. Good: `subjects/p51d/spec.json` — identity P-51D Mustang, length 9.83 m and span 11.28 m from two sources, 11 fuselage stations, NACA wing with real taper and dihedral, belly radiator scoop, four-blade propeller, top-view silhouette registered by a datum, IoU ≥ 0.85 (auto-fitted: 0.968).
1. `subject init` → parts, builders, relations, `assembly_claims`; rough numbers allowed only as a start, with `free` bounds you can justify without the answer.
2. Drawing: silhouette with `px_per_m` and a `register` rule (a part face ↔ the drawing's extreme pixel, symmetry line ↔ axis 0) → `subject trace --register --part ...` → `subject fit` → read `candidates/*.json`, then `--apply`. CAD: `subject from-dxf`.
3. Building elements: `profile` (KS/EN table sections, exact polygons), `wall` (openings), `array pattern: grid`; set `dim_role` from `look_data/real_dimensions.json`. → `references/building_elements.md`
When fidelity fails — follow #9 (in order: re-trace/add stations, free bounds, split part, better reference, ask). Forbidden: lowering tolerances/`min_iou`, deleting a feature or claim, or marking a sourced number `assumed` to pass.

## Phase 2 — Author and build
- Iterate in the workbench (`workbench start/call/commit`): typed tools, millisecond measurements, ID previews where each part has its own colour and pixel count. `exec` is off by default and a session that used it cannot be committed. Commit replays the session through `shot build --base` and fails on any measurement difference. → `references/workbench.md`
- Unknown or version-sensitive bpy names: `api search --query` / `api show --path` (index of the installed Blender) before writing code.
- Create or patch through shot build. Author scripts receive a job JSON after `--`, containing `shot`. Custom animation sets scene['studio_authored_animation']=True; otherwise standard action/camera contracts apply. A declared `shot.camera.rig` is baked after the author script even when that flag is set. Use --base to patch saved geometry.
- Craft rules for geometry, disassembly, cuts, textures and reference studies: `references/blender_craft.md` (always read before authoring geometry or materials).
- Look: set `render.look_preset` (or `style.look.preset`): `flat_stylized` (default, no-op), `photoreal_product|exterior|interior|night`, `previs_clay` (hybrid input only). Read `references/look_photoreal.md` before choosing.

## Phase 3 — Review stills, then motion
- Prepare a whole-reel rough cut early. Prove the hardest hero frame from multiple angles at look quality before full renders. Compare reference, first attempt and latest at equal display size.
- Render and view images; correct materials, composition, occlusion and detail. Process success isn't visual acceptance. Preserve the best version when an iteration regresses.

## Phase 4 — Generative / hybrid shots (after approval only)
Hybrid: the Blender motion pass is complete (full length, final camera, timing, positions) and rendered with `previs_clay`; `generate control` makes depth/clay/canny; the model may change only the look. Video-input models only (seedance-2.5, wan-2.2-vace, kling-o1-edit, luma-ray-modify); first-frame models (Veo i2v) invent motion and are refused for hybrid. Details and fallback order: `references/generative_safety.md`.

## Phase 5 — Edit, QA, delivery
- Make scratch narration, align actions and ensure speech fits; never truncate it silently. Resolve cue-bound actions before final render. Mark scratch voice explicitly.
- Submit renders, inspect job status, recover partial frames. Build edit and collect QA. Inspect contact sheets and full frames; encoding checks don't establish factual or visual correctness.
- Return the requested artifact: stills for a still study, playable output when video is requested, plus scene snapshots, concrete reference differences and the next valuable change. Do not render an animation merely to satisfy a default workflow when the current still-quality gate fails. Respect iteration budgets; change strategy on repeated failure.

## Machine checks (run them; report numbers, not impressions)
| # | Check | Command / file | Severity |
|---|---|---|---|
| 1 | Shot contracts | `project validate` | ❌ Error |
| 2 | Routes, prompts, budget | `route lint` | ❌ Error on E*, ⚠️ on W* |
| 3 | Render device and settings | `renders/<fp>/renderer_actual.json` (device, samples, threads_mode, warnings) | ❌ if device differs from request without disclosure |
| 3b | Subject fidelity (dimensions, features, silhouettes, assembly claims) | `versions/<v>/fidelity_report.json`; look/final renders and paid generation refuse `FIDELITY_FAILED` | ❌ Error |
| 3c | Spec freshness and lint | build refuses `SUBJECT_SPEC_INVALID`; renders refuse `FIDELITY_STALE` when the spec changed after the version | ❌ Error |
| 3d | Repair budget and best version | `repair status`; `REPAIR_BUDGET_EXHAUSTED`, `REPAIR_REVERTED` warning | ❌ Error (ask the user) |
| 3e | Workbench replay | `versions/<v>/replay_report.json` (`WORKBENCH_REPLAY_MISMATCH`) | ❌ Error |
| 3f | Declared deviations | `fidelity_report.json` `deviations_applied`, `unused_deviations`, `summary.stylized`; build result `fidelity.deviations` | ⚠️ unused → remove |
| 3g | Alternatives considered | commit `variants_considered` / `chosen_variant` / `why` (changes.json, repair.json); `SINGLE_VARIANT`, `CAMERA_KEYS_OVERRIDDEN_BY_RIG` warnings | ⚠️ Warning |
| 4 | Look passes | `versions/<v>/look_report.json` (gate_failures, scale, guard) | ❌ gate_failures |
| 5 | Camera rig guards | `versions/<v>/camera_rig_report.json` | ❌ gate_failures |
| 6 | Encoding, colour, motion | `qa collect` (bt709 tags, per-shot motion warnings) | ❌ technical, ⚠️ motion |
| 7 | Generated clips | `qa_generative` structure/flicker/morph/text | ❌ hybrid structure fail → reject_route |

## Quick reference
| Item | Default | Limit |
|---|---|---|
| Samples | look 64, final 128 (16-bit PNG) | animation ≥ 64 unless set explicitly |
| Real scale | within ±5 % of real dimensions | flag_ratio ≤ 0.2 |
| Bevel | 0.5–2 mm | never on mechanical guard pairs |
| Lens (photoreal) | 50–85 mm product/mechanism | 18–28 mm only for high-energy rigs |
| Key:fill | 2–4 : 1 | no plain grey cyclorama for photoreal |
| Motion blur shutter | 0.5 | label anchors blur ≤ 2 px |
| Generation attempts | ≤ 3 per shot | budget from `route approve --budget-usd` |
| Subject repair builds | ≤ 3 without improvement | `limits.look_iterations_per_shot` |
| Assembly tolerances | contact gap ≤ 1 mm, interference ≤ 0.5 mm, floating > 1 mm | per claim `tol_m` / `value_m` |
| Silhouette IoU | ≥ 0.85 drawing, ≥ 0.95 CAD | registered by datum when the spec has one |

Revision invariants:
- Saved .blend snapshots are immutable. Create new versions and preserve unrelated projects.
- For each revision, explicitly list the constraints required by that request in top-level preserve. An explicit list replaces the previous list; omission inherits it. Replace a previous metadata-only scene_version constraint with geometry/materials when a camera change is requested. Do not clear unrelated user constraints merely to make a patch pass.
- Text-only changes reuse 3D; camera/geometry invalidate affected shots only. Pin asset versions.
- Frames are 0-based half-open; convert +1 at Blender. Preview/final share 30fps.
- Labels follow real anchors; hide missing/behind-camera coordinates and review moving callouts.
- Credentials remain in environment, never logs. Missing paid voice access permits scratch completion. Purchasing, posting and external messages need their own authorization.
- Candidate and human-approved master differ. Never manufacture human approval; progress to a candidate without repetitive intermediate permission requests.

Reporting: report the actual numbers (metrics, costs, frame times, gate results) and every failed or skipped check; never compress a failure into "mostly works".

Use projects/harness_validation for experiments. Existing DDP/station projects belong to another session and their references are read-only.
