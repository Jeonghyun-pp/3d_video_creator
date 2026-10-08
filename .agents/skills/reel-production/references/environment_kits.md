# Environment kits — context around the subject, for every production

Code: `studio/blender_ops/env_kits.py` (compositions), `env_fill.py` + `env_fill_core.py` (fill functions, placement maths),
`env_materials.py` (shaders), `env_kits_data/presets.json` (densities); kit exemplars in `library/exemplars`
(made by `examples/kits/environment_kits/setup_project.py` + `examples/kits/building_elements/author_elements.py`, fidelity-verified, promoted).
Smokes: `tests/studio/env_kits_smoke.py`; units `tests/test_env_fill.py`. Read when a shot shows anything around
the subject: streets, facades, traffic, roofs, signage.

## Kits (city kit, first set — dimensions marked "agent recall" need a human check before publication)
| exemplar | parts | variation |
|---|---|---|
| `tower_block` | podium (shopfronts), shaft (office windows), crown | `window_grid` shader: lit ratio, bay/floor size, colour temperature palette, wall/glass colour per variant |
| `streetlight` | 9 m pole, 2 m arm (+X = road), lamp head | head emissive, `light_fixture` |
| `car` | body, cabin, 4 wheels, head/tail lights | body colour per source; lights `light_fixture` |
| `sign_panel` | blank lit panel (never text) | colour per source |
| `rooftop_unit` | housing + fan deck | scale, 90° turns |
| `lane_dash` | 5 m paint | realized as `clutter` so a reveal cuts it with the road |
| `traffic_signal` | pole, 7.4 m arm, two 4-aspect heads 1.42 × 0.355 m, bottom 4.7 m | red lit (emission 6), others glow 0.35 |
| `ped_signal` | 2.4 m post, red/green head | — |
| `bare_tree_1..6` | trunk + 3 levels of tapered limbs (≤ 41 lofts, ~650 tris) | 6 seeded variants, weighted equally |
| `pedestrian_stand/walk_a/walk_b/bag` | box figure 1.72 m (≈ 200 tris) | coat colour per pose; crowds only, never a hero |
| `bus`, `taxi`, `bus_shelter` | lit window band / roof sign / lit ad panel | traffic mix `vehicle_mix` |
The streetlight (v2) reaches 4.6 m: the head sits 3–4 m over the carriageway, as in the reference stills.

## Fill functions (any kit)
| function | places | notes |
|---|---|---|
| `along(name, sources, path, pitch_m, offset_m, both_sides, face, jitter_m, weights, realize, role)` | lamps, trees, paint, bollards | heading from the path; `face='road'` turns local +X to the path |
| `blocks(name, sources, path, side, base_size, lot_width/depth/height ranges, gap, setback, weights, avoid)` | buildings on lots | per-axis scaled instances; windows stay true (world-space shader) |
| `on_top(name, sources, lots, spacing_m, keep_ratio)` | rooftop plant | jittered grid on each roof |
| `on_front(name, sources, lots, per_lot, z_range)` | signs, canopies | on each lot's street face only — never floating in gaps |
| `traffic(name, sources, lanes, frames, per_100m, speed, min_gap_m, avoid)` | vehicles | one collection-instance empty per vehicle, linear keys, spacing kept, never crosses `avoid` |
| `street(name, path, road_w_m, lanes_per_direction, sidewalk_w_m, night, density, avoid, keep_clear, frames, seed)` | all of the above | `avoid`: no road/buildings (an open excavation); `keep_clear`: road present, no traffic (a reveal opens it) |

`street` also takes `lane_w_m` / `median_w_m` (default preset 3.4 m / 0: KS lanes 3.25–3.5 m; the road's spare
width becomes a shoulder with a solid edge line), `intersections=[{'s': arc length, 'cross': {...}}]` (four-way:
crossing street, 2-band crosswalks, stop lines 3 m behind them, 5 m arrows 3 m behind those, near-side signals,
pedestrian signals; `env_fill_core.intersection_layout`, all numbers in `MARKINGS`), `sightline` (sky rule below)
and `bare` (ranges with road and buildings but no lamps, trees or people — ground a section cut removes). At night
each lamp head gets one wide spot (115°, blend 1, 5.5 kW, 3500 K, `studio_keep_light`): the light pools on the road.
Lamps, trees, dashes and people respect `avoid` and the intersections (`along(..., avoid=...)`); people keep ≥ 0.6 m
from poles, trunks and signals (`keep_out`). The report adds `placements` (copies, not part instances), `geometry`
(lane/median/shoulder) and `lots_capped_for_sky`.

