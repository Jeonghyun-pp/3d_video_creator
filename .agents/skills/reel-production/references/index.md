# reel-production reference — module index
**Do not read all modules.** Read this index, then load only what the current phase needs.

| Module | File | Read when... |
|---|---|---|
| Routing | `routing.md` | deciding or changing a shot's route; any paid generation |
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
- Phase 0 (state, route): **always** `routing.md`.
- Phase 1 (assets): **always** `asset_ladder.md`; **always** `subject_fidelity.md` before modelling a named subject; **if** architecture/structure → `building_elements.md`.
- Phase 2 (author/build): **always** `blender_craft.md`; **always** `workbench.md` before iterating on a built shot; **if** photoreal → `look_photoreal.md`; **if** high energy → `camera_rig.md`; **if** the scene has shells, fog, markings or fixtures → `scene_roles.md`; **if** repeated background or falling/drifting matter → `scatter_simulation.md`; **if** anything around the subject (city, street, facades) → `environment_kits.md`.
- Phase 2: **if** the shot reveals a structure under ground or inside a box → `section_staging.md`; **always** `fill_brief.md` before filling any level or interior.
- Phase 3/5 (review, edit): **if** the shot points at, measures or outlines something → `explainer_graphics.md`; **if** it shows a title or big text → `titles.md`.
- Phase 4 (generation): **always** `generative_safety.md`.
