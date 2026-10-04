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

## Phase → modules
- Phase 0 (state, route): **always** `routing.md`.
- Phase 1 (assets): **always** `asset_ladder.md`; **always** `subject_fidelity.md` before modelling a named subject; **if** architecture/structure → `building_elements.md`.
- Phase 2 (author/build): **always** `blender_craft.md`; **always** `workbench.md` before iterating on a built shot; **if** photoreal → `look_photoreal.md`; **if** high energy → `camera_rig.md`.
- Phase 4 (generation): **always** `generative_safety.md`.
