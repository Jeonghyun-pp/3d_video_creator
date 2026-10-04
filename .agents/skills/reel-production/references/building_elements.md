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

Joint pattern (validated: column base, all claims pass): relations `through` + `on_surface` stack pedestal → plate → column; `align` sets anchor embedment; claims `contact` (column-plate, plate-pedestal), `through` + `no_interference` (anchors-plate), `clearance` (nut-column wrench room), `no_floating`.
IFC import is not supported yet (IfcOpenShell with Blender 5.2 unverified).
