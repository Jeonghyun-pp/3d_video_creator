# Subject fidelity: request → spec → builders → gate

Code: `studio/subjects.py` (spec, lint), `studio/blender_ops/modeling/` (builders), `studio/blender_ops/fidelity.py` + `studio/fidelity.py` (measure, judge). Schema: `schemas/studio-v1/subject.schema.json`. Template: `templates/subject_spec.json`.

## 1. Write the spec (`subject init --project P --subject S --identity "..." --request "<user words>"`)
- `request`: the user's words verbatim. `request_trace`: split them into phrases (copied verbatim) and map each to spec items (`dim.*`, `feat.*`, part ids) or shot decisions (`camera.*`, `motion.*`, `look.*`, `route.*`). Lint fails while any word of the request is untraced.
- `identity`: the exact thing (make/model/variant). Pick one; "WWII-style fighter" is not an identity.
- `sources`: where each number comes from (manufacturer data, museum spec, standard, measured drawing). Real subjects need ≥ 2 independent dimension sources. Image sources (drawings/photos) need a licence you can state (public domain, CC with attribution, own photo) or `local_only` (a photo the user supplied for comparison: kept on this machine, never committed, never sent to a generation model - the tools refuse it there); `unknown` is refused.
- `dimensions` with tolerance (2 % for overall size of a real subject); `proportions` for ratios that define identity (span/length, wheelbase/length, height/width).
- `features`: what makes it recognisable at the shot's screen size — each with `part_ids` and `verify` (presence, count, dimension, silhouette, visual). Use `min_screen_px` to state when a feature must be modelled in detail; below it, simplification is allowed and reported.
- `silhouettes`: side/top/front drawings (licensed), `min_iou` default 0.85; images live in the project.
- `builders`: geometry as data, with `ops` (bevel, boolean, subdivide, ...) baked onto a part. Numbers that make a claim live here; appearance shape may also be modelled in the author script (it is measured on the evaluated result).

