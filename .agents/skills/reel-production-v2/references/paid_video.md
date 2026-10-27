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
| 3. what holds | `video.holds`: positive sentences, or `{says, held, holder, kind, at}` (the prompt gets `says`) | contact and grip said positively; never "no/not/nothing"; a named pair is measured on the clip |
| 4. the end | "Single continuous shot." | no cut inside the clip |
| negative prompt | base nouns + the camera row's `avoid` + `video.avoid` | what to avoid is nouns in the model's own field |

The picture already holds the place, look and light, so the prompt never describes them again. Each row of the grammar
brings its own words (a new camera move is a row with `video.says/terms/avoid`, no code).

Bad hold: "Nothing else moves and the jar does not tip." Good hold: "The jar stays upright and full in the robot's
gripper; gripper and jar rise together as one piece." Good `video.avoid`: `["tipping jar", "spilled liquid"]`.

<CRITICAL>
**A contact the cut depends on names its pair, or it is not checked.** Write the hold as `{"says": "<the sentence>",
"held": <part id>, "holder": <part id>, "kind": <contact kind>, "at": [start, end]}`: held and holder are two of the
cut's parts, kind a row of `library/video_checks.json` `contact_kinds`, `at` the share of the clip it holds over (the
whole clip when left out; from the end of the change when the change is the taking up). `cuts lint` refuses a pair
that is not the cut's parts or names no kind (`VIDEO_HOLD_PAIR`). Why: a reader asked "is the contact kept" called fab
c02's riding box floating on both runs; the contact is now measured on the parts located on the clip, and only for a
pair the plan names - a sentence alone is said to the model and measured by nobody.

Bad: `"holds": ["The box rises into the hoist and stays gripped inside it."]` (c01: said, never measured).
Good: `"holds": [{"says": "The box rises into the hoist and stays gripped inside it.", "held": "box", "holder": "hoist",
"kind": "inside", "at": [0.7, 1.0]}]` (the box rises during the change, 15-70 % of the cut; from 0.7 it is in the hoist).
</CRITICAL>

| Kind | Means | Keeps |
|---|---|---|
| `carried` | held, gripped, carried, hanging from it | apart, drift, gone |
| `inside` | in a box, a slot, a hoist's jaws | outside, drift, gone |
| `rides` | rides or rests on it, may move along it | apart, gone |

A new kind of contact is a row (`means`, `example`, `keeps`: which of apart, outside, drift, gone it holds), no code.
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
2. `studio2 video check --cut C [--video <fingerprint>]`: three frames of the video against the approved pictures and the
   line (motion matches the line, the middle near the approved middle, nothing morphs, it ends near the last; not
   calibrated: advice). The reader is told our marks are drawn later and which approved pictures are preview frames, so a
   video is never marked down for lacking our labels. Then the shape, contact and time checks below, against what the
   clip was asked (`asked` in its video.json: the cut's camera, `video.holds`, the prompt and the parts, kept at make -
   the plan may change after the clip). `--video` checks a clip made for an earlier request of the cut (shots/<cut>/video/
   <fingerprint>); without it, only the current approved request's clip. A clip made before `asked` was kept gets it once
   from its own `request/request.json`: the prompt, the camera read back from those words by the grammar's terms (none
   or two named: unknown, its camera checks unmeasured), no holds, and the part ids located on it (`asked.from` says so).
   Its parts are located on the clip's frames (every second and both ends; the composite's readings of the same frames
   are used as they are).

