---
name: reel-production
description: Create and revise technical cutaway, exploded-view and architectural reels with this repository's studio CLI — Blender scenes, per-shot routing to Blender / generative / hybrid, photoreal look presets, camera rigs, reusable 3D assets and reference-frame review. Use for scene creation, reel production, part or camera changes, look and asset work, and production recovery.
---

Work from the repository root (`ai_technical_visualization_starter`; new machine: `scripts/bootstrap.sh`, `docs/SETUP.md`). Run `.venv/bin/python -m studio --help` for the installed interface. Read `docs/AGENT_BUILD_PLAN.md` selectively for contracts and `docs/BUILD_REPORT.md` for verified capability when present. The design isn't proof of implementation.

The requested orchestrator is `gpt-6-astra`; the launcher `scripts/reel_agent.py` requests it explicitly. Never claim a different active model is Astra. Use authorized independent subagents for research, implementation and read-only review. Keep one writer per scene. Astra handles shot design, visual decisions and orchestration; Sol handles bounded implementation; Luna may handle checkable inventories. Report actual model availability.

Turn the request into brief/project/shot JSON. Preserve explicit intent and reference identity. An illustrative model must not be represented as a surveyed real building; invented internals aren't documented fact.

<HARD-GATE>
Paid generation: do not run `generate clip`, `asset image3d` or any fal/Google call until the user saw the review sheet for exactly that request (`generate review`; `asset image3d-review` for meshes), approved it and its cost in this conversation, AND their words were recorded against it (`route approve --review <id> --user-words "<their words, verbatim>"`; `asset image3d --review <id> --user-words ...`) (the approval is bound to the request; any later prompt/input/model change needs a new sheet). `generate reconcile` only with the user's own report of the fal dashboard. No exceptions. Never write, paraphrase into, or infer an approval yourself.
</HARD-GATE>

<HARD-GATE>
Turnaround: do not render `final` for a shot that uses an AI-generated or not-cleared asset, or a specific real subject, until a human approved the turnaround (`asset approve` / `review record` with `turnaround_approved`). Show front/back/side previews and wait. No exceptions.
</HARD-GATE>

## Non-Negotiable Rules
Violations here are what made past reels wrong or cost money. Measurements behind each: `references/rule_rationale.md`.

