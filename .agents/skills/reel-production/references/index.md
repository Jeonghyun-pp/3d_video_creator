# reel-production reference — module index
**Do not read all modules.** Read this index, then load only what the current phase needs.

| Module | File | Read when... |
|---|---|---|
| Decision ladder | `decision_ladder.md` | settling brief, facts, script, shot list and look with the user (before any build) |
| Storyboard | `storyboard.md` | agreeing a shot as pictures: sheets, the user's words as edits to any value (word-ops or set/add/remove by path), the approved frames as a contract |
| Routing | `routing.md` | deciding or changing a shot's route; any paid generation |
| Mechanisms | `mechanisms.md` | a subject with moving parts: gears, joints, couplings, `drive` actions, interference |
| Declarative scene | `declarative_scene.md` | building a shot's scene from data (`shot.scene`): exemplars, kits, repeats, levels, sections |
| Blender craft | `blender_craft.md` | authoring geometry, disassembly, cuts, textures, reference studies |
| Photoreal look | `look_photoreal.md` | choosing `look_preset`, lighting, materials, scale, camera realism |
| Asset ladder | `asset_ladder.md` | finding or generating any asset |
| Generative safety | `generative_safety.md` | generative or hybrid shots, prompts, QA of generated clips |
| Subject fidelity | `subject_fidelity.md` | any named subject: spec, sources, builders, fidelity gate |
| Camera rig | `camera_rig.md` | `camera.energy: high`, following a moving subject, fly-throughs |
| Workbench | `workbench.md` | iterating on a built scene or subject; measuring; committing changes |
| Building elements | `building_elements.md` | steel sections, plates, walls with openings, grids, CAD drawings |
| Scatter and simulation | `scatter_simulation.md` | crowds, trees, rubble, repeated fixtures; debris or dust that moves on its own |
| Environment kits | `environment_kits.md` | streets, facades, traffic, roofs, signs around the subject; look density styles |
| Explainer graphics | `explainer_graphics.md` | arrows, dimension lines, outlines, drawn-on lines in a shot |
| Titles | `titles.md` | a title, place name or any big text in a shot |
| Fill brief | `fill_brief.md` | filling any level, floor, platform or interior (what goes there is decided by topic, with the user) |
| Section staging | `section_staging.md` | showing an underground/enclosed structure as a cut (cutaway section, poché, interior light) |
| Scene roles | `scene_roles.md` | shells, fog, helpers, markings or fixtures in a scene (what every mesh-sweeping pass counts) |
| Rule rationale | `rule_rationale.md` | a rule or default in SKILL.md is unclear, or before overriding a craft default (measurements, Bad/Good, incidents) |

## Phase → modules
- Phase D (decide with the user): **always** `decision_ladder.md`; `fill_brief.md` and `storyboard.md` once the shot list is approved.
- Phase 0 (state, route): **always** `routing.md`.
- Phase 1 (assets): **always** `asset_ladder.md`; **always** `subject_fidelity.md` before modelling a named subject; **if** architecture/structure → `building_elements.md`.
- Phase 2 (author/build): **always** `declarative_scene.md` and `blender_craft.md`; **always** `workbench.md` before iterating on a built shot; **if** photoreal → `look_photoreal.md`; **if** high energy or a camera move → `camera_rig.md`; **if** parts move together (gears, joints) → `mechanisms.md`; **if** the scene has shells, fog, markings or fixtures → `scene_roles.md`; **if** repeated background or falling/drifting matter → `scatter_simulation.md`; **if** anything around the subject (city, street, facades) → `environment_kits.md`.
- Phase 2: **if** the shot reveals a structure under ground or inside a box → `section_staging.md`; **always** `fill_brief.md` before filling any level or interior.
- Phase 3/5 (review, edit): **if** the shot points at, measures or outlines something → `explainer_graphics.md`; **if** it shows a title or big text → `titles.md`.
- Phase 4 (generation): **always** `generative_safety.md`.
