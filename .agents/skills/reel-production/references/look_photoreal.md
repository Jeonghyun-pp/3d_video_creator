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

Order (fixed): scale audit → bevel/contact/snap → jitter → camera realism → lighting/metering → compositor. Same inputs on a base version → skipped ("unchanged").
- Scale: tag parts with `studio_dim_role` (see `look_data/real_dimensions.json`); flag_ratio > 0.2 is a warning (fatal if `style.look.qa.fail_on` has `scale`).
- Mechanical contact: mark parts `studio_mechanical` and list pairs in `scene['studio_guard_pairs']`; any change of a guard distance is fatal.
- Lighting: HDRIs come only from the library with CC0 + sha256 check; metering is deterministic (0.05 EV steps); `style.light_rig.exposure_ev` pins EV.
- Materials (author API): `look_materials.make_material(name, kind, library_root=...)`; textures are packed and sha-checked; object-local box mapping (no UVs needed).
- Camera realism: DOF on `focus_anchor`, shutter keeps label anchors ≤ 2 px, shake ≤ 2.5 px RMS on anchors; no lens distortion with labels; no grain.
