# Story first, options drawn, three moments of the user's time

`studio/story_core.py` (rules, draws), `studio/story.py` (story.json, directions, sheet, pick), `studio/taste_picks.py`
(picks as taste), `studio/reel_flow.py` (pictures shot to shot), `studio/reel.py` (clay reel, redo).

Why (2026-10-17): the rocket reel's 25 built shots passed every check and the user called the clay reel undeliverable -
20 shots of one upright cylinder mid-frame, no sea or ship, a camera that never rested, shots of 3.95 s at the median.
The execution was exact; the plan was flat: one story, one picture per moment, each the safe first idea. Published
studies of writing with models agree: a model's outputs converge (each piece better, all pieces alike); a model
judging novelty does not agree with expert judges; options drawn in code and handed over as conditions, a
prose draft before the format, and an explicit tension structure keep stories apart and alive. And the user: "기획단계에서
너무 시간 안쓰고싶어" - three moments of their time, not fifteen sheets.

## The user's three moments
| Moment | What they see | What they say | Command |
|---|---|---|---|
| 1. Request | - | the topic in a line | launcher |
| 2. Story | `story/directions/rNN/sheet.html`: 3 cards (opening picture, logline, beats, tension curve) | a number and a line of why; or a mix | `story pick --slot k --user-words "…"` (`--body` for a mix) |
| 3. Clay reel | `review/clay/index.html`: the reel and every shot captioned, the reel flow warnings | shot numbers and a word each | `reel redo start --shot sNN --user-words "…"` → `reel redo add` → `reel redo build` → `reel redo pick` |

Between them everything is the agent's (a delegated run, `decide note` for each choice) or, on the ladder, one sheet
per layer as before. Ask nothing else.

## The story (a layer: brief → facts → story → script)
Body: `logline`, `direction {hook, arc, angle, tone}` (the draw), `beats [{story_beat_id, function, what_happens,
why_watch, tension 0-1, picture_idea, length_share}]`. Functions: hook, question, setup, explain, setback, turn,
climax, payoff, close. Approval (or a pick off the ladder) writes `story.json`.

| Rule (`STORY_RULES`) | Value | Why | Code |
|---|---|---|---|
| the first beat is a hook | - | a reason to keep watching before any explaining | STORY_HOOK_FIRST (error) |
| the hook is short | 3.0 s | short-video data: the core of the best performers in the first 3 s; the reference opens on 2.6 s shots | STORY_HOOK_LONG (error) |
| something turns | turn or climax | nothing changes, nothing peaks | STORY_NO_TURN (error) |
| shares make one reel | ±0.05 | | STORY_SHARES (error) |
| the climax sits late | 0.55-0.9 | the reference's consequences at 0.6-0.8; films peak late before a short ending | STORY_CLIMAX_PLACE |
| tension moves | span ≥ 0.3 | one level all through reads flat | STORY_TENSION_FLAT |
| no lecture | ≤ 2 explaining beats in a row | the reference repeats a role 21 % of the time, the flat reel 44 % | STORY_EXPLAIN_RUN |

The script's lines name their beat (`story_beat_id`) in the story's order (STORY_LINE_UNPLACED, STORY_BEAT_UNKNOWN,
STORY_ORDER; STORY_BEAT_UNSAID, STORY_BEAT_CROWDED warn); shots carry their beats (`story_beat_ids`, from their lines;
a wordless shot names its own) and follow them (SHOTS_OUT_OF_ORDER, STORY_BEAT_MISMATCH). A ladder adopted before the
story shows it `not_in_ladder` and goes on; a project with shots and no story.json is STORY_MISSING (a warning).

