# The world — the minute seen first, then who makes each element

`studio/world.py`, ladder layer `world` (after the script, before the shot list). Why (2026-10-10): a floor-noise reel
was built as two rooms in a box floating in the dark; its concept was a bare block too, and a generated take of it read
as a dollhouse ("주변에 배경도 없고 박스안에 그려놓은것같고"). Nothing had asked for a place.

## Order
1. **Pictures of the whole minute.** For every beat of the script (a line or a few), make one picture or more with
   your built-in image tool - with its surroundings, the light and the people, as the finished video would show it -
   and add it: `concept add-beat --beat b1 --image … --prompt …`. Free; this is how you find out what the world is.
   Before the world names its beats, `concept brief --beat b1` briefs the settled story's beat (its picture idea, the
   script's lines over it), so the first pictures are drawn from the engine's prompt too.
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
| `far_background` | (allows) shape generative | nothing past the horizon to model; its ground and sky are the shell (4b) |
| none of these | shape blender (clay stand-in), look generative | the user: "구도랑 핵심 대상만 블렌더로 하고 나머지 화이트점토로" |

4b. **The environment in 3D** (2026-10-18; the rocket's 25 clay shots read as a grey floor in a grey void because the sea
   and the sky were handed whole to the model). What decides the composition is the engine's even as a blockout; only
   its look is the model's:
   - `shell {ground: land|sea|floor|none, sky?: day|dusk|night|space|none, ground_z_m?}` on the world (every shot) or
     on `shot.scene.shell` (one shot; wins). The engine builds a ground disc reaching the solved camera's horizon and a
     sky dome (`blender_ops/env_shell.py`, rules and colours in `env_shell_core.py`); the sky follows `time_light`
     when not named. The disc's edge shows where the real horizon shows (`horizon_m`: the true dip under eye level)
     for every camera of the shot, however far across it the camera stands. An author's own ground (a world-space box
     wide, flat and level with `ground_z_m` - one object or its instances together) or a piece tagged
     `studio_env_shell` is kept. The probe reads the sky as background and the ground as what is around the subject.
     In a shot the model paints, the shell is a region (`@background`, `world.shot_plan`); a blender shot keeps the
     clay shell and says so (`ENV_SHELL_CLAY`). The dome holds every camera and the shot camera's far clip is moved
     out to it (`env_shell_report.json`: `clip_end_raised`); the ground drops under any author face at its height by
     the depth precision at which the cameras see those faces (`level_reach` + `ground_clear`; capped: `ENV_SHELL_CLEAR_SHORT`);
     a keyed far clip that keeps the dome inside the camera range says `ENV_SHELL_DOME_SHORT`; a scatter of more than
     `AUTHOR_SCAN_MAX` instances is read only that far (`author_scan_cut` in the report). The ground is a
     real surface: a camera move diving under it is refused like one through any surface. Missing: `SHELL_MISSING`.
     Under a look that lights the scene itself (the shot's or the style's preset, or the style's light rig) the shell
     is held (`ENV_SHELL_HELD`: needs a frozen scene_roles row and the user's words); above 20 km the flat ground is
     left out (`ENV_SHELL_GROUND_OUT`). `world.color` (what Workbench draws) is set; lit renders keep their world.
   - One or two **set/context elements that place the composition** (a ship, a pad, a building) get `blockout {kind:
     box|cylinder, size_m: [x, y, z]}` from the facts; a shot places them with `scene.stand_ins [{element, at,
     rot_deg?}]` - built at that size, ids the element's (copies `<element>.n`), linked without `world link`. An element
     linked to author ids is the author's: a stand-in for it is refused (`STAND_IN_NOT_ALLOWED`), as is one for the
     subject, a Blender look or anything a requirement holds (`world.stand_in_allowed`: a box stands only where a model
     paints over it and nothing holds its shape - submarine 2026-10-21: the boat as a box read as a grey bar). A blockout
     on those is a size to start from (`ELEMENT_NEEDS_BUILD`), not a build; and an author object with a stand-in's id
     stops the build (`STAND_IN_DUPLICATE`). A clay element with neither a blockout nor ids: `BLOCKOUT_SIZE_MISSING`.
   - **Scale:** a shot opening a new place (handover `new_place`, role `establish`) shows something everyone knows the
     size of - a library person (`scene.characters`) or a stand-in whose blockout is `scale_ref: true` - at least
     `world.SCALE_REF_MIN_PX` tall, counting only the part the camera sees (`SCALE_REF_MISSING`; depart with `screen.pattern_departs` item `scale_ref_missing`). It is a taste code: under a
     project policy `all-strict` it stops the build like an error - soften it per project in `policy.gates`.
   - Far mountains, clouds, a city past the horizon, crowds and foreground props stay the model's.
   - **Per cut, build only what it holds in place** (`world.placed_ids`: its focus, its show parts, what its lines speak
     of, and anything the shot points at by id - `world.named_in`); in a cut the maker makes hybrid, the other elements
     it shows that would only be plain clay stand-ins (a generated look, no linked ids, no scale reference, nothing
     exact, labelled, motion-bound or recurring) are `screen.described` (the shot-list sheet says so per cut: `in words:`) - no stand-in in that cut, the background region, the
     element's whole description (where, how much; `clip.DESCRIBED_WORDS`) and the picked picture tell the model what to
     make. Five clay trees come back as five trees; "a dense forest on the hill left of the lock" comes back as a forest.
5. **The sheet** (`decide propose --layer world`): beat pictures, the summary, the element table. A world with a place
   and no context element is refused (`CONTEXT_MISSING`); a continuous one without `reference` too
   (`WORLD_REFERENCE_MISSING`); an owner a requirement forbids (`OWNER_CONFLICT`). Delegated runs: `world write --body
   … --agent-note …` (= `decide write --layer world`: your decision, never an approval; lint errors keep it a draft).
6. **Shot list** names each shot's `elements` (and `beat_ids`). Approval sets each shot's regions and route from the
   owners: no generated look → blender; a Blender shape on screen (clay stand-ins count) → hybrid, the generated looks
   are `screen.generate_only` (a generated far background is `@background`), role explain when a Blender-look element
   shows, else mood; nothing of Blender's → generative.
7. **Build** the subject properly (photos, subject spec, catalog materials, sourced shape: `subject_fidelity.md` 1b) and
   what a requirement or a Blender look holds; every other element a cut holds in place as a
   clay stand-in of the right size and silhouette (library people, kits, massing); what a cut only shows stays words - no materials needed (`MATERIAL_NOT_CATALOG` skips them).
   Link each: `world link --element room --ids room` (the shots' regions follow). A build that shows such an element and
   made nothing of its id is refused (`ELEMENT_UNBUILT`, broken); the subject is judged by its measured
   spec instead (`SUBJECT_UNMEASURED`, never softened); the clay page names each cut's boxes (`점토 상자`).
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
