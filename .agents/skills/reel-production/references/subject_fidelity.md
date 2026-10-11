# Subject fidelity: request → spec → builders → gate

Code: `studio/subjects.py` (spec, lint), `studio/blender_ops/modeling/` (builders), `studio/blender_ops/fidelity.py` + `studio/fidelity.py` (measure, judge). Schema: `schemas/studio-v1/subject.schema.json`. Template: `templates/subject_spec.json`.

## 1. Write the spec (`subject init --project P --subject S --identity "..." --request "<user words>"`)
- `request`: the user's words verbatim. `request_trace`: split them into phrases (copied verbatim) and map each to spec items (`dim.*`, `feat.*`, part ids) or shot decisions (`camera.*`, `motion.*`, `look.*`, `route.*`). Lint fails while any word of the request is untraced.
- `identity`: the exact thing (make/model/variant). Pick one; "WWII-style fighter" is not an identity.
- `sources`: where each number comes from (manufacturer data, museum spec, standard, measured drawing). Real subjects need ≥ 2 independent dimension sources. Image sources (drawings/photos) need a licence you can state (public domain, CC with attribution, own photo) or `local_only` (a photo the user supplied for comparison: kept on this machine, never committed, never sent to a generation model - the tools refuse it there); `unknown` is refused.
- `dimensions` with tolerance (2 % for overall size of a real subject); `proportions` for ratios that define identity (span/length, wheelbase/length, height/width).
- `features`: what makes it recognisable at the shot's screen size — each with `part_ids` and `verify` (presence, count, dimension, silhouette, visual). Use `min_screen_px` to state when a feature must be modelled in detail; below it, simplification is allowed and reported.
- `silhouettes`: side/top/front drawings (licensed), `min_iou` default 0.85; images live in the project.
- `plain` (on a builder entry): why the real part is plain (a slab, a sheet guard) - the fidelity `detail` check passes it and reports the reason.
- `detail` check (SKILL #8, shots with a camera): per mesh object, √(largest on-screen bbox area clipped to the frame / distinct face directions); above 40 px the part fails as a placeholder (plain slabs, prisms, faceted tubes) unless `plain`. Raising segments adds faces, not directions of a real shape - model the missing tier. Policy `gates.detail_placeholder: warn` softens it per project. Not covered: the 27-40 band (honest plain cylinders and rough ones look alike there) and objects outside the spec - the detail list, simplification table and reviewer cover those.
- `CONTRIB_PARAM_UNUSED`: each contrib manifest param is nudged in an isolated interpreter (studio/contrib_probe.py); one that changes nothing refuses a draft, warns for a promoted version.
- `photo_views`: `[{id, view: '<key>/<view id>', min_iou, exclude_parts, parts_min_box_iou}]` - the subject's triangles projected with a reference photo's solved camera (in the subject's root frame; `references/workbench.md` Matching a reference photo), visible parts only, against the photo's mask. Kind `photo`: an error for `specific_real`, a warning otherwise (policy); a deviation `photo:<id>` with `min_iou` records an intended difference (a cutaway). Required for a real subject - section 1b.
- `builders`: geometry as data, with `ops` (bevel, boolean, subdivide, ...) baked onto a part. Numbers that make a claim live here; appearance shape may also be modelled in the author script (it is measured on the evaluated result).

## 1b. Photos of a real subject (before modelling)
<CRITICAL>
A `specific_real` subject is matched to licensed photos from at least two sides before its look is rendered: fidelity
`photo coverage` needs >= 2 `photo_views` with solved cameras >= 30 deg apart in viewing direction (an error for a real
subject, a warning for schematic/fictional; look, final and paid generation refuse it through `FIDELITY_FAILED`;
layout and storyboards stay open). Why (2026-10-11, rocket_landing): the booster was modelled from text sources alone -
its spec report said "no ... photographic view was supplied" - and the user read the board as "a grey model shaped
like a rocket", not a Falcon 9. Numbers give size; only a photo gives the leg stance, the fin size, the soot.
</CRITICAL>

Order, before the builders:
1. Collect: `reference collect --project P --subject S --query "<identity> <view words>"` (or `--title "File:..."`
   repeatable). Free licences only (PD, CC0, CC BY, CC BY-SA, as the source states them); every file gets a row in
   `references/photos/index.json` (source page, licence, author) and a contact sheet. In your sandbox there is no
   network: run it through `studio_run` - the broker starts the download outside and returns `awaiting_dispatch`;
   poll `reference collect --project P --status <job>`. Collect at least 3: the whole subject from the side, from three
   quarters or above, and the detail the reel explains.
2. Look at the contact sheet and write the visible features as a checklist (stance, proportions, colour and wear) -
   these become `features` and `materials`, and the reviewer's crops.
3. Match: `reference view add --view S_photos/<side> --image references/photos/<file> --licence local_only --subject S`
   with 6-20 points, `reference_fit_camera {view, frame}` at a frame where the subject stands as in the photo (legs
   deployed for a landed photo), then iterate the builders on `reference_compare` until `iou >= 0.85` and each marked
   part `box_iou >= 0.7` (`references/workbench.md`). List the views in `photo_views`. The fit records the subject's
   pose at that frame on the view; every shot's fidelity judges the photo in that pose, not in the shot's own first
   frame (`reference_fit_camera {view, frame, pose_only: true}` records it for a view fitted before). Why (2026-10-11): s03, folded on the
   launch pad, was judged against the landed photo and failed 19 checks the booster did not have.
4. Appearance from the same photos: `reference_compare` lit at the photo camera; materials (white, black, soot,
   scorching) are changed until the crops read alike.

A collected photo is compared here only: its view licence is `local_only` (a `cleared` collected photo is refused) and
it never reaches a generation model without the user's words. A file dropped into `references/photos` by hand has no
index row and is refused as a view; a photo the user gives goes in its own `references/<key>/` folder.

When photos and subject differ in version (a 2016 landing vs the Block 5 the reel names) - in order:
  1. stance, proportions and part placement from the photos; the version's differences (colour of the interstage,
     fin material) from a photo or document of that version, recorded as a `photo:<id>` deviation with `min_iou`;
  2. a different photo of the right version;
  last: a `photo:coverage` deviation - `waive`, `reason`, `searched` (the queries tried) and `user_evidence` (the
     user's words; lint refuses it without them).
  Forbidden: skipping the photos because none was supplied - collect them; loosening `min_iou` to pass a shape that
  differs - change the shape.

Bad: spec report "Silhouette matching/photo matching were not declared: no licensed drawing, reference mask or
calibrated photographic view was supplied" -> legs as thin rods, a plain grey cylinder, flames as orange cones.
Good: `reference collect --title ...` -> 17 CC0/PD landing photos indexed; the side view on the drone ship and the
legs-deployed descent each solved from 6+ points, their cameras well over 30 deg apart; the legs changed until the
side view reaches `iou >= 0.85`; soot and the black interstage added until the lit crops read alike.

## 2. Builders (Blender, `modeling.assemble.build_subject(spec)`)
| builder | params | use |
|---|---|---|
| loft | stations [{s, section: ellipse a,b / superellipse a,b,n / rect / rounded_rect a,b,r / points (+ fillet_r), center}] along `axis` | fuselages, hulls, car bodies, train noses, tanks |
| wing | span, root/tip chord, quarter-chord sweep, dihedral, airfoil NACA 4/5-digit or points, washout, mirror, elliptic | wings, tailplanes, fins, propeller/fan blades |
| revolve | profile [[r, z]] about axis, `fillet_m` (one radius or one per point) rounds its corners | wheels, drums, spinners, columns, nozzles |
| sweep | circle / rounded_rect / points (+ fillet_r) profile along a path; `path_smooth: catmull_rom` (smooth through the points), `scale: [[u, s], ...]` taper | pipes, rails, ropes, handles, canopy frames |
| box | size [x, y, z], bevel_m | blocks, slabs, pedestals |
| profile | exact polygon, `{points, fillet_r}`, `{rounded_rect: {a, b, r}}` or `{table: 'KS D 3502' / 'EN 10365', designation}` extruded along `axis` | H/I sections, plates, mullions, rails (corners stay exact) |
| wall | length, height, thickness, openings [{x, z, w, h}] | walls, panels, plates with holes (closed, no booleans) |
| array | count about an axis, or `pattern: grid` with counts/pitch_m/axes, with an item builder | propeller blades, spokes, bolt groups, mullion grids |
| mirror | source part, axis | symmetric parts |
| asset | prepared library manifest | factory/library parts |
| casting | `members` / `subtract` items blended as one body (SDF, `voxel_m`), `fillet_m` inner corners, `round_m` outer edges, `offset_m`, `adaptivity`; `cuts` items machined exactly afterwards | engine blocks, heads, gearbox and pump housings, brackets, cast or forged parts |
| subd | closed low-poly cage `verts` / `faces`, `creases [[i, j, w]]`, `levels` | molded covers, plastic housings, ducts, handles, pressed panels |

`ops: [{op, ...}]` on a geometry part, an array item or a boolean operand is baked into its mesh in order, in the part's local metres, before its transform - anchors, mirrors, array copies and the workbench (`set_spec_param` on `/builders/i/ops/j/...`) see the result:
`bevel` width_m, segments, angle_deg, profile · `boolean` with {builder, params, transform, ops} (or a group), mode difference/union/intersect, solver manifold/exact (inputs closed) · `subdivide` levels, crease_angle_deg (sharper edges stay) · `solidify` thickness_m, offset · `remesh_voxel` voxel_m, adaptivity · `displace` strength_m, scale_m, seed (cast/worn surface) · `weld` dist_m · `shade` smooth, sharp_angle_deg. Rounded corners use one rule everywhere (`blender_ops/fillet_core.py`: a tangent arc of that radius; radii that do not fit an edge are refused). A `contrib:` mesh part takes `smooth` / `sharp_angle_deg` for its shading (default flat) and `ops` like any geometry part. E.g. a bored, chamfered block: `"ops": [{"op": "boolean", "with": {"builder": "revolve", "params": {...}}}, {"op": "bevel", "width_m": 0.004}]`.
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
