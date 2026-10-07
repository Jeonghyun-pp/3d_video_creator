# Request template for Astra (scripts/reel_agent.py)

Why this shape (engine test, 2026-10-07): the mechanism had numeric acceptance and unlimited builds, the exterior had one
sentence and no iteration before the final renders - and came out as slabs while the mechanism matched to 1e-8 m.
Give appearance its own track, its own acceptance and its own (free) iteration budget.

```
Goal: <what the video explains, for whom, how long>
Project: create projects/harness_validation/<name> (new)
Reference images (local only, --image): <paths>  - never committed, never sent to a generation model

Launch: scripts/reel_agent.py "<this request>" --delegate "<the user's own words handing the run over>" [--image ...]
(the delegation is recorded on the project; no decision-ladder sheets; Astra records its own choices with decide note)
Decisions already made by the user (do not ask again):
- <subject, shots, place, look>

## Track 1 - mechanism (numbers that make a claim)
- What must be exact: <parts, laws, ratios, positions>; numbers from sources (cite) or marked inferred.
- Acceptance: <motion = calculation within ..., interference 0, frame probe 0, key parts visible>.

## Track 2 - appearance (illustrative; any modelling path)
- Before modelling, list the visible features from the reference images as a checklist, e.g.
  "black ribbed molded cover", "four smooth curved runners", "vented alternator with copper inside", "soft ground shadow".
- Model them any way: spec builders with ops, the casting / cage kits, or any bpy in the author script. Appearance
  numbers are illustrative and need no source.
- Iterate against the reference: match the camera to the photo (reference_fit_camera), compare previews
  (reference_compare, lit workbench previews) - previews are unlimited and free.
- Acceptance: every checklist item pass by the reviewer (crop evidence), silhouette IoU at the photo's view >= <value>.

## Detail (SKILL #8: build what shows)
- List the reference's detail in four tiers before modelling: silhouette / major forms / secondary (ribs, bosses,
  flanges, bolts, clamps) / finish (fillets, chamfers, cast surface, parting lines). Model every item, or put it in the
  simplification table with its on-screen size (only reason: below min_screen_px, default 24 px).
- Acceptance: fidelity `detail` checks pass (no coarse primitive on screen without a `plain` reason), no
  CONTRIB_PARAM_UNUSED, and the reviewer finds no rough part missing from the simplification table.

## Budgets and limits
- Previews: unlimited. Counted renders: <n> final stills (render submit --profile look --frames <f>).
- No paid calls. Engine code and frozen files are not edited; say what the engine lacks.
- While fidelity fails: three builds without improvement on the same shot -> stop and report.
- Contrib entries only for reusable laws and parts; one-off forms in spec ops or the author script.

## Deliverables
Shot versions, the counted renders, reference-vs-render sheets, a report: source table (track 1), checklist verdicts
(track 2), the detail list and simplification table, the decisions you made (decide notes), what was hard, what the
engine should have.
```
