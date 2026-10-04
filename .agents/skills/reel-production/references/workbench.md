# Workbench: resident Blender, typed tools, replayed commits

Code: `studio/workbench.py` (host CLI), `studio/blender_ops/workbench_server.py` (Unix socket, token, allow-list), `studio/blender_ops/workbench_tools.py` (tools + replay), `studio/workbench_mcp.py` (Codex MCP server `studio_workbench`).

## Loop
1. `workbench start --project P --shot S` (copy of the current version) or `--subject ID` (empty scene for subject work). Ready in ~1 s.
2. Look and measure (read tools, ~0.1-5 ms): `scene_graph`, `measure {target | pair}`, `subject_report {subject_id}` (host returns the fidelity checks), `api_lookup {path}`, `preview {views, passes: [shaded, id]}` (Workbench, 4 views ≈ 0.3 s; the id pass gives each part one flat colour and a pixel count per view, `unmatched_px` must be 0).
3. Change (write tools): `set_spec_param {subject_id, pointer, value}` rebuilds only the affected part; `set_spec`; `set_transform` / `set_modifier_input` / `set_material_param` for objects the spec does not own; `set_camera_keys`. `checkpoint {name}` / `restore {name}` instead of undo.
4. `workbench commit --diagnosis "<the one failure>"`: writes changed specs (after lint), replays the write ops through `shot build --base`, and fails with `WORKBENCH_REPLAY_MISMATCH` if the build measures differently. The repair policy (#9) then scores the new version.
5. `workbench stop`.

## Explore, then choose
- Alternatives: change the scene (spec params, `set_camera_rig` for rig shots, `set_camera_keys` for keyed ones), then `variant_save {name, note}`; repeat. Each save records the fidelity summary (and rig guard summary).
- `workbench compare --names a,b,c --frames 1,45,90 --views shot` renders every variant at the same moments into one contact sheet (rows = variants) and returns to the state you had.
- `variant_restore {name}` the winner, then `workbench commit --chosen-variant name --why "..."`. A rig choice is written into shot.json and re-baked by the build. Exploring costs nothing; only the commit is a version and a repair attempt.

## Rules
- Spec-owned data (subject parts, their placement, spec materials) changes only through `set_spec_param`; the other tools refuse it so the spec, the version and the session never disagree.
- `exec` needs `--allow-exec` and makes the session uncommittable; it is never exposed over MCP. Use it only to explore, then express the change with typed tools.
- One diagnosis per commit; read `preview` id pixel counts and `subject_report` numbers, not impressions.
Forbidden: editing versions/*.blend directly, committing a session whose result you did not measure, or using exec to make a change you then retype by hand into a spec.
