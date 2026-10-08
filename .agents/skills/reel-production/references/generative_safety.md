# Generative and hybrid shots

## Before any paid call (in order)
1. Role set (`route.role`: explain | mood, `routing.md`) and route proposed with a cost estimate.
2. `acceptance.md` written for the shot (what must match: parts, motion, timing) — relaxed only in the user's own words.
3. `route.generative.prompt_spec`, then `generate prompt` (subject specs or, without them, the version's `subjects_index.json`: every spec-built element on screen becomes a "clay shape = part" line; off-screen ones are left out).
4. Inputs by role: `generate inputs` — explain gets the control clay (previs) + depth, mood gets the look render
   (`render submit --profile review` of the current version with its look preset). Lint W9 (mood fed clay), W10 (stale).
   Inputs: hybrid previs (`previs_clay` or the control clay), optional control depth; reference models (Seedance, Kling, Luma) get 1–4 look stills from this project (`generate still`) or cleared library assets — never frames of a third-party reel (`REFERENCE_NOT_CLEARED`).
5. Control structure check (free): `generate control` richness, W5 (see below).
6. **`generate review`** → show `sheet.png` / `sheet.md` (frames, prompt items, final prompt, refs, model, cost). The user adds, changes or forbids items in their own words; you edit `prompt_spec`, re-run `generate prompt` and `generate review --after <id> --user-words "<what they asked>"`; repeat until they approve that sheet.
7. `route approve --review <id> --user-words "<their words>"` (`--shot all` for the whole sheet). Only the user's words go there; your notes go to `--agent-note`.

### Prompt template (short: ≤ 120 words, lint W7)
```
Photorealistic film footage.
Look: <materials, light, colour, mood — prompt_spec.look>
Follow the input video camera, timing and positions exactly; …        (hybrid, automatic)
The input video is a grey clay model; its shapes are: <shape = part>   (automatic)
Keep: <prompt_spec.keep>
Add: <prompt_spec.add>
No text, no letters, no captions. Avoid: <prompt_spec.forbid>
```
Bad: "five levels, 9 columns per row, nothing added, escalators at 30 degrees" (counts and positions the model will not obey). Good: look "cool LED light on fair-faced concrete, polished granite floor", add "two inspectors in orange vests", forbid "ceiling louvres"; structure stays in the input video.

## Run
`generate clip --project P --shot S --allow-paid --max-usd N` → `shots/S/generated/<key>/clip.json` (30 fps, BT.709, exact frame count). Same request = no new charge; raise `take` for a new attempt (≤ 3 attempts; the schema's `max_attempts` ceiling of 5 is a hard cap, not a target, and needs the user's budget). Several takes → `generate select --take <key>`.

A take that is not 30 fps becomes 30 fps by **motion interpolation** (`retime: interpolate`, the default: RIFE at the exact
30 fps instants; `scripts/install_rife.sh`); `duplicate` (nearest frame) repeats every fourth frame of a 24 fps take and
is only for a deliberately choppy look. Measured 2026-10-08 against a clip drawn at the exact instants: 31.8 vs 27.1 dB,
0 % vs 20 % repeated frames; a take matched frame for frame (Wan `match_input_num_frames`) is mapped 1:1.

## QA (`studio/qa_generative.py`)
- flicker, morph, text (OCR when tesseract exists), judder (`GENERATED_JUDDER`: more than 2 % of frames repeat the one before inside motion): warnings.
- hybrid structure: median edge IoU vs the clay pass ≥ 0.5 (calibrated: restyles 0.69–1.0, a 1.3 % shift 0.34–0.42; re-measured on the shadowless clay 2026-10-05: grain 0.977, 5 % shift 0.435) and anchor error ≤ 1 % of width — **fail by default**; a failed hybrid goes back to the Blender pass (`reject_route`). Only a passed structure check writes `anchors_2d.json`, which is the only way labels may sit on generated pixels.

## Kept parts and light (2026-10-07)
- `shot.screen.keep`: parts the explanation depends on. `generate keep-masks` renders where they show on every frame
  (the frame probe's classes, occluders included; free, one Workbench pass); after a take, `generate clip` runs
  `generate keep`, which merges the Blender look render over the take inside the masks (2 px feather) into
  `clip_kept.mp4` - the edit uses it. QA judges the take as generated, never the kept clip. It needs a complete look
  render of the same scene version (`render submit --profile review`); without one the take says `KEEP_NOT_APPLIED`.
- `qa.parts`: per declared key part and kept part, the share of its clay edges found again in the take, between an upper
  bound (the clay blurred and hue-turned: a perfect restyle) and a lower one (the clay shifted 5 %). ratio ≤ 0 = lost.
  An explain take with a lost part that `keep` did not put back is unusable; on a mood shot it is a warning.
- `qa.light`: with the control `normal` pass and a look render, the key light is fitted on both (luminance against
  camera-space normals) and the angle between them recorded; past 45° a warning (provisional - no measured takes yet).
- Explain shots send grey clay; when a look render exists, `generate inputs` adds its middle frame as a reference image
  for reference models (seedance, kling, luma), so the designed light reaches them. It is the studio's own render.

## After generation
- Every take records `qa` (structure with IoU, preservation, extra; flicker; morph; text) and `policy` {role, usable, reasons, warnings} in `clip.json`. An explain take that failed structure is never used: the edit falls back to the Blender pass and warns `reject_route`.
- Show the takes; `generate select --take <key> --user-words "<their choice>" --additions present:<item>,absent:<item>` (one entry per `prompt_spec.add` item).
- Provider trouble: polling errors are retried for free; `GENERATION_REMOTE_FAILED` (failed on fal) or `GENERATION_REQUEST_UNKNOWN` (no receipt) keep the cost counted — ask the user to check the fal dashboard and record their answer with `generate reconcile --request <dir> --charged yes|no --user-words "..."`. Forbidden: resending, or reconciling without the user's answer.

## When a generated shot fails — in order
1. Tighten the prompt (look words only) and regenerate once.
2. Switch to a model with stronger structure adherence (wan-2.2-vace depth for structure, seedance-2.5 for texture).
3. Reduce the shot: shorter duration, simpler motion in the previs.
4. Change the role: a take that looks right but fails structure may serve as a `mood` shot (captions only), with the explanation moved to a Blender shot.
5. Route back to blender with a photoreal look preset.
Forbidden: loosening the acceptance criteria or structure threshold to pass, or using a first-frame model for a hybrid shot.

Explainer graphics (`shot.graphics`) are never in the previs, control or prompt: they are rendered as their own layer
and composited in edit, and only on a generated clip that passed the structure check (`anchors_2d.json`), else
`GRAPHICS_UNSUPPORTED` (→ `explainer_graphics.md`). Baked debris and scatter are part of the previs like any geometry;
dust is `atmosphere` and stays out of control passes (describe it in `generative_look.motion` if the model should add it).

Delivery: AI-generated shots need `ai_disclosure_confirmed: true` in the human review; review-only assets need `license_overrides`.