<CRITICAL>
**#1 — Route by declared features, and the role decides what generation owns**: `route plan` from `route_features`, never from how the goal text sounds; ask the user on R0. Exact geometry, motion, identity and anchored labels stay in Blender; generation owns looks, phenomena and atmosphere. Set `route.role`: `explain` = Blender or a hybrid restyle that passed the structure gate (or, when `project.policy.explain_generated` allows it, a take the user picks in their words on a shot with no labels or graphics); `mood` = generated, captions only. Why: all six A/B restyles looked photoreal and failed structure (IoU 0.07–0.23). → `references/routing.md`, `references/generative_safety.md`
**#2 — Words and marks never live in generated or lit pixels**: every generation prompt says "no text, no letters, no captions" and holds no quoted strings; titles are 2D `shot.titles` inside the safe rect, arrows/dimensions/outlines are `shot.graphics` on their own layer, labels follow 3D anchors or measured 2D anchors. Why: generated video garbles letters; 3D text gets lit, bloomed and leaks into control passes (s01's 3D title sat 78 % outside the frame). Instead of 3D text or mesh arrows, use those layers. → `references/titles.md`, `references/explainer_graphics.md`
**#3 — Decisions come only from the user's words, and their words reach every value**: approvals, budgets, fill-brief sign-off, take picks and repair resets are recorded with the user's own words, verbatim; a candidate is never an approved master. Any value of a shot (camera, scene, actions, titles, graphics) or a fill brief changes when the user asks: a word-op if one fits, otherwise `set`/`add`/`remove` on its path (`storyboard revise --ops`, `fill revise --ops`, workbench `set_shot_value`) - not a lesser fallback; a value nothing reads is refused and the error lists what is read - pick from it. Why: the gates trust those records; on 2026-10-06 words reached only distance/height/angle, so slide `span` and macro_push `detail_fill` were out of reach, and crane/dive_through "higher" named params the moves never read - it built and changed nothing. Instead of writing or paraphrasing an approval, show the sheet and ask; instead of "that can't be changed", `set` it and say what changed (`path: before → after`). → `references/storyboard.md`
**#4 — Real things are sourced; invented things say so**: a specific real subject takes its numbers from ≥ 2 sources, `subject trace` (drawing pixels) or `subject fit`, never from numbers typed into a script; a deliberate change is a spec `deviation` with the user's words for large changes; every narration sentence rests on a sourced claim or is marked illustrative without numbers (`facts check`). Why: LLM-picked numbers made the toy jet and the 0.62 m drawing offset; unsourced claims about a real station reached a public repo. → `references/subject_fidelity.md`
**#5 — Fix the work, never the check**: when a gate fails — in order: 1) re-trace or add stations where the silhouette diverges, 2) widen `free` bounds or add parameters, 3) split the part, 4) a better reference (CAD, second view), 5) ask the user. Forbidden: lowering tolerances or `min_iou`, deleting a claim or check, editing a sourced number to pass. Each rebuild names one `--diagnosis`; the engine keeps the best passing version. → `references/workbench.md`
**#6 — Broken output is a build error, not a look**: real scale before look (scale audit), shells/fog/markings tagged with scene roles before any look or control pass, cameras that travel are `camera.move`/`camera.rig` (guards: no pass through geometry, clip, pitch), reveals cut closed outward meshes, falling matter is a baked `simulate` action. Why: an untagged 400 m earth box set the lighting bounds (lamps at +64 m); hand keys flew through walls; a live simulation renders differently per worker. → `references/scene_roles.md`, `references/camera_rig.md`, `references/scatter_simulation.md`
**#7 — Free in Blender, never around the build**: an author script may use any bpy (its own process, `build_author.py`); new geometry kinds, profiles and joint laws go in `contrib/<kind>/<name>/` (a pure function + manifest, used as `contrib:<name>@draft`, promoted automatically after a passing build); render looks go in `shot.render.{grade, compositor, engine_settings, addons}`. Forbidden: os/subprocess/network, `exec`/`eval`, handlers or timers, saving or exporting files from bpy, writing outside the build folder, editing frozen code without the user's words. Why: the author used to share the gates' interpreter - it could replace a gate or edit the job and nothing would notice; now the lint (`AUTHOR_SCRIPT_REFUSED`), the sandbox (`AUTHOR_SANDBOX_VIOLATION`) and the hash checks (`FROZEN_CODE_CHANGED`) refuse it. Instead of reaching around a check, change the data the check reads, or add a contrib entry. → `references/blender_freedom.md`
</CRITICAL>

## Craft defaults (follow them; override with a one-line reason in the change or commit)
- Render on GPU (`STUDIO_RENDER_DEVICE=GPU`, Metal ~11× CPU); CPU, one worker, one small frame first only when the user limits load.
- Camera: choose the move from what the narration shows (`dive_through`, `pass_between`, `descend_levels`, `section_push`, `waypoints`; one object: `turntable`, `slide`, `macro_push`), timing from a motion style + `camera fit --apply`; for hero or high-energy shots compare 2–3 variants in the workbench. → `references/camera_rig.md`
- Building elements, context and repeated background come from exemplars, `env_kits.street` and `scatter`, not bare boxes; lit windows are the `window_grid` shader. → `references/building_elements.md`, `references/environment_kits.md`
- Readable frame: hold the horizon where the style learned it (~0.40 for cutaway aerials), keep sky (`street(..., sightline=...)`), show structure as a section (`section_push` + `section.stage`). → `references/section_staging.md`
- What fills a level is decided by topic with the user: `fill propose` → their words → `fill revise` → `fill approve`. → `references/fill_brief.md`
- A place around an exact subject comes in this order: the place in words (brief `place`) → the view (storyboard camera) → the relation (`shot.scene.backdrop.relation`: support, view, light) and the backdrop objects → the backdrop image (`generate backdrop-review --shot`, made for that view) → build. Why: on robot_joint, factory images pasted behind exact gears looked like the gears floated (no support, studio light against a bright hall); with the relation the gears stood on the bench and the hall showed past its edge. Instead of pasting a backdrop after the fact, decide the relation and let the scene and the prompt both follow it. → `references/backdrop.md`
- Taste gates (fill limits, framing, subject margin, arrow legibility, repair budget, shape on schematic subjects, frame-probe size/edge, look scale/depth of field/shake/two-point) can be warnings for the whole project with `project.policy.strictness: look-first`; nothing else can be softened.

