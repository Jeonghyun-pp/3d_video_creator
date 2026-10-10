# Generative and hybrid shots

## Before any paid call (in order)
1. Role set (`route.role`: explain | mood, `routing.md`) and route proposed with a cost estimate.
2. `acceptance.md` written for the shot (what must match: parts, motion, timing) — relaxed only in the user's own words.
3. `route.generative.prompt_spec`, then `generate prompt` (subject specs or, without them, the version's `subjects_index.json`: every spec-built element on screen becomes a "clay shape = part" line; off-screen ones are left out).
4. Inputs: `generate inputs` — every role sends the look render as the input video (`render submit --profile review`
   of the current version with its look preset; no fallback to clay); explain also needs the control clay as its QA
   baseline (`generate control --kinds depth,clay`). Lint W9 (clay or a flat look sent), W10 (stale). Reference models
   (Seedance, Kling, Luma) get the look render's middle frame automatically, plus stills added by name
   (`--reference images/<id>/0.png`, a generated image with provenance) or cleared library assets — never frames of a
   third-party reel (`REFERENCE_NOT_CLEARED`). Optional structure still: `generate structure-still-review` → sheet →
   `generate image --review <id> --user-words "..."` → `generate inputs --reference ...`.
5. Control structure check (free): `generate control` richness, W5 (see below).
6. **`generate review`** → show `sheet.png` / `sheet.md` (frames, prompt items, final prompt, refs, model, cost). The user adds, changes or forbids items in their own words; you edit `prompt_spec`, re-run `generate prompt` and `generate review --after <id> --user-words "<what they asked>"`; repeat until they approve that sheet.
7. `route approve --review <id> --user-words "<their words>"` (`--shot all` for the whole sheet). Only the user's words go there; your notes go to `--agent-note`.

### Prompt template (short: ≤ 200 words, lint W7; the structure clause has its own 70-word share)
```
Photorealistic film footage.
Look: <materials, light, colour, mood — prompt_spec.look>
Follow the input video camera, timing and positions exactly; …        (hybrid, automatic)
The input video is a rendered model; its shapes are: <shape = part>   (automatic)
Structure that must read clearly - layers from top to bottom: <colour part, share>; …   (explain, automatic, measured)
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
- hybrid whole-frame structure: median edge IoU vs the clay pass ≥ 0.5 and anchor error ≤ 1 % of width. On an explain
  shot it decides only two things (since 2026-10-10): a pass writes measured `anchors_2d.json` (labels and graphics may
  ride on it), and with no key part declared it is the only evidence of structure. Otherwise a miss is a warning.

## Key parts, automatic keep and light
- `qa.parts`: per declared key part and kept part, the share of its clay edges found again in the take, between an upper
  bound (the clay blurred and hue-turned: a perfect restyle) and a lower one (the clay shifted 5 %). ratio ≤ 0 = lost.
- After every hybrid take `generate clip` runs `auto_keep`: Blender look-render pixels go back only for the parts the
  take lost, `shot.screen.keep`, labelled parts (unless the whole-frame gate measured anchors) and decals — nothing when
  that list is empty, so a take that held stays 100 % generated. Per-frame masks, 2 px feather, `clip_kept.mp4` (the
  edit uses it); `kept.reasons` says why each part is there. The render is graded to the take's colour in a 6-px ring
  around the seam (mean and spread per channel); past ×1.5 contrast or 40 levels it is merged ungraded with
  `SEAM_COLOUR_FAR`. Labels then use the kept parts' 3D anchors (`anchors_2d.json`, `anchors_source: kept`).
  It needs a complete look render of the same version; without one the take says `KEEP_NOT_APPLIED`.
- An explain take is usable when no declared part is lost after the keep; a take with nothing declared and no
  whole-frame pass is `structure not verified` (pickable in the user's words only where `explain_generated` allows).
- `qa.light`: with the control `normal` pass and a look render, the key light is fitted on both and the angle between
  them recorded; past 45° a warning (provisional - no measured takes yet).

## After generation
- Every take records `qa` (structure with IoU, preservation, extra; parts; flicker; morph; text), `kept` and `policy` {role, usable, reasons, warnings} in `clip.json`. An unusable explain take is never used: the edit falls back to the Blender pass and warns `reject_route`.
- Show the takes; `generate select --take <key> --user-words "<their choice>" --additions present:<item>,absent:<item>` (one entry per `prompt_spec.add` item).
- Provider trouble: polling errors are retried for free; `GENERATION_REMOTE_FAILED` (failed on fal) or `GENERATION_REQUEST_UNKNOWN` (no receipt) keep the cost counted — ask the user to check the fal dashboard and record their answer with `generate reconcile --request <dir> --charged yes|no --user-words "..."`. Forbidden: resending, or reconciling without the user's answer.

## When a generated shot fails — in order
1. A part the explanation needs keeps getting lost: declare it (`key_parts`) so `auto_keep` puts it back, then check the structure clause names it (`generate prompt`, `structure` in the result).
2. The take looks pasted at the seams (`SEAM_COLOUR_FAR`) or the structure is read wrongly: add a structure still (`generate structure-still-review`) so a reference model copies the look and the order, and regenerate once.
3. Tighten the prompt (look words only) and regenerate once; switch model (seedance-2.5 for look and part identity, wan-2.2-vace depth for edges).
4. Hand the model less: `screen.generate_only` (people, sky) with the rest the Blender look render.
5. Change the role: a take that looks right but loses the explanation may serve as a `mood` shot (captions only), with the explanation moved to a Blender shot; or route back to blender with a photoreal look preset.
Forbidden: loosening the acceptance criteria or the part threshold to pass, keeping the whole frame to hide a bad take (`screen.keep` of the whole subject turns the shot back into a render - then route it to blender honestly), or using a first-frame model for a hybrid shot.

Explainer graphics (`shot.graphics`) are never in the previs, control or prompt: they are rendered as their own layer
and composited in edit, and only on a generated clip that passed the whole-frame structure check (measured `anchors_2d.json`), else
`GRAPHICS_UNSUPPORTED` (→ `explainer_graphics.md`). Baked debris and scatter are part of the previs like any geometry;
dust is `atmosphere` and stays out of control passes (describe it in `generative_look.motion` if the model should add it).

Delivery: AI-generated shots need `ai_disclosure_confirmed: true` in the human review; review-only assets need `license_overrides`.
