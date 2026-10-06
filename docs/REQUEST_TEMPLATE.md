# Request template for Astra (scripts/reel_agent.py)

Why this shape (engine test, 2026-10-07): the mechanism had numeric acceptance and unlimited builds, the exterior had one
sentence and no iteration before the final renders - and came out as slabs while the mechanism matched to 1e-8 m.
Give appearance its own track, its own acceptance and its own (free) iteration budget.

```
Goal: <what the video explains, for whom, how long>
Project: create projects/harness_validation/<name> (new)
Reference images (local only, --image): <paths>  - never committed, never sent to a generation model

Decisions already made by the user (do not ask again; do not open decision-ladder sheets unless asked):
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

## Budgets and limits
- Previews: unlimited. Counted renders: <n> final stills (render submit --profile look --frames <f>).
- No paid calls. Engine code and frozen files are not edited; say what the engine lacks.
- While fidelity fails: three builds without improvement on the same shot -> stop and report.
- Contrib entries only for reusable laws and parts; one-off forms in spec ops or the author script.

## Deliverables
Shot versions, the counted renders, reference-vs-render sheets, a report: source table (track 1), checklist verdicts
(track 2), what was hard, what the engine should have.
```