## Phase D — Decide with the user (before anything expensive)
Brief (with the `place` in words) → facts → script → shot list → (per shot) fill brief and storyboard (the view) → look (the backdrop relation and image, made for that view), one sheet each: `decide propose` → show the sheet → their words with `decide revise` → `decide approve --sheet rNN --user-words "…"`; fill briefs per shot after the shot list. The tools refuse builds, renders, paid calls, final voice and candidates whose layers are not approved and fresh (`DECISION_UNAPPROVED`, `DECISION_STALE`, `DECISION_DRIFT`). Aim for 10–15 user turns before the first look render. Storyboards are Workbench sheets of the data-built shot; while a shot's staging is open, show 2–4 genuinely different takes first (`storyboard variants`, the user picks in their words with `storyboard pick` — never three values of one knob); their words become edits on any value (`storyboard revise --ops`: a word-op, else `set`/`add`/`remove` on its path — never "that can't be changed"), and the approved frames are a contract the final keeps (`STORYBOARD_DRIFT`). → `references/decision_ladder.md`, `references/storyboard.md`

## Phase 0 — State and route
1. `doctor`, `project status`; resume useful results rather than restarting.
2. Inspect reference frames; write what must read in each shot, and when, before authoring.
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
When fidelity fails — follow #5. Forbidden: lowering tolerances/`min_iou`, deleting a feature or claim, or marking a sourced number `assumed` to pass.

## Phase 2 — Author and build
- Blender runs only through the studio MCP server (`studio_run` for any studio command that needs it; inside your sandbox the CLI refuses with `BLENDER_NEEDS_BROKER`). Explore in the workbench before writing code (`studio_workbench` MCP tools, or `workbench start/call/commit`): typed tools, millisecond measurements, ID previews where each part has its own colour and pixel count, `variant_save`/`compare`. `exec` needs `--allow-exec --user-words "<their words>"` and a session that ran it (even one that failed) cannot be committed. Commit replays the session through `shot build --base` and fails on any measurement difference. → `references/workbench.md`
- Every build ends with the frame probe: an id pass through the real camera, 8–12 frames. Declare what must read as `shot.key_parts` (with windows); open `frame_probe/*_id.png` in the version when a `FRAME_*` / `KEY_PART_*` code comes back. → `references/blender_freedom.md`
- Unknown or version-sensitive bpy names: `api search --query` / `api show --path` (index of the installed Blender) before writing code.
- Describe the scene as data in `shot.scene` (exemplars, kits, repeats, levels, section, binds) and build without `--script`; write an author script only for what the data cannot say. → `references/declarative_scene.md`
- Create or patch through shot build. Author scripts receive a job JSON after `--`, containing `shot`. Custom animation sets scene['studio_authored_animation']=True; otherwise standard action/camera contracts apply. A declared `shot.camera.rig` is baked after the author script even when that flag is set. Use --base to patch saved geometry.
- Craft rules for geometry, disassembly, cuts, textures and reference studies: `references/blender_craft.md` (always read before authoring geometry or materials).
- Matter that falls or drifts (debris through an opening, dust): a `simulate` action bound to cues, baked into the version at build. Never physics evaluated at render time: workers render frames in any order, so the build refuses `SIMULATION_NOT_BAKED`. → `references/scatter_simulation.md`
- Arrows, dimension lines, outlines, drawn-on paths: `shot.graphics`, a separate layer (`graphics render`, composited in edit under labels). Forbidden: mesh arrows or 3D text for explanation — they get lit, bloomed and leak into control passes. → `references/explainer_graphics.md`
- Titles and place names: `shot.titles` (2D, receding or held, safe-rect checked every frame). → `references/titles.md`
- Look: set `render.look_preset` (or `style.look.preset`): `flat_stylized` (default, no-op), `photoreal_product|exterior|interior|night`, `previs_clay` (hybrid input only). Read `references/look_photoreal.md` before choosing.

