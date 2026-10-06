# Photoreal look presets

Code: `studio/blender_ops/look.py` + `look_scale.py`, `look_perfection.py`, `look_camera.py`, `look_lighting.py`, `look_materials.py`; data in `look_data/`. Verified in `projects/harness_validation/photoreal_research/` (2026-10-03).

| preset | lighting | camera default | passes |
|---|---|---|---|
| flat_stylized | none (default; existing shots unchanged) | — | — |
| photoreal_product | studio_product HDRI + 3-light rig, AgX | product, no shake | bevel, jitter |
| photoreal_exterior | exterior_day (sun:sky 4–10) | exterior, handheld_light | bevel, snap, jitter |
| photoreal_interior | interior_industrial | interior, tripod | bevel, snap, jitter |
| photoreal_night | night_city | exterior, tripod | bevel, jitter |
| previs_clay | none, matte role colours | motion blur off | none (hybrid input) |

Order (fixed): scale audit → bevel/contact/snap → jitter → camera realism → lighting/metering → bake (opt-in) → compositor. Same inputs on a base version → skipped ("unchanged").
- Scale: tag parts with `studio_dim_role` (see `look_data/real_dimensions.json`); flag_ratio > 0.2 is a build error (`look_scale`; a warning only under `project.policy` look-first or `gates.look_scale: warn`). A bench, floor or set piece without a catalogued size takes `studio_dim_role: none`.
- Mechanical contact: mark parts `studio_mechanical` and list pairs in `scene['studio_guard_pairs']`; any change of a guard distance is fatal.
- Lighting: HDRIs come only from the library with CC0 + sha256 check; metering is deterministic (0.05 EV steps); `style.light_rig.exposure_ev` pins EV.
- Materials (author API): `look_materials.make_material(name, kind, library_root=...)`; textures are packed and sha-checked; object-local box mapping (no UVs needed).
- Camera realism: DOF on `focus_anchor`, shutter keeps label anchors ≤ 2 px, shake ≤ 2.5 px RMS on anchors; no lens distortion with labels; no grain.

## Interior lighting, atmosphere (2026-10-05)
- Practicals hang at `height_fraction` of the lit bounds; when a ceiling is found straight above the target centre (ray cast, scene roles), they hang 0.3 m under it instead (`look_report.lighting.practical_ceiling_z`).
- Author lights are hidden by the lighting pass unless tagged `obj['studio_keep_light'] = True` (`kept_author_lights` in the report). Measured on s02: kept fill lamps + metering overexposed the interior — keep a lamp only when nothing else reaches that space.
- `style.light_rig.emissive_to_area: true` (experimental) turns every `light_fixture` mesh into a camera-invisible area light sized to its footprint. Measured on s02 it added 136 lights and did not improve exposure; prefer the preset's practicals.
- Atmosphere is opt-in per shot: `shot.render.atmosphere {density, anisotropy, color_srgb, box, beams[{location, aim, spread_deg, blend, power_w, temperature_k}]}` builds a Principled Volume box (role `atmosphere`) and spot-light shafts. Confine the fog with `box` (an atrium, a shaft): fog over a whole site reads as flat haze (measured: density 0.015 over 400 m washed s02 out; 0.02 in the atrium box with two 12° 300 kW beams gave visible shafts). Volumes cost render time; atmosphere never reaches control passes, clay, metering or ray-cast guards.
- Explanatory atmosphere (structure lit by shafts) stays in Blender; real-place haze, smoke, weather still route to generation (`routing.md`, R5).

## Finish and bake (2026-10-05)
- `style.look.compositor` overrides the preset's compositor; `style.look.passes` overrides preset passes key by key.
- `explainer_finish`: bloom (threshold 1.1, strength 0.28), vignette 0.24, soften 0.15 — position-preserving nodes
  only, so label anchors stay put. Explainer graphics and labels are composited after it (no bloom on them).
  `compositor_setup` drops any non-position-preserving op when the shot has anchored labels.
- Bake (`style.look.passes.bake: {min_distance_m, resolution_px, samples, max_objects}`, `look_bake.py`): static,
  distant objects with procedural catalog materials → packed colour/metallic/roughness/normal maps on one plain
  material; LOD pinned at the object's closest approach. Measured on s02: same picture (dE median 0.0, p95 1.07) but
  only −6 % frame time for +606 s of build — **opt-in only**, for scenes where shader time dominates. Not for
  near objects, moving or reveal-cut objects, glass, emission (they are skipped with their reason in the report).
