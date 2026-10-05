# Scene roles — one tag, every mesh-sweeping pass

Code: `studio/blender_ops/scene_roles.py` (`ROLES` table, `counts(obj, rule)`, `first_blocking_hit`).
Read when a scene has anything that is not the subject: an earth or sky shell around an underground set, fog,
hidden helpers, road markings or window bands, self-lit fixtures.

Many passes sweep every mesh: look target bounds and metering, previs clay, perfection (bevel/snap), scale audit,
control near/far and render, camera visibility / pass-through / screen-flow ray casts, reveal overlap cuts.
Tag an object once with `obj['studio_scene_role'] = <role>` and every pass treats it consistently. Do not use
`studio_role` for this: that key already names asset parts and clay colour categories.

| role | bounds (light/meter/scale) | clay | control depth range | control render | blocks rays | reveal overlap | perfection |
|---|---|---|---|---|---|---|---|
| object (untagged, visible) | yes | yes | yes | yes | yes | yes | yes |
| environment_shell | yes* | yes | **no** | yes | yes | no | no |
| atmosphere | no | no | no | **no** | **no** | no | no |
| helper (untagged + hide_render) | no | no | no | no | no | no | no |
| clutter | yes | yes | yes | yes | yes | yes | no |
| light_fixture | yes | yes | yes | yes | yes | no | no |
| scatter (GN host) | yes | yes | yes | yes | yes | no | no |
| scatter_source | no | no | no | no | no (hits count as scatter) | no | no |
| graphic | no | no | no | no | no | no | no |
| simulated | yes | yes | yes | yes | yes | no | no |

\* Measured 2026-10-05 on samsung_photoreal s02: dropping the shell from the look's bounds moved the practicals into
the set and the log-average meter blew the interior out (EV 5.5–6.0 vs 3.35, visibly worse). The shell stays in the
bounds until interior metering is reworked; its tag still keeps it out of the control depth range, perfection and reveals.

Engine helpers tag themselves: camera paths, aim empties, reveal cutters (`helper`); the look's fog box and `dust`
(`atmosphere`); scatter hosts and sources (`scatter`, `scatter_source`), debris (`simulated`), explainer graphics
(`graphic`). Instances are invisible to `bound_box`/`to_mesh`; passes read them through `scene_geometry`
(→ `scatter_simulation.md`). Grease Pencil is hidden from control passes by type as well as by role.
Rules: a new kind of object is one new row in `ROLES`, never a new exception in a pass. An unknown role fails loudly.
Bad: hiding the earth shell in the author script so the control pass ignores it (the meter and lighting then change too).
Good: `earth['studio_scene_role'] = 'environment_shell'` — control depth range and reveals skip it, lighting is unchanged.