## Directions: the code draws, the agent writes, the user picks
`story directions` draws 3 on `library/story_axes.json` (hook × arc × angle × tone): slot 1 nearest the user's story
picks, slot 2 as far from it as the axes go, slot 3 on values no direction used before - and never reads the taste.
Each slot's brief: the request, the facts, the drawn values with their meanings, the user's taste (slot 1 only), then
"write 4-6 free sentences first, only then the body", the shape rules. `story add` refuses a body not written to its
draw (STORY_DIRECTION_DIFFERS) and paints the opening picture from the hook's `picture_idea` (`--paint`, the image
tool; STORY_PICTURE_MISSING when it fails). `story sheet` warns STORIES_ONE_IDEA when two open the same way with the
same run of beats.
Bad: three drafts that open on the subject and explain in order. Good: the crash that came before (failure, tense);
a question over the sea (question, calm); a countdown from the booster's view (countdown, object_pov).
No direction fits: 1) their mix (`story pick --body`); 2) a new round (`story directions` - the draw moves away from
every direction shown); never a fourth draft of the same kind.

## Pictures as variants
`concept brief --variants 3` draws each moment's pictures on `concept.CONCEPT_AXES` (size wide/medium/close, view
high/eye/low, place with_env/isolated) the same way; how the moment shows its parts stays the shot list's. A variant
(`concept add --variant size=..,view=..,place=..`) is a candidate until picked; the pick makes it the moment's frame
picture. CONCEPTS_ONE_IDEA: pictures of a moment with one size and view. Pattern examples (`pattern show`) show a
different few per project, from the edges of the range too, each led by its structure - read for size and rhythm,
not copied as a scene.

## Taste
Every user pick between options (story, concept, take, redo) is kept in the local taste file as the chosen option's
axis values against those passed over, with their words (`pattern taste picks`). `pattern taste summary` turns them
into at most five lines (counts, leanings, passings-over, their latest words, the shot taste entries) - assembled by
code - that the launcher and slot 1 carry as "User taste:". The explore slot never follows it: a taste followed
everywhere makes every video one kind.

## The reel's pictures (`reel_flow.REEL_FLOW`, the reference reels measured on this machine)
| Check | Warns when | Reference |
|---|---|---|
| ESTABLISH_MISSING | a shot after a `new_place` hand-over is neither wide (focus ≤ 0.5 of the frame tall) nor shows its surroundings (≥ 0.15) | re-establishing shots |
| ESTABLISH_GAP | over 40 % of the reel passes with no wide shot | wide shots return through a reel |
| SAME_PICTURE | neighbours' focus boxes overlap ≥ 0.7 from angles under 30° apart (not `continue`/`match_action`) | the 30-degree rule |
| OVERFILL_SHARE | over 25 % of shots fill the frame with their focus | 16 % (the flat reel 32 %) |
| STATIC_SHARE_LOW | under 20 % of shots hold the camera still | 33-53 % |
| PACE_SLOW | median shot over 3.0 s, or the climax shots slower than the median | median 1.8 s, p75 3.0 s |
| CLOSE_RUN | over 4 close shots (focus > 0.66) in a row | re-establish after close-ups |
| ROLE_RUN | over 2 shots of one role in a row | 21 % repeats |

Read on the shot list (planned) and on the builds (`reel flow`, the storyboard brief, the clay page). A shot that
departs on purpose records it in its pattern departures (`scale_step` for ESTABLISH_MISSING, `height_share` for
SAME_PICTURE and CLOSE_RUN). Warnings, never a stop.

## Clay reel and redo
`reel clay`: every built shot's Workbench clip (storyboard's layout animatic, seconds a shot), its number and lines
drawn on it, cut into `review/clay/reel_clay.mp4`, one page with the flow. `reel redo start --shot --user-words` draws
2-4 options of that shot (variants away from what it was drawn as, their words in every prompt); `reel redo add
--option k --frame start|end (--image | --paint)`; `reel redo build` solves the camera to each option's pictures
(frames measure, frames apply), puts the shot back and builds them side by side as storyboard takes; `reel redo pick
--option k --user-words` makes it the shot and its pictures the moments', recorded as taste.