## Phase 3 — Review stills, then motion
- Prepare a whole-reel rough cut early. Prove the hardest hero frame from multiple angles at look quality before full renders. Compare reference, first attempt and latest at equal display size.
- Render and view images; correct materials, composition, occlusion and detail. Process success isn't visual acceptance. Preserve the best version when an iteration regresses.

## Phase 4 — Generative / hybrid shots (after approval only)
Order: `route.role` → `generate inputs` (role decides clay+depth or look render) → `prompt_spec` (look / keep / add / forbid) → look reference stills for reference models (`generate still`; never third-party frames) → `generate prompt` → `generate review` (show the sheet; the user adds or changes items in their words → edit `prompt_spec` → `generate review --after <id> --user-words "..."`) → `route approve --review <id> --user-words "..."` → `generate clip` → show the takes → `generate select --user-words "..." --additions present:<item>,absent:<item>`.
Hybrid: the Blender motion pass is complete (full length, final camera, timing, positions) and rendered with `previs_clay`; `generate control` makes depth/clay/canny (add `lines,normal,id` — Freestyle line art, camera-space normals, object ids — when structure matters more than the extra control time); the model may change only the look. Video-input models only (seedance-2.5, wan-2.2-vace, kling-o1-edit, luma-ray-modify); first-frame models (Veo i2v) invent motion and are refused for hybrid. Details and fallback order: `references/generative_safety.md`.

## Phase 5 — Edit, QA, delivery
- Make scratch narration, align actions and ensure speech fits; never truncate it silently. Resolve cue-bound actions before final render. Mark scratch voice explicitly.
- Submit renders, inspect job status, recover partial frames. Build edit and collect QA. Inspect contact sheets and full frames; encoding checks don't establish factual or visual correctness.
- Return the requested artifact: stills for a still study, playable output when video is requested, plus scene snapshots, concrete reference differences and the next valuable change. Do not render an animation merely to satisfy a default workflow when the current still-quality gate fails. Respect iteration budgets; change strategy on repeated failure.

## Machine checks (run them; read the reports, then judge by eye)
| # | Check | Command / file | Severity |
|---|---|---|---|
| 1 | Contracts, routes, prompts, budget | `project validate`, `route lint` | ❌ E* · ⚠️ W* |
| 2 | Paid request bound to the user's approval | `ROUTE_APPROVAL_STALE`, `ROUTE_REVIEW_MISSING`, `GENERATION_REQUEST_UNKNOWN` (never resend: `generate reconcile`) | ❌ Error |
| 3 | Subject fidelity and spec freshness | `fidelity_report.json`; `FIDELITY_FAILED`, `FIDELITY_STALE`, `SUBJECT_SPEC_INVALID` | ❌ Error |
| 4 | Narration facts | `facts check`; `FACTS_*` (candidates and delivery refuse) | ❌ Error |
| 4b | Decision ladder and storyboard | `decide status`, `storyboard show`; `DECISION_UNAPPROVED`, `DECISION_STALE`, `DECISION_DRIFT`, `STORYBOARD_DRIFT`; an edit or shot naming a value nothing reads → `INPUT_INVALID` (lists what is read) | ❌ Error |
| 5 | Revision integrity | `BASE_NOT_REVISABLE`, `PRESERVE_VIOLATION`, `WORKBENCH_REPLAY_MISMATCH` | ❌ Error |
| 5b | Author isolation and frozen code | `AUTHOR_SCRIPT_REFUSED`, `AUTHOR_SANDBOX_VIOLATION`, `AUTHOR_SCRIPT_FAILED`, `TEXT_3D_FORBIDDEN`, `LINKED_ASSET_UNRESOLVED`, `FROZEN_CODE_CHANGED`; `author_audit.json` settings changes (recorded) | ❌ Error · ⚠️ audit warnings |
| 5c | Frame probe | `frame_report.json`: `FRAME_EMPTY`, `FRAME_NEAR_CLIP_CUT`, `KEY_PART_INVISIBLE` (explain shots) | ❌ Error · ⚠️ `FRAME_EDGE_CUT`, `FRAME_SUBJECT_SMALL`, `KEY_PART_SMALL` may warn by policy |
| 5d | Expressive settings and contrib | `EXPRESSIVE_APPLY_FAILED`, `ADDON_NOT_BUNDLED`, `CONTRIB_INVALID`, `CONTRIB_DEPRECATED`; `expressive_report.json`; `CONTRIB_PROMOTED` warning (pin the version) | ❌ Error |
| 6 | Camera | `camera_rig_report.json` gate_failures (after the look), `CAMERA_MOVE_FAILED`, `MOVE_PATH_LOOP` | ❌ Error (taste guards may warn by policy) |
| 7 | Reveal, simulation, graphics, titles | `REVEAL … inside-out`, `SIMULATION_NOT_BAKED`, `GRAPHICS_NOT_RENDERED`, `TITLE_OUT_OF_SAFE`, `graphic_in_frame` | ❌ Error |
| 8 | Fill brief | `FILL_BRIEF_UNAPPROVED/STALE` (renders), `fill_report.json` gates | ❌ Error (fill gates may warn by policy) |
| 9 | Look | `look_report.json` gate_failures, `passes.lighting.exposure_ev` (EV at −4/+6 = the meter saw no subject: check scene roles) | ❌ / ⚠️ |
| 10 | Generated takes | `generated/<key>/clip.json` `policy` (usable, reasons, pickable, warnings) | ❌ explain unusable · ⚠️ mood |
| 11 | Encoding and delivery | `qa collect` technical rows | ❌ Error |
| 12 | Style, motion, composition, look density | `qa collect` style rows, `motion style check` | ⚠️ advisory (one reference) |
Every code and report path from earlier versions: `references/rule_rationale.md`.

