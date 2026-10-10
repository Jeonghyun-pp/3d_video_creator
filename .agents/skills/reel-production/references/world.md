# The world — the minute seen first, then who makes each element

`studio/world.py`, ladder layer `world` (after the script, before the shot list). Why (2026-10-10): a floor-noise reel
was built as two rooms in a box floating in the dark; its concept was a bare block too, and a generated take of it read
as a dollhouse ("주변에 배경도 없고 박스안에 그려놓은것같고"). Nothing had asked for a place.

## Order
1. **Pictures of the whole minute.** For every beat of the script (a line or a few), make one picture or more with
   your built-in image tool - with its surroundings, the light and the people, as the finished video would show it -
   and add it: `concept add-beat --beat b1 --image … --prompt …`. Free; this is how you find out what the world is.
2. **Kind.** `continuous` (one place the shots share: one world reference picture goes to every generated shot),
   `independent` (each shot its own place), `none` (an explanation with no world - a mechanism, a body section: a stage
   tone and its light only). The user: continuity only when the world continues.
3. **Place, light, people** from those pictures: `place`, `time_light {time_of_day, mood, sun {elevation_deg,
   azimuth_deg}, temperature_k, white_balance_k?}`, `inhabitants`. The light is one value: the build lights every shot
   from it (where the shot sets none) and the prompt's Look sentence reads it.
4. **Elements** by what they do on screen: `subject` (explained), `set` (where it stands), `context` (what tells where
   we are), `agent` (people, animals and their action), `phenomenon`, `light`. Each with `requirements` and two owners:

| requirement | forces | why |
|---|---|---|
| `exact` (a sourced number or count) | shape blender, look blender | generation keeps no edges (11/11 measured) |
| `labelled` (a label or graphic rides on it) | shape blender, look blender | labels need exact anchors |
| `motion_bound` (the narration times its motion) | shape blender | the thump must land on the cue |
| `recurring` in an independent world | shape blender | nothing else keeps it alike |
| `far_background` | (allows) shape generative | nothing to model |
| none of these | shape blender (clay stand-in), look generative | the user: "구도랑 핵심 대상만 블렌더로 하고 나머지 화이트점토로" |

5. **The sheet** (`decide propose --layer world`): beat pictures, the summary, the element table. A world with a place
   and no context element is refused (`CONTEXT_MISSING`); a continuous one without `reference` too
   (`WORLD_REFERENCE_MISSING`); an owner a requirement forbids (`OWNER_CONFLICT`). Delegated runs: `world write --body
   … --agent-note …` (your decision, never an approval).
6. **Shot list** names each shot's `elements` (and `beat_ids`). Approval sets each shot's regions and route from the
   owners: no generated look → blender; a Blender shape on screen (clay stand-ins count) → hybrid, the generated looks
   are `screen.generate_only` (a generated far background is `@background`), role explain when a Blender-look element
   shows, else mood; nothing of Blender's → generative.
7. **Build** the subject properly (catalog materials, sourced shape) and every other element as a clay stand-in of the
   right size and silhouette (library people, kits, massing) - no materials needed (`MATERIAL_NOT_CATALOG` skips them).
   Link each: `world link --element room --ids room` (the shots' regions follow).
8. **Light:** `look match --shot s` grades the render toward the shot's picture (white balance from warmth, key from
   exposure, at most 2 EV); a lamp far from the world light stops the build (`WORLD_LIGHT_CAST`).
9. **Generate:** `generate inputs` sends the clay-fill input (the look render, clay inside the regions) with the shot's
   concept and the world reference - a picture is required (`REFERENCE_REQUIRED`: clay alone comes back as clay).

Bad: "s02: a floor section in a box, concept a bare block, everything Blender, generation restyles the frame" (the
take was a lit dollhouse). Good: "continuous world - a Seoul estate, late afternoon 4500 K; floor layers subject (exact,
labelled: Blender); living room and child set/agent (clay, generated look; the child's jump motion_bound: Blender
shape); estate context (far_background: generated); s02 hybrid, regions room, child, @background".

Fallback when a shot looks wrong — in order: a better beat picture (it is what the model copies) → the stand-in's shape
and size → `look match` → the element's owner (say why). Forbidden: dropping the context to make a check pass; a
generated look on a labelled element; sending clay with no picture.
