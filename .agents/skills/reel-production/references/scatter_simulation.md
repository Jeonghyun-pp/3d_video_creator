# Scatter and baked simulation — dense backgrounds, things that fall and drift

Code: `studio/blender_ops/scatter.py`, `simulate.py`, `scene_geometry.py`; smokes `gn_scatter_smoke.py`, `sim_bake_smoke.py`,
`blender_facts_smoke.py` (the Blender 5.2 behaviour both rely on, measured).
Read when a shot needs crowds, trees, rubble, repeated fixtures, or debris / dust that moves on its own.

## Scatter (Geometry Nodes instances)
`scatter(name, sources, surface=obj, density=per_m2)` or `scatter(name, sources, points=[[x, y, z], ...])`, plus
`seed`, `scale=(min, max)`, `rotation_deg=(min, max)`, `align_to_surface`. A source is an object or a template empty
holding parts (a worker = body + head + helmet). One host object (role `scatter`) carries every instance; the sources
move to an excluded collection (role `scatter_source`) and render only as instances. Same arguments, same instances.
- Use it for **background repetition no label, reveal, anchor or fidelity check names**: street trees, tunnel rings
  and sleepers, rubble, crowds, bollards. Measured on samsung s12: 344 objects → 48, same picture.
- The subject and anything a label or reveal targets stays real geometry from the spec (`subject_fidelity.md`).
  Forbidden: scattering a subject part or an anchored object — an instance has no name to anchor, measure or cut.
- Points mode takes per-point attributes `rot_z / scale / scale_xyz / source_index` (computed by the fill functions,
  `environment_kits.md`) and `weights` per source; without them the random draws are unchanged (old scenes identical).
  A source keeps its own role as `studio_source_role` (a lamp head stays a light fixture) while it is `scatter_source`.
- Every mesh-sweeping pass sees instances through `scene_geometry` (clearance and pass-through, control depth range,
  look lit bounds); ray casts hit them as their host. Do not add a boolean or bevel after the host's node modifier:
  an ordinary modifier realizes every instance (`realize=True` only for a small hero batch that must be cut).

Bad: 160 rubble boxes typed in a loop (160 objects, each bevelled and perfection-snapped).
Good: three rubble shapes as sources, `scatter('rubble', shapes, points=pts, seed=7, scale=(0.6, 1.4))` — one host.

## Baked simulation (`simulate` action)
```json
{"action_id": "debris", "type": "simulate", "targets": [{"instance_id": "concourse", "part_id": "concourse"}],
 "time_binding": {"start_cue_id": "cam-mouth", "end_cue_id": "cam-inside", "start_offset_frames": -3, "end_offset_frames": 10},
 "params": {"kind": "rigid_debris", "region": [[3, -2, 0.6], [5, 2, 1.6]], "count": 24, "size_range": [0.2, 0.5], "seed": 3}}
```
- `rigid_debris`: seeded chunks spawn in `region`, held still until the action starts, then fall onto `targets`
  (passive colliders). Role `simulated`. `dust`: `count` points drifting up and out (`drift_mps`, `grain_m`,
  `color_srgb`), role `atmosphere` (never in control passes, clay or ray casts).
- Bind to camera cues like a reveal (`time_binding` with `cam-*` ids) so debris falls when the road opens.
- Everything is baked at build time into the .blend (rigid-body memory cache; simulation zone `PACKED`), before
  clearance, rig guards, look and fidelity measure the scene: render workers render any frame in any order.
  `SIMULATION_NOT_BAKED` refuses a version with an unbaked world or an on-disk cache — author scripts that add their
  own physics must bake too.
- A collider a reveal is cutting is refused (`SIMULATE: collider … is cut by a reveal`): Bullet keeps the shape it
  had at the start (measured: debris rested on the opened road). Instead spawn the debris over the opening and
  collide with what lies below (the concourse).
- Seeds fix everything: same params, same fall, run to run. Change `seed` for a different fall, never hand-edit.

Bad: debris keyed by hand falling at constant speed. Good: `rigid_debris` with a seed, bound to the reveal's cues.

## Streams and cloth (2026-10-08)
`simulate` kinds beyond debris and dust, all baked at build inside the .blend (no disk cache, the same frame in any
order):
- `particles {region, count, direction, speed_mps, spread, gravity, drag, emit_frames, life_frames, size_m, glow}`:
  sparks, specks, spray - instanced spheres, glowing with `glow`.
- `smoke {..., size_m, growth_m_per_s, voxel_m, density}`: points to a volume that grows with age; a cloudy noise
  thins the density into wisps. Role atmosphere (out of control passes).
- `liquid {..., size_m, voxel_m}`: points to a volume, meshed at a low iso level - reads as a beaded stream, not a
  continuous pour (measured 2026-10-08): good for previs and structure; hand its look to generation
  (`screen.generate_only`) when the pour itself is the shot.
- `cloth {target_object_id, pin: top|null, collide_object_ids, subdivide, quality}`: a mesh hangs or drops onto
  colliders; the point cache is baked in memory and saved in the .blend (check_baked refuses one on disk).