Sky rule (`sightline={'camera', 'forward', 'horizon_v', 'keep_sky_v', 'lens_mm'}`): every lot the establishing
camera sees is capped so its roof stays below screen height `keep_sky_v` (closed form for a level camera). Measured
on s01 (verify A): framing alone could not raise the sky share above 0.08–0.11 (reference 0.21) because the near
towers filled the top of the frame; the rule capped 15 lots. A distant `skyline` (preset) closes the street's end.

Every generator has its own seed (`seed:name:part`): more cars never move a building. Night/day changes emission data
only, so day and night shots of one street match. Points carry `rot_z / scale / scale_xyz / source_index` into scatter.

## Rules
- Context is kit + fill, never bare boxes or hand-placed copies. Forbidden: a window, light or car per hand-written
  object or loop — it costs objects, breaks determinism and cannot vary. Instead add a kit exemplar or a preset.
- Variation lives in shaders and preset data (`presets.json`: lot sizes, heights, densities, building variants, colours).
  A new density is a new preset entry, never a code branch.
- A new kind of environment (interior, plant, site) is a new set of exemplars made like `examples/kits/environment_kits`
  (spec data only, fidelity pass, `subject promote`) plus a composition using the same fill functions. N+1 check:
  the curved two-lane suburban street in `env_kits_smoke` builds with no code change.
- Judge density against a look style, not by eye: `look style learn` on the reference range (numbers only, sha256
  provenance), then `look style check` on renders (`style.look.look_style` or `shot.render.look_style`).
- Street detail is placed by rule, not by eye: trees every 8 m (bare in winter), asphalt albedo 0.34 sRGB (0.16
  left no visible light pools), window colours mixed toward white (`window_white_mix`; black-body kelvins below
  4000 K made s01 saturation 0.50 against the style's 0.21–0.24). Objects under ~16 px (signals, people) are
  modelled here, never left to a generative model: models invent or drop them frame to frame.
- Measured 2026-10-05 (s01 night aerial, box city vs reference): saturation 0.10 vs 0.19–0.25, highlights p95 129 vs
  155–196, shadow detail 0.016 vs 0.023–0.13; bright-point count was already high (lane paint, title) — the gap is
  colour, highlights and detail, which the kit adds (lit window grids, coloured signs, car lights).

Bad: `for k in range(60): box(f'tower.{k}', ...)` with one lit band per floor.
Good: `env_kits.street('city', path, library_root=..., road_w_m=44, lanes_per_direction=4, night=True, keep_clear=[(205, 230)])`.

## Space kits (2026-10-08)
`shot.scene.kits[].kit` is any kit by name - `street`, `hall`, `strata`, `vegetation`, `water` - each a function in
blender_ops/env_kits.py or env_kits_space.py whose keyword names are the arguments (`scene lint` refuses an argument
nothing reads and a missing required one):
- `hall {box, bay_m, column_m, column_shape, beam_depth_m, walls, *_kind, light_rows, rail}`: a columned interior -
  slabs, columns on the bay grid, a downstand beam grid, light strips between beams, walls on the listed sides.
- `strata {box, layers: [{thickness_m, kind, color_srgb}]}`: layered ground for a section (top down, no gaps).
- `vegetation {area, kinds, per_100m2, keep_clear}`: library trees scattered in an area.
- `water {area, z, color_srgb, roughness, wave_scale}`: a transmissive surface with a fine wave bump.
Materials are catalog kinds (photoreal: the catalog material; other looks: the flat colour). A new kit is one function
and one row in KIT_SOURCES (studio/layout.py) and the dispatch table (blender_ops/layout.py), plus a smoke.
