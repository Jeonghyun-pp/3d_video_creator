# Building and structural elements

All from spec data (no new code per element). Sources: standard tables in `studio/asset_factory/tables` (each row cites its source; `_verification` says a human must check against the standard text), CAD via `subject from-dxf`.

| element | builder | notes |
|---|---|---|
| H/I column, beam | `profile` `{table: 'KS D 3502', designation: 'H-300x300x10x15'}` or EN 10365 `HEB 300` | section area matches the table within 0.5 %; `dim_role: wide_flange_beam` |
| plate, base plate, end plate | `wall` (holes as openings), rotate 90 deg about X to lay flat | holes are exact rectangles; `dim_role: steel_angle_or_bracket_plate` |
| wall / slab with openings | `wall` length/height/thickness/openings | from DXF: `subject from-dxf` gives `wall` params; `dim_role: concrete_wall_or_slab` |
| curtain-wall mullions, bolt groups, rebar sets | `array` `pattern: grid` (counts, pitch_m, axes) | each copy gets `studio_array_index` [i, j] |
| bolts, anchor bolts | `revolve` profile (shank, nut) or a factory `asset` | claims: `through` the plate, `no_interference` with hole walls |
| concrete pedestal, footing | `box` | `cover` claim for embedded bars |
| anything repeated along a route (steps, sleepers, posts, hangers, light fixtures) | `array` `pattern: path` {points, pitch_m, start_m, count, orient tangent/fixed} | the last copy may sit exactly on the path end; overrun is refused |
| one copy made of several parts (tread + riser, post + rail, hanger pair + bar) | array `item: {builder: group, params: {items: [...]}}` | groups nest; `count` features count copies, not meshes |
| channel, angle, rail sections | `profile` table rows with `shape: channel / angle / polygon` (KS D 3502 C/L rows, KS R 9106 50N) | sharp corners; rows flagged `needs_human_check` (agent recall) |
| self-lit fixtures | spec material `emission_strength`, or catalog kind `light_panel` | |

Verified exemplars (`library/exemplars`, built and fidelity-passed in `projects/harness_validation/building_elements`, spec data only):

| exemplar | made of | key numbers |
|---|---|---|
| `stair` | path array of step boxes | 16 risers × 170 mm, 280 mm treads, 1.5 m |
| `escalator` | path array of tread+riser groups, profile balustrades + mirror, sweep handrails, profile truss | S1000, 30°, 7 m rise (one station storey) |
| `glass_railing` | path arrays of posts and glass panels, sweep top rail | 1.2 m guard height |
| `beam_grid_ceiling` | grid arrays of KS H-400x200 girders, slab | 9 m bays |
| `light_row` | path array of 1.2 m emissive fixtures | 2.4 m pitch |
| `track` | KS 50N rail profile + mirror, path array of PC sleepers | 1435 mm gauge, 600 mm sleeper pitch |
| `slab_opening` | `wall` laid flat with an opening | 300 mm slab |
| `vent_duct` (N+1, no code) | sweep rectangle along an L route, path array of hanger groups | 600 × 400 mm, hangers every 2 m |

Place them by data, not by copying numbers into the author: load the exemplar spec, change only run lengths / counts / dropped parts, give each instance its own `subject_id` (see `samsung_lib.exemplar`, `run_length`, `drop_parts`, `girders`).

Joint pattern (validated: column base, all claims pass): relations `through` + `on_surface` stack pedestal → plate → column; `align` sets anchor embedment; claims `contact` (column-plate, plate-pedestal), `through` + `no_interference` (anchors-plate), `clearance` (nut-column wrench room), `no_floating`.
IFC import is not supported yet (IfcOpenShell with Blender 5.2 unverified).

Scene roles for environments built around elements: tag earth/sky shells `environment_shell`, markings and window bands `clutter`, emissive panels `light_fixture` (`scene_roles.md`); exemplar instances need no tag.

Spec materials (all builders): `color_srgb`, `metallic`, `roughness`, `emission_strength` + optional `emission_color_srgb`
(a light that is not its housing's colour), `shader: {kind: window_grid | emissive, params}` (variation in the shader —
`environment_kits.md`) and `scene_role: light_fixture | clutter` on the parts it covers.
