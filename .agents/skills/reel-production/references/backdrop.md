# Backdrops — the place around an exact subject

Code: `studio/generative/backdrop.py` (paid image request: sheet → user's words → budget/ledger), `studio/blender_ops/backdrop.py`
(card, relation lights, reflection dome), `studio/blender_ops/layout.py` `_support` (the bench or floor), `studio/layout.py` lint.
Plan and measurements: `docs/BACKDROP_STAGING_PLAN.md`.

## Order (place → view → backdrop)
1. **Brief:** the place in words (`place`, e.g. "a modern robot assembly hall"). Story-level, before any camera.
2. **Storyboard:** subject, view (height, how far the camera looks down, lens) and move. A backdrop image is one view, so the
   view must exist before the image.
3. **Look:** the relation in `shot.scene.backdrop.relation` -
   - `support {kind: bench | floor | none, material (a scene.materials name), margin, behind}`: the subject stands on it
     (top at the subject's lowest point); it reaches toward the view and only `behind` (0.6 subject sizes) past the subject,
     so the place shows beyond its back edge;
   - `view {elevation_deg, azimuth_deg}`: the storyboard camera's view (lint warns past 10 degrees of difference);
   - `light {key_side, key_kelvin, fill_kelvin, reflections}`: key and fill lamps the look keeps, and a dome of the image
     only reflections see.
   Then what is in the place and where (backdrop objects - in `--place` words today: e.g. "an orange six-axis arm upper right,
   a conveyor across the lower third, tall windows upper left"), keeping the centre clear for the subject.
4. **Backdrop image:** `generate backdrop-review --project P --id ID --shot S [--place "…"] --count 2` composes the prompt
   from the relation (view, light sides and kelvins, empty centre, no text) and the place (`--place`, else the approved brief's
   `place`) → show the sheet → `generate backdrop --review R --user-words "…" --allow-paid --max-usd N [--budget-usd B]` →
   show the images, the user picks one → `shot.scene.backdrop.image` (+ `blur_px`, `strength`).
5. **Build and render.** Small camera changes keep the image; a different view (> 10 degrees) means a new image.

## Exceptions, in order
1. The camera turns far around the subject (turntable, orbit past 30 degrees): one image cannot follow - keep the turn small.
2. If the turn is the point of the shot: the backdrop needs a panorama (not built yet - say so; do not paste a plate).
3. Never: a 3D environment modelled for the backdrop alone (no assets for it; the user ruled it out on 2026-10-06).

## Bad / Good
Bad: render the subject on grey, generate "a factory" afterwards, paste it behind - the subject floats, the light disagrees.
Good: brief `place: robot assembly hall` → storyboard macro push at 30 deg → relation bench (behind 0.6), view 30, key left
6000 K / fill right 3200 K → `backdrop-review --shot s01` → pick → build: the gears stand on the bench edge, the hall
shows past it (robot_joint v0016).

## Machine checks
| check | where | severity |
|---|---|---|
| backdrop image missing in the project | layout lint (`LAYOUT_INVALID`) | ❌ Error |
| camera elevation vs `relation.view` > 10 deg | layout lint | ⚠️ Warning |
| orbit/turntable sweep > 30 deg with a backdrop image | layout lint | ⚠️ Warning |
| subject floating above the support | `tests/studio/backdrop_smoke.py` (contact gap 0) | test |
