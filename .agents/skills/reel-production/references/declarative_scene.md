# Declarative scenes — the scene is data, an author script is the exception

`shot.scene` (schema `shot.schema.json` `$defs/scene`; host `studio/layout.py`, Blender `studio/blender_ops/layout.py`).
`shot build` without `--script` builds it; `dependencies.json` records `layout_sha256`, the edited exemplar specs and
`author_lines` (0 = built from data alone). Measured 2026-10-06: samsung s01 rebuilt from data matches the author-script
build object for object (3983, geometry + material kind + lights), camera samples and street digest equal.

## What goes where
| Need | Entry |
|---|---|
| Flat (blockout) world and sun | `world` (skipped under a photoreal look preset: the preset owns world and lights) |
| Material kinds (flat colour, emission, catalog for photoreal) | `materials` |
| A box the camera move or section refers to | `volumes` |
| Streets, traffic, facades | `kits` (`street`; `sightline: {from_move: true}` keeps sky from where the move starts) |
| Building elements, columns, escalators, track | `instances` — `exemplar@vNNN`, `at`, `rot_z_deg`, `edits` (`set` a JSON pointer, `drop_parts`) |
| Repetition | `repeat {counts, pitch_m}`, `mirror_x`, `level_by_z`, `yields_to_fill` (left out where the fill brief puts its subject) |
| Plain boxes | `primitives` — last resort (> 400 warns) |
| Fill levels | `levels` (what `fill_brief` fills) |
| A cut through the ground | `section` (stage, `front_cutter`, `copy_materials`) |
| A reveal or action target from a kit object | `bind` |
| Shared parts of several shots | a project set `sets/<id>.json` and `use` (entries replaced by id) |

No expressions and no loops in the data. Bad: an author script with `for lvl in range(5): box(...)`. Good: one `primitives`
entry with `repeat: {counts: [1, 17, 5], pitch_m: [0, 9, -7]}`, `mirror_x: true`, `level_by_z: [B1..B5]`.

Revisions: a change to `shot.scene` (`shot revise` scope `scene`, change `{"scene": …}`) builds fresh from the data.
Escape hatch: an author script may still run on top of the scene (it then owns whatever it adds).
