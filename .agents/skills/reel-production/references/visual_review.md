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
