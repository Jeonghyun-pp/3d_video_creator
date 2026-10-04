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

## Cost and approval
`route plan` writes `route_plan.md` with cost/minutes per shot (estimates: measured frame times when present, else GPU 1.1 s/frame). Approval: the user's words via `route approve --evidence "..." [--budget-usd N]`. A route revision always returns to `proposed`.