## 2. Builders (Blender, `modeling.assemble.build_subject(spec)`)
| builder | params | use |
|---|---|---|
| loft | stations [{s, section: ellipse a,b / superellipse a,b,n / rect / points, center}] along `axis` | fuselages, hulls, car bodies, train noses, tanks |
| wing | span, root/tip chord, quarter-chord sweep, dihedral, airfoil NACA 4/5-digit or points, washout, mirror, elliptic | wings, tailplanes, fins, propeller/fan blades |
| revolve | profile [[r, z]] about axis | wheels, drums, spinners, columns, nozzles |
| sweep | circle/points profile along a path | pipes, rails, ropes, handles, canopy frames |
| box | size [x, y, z], bevel_m | blocks, slabs, pedestals |
| profile | exact polygon or `{table: 'KS D 3502' / 'EN 10365', designation}` extruded along `axis` | H/I sections, plates, mullions, rails (corners stay exact) |
| wall | length, height, thickness, openings [{x, z, w, h}] | walls, panels, plates with holes (closed, no booleans) |
| array | count about an axis, or `pattern: grid` with counts/pitch_m/axes, with an item builder | propeller blades, spokes, bolt groups, mullion grids |
| mirror | source part, axis | symmetric parts |
| asset | prepared library manifest | factory/library parts |
Parts get `studio_subject_id`, `studio_part_id`, `studio_features`, `studio_dim_role` (from the builder's `dim_role`) and `studio_anchors`; the author script calls `build_subject(spec)` with the version snapshot `STUDIO_JOB['subject_spec_paths'][id]` and then places/animates the root - and may model further on the parts (modifiers, booleans, detail); fidelity measures the evaluated geometry. Rebuilding in the same scene needs `replace=True` (no .001 copies).

## 2b. Numbers by code: datum, trace, fit
- A drawing silhouette needs `px_per_m` and a `register` rule: `{u: {anchor: 'fuselage/-y', image: '+x'}, v: {symmetric: true}}` = "the fuselage's aft face is the drawing's right-most pixel; the drawing is symmetric about the centre line". `subject trace --register` turns it into a `datum` (pixel ↔ model point + axis directions); a search-based registration is only the fallback because a wrong model drags it (measured: 0.61 m off with a rough wing).
- `subject trace --part <loft or wing>` reads station half-widths (loft) and chord/sweep/span/root position (wing planform) from the registered drawing, planforms first. Stations hidden by another part are reported `occluded`, never guessed. Each value carries its pixel evidence in `candidates/trace_*.json`.
- `free: [{pointer, min, max}]` on a builder marks what `subject fit` may change; bounds must be justifiable without knowing the answer. Fit maximises datum-registered IoU minus a penalty for any sourced dimension outside tolerance, then restores changes the drawing cannot see (`kept_unobservable`). Measured on the P-51D: rough guess IoU 0.62 → trace 0.961 → fit 0.968 (hand-typed spec: 0.947), 258 evaluations in 47 s.
- CAD: `subject from-dxf --layer L` → silhouette PNG with an exact datum plus `profile_points` / `wall` candidates; source kind `cad`.

## 2c. Relations and assembly claims
- `relations` (solved after parenting, before mirrors; translation only, subject-root frame): attach (anchor a := anchor b + offset_m), align (one axis), through (the two other axes), on_surface (drop along axis onto b's first surface), symmetric. Anchors: center, origin, ±x/±y/±z faces of the part as placed, plus `anchors` you declare. A mirror follows its source; never relate a mirror part.
- `assembly_claims` are measured on every build and fail fidelity like dimensions: contact (gap ≤ 1 mm, no interference), no_interference (≤ 0.5 mm), clearance (≥ value_m), through (spans b along axis, centred, no interference), cover (inside b, cover ≥ value_m), no_floating (each part within 1 mm of another; embedded counts as attached). Failures read like "plate -> stand: gap 2.0 mm".
- Bad: `"transform": {"location": [0.6, 0, 0.232]}` for a handle. Good: `{"type": "attach", "a": "handle/start", "b": "wheel_rim/top"}` + `{"type": "no_floating"}`.

## 2d. Deliberate changes: `deviations`
A deviation retargets one check, it never removes it: `{id, check: '<kind>:<id>', factor | min_iou | waive, reason, user_evidence?}`.
- Legibility: `{check: 'dimension:dim.bolt_d', factor: 1.5, reason: 'bolt is 8 px tall on a 1080x1920 frame'}` → judged against 1.5x; built at 1x it fails.
- Exploded view: `{check: 'assembly:nothing-floats', waive: true, reason: 'exploded view separates the parts'}` (the gaps are still measured and reported).
- Stylised outline: `{check: 'silhouette:top', min_iou: 0.75, reason: '...'}`; scored on a canvas covering model and drawing, so geometry beyond the drawing counts.
Real subjects need `user_evidence` for a waive, a factor outside 0.8–1.25 or min_iou < 0.7. The prompt tells the video model the change is deliberate, the repair loop treats a new set of deviations as a new baseline, and `unused_deviations` lists declarations that changed nothing.

## 3. Gate
Build lints every spec first (`SUBJECT_SPEC_INVALID`), snapshots it into the version (`subjects/<id>.spec.json`, hash in `dependencies.json`), measures every subject in its own frame (`fidelity_geometry.json`) and writes `fidelity_report.json` (dimensions, proportions, feature presence/counts, silhouette IoU with an overlay `silhouette_<view>.png`, assembly claims, `summary`, `spec_sha256`). `look`/`review`/`final` renders and paid generation refuse `FIDELITY_FAILED`, and `FIDELITY_STALE` when the spec changed after that version; `layout` stays allowed for blocking. Features with `verify: visual` are marked `needs_review`.
Passing specs can be promoted to `library/exemplars` (`subject promote`, `subject exemplars --query`, `subject init --from-exemplar`).

## 4. Generation
`generate prompt --project P --shot S` assembles the prompt from the specs (identity + features, materials/look, previs-first sentence, avoid list, no text). Licensed reference photos listed in `generative_look.references` are returned for the route inputs (`kind: reference_image`).
