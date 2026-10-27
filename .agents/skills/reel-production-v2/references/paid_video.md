# The approval sheet and the paid video (studio2/sheet.py, video.py)

## The sheet
`studio2 sheet build` makes review/sheet/<rev>/index.html (and review/sheet/index.html, the latest): per cut its first,
middle and last pictures (a frame of the preview where it has no such picture, said so), its line, what the viewer learns,
the motion in words, the free preview clip, the paid model, billed seconds and cost, the exact prompt; the reel's total
with the budget and what is spent. A cut failing the calibrated style floor is refused (STYLE_OFF).

<CRITICAL>
The sheet shows every cut, its whole line and the exact prompt the model would get - never a summary. The user's answer is
recorded verbatim: `studio2 sheet approve --user-words "<exactly what they said>"`. A cut their words ask to change goes in
`--cuts` (reopened, never approved); words that name a cut left out of `--cuts` are refused. Never write words for them.
Words that pay for some cuts only ("the first 12 s") name those in `--only` - the rest stay as they are, neither paid for
nor sent back - and a total they allow goes in `--budget-usd` (the project's budget the paid calls are held to; refused
when the spent plus the approved price pass it). Why: without `--only` every cut left out of `--cuts` is approved.
</CRITICAL>

A reopened cut: `studio2 frames make --cut C --frame first --reason "<what the user asked>"` (or `cuts write` when the plan
changes), `motion render`, `sheet build`, show it again. An approval holds while the request is unchanged (the first
picture, the prompt, the length, the arguments).

## The prompt the video model gets (studio2/prompts.video, written from the cut)
You never type the prompt: the writer builds it from the cut's camera, change and `video` fields, in a fixed order, and
the check (`prompts.video_lint`, run by `cuts lint` and again before any paid request) refuses what breaks it. You
control it through the plan.

<CRITICAL>
**One clip, one move of the camera, one thing that moves.** A 4 s clip holds one subject action (`ACTION_S`, one per 4 s);
the camera makes at most one named move, which says where it ends and holds there. Why: several events in one clip come
out "muddled or incomplete" (Vertex best practices). fab c04 asked for four moves plus a push in 4 s and got a blurred
swing with no trip. c03's "about 10% closer" had no end framing: it pushed in, drifted back out, and its wafers merged.
Instead: split the steps into cuts, one leg each (the line runs on across them), and give the move its end in words
(`camera.ends`).

Bad change: "the cart rolls off the lift, along the corridor to the ward, then back to the lift again" (one 4 s cut).
Good: three cuts of about 1.5 s. The cart rolls off the lift. The cart runs down the corridor (a ride). The cart turns
in at the ward door. Each cut's path is one going.
</CRITICAL>

The order the writer keeps, and what feeds each part:

| Part | Written from | Rule behind it |
|---|---|---|
| 1. camera | the `camera.move` row's `video.says`; `camera.ends` (else the row's end, e.g. "a closer framing of it"); `camera.along` for a travel; the `view` row's lead ("First-person view ...") | named move, end framing and a hold; never a percentage |
| 2. the one thing that moves | `change.element` (drawn picture) path, a state's `what`, or a move's `what` | one action per clip length |
| 3. what holds | `video.holds`: positive sentences | contact and grip said positively; never "no/not/nothing" |
| 4. the end | "Single continuous shot." | no cut inside the clip |
| negative prompt | base nouns + the camera row's `avoid` + `video.avoid` | what to avoid is nouns in the model's own field |

The picture already holds the place, look and light, so the prompt never describes them again. Each row of the grammar
brings its own words (a new camera move is a row with `video.says/terms/avoid`, no code).

Bad hold: "Nothing else moves and the jar does not tip." Good hold: "The jar stays upright and full in the robot's
gripper; gripper and jar rise together as one piece." Good `video.avoid`: `["tipping jar", "spilled liquid"]`.
Bad camera: "push in about 8 % toward the gauge". Good: `camera: {move: push, amount: 0.08, ends: "a close-up where the
gauge's needle fills the frame"}`, written as "Slow, steady dolly in toward the gauge, ending on a close-up where the
gauge's needle fills the frame, and the camera holds that framing to the end."