<CRITICAL>
**A floor refuses the pass; advice goes to the person with the clip.** `video review` (passing it) refuses a clip not
checked (`VIDEO_UNCHECKED`) or failing a floor (`VIDEO_CHECK_FLOOR`); turning it down (`--reject`) is always open. Which
check is a floor, and each limit, is `library/video_checks.json`, set by `studio2 video calibrate` on clips a person
labelled by watching them: a floor only where every labelled good and bad were told apart on every run. Why: fab's first
paid clips (2026-10-27) came back with 25 wafers merged into one cylinder (c03), a box hanging below the hoist meant to
clamp it with the hand vanishing (c01), a push drifting back out (c03) and no trip at all (c04), and the three-frame read
called c03's shape kept. Instead of passing a clip whose advice you did not look at, show the person each finding with
the clip and the frame it names; never edit a limit or a floor by hand (#6) - re-calibrate with more labelled clips.
</CRITICAL>

| Check | Code | How | What it means |
|---|---|---|---|
| contact_apart | `VIDEO_CONTACT_APART` | measured on the located boxes: the gap between held and holder over the held thing's size | it comes off what holds it or what it rides |
| contact_outside | `VIDEO_CONTACT_OUTSIDE` | measured: the share of the held box outside the holder's box | it hangs below or beside what it should be in |
| contact_drift | `VIDEO_CONTACT_DRIFT` | measured: how far the held thing moved against its holder since the hold began, over the holder's size | it slides off, slips out, swings away |
| contact_gone | `VIDEO_CONTACT_GONE` | measured: the share of the hold's frames where one of the pair is shown and the other is not (both leaving the frame is no loss) | one of the pair vanishes |
| shape_detail | `VIDEO_SHAPE_BLURRED` | measured: each part's crop, its edge strength against its crop on the clip's own first frame | it blurs, merges, smears, melts |
| shape_count | `VIDEO_SHAPE_COUNT` | read, narrow: how many separate items in each tile of the part's crop strip, three wordings, the median per tile against the first tile | many thin things fuse into one, one splits |
| shape_likeness | `VIDEO_SHAPE_UNLIKE` | measured: the correlation of each part's crop with its crop on the first frame | it turns into something else |
| shape_kept | `VIDEO_SHAPE_LOST` | read: 6 frames across the clip + the approved first picture, the prompt, the holds | something melts, smears, merges, splits, changes count or identity |
| flicker | `VIDEO_FLICKER` | measured: a frame's brightness out of line with its neighbours | lights pulse frame to frame |
| jump | `VIDEO_JUMP` | measured: a frame change many times the changes around it | a dropped stretch, a pop |
| repeat | `VIDEO_REPEAT` | measured: still frame pairs between moving ones | duplicated frames, stutter |
| camera_reverse | `VIDEO_CAMERA_REVERSES` | measured: the camera's zoom/shift path against the first frame; the share it came back | a push that drifts back out |
| motion_low / motion_high | `VIDEO_MOTION_LOW/HIGH` | measured: the path's growth over `camera.amount` (push/pull only) | the asked move is not made / far overdone |
| camera_held | `VIDEO_CAMERA_MOVES` | measured: the most growth or shift of a camera the plan holds | a locked-off camera drifts |

Calibrated 2026-10-27 on 23 labelled clips (8 earlier paid takes, 4 video-to-video takes, 11 made bad from good ones),
2 runs. Floors: flicker, repeat, camera_reverse, motion_low, motion_high, camera_held - each told every labelled good
from every bad. Advice: jump (a near-stop then a small move reads as a pop: otis 4.5 good against a 4-frame drop 3.8 bad),
shape_kept (read fab c03 as kept on both runs where the person said lost). Thin: flicker's gap is 0.06 levels, and
motion_low/high and camera_held stand on 1-2 bad clips each.

The part checks were calibrated the same day on 21 clips with their parts and pairs (real fab c01-c04 and rocket c2, the
time edits of c02, six breaks made from good clips: a vehicle falling off its rail, fading out, wafers sliding out of
their box, fusing, a vehicle smearing, a booster blurring), 2 runs, parts located afresh each run:

| Check | Floor | Limit | Goods (worst) | Bads (closest) | Bad clips |
|---|---|---|---|---|---|
| contact_apart | yes | above 0.446 | 0.0 | 0.892 | 1 (falloff) |
| contact_outside | yes | above 0.290 | 0.0 | 0.580 | 2 (fab c01, slide) |
| contact_drift | yes | above 0.401 | 0.157 | 0.646 | 1 (slide) |
| contact_gone | yes | above 0.2 | 0.0 | 0.4 | 1 (vanish) |
| shape_detail | yes | below 0.601 | 0.860 | 0.468 | 5 (fab c04, blurmorph, merge, smear, melt) |
| shape_count | yes | below 0.283 | 1.0 | 0.067 | 1 (merge) |
| shape_likeness | no | - | 0.164 | 0.727 | 4: a moving part's crop takes in a changing background |

contact_kept (the reader) was replaced: 5 misses over 2 runs (c02 floating twice, a time edit of c02 once, the fused
wafers "detached" twice) against 0 for the four measures. fab c03 is labelled `shape_held` good: its frames keep the
first frame's discs (the count reader read 15 on every tile, both runs); its one-cylinder look is the approved
picture's, which a video check cannot see - that is the picture's to answer, before the sheet. Thin: apart, drift, gone
and count stand on one made bad clip each - re-calibrate with each newly labelled clip (`studio2 video calibrate --set
<labelled set> --out <dir> [--checks <ids>] --write`; the sets' items are in the table: `calibrated_on`,
`calibrated_on_parts`).

The planned direction is the camera row's `measured` in the cutaway grammar (`zoom` +1 push/travel, -1 pull, 0 still;
`amount: true` where camera.amount is the picture's growth): a new move is a row with its own `measured`, and a row
without it is reported `unmeasured`, never guessed. Each check's numbers are in `check.json` (`measured`, `verdicts`,
`findings`, `unmeasured`). A contact is unmeasured when the plan names no pair; a part's shape when it is shown on fewer
than two located frames. What they cannot see: a contact or shape change the located frames (one a second) fall between;
a pair whose boxes cannot tell them apart (a long diagonal rail's box holds most of the frame: a vehicle falling within
it is not apart); a flaw already in the approved first picture (c03's merged-looking stack); a wrong motion
of the subject (the box goes the wrong way) when the camera is right; a scene's own fast motion read as a camera move
(blur and lens bend can look like a zoom); judder in clips retimed by duplication (`repeat` measures it, the label was not
seen). Still look at the clip yourself before showing it.
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
