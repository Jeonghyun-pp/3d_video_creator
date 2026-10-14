# Scene roles — one tag, every mesh-sweeping pass

Code: `studio/blender_ops/scene_roles.py` (`ROLES` table, `counts(obj, rule)`, `first_blocking_hit`).
Read when a scene has anything that is not the subject: an earth or sky shell around an underground set, fog,
hidden helpers, road markings or window bands, self-lit fixtures.

Many passes sweep every mesh: look target bounds and metering, previs clay, perfection (bevel/snap), scale audit,
control near/far and render, camera visibility / pass-through / screen-flow ray casts, reveal overlap cuts.
Tag an object once with `obj['studio_scene_role'] = <role>` and every pass treats it consistently. Do not use
`studio_role` for this: that key already names asset parts and clay colour categories.

| role | bounds (light/meter/scale) | clay | control depth range | control render | blocks rays | reveal overlap | perfection | floor |
|---|---|---|---|---|---|---|---|---|
| object (untagged, visible) | yes | yes | yes | yes | yes | yes | yes | no |
| environment_shell | yes* | yes | **no** | yes | yes | no | no | no |
| atmosphere | no | no | no | **no** | **no** | no | no | no |
| helper (untagged + hide_render) | no | no | no | no | no | no | no | no |
| clutter | yes | yes | yes | yes | yes | yes | no | no |
| light_fixture | yes | yes | yes | yes | yes | no | no | no |
| scatter (GN host) | yes | yes | yes | yes | yes | no | no | no |
| scatter_source | no | no | no | no | no (hits count as scatter) | no | no | no |
| graphic | no | no | no | no | no | no | no | no |
| simulated | yes | yes | yes | yes | yes | no | no | no |
| support (deck, pad, table, ground) | yes | yes | yes | yes | yes | yes | yes | **yes** |

\* Measured 2026-10-05 on samsung_photoreal s02: dropping the shell from the look's bounds moved the practicals into
the set and the log-average meter blew the interior out (EV 5.5–6.0 vs 3.35, visibly worse). The shell stays in the
bounds until interior metering is reworked; its tag still keeps it out of the control depth range, perfection and reveals.

`floor` (2026-10-14): what a subject stands on. A frame_to_frame camera stays above the support top under it by a
quarter of the framed subject's height, between 0.05 and 0.5 m (a product on a bench or a car from 0.3 m stays
possible) - a pose lower is refused (`FRAME_BELOW_SUPPORT`), never lifted; `guards.min_clearance_m` (a plume, the sea)
is a separate clearance. A camera inside a support is kept out with no guards declared. A subject the shot moves
(keys, drives, pose, deformation) never moves more than 2 cm under its support on any frame (`SUBJECT_SINKS`,
broken); a subject placed once is the author's design and is not judged (a cutaway building's basement below a
support ground) - a statically misplaced subject is not this gate's job. The search for the support under a point
starts at the subject (its middle for the camera, its top for sinking): a support over the subject - a street over a
station hall - is its ceiling. An `environment_shell` is never a floor (a camera inside the underground set it wraps
is below its top). Water is not a support: a floating hull sits in it. The engine tags the street kit's road and
sidewalks, the hall kit's floor and the backdrop relation's support; tag a deck, pad or ground the author script makes
(`obj['studio_scene_role'] = 'support'`, or `role: support` on a layout primitive). Library exemplar data (a slab, a
track) is deliberately not tagged yet: changing an exemplar spec would stale another session's projects that pin it -
tag it in the scene that uses it.

Engine helpers tag themselves: camera paths, aim empties, reveal cutters (`helper`); the look's fog box and `dust`
(`atmosphere`); scatter hosts and sources (`scatter`, `scatter_source`), debris (`simulated`), explainer graphics
(`graphic`). Instances are invisible to `bound_box`/`to_mesh`; passes read them through `scene_geometry`
(→ `scatter_simulation.md`). Grease Pencil is hidden from control passes by type as well as by role.
Rules: a new kind of object is one new row in `ROLES`, never a new exception in a pass. An unknown role fails loudly.
Bad: hiding the earth shell in the author script so the control pass ignores it (the meter and lighting then change too).
Good: `earth['studio_scene_role'] = 'environment_shell'` — control depth range and reveals skip it, lighting is unchanged.