A cut's steps do not fit its clip — in order:
  - 1st: split the steps into cuts, a leg each, in different places (the line runs on: `line_ids`, `line_at_s` [0]);
  - 2nd: show the one step that carries the line, and leave the rest to the cuts around it;
  - 3rd: a longer clip only where the length is the cut's (8 s holds two steps; the reel's rhythm still rules, #4);
  - forbidden: writing the steps without a sequence word to pass the check, or two moves joined in one sentence.

| Code | Refused when | Fix |
|---|---|---|
| `VIDEO_PROMPT_ACTIONS` | more steps (1 + each then/again/and back/...) than the clip holds | split into cuts / say the one step |
| `VIDEO_PROMPT_CAMERA` | two named camera moves (a row's terms, or a move phrase like "pans left") | keep the row's move only |
| `VIDEO_PROMPT_PERCENT` | a distance as a percentage | `camera.ends` in words |
| `VIDEO_PROMPT_NEGATION` | a sentence with no/not/never/nothing/without... | a positive `video.holds`; nouns in `video.avoid` |
| `VIDEO_PROMPT_ORDER` | the camera is not first, or no "Single continuous shot." | (the writer keeps it; a hand-made prompt) |
| `VIDEO_NEGATIVE_NOUNS` | an avoid item is a sentence (over 4 words, or a negator) | short nouns |
| `VIDEO_PROMPT_REFUSED` | the request is checked again at the sheet and before pay | change the cut, `cuts write` |

The provider never rewrites the prompt: `generative/clip.PROMPT_REWRITE_OFF` sends each model's rewrite switch off
(Veo's `auto_fix`, which defaults to true on its text-to-video endpoint; Wan's `enable_prompt_expansion`). A new model with
such a switch is a row there. A model without a negative-prompt field (`NEGATIVE_FIELD`) refuses a negative prompt and
never drops it silently. The sheet shows the negative prompt under the prompt.

## The paid video
1. `studio2 video make --cut C`: veo-3.1 image-to-video (`fal-ai/veo3.1/image-to-video`, $0.20/s, billed 4/6/8 s) from the
   approved first picture through v1's generative/clip.py; never above the approved price; made once per request.
2. `studio2 video check --cut C`: three frames of the video against the approved pictures and the line (motion matches the
   line, the middle near the approved middle, nothing morphs, it ends near the last). Not calibrated on real takes: advice
   for the person. The reader is told our marks are drawn later and which approved pictures are preview frames, so a
   video is never marked down for lacking our labels.
3. `studio2 video composite --cut C`: the marks located again on the video's frames (every second, both ends), judged on the
   middle frame, followed between them and drawn over every frame.
4. Show the person the marked clip; `studio2 video review --cut C --user-words "<their words>"` (`--reject` keeps the free
   clip). Only then is it the cut's clip in v1's edit.
   To show the marked clips in the reel before that: `studio2 reel preview --with-paid` cuts them in for that edit only
   (its manifest names them `unreviewed_paid`); the cuts keep their free clips and nothing is passed. Why: a person
   judges generated motion; a preview is how they see it, never a pass.

## Cost and models
Image-to-video holds only the first picture. First-last-frame and cheaper models on fal (with price evidence) are listed as
candidates in the build plan; adding one needs a `fal_client.PRICING` row - frozen code: ask the user for their freeze words.
Veo 3.1's fal endpoints say they are deprecated on 2026-11-17.

Over the remaining budget — in order: 1) fewer paid cuts (the key change and the hook first; the rest stay free previews),
2) a cheaper model (the user's freeze words for its price row), 3) the user's new cap. Forbidden: splitting a request to
pass under a cap, or paying without the words.
