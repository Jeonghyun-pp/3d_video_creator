# Generative and hybrid shots

## Before any paid call (in order)
1. Route approved by the user (`route approve`), cost within the budget.
2. `acceptance.md` written for the shot (what must match: parts, motion, timing) — never relaxed afterwards.
3. Prompt file `prompts/<shot>.txt`: material/light/mood only; "no text, no letters, no captions"; no quoted strings. Hybrid prompts must say "follow the input video camera, timing and positions exactly" and must not describe camera moves.
4. Hybrid inputs: full-length motion pass rendered with `look_preset: previs_clay` (`kind: previs`), up to 4 look reference images (`kind: reference_image`, no framing info; `start_s`/`end_s` when one applies to part of the clip), optional `generate control` depth (`kind: control`).
5. Previs conventions (`route.generative.previs`, written into the prompt by `generate prompt`): the prompt maps each clay shape to the real part ("the long rounded body = ..."); `orientation_colors: true` tints faces pointing along the subject's length axis red and backward faces blue so the model keeps the heading; `placeholders: [{start_frame, end_frame, part_ids, description}]` keys those parts black only inside the window and tells the model what to generate there. The previs fixes blocking only; secondary motion (smoke, haze) goes in the spec's `generative_look.motion`.

## Run
`generate clip --project P --shot S --allow-paid --max-usd N` → `shots/S/generated/<key>/clip.json` (30 fps, BT.709, exact frame count). Same request = no new charge; raise `take` for a new attempt (≤ 3 attempts; the schema's `max_attempts` ceiling of 5 is a hard cap, not a target, and needs the user's budget). Several takes → `generate select --take <key>`.

## QA (`studio/qa_generative.py`)
- flicker, morph, text (OCR when tesseract exists): warnings.
- hybrid structure: median edge IoU vs the clay pass ≥ 0.5 (calibrated: restyles 0.69–1.0, a 1.3 % shift 0.34–0.42) and anchor error ≤ 1 % of width — **fail by default**; a failed hybrid goes back to the Blender pass (`reject_route`). Only a passed structure check writes `anchors_2d.json`, which is the only way labels may sit on generated pixels.

## When a generated shot fails — in order
1. Tighten the prompt (look words only) and regenerate once.
2. Switch to a model with stronger structure adherence (wan-2.2-vace depth for structure, seedance-2.5 for texture).
3. Reduce the shot: shorter duration, simpler motion in the previs.
4. Route back to blender with a photoreal look preset.
Forbidden: loosening the acceptance criteria or structure threshold to pass, or using a first-frame model for a hybrid shot.

Delivery: AI-generated shots need `ai_disclosure_confirmed: true` in the human review; review-only assets need `license_overrides`.
