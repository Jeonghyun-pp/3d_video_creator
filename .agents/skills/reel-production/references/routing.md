# Routing: blender | generative | hybrid

Code: `studio/routing.py` (rules R0–R5, model registry, estimates, gates). CLI: `route plan [--apply] | approve | check | lint`.

## Features (declare in the brief as `route_features`, or in `route.features`)
| feature | means |
|---|---|
| exact_geometry | part count, dimensions or connections must be correct |
| exact_motion | mechanism or camera motion must follow a defined path/timing (inferred from `actions`) |
| cross_shot_identity | the same object must look identical in another shot (inferred from shared asset ids) |
| anchored_text | labels or numbers sit on 3D positions (inferred from `labels`) |
| simple_hard_surface | packshot-like simple solid (revolve, box, glass, can) |
| unstructured_phenomena | fluid, fire, smoke, weather, crowds |
| real_place_atmosphere | mood of a real place, aerial city, historical scene |
| photoreal_beyond_assets | needs realism the available assets/materials cannot reach |
| ai_label_unacceptable | client/education use where an AI-generated label is not allowed |

## Rules (first match wins)
R1 ai_label_unacceptable or policy forbids generation → blender · R2 structural (geometry/motion/identity/anchored text) + (phenomena or beyond-asset look) → hybrid · R3 structural → blender · R4 simple_hard_surface → blender · R5 phenomena/atmosphere/beyond-asset only → generative · R0 nothing declared → blender, low confidence, ask the user.

## Why
The reference reels (@archcutaway) are Veo-generated; their look is not evidence that mechanisms should be generated. Generated video invents motion and geometry, so anything that must be exact is made in Blender; a hybrid only restyles a finished Blender motion pass.

## Role (`route.role`, decided per shot — `studio/generative/policy.py`)
| role | the shot | generation may own | overlays | a generated take is usable when |
|---|---|---|---|---|
| explain | shows parts, positions, connections, dimensions, labels | only a hybrid restyle | labels and graphics on measured 2D anchors | structure gate passed (edge IoU ≥ 0.5, anchors ≤ 1 %) |
| mood | sets the place: establishing aerial, interior at large, transition | the whole look | captions only (`ROUTE_ROLE_CONFLICT` otherwise) | always; structure/flicker/morph/text are warnings for the person choosing |
Default when unset: `mood` for generative, `explain` otherwise (lint W6). Decide by what the narration needs from the frame, never by how real the take looks.
Bad: "s03 looks reference-grade, call it explain". Good: s03 as `mood` (crew in the hall, caption "플랫폼 층"); the inspected column with its outline and dimension is a separate explain shot from Blender.

## Cost and approval
`route plan` writes `route_plan.md` with role, cost and minutes per shot (estimates: measured frame times when present, else GPU 1.1 s/frame). Paid shots: `generate review` sheet → user's words → `route approve --review <id> --user-words "..." [--budget-usd N]` (`--shot all` for every shot on the sheet). The approval is bound to the request fingerprint (endpoint, prompt, inputs, seed, take, durations, padding, adapter arguments). A route revision always returns to `proposed` and clears the binding.

Atmosphere boundary (2026-10-05): fog and light shafts that explain structure (an atrium lit by skylight beams) are a Blender opt-in (`shot.render.atmosphere`, `look_photoreal.md`) and do not make a shot `unstructured_phenomena`. Real-place haze, smoke, weather and crowds still route to generation.