## Starting points
| Item | Default | Limit |
|---|---|---|
| Samples | look 64, final 128 (16-bit PNG) | animation ≥ 64 unless set explicitly |
| Real scale | within ±5 % of real dimensions | flag_ratio ≤ 0.2 |
| Bevel | 0.5–2 mm | never on mechanical guard pairs |
| Lens (photoreal) | 50–85 mm product/mechanism | 18–28 mm only for high-energy rigs |
| Key:fill | 2–4 : 1 | no plain grey cyclorama for photoreal |
| Motion blur shutter | 0.5 | label anchors blur ≤ 2 px |
| Atmosphere (opt-in) | fog density 0.01–0.03 inside a `box`, beams 12–20° | never over a whole site (reads as haze) |
| Blockout preview | Workbench ~0.02 s/frame for blocking and motion; Cycles + Fast GI for lit review | EEVEE renders headless but its lighting does not match Cycles |
| Generation attempts | ≤ 3 per shot | budget from `route approve --budget-usd` |
| Subject repair builds | ≤ 3 without improvement | `limits.look_iterations_per_shot` (a warning under look-first) |
| Assembly tolerances | contact gap ≤ 1 mm, interference ≤ 0.5 mm, floating > 1 mm | per claim `tol_m` / `value_m` |
| Silhouette IoU | ≥ 0.85 drawing, ≥ 0.95 CAD | registered by datum when the spec has one |

Revision invariants:
- Saved .blend snapshots are immutable. Create new versions and preserve unrelated projects. A revision starts from the base version's `authored.blend`, so it equals a fresh build with the same inputs.
- For each revision, explicitly list the constraints required by that request in top-level preserve. An explicit list replaces the previous list; omission inherits it. Replace a previous metadata-only scene_version constraint with geometry/materials when a camera change is requested. Do not clear unrelated user constraints merely to make a patch pass.
- Text-only changes reuse 3D; camera/geometry invalidate affected shots only. Pin asset versions.
- Frames are 0-based half-open; convert +1 at Blender. Preview/final share 30fps.
- Labels follow real anchors; hide missing/behind-camera coordinates and review moving callouts.
- Credentials remain in environment, never logs. Missing paid voice access permits scratch completion. Purchasing, posting and external messages need their own authorization.
- Candidate and human-approved master differ. Never manufacture human approval; progress to a candidate without repetitive intermediate permission requests.

Reporting: report gate results, the numbers that matter (costs, frame times, metrics) and what you see; report every failed or skipped check; never compress a failure into "mostly works".

Use projects/harness_validation for experiments. Existing DDP/station projects belong to another session and their references are read-only.
