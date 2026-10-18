# Visual review: is the shot worth watching? (2026-10-09)

Why this exists: floor_noise (2026-10-09) passed every check and still read as a doll's house in a factory - a 3 cm
buffer layer "modelled" and never seen, waves drawn as a few lines, the run stopped with three quarters of its budget
left. Checks say a picture is not broken; this review says whether it does its job. It runs after each appearance
build while the shot's appearance budget remains (`appearance` in the build result), on the hero frame and its concept.

## Who and how
- A separate reviewer subagent, `reasoning_effort: high`, that did not build the shot.
- Input:
  - the shot's picked concept (`concept show`)
  - the hero frame as a lit preview (workbench `preview` with `lit`)
  - `reference_critique` (defaults to the concept at the hero frame)
  - the frame probe report (`DETAIL_NOT_SHOWN`, `FRAME_MODEL_EDGE`, key parts)
- It judges pictures, not code or logs.

## The seven questions
Answer each with pass or fail, one sentence, and the crop that proves it.

1. **Hook:** in its first second, does the shot give a reason to keep watching (motion with a purpose, a reveal, a person)?
2. **One message:** can you say in one sentence what this shot shows, and is that the narration's sentence?
3. **Readable detail:** is every part the narration names readable at phone size (see `screen.details`)?
4. **Depth:** is there a foreground, a middle and a background, rather than one flat plane?
5. **Scale:** is something of known size (a person, a door, a car) in frame where size matters?
6. **A world, not a model:** does the structure carry on past the frame (floors, neighbours, surroundings), or does it end in a void or an HDRI?
7. **Motion with a reason:** does the camera move toward what it explains, then hold while it explains?

## The picture beside its moment (2026-10-12)
<CRITICAL>
For every frame picture of the shot, put the picture beside the built frame of the same moment (the storyboard sheet
shows both) and answer four questions with yes or no and one sentence - then record them:
`frames review --shot S --concept cNN --objects yes|no --extent yes|no --state yes|no --viewpoint yes|no --why "..." --by reviewer`.
1. objects: the same things are on screen (nothing drawn is missing, nothing large is added);
2. extent: they show as far - cut at the same edges, the whole subject where the picture shows it whole;
3. state: they stand the same way (legs folded or deployed, fins open, engines firing, steam);
4. viewpoint: from the same side and height.
Why: s03 (the booster drawn alone, built with the second stage above it running off the top), s04 (legs drawn deployed
on the pad) and s21 (another viewpoint) read "matches" on every number; the user saw it at once. A number never
passes a picture an eye calls different: a "no" is `FRAME_REVIEW_DIFFERS` on the brief, whatever the checks say.
</CRITICAL>
Bad: `{"q": "objects", "pass": true, "why": "FRAME_TARGET_MISS empty"}` - a check read back as an eye.
Good: "extent: no - the white upper stage continues past the top edge; the picture ends at the grid fins, sky above."

## The clip of a take (2026-10-12)
For a camera candidate, watch its clay clip (`storyboard animatic`) and answer pass/fail with one sentence each:
1. the first second gives a reason to keep watching;
2. the camera moves toward what the narration explains (a reason for the motion);
3. it goes into the space (depth changes, things pass at different speeds), not only across the picture;
4. it differs from the shots before and after;
5. it is comfortable - no lurch, no turn faster than the eye can follow.
6. it feels as its recipe says (pass_by: something really passes near the lens; dig_in: it ends on the part).
Beside the dynamics and feel lines, never instead of them: the lines say how much it moves and how, this says whether
that motion works.

## Output (fixed; nothing else)
```
{"shot": "s02", "version": "v0005", "verdicts": [{"q": 1, "pass": true, "why": "...", "crop": "path"}, ...],
 "largest_gap": "one sentence: the single change that would close the most of the gap to the concept",
 "next_value": {"path": "/camera/move/...", "op": "set|add|remove", "value": ...} | null}
```

## Using the verdict
- **Any fail and budget remaining:** apply `largest_gap` (one change per build, `--diagnosis` naming it), rebuild, review again.
- **All pass, or budget spent:** stop the shot, and list the remaining fails and `reference_critique` differences with crops in the report.
- **Never:**
  - loosen a check to pass a review;
  - restate a fail as a pass because time ran out.


## Requests the studio cannot answer itself (studio/outside_model.py, 2026-10-18)
Every picture reading (`screen_grammar.codex_json`: beat pictures, frame pictures, picture3d claims and checks, video review, reference labelling) and every painting (`picture3d.paint_into`: picture3d paint, frames proposals, reel redo, story hook pictures) starts Codex as a child where it can. Inside an agent sandbox (`CODEX_SANDBOX`, network or not) or where the network is denied (`CODEX_SANDBOX_NETWORK_DISABLED=1`, any brokered command) it asks instead: a request in `<project>/asks/<key>/request.json` (`.studio/asks/` for a reference measure outside a project) naming the pictures, the prompt and the schema, and the command stops with `OUTSIDE_MODEL_ASKED` (retryable). A loop asks all its questions first and stops once (video review frames, frames proposals tiles). `STUDIO_ASK=1` asks everywhere, `STUDIO_ASK=0` never (a brokered command does not receive either: it always asks).
- Answer: a fresh subagent given only the request file; `studio ask answer --request … --answer … --model <model id> --by "a fresh subagent"`. Refused: an answer outside the schema, a picture changed since (pictures are compared by their pixels, so a pass rendered again with a new render stamp is the same picture), a request edited to point at another target, a target that is one of the studio's records (project.json, shot.json, decisions/, runs/), a painting that does not open as an image.
- The answer is kept in the request's folder (so a folder a command clears keeps it) and taken when the command runs again with exactly that question (same pictures, prompt, schema); `--force` reads again. A model started here clears an older request for its target. `studio ask pending --project <p>` lists what waits (`stale`: a picture changed - run the command again).
- `READER_MIXED` (warning, on answer): the project's readings come from more than one model (`--model`; Codex's are the labeller model) - a picture may read differently by another (2 of 55 answers differed between models).
- An unattended `picture3d run` records each ask as that moment's error (a claims ask as the shot's) and goes on: answer every pending request, then run it again. Reference labelling with two runs measures agreement - answered by one agent, both runs are that agent's.
