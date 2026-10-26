# Story first, options written wide and judged blind, three moments of the user's time

`studio/story_core.py` (rules), `studio/story_pitch.py` (pitches, tournament), `studio/story.py` (story.json, directions, sheet, pick), `studio/taste_picks.py`
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
| 2. Story | `story/directions/rNN/sheet.html`: 3 cards (opening picture, logline, beats, tension curve) | a number and a line of why; or a mix | `story pick --slot k --user-words "…"` (`--body` for a mix) - never delegated |
| 3. Clay reel | `review/clay/index.html`: the reel and every shot captioned, the reel flow warnings | shot numbers and a word each | `reel redo start --shot sNN --user-words "…"` → `reel redo add` → `reel redo build` → `reel redo pick` |

Between them everything is the agent's (a delegated run, `decide note` for each choice) or, on the ladder, one sheet
per layer as before. Ask nothing else.

## The story (a layer: brief → facts → story → script)
Body: `logline`, `pitch {pitch_id, line, group}` (the judged pitch it grew from; `direction {hook, arc, angle, tone}` on stories drawn before 2026-10-20), `beats [{story_beat_id, function, what_happens,
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
story shows it `not_in_ladder` and goes on; a project with shots and no story.json is STORY_MISSING (a warning, said by
`decide status` and every build).

**The pick is the user's, and it settles whole.** `story pick` refuses: a round whose sheet was not made after its last
written direction (the user picked from what they saw); words that are the delegation's own (a delegated run stops at
the sheet - the story is never delegated); a story approve would refuse (lint, an unapproved parent - checked before
anything is written). On the ladder the propose and the approve run under one hold that puts the old story back if
either fails (`decisions.settle`, logged `story_pick_undone`): no proposed story is left for the script to wait on.
Only when the user hands a test's pick to a stand-in in their own words (2026-10-20, an overnight E2E): `story pick
--proxy "<who picked>" --user-words "<their words allowing it>"` - the story and the round say it was the stand-in's,
and the user's taste learns nothing from it. Never for a reel the user will publish without their own pick.

**A ladder adopted without the story** keeps that shape: `story pick` there refuses unless `--migrate`. Migrating:
1) `story pick --migrate` (the story is approved); 2) `decide revise --layer script` giving every line its
`story_beat_id`, approve; 3) world and shot list then show stale - approve each again (the shot list carries the beats
into the shots). Until then builds wait, so migrate between productions, not in the middle of one.

## The thread and the chain (2026-10-19, `story_core.chain_lint`)
Why: the bike reel read as "부품만 쭉 보여주는" - each shot a new part joined by "and", the order free to change. The
study set (10 reels, 61-153 s, measured beat by beat) told what moves, the clock, a question, a change; the user: "따라가는
것도 좋지만, 좀더 일반화해도 좋지 않을까". The general rule: every beat happens because of the one before (so) or turns
against it (but); one thread the viewer holds runs through them and stays on screen.

| Field | On | Says |
|---|---|---|
| `thread {kind, carrier, carrier_ids}` | the story | what the viewer keeps hold of; `kind` from `library/story_axes.json` thread (a story picks the kind that fits its pitch), with what orders the beats and its on-screen devices |
| `link: so \| but \| and` | every beat after the hook | because of the last / against it (a doubt, a complication, what goes wrong) / merely after it (the next stop) |
| `raises` | a beat (the hook always) | the question the viewer is left with |
| `answers: [beat ids]` | a beat | which earlier questions it closes |

| Rule (`STORY_RULES`) | Value | Study set | Code |
|---|---|---|---|
| a thread is named, of a known kind (the drawn one, on stories drawn on the axes) | - | 4 moving, 4 question, 1 change, 1 time | STORY_THREAD_MISSING / _UNKNOWN / _DIFFERS (error) |
| every beat after the hook says its link | - | - | STORY_LINK_MISSING (error) |
| 'and' is not the story | ≤ 3 in a row, ≤ 0.6 of links | runs 1-4 (median 2.5), share 0.27-0.75 (median 0.43) | STORY_LIST (error) |
| the hook asks, a later beat answers | - | every reel | HOOK_UNANSWERED (error) |
| ... late | first answer ≥ 0.3 | 0.05-0.69 (median 0.45) | HOOK_ANSWERED_EARLY |
| no question dropped | - | - | QUESTION_DANGLING |
| the end comes back to the opening | - | 9 of 10 | STORY_CLOSE_OPEN |
| a doubt now and then | one 'but' per 30 s | 1-5 a reel, one per 19-75 s | STORY_NO_BUT |
| a 'but' is heard | - | - | STORY_LINK_UNSAID (script) |

The link is the writer's claim; the code checks what it can - the order of questions and answers, the run of 'and's.
No word list ("그리고", "그래서") is read: a language's connectives are not the structure. A story approved before
2026-10-19 stands: `story check`, `decide status` and builds say `STORY_CHAIN_UNSET`, and adopting the ladder or approving
the same body again after a parent changed asks nothing new of it (`decisions.settled_unchained`); any change to it - a
revise, a pick from an old round - is a re-plan and writes the fields. A retired axis value (`arc` `journey_through_parts`,
marked `retired`) still reads where it was drawn and is never drawn again.
On screen (rule 14, `THREAD_OFF_SCREEN`): a shot holds the thread when a `show` part or a world element is a carrier
(or a part of one; a shown whole shows its parts); built, when that part has pixels in half the probed frames and is
no more than 0.4 hidden. At most 0.25 of the shots off it and 3 in a row (the study set: 0-0.59, median 0.15; runs
0-9, 7 of 10 at most 3; the bike plan 0.31). Where the thread passes to another thing (the water pushes the air) the
shot says `handover.thread_to: [ids]` and the shots after hold those.
Bad: lever - and pulley - and chain - and cage (four parts, any order). Good: press the lever (does it shift?) - but
nothing shifts (what is missing?) - so look: the pulley moved, but the chain sits - so turn the crank - the chain follows
the pulley up the next sprocket (answers the hook) - back to the lever, now it works.

## The stake and the screen (2026-10-20, `story_core.stake_lint`)
Ten topics planned blind against the study set's reels won 6 of 10 pairs; all four losses were judged "a tour of the
mechanism, nothing at stake", and every winner opened on something happening. The study set's narrations (10, labelled
beat by beat 2026-10-20): 8 of 10 turn on screen at least once, runs of beats that neither ask nor answer go 1-5 (8 of
10 at most 4), 6 of 10 name a stake and all 6 close on it, 8 of 10 end back at the opening. The four references that
beat our plans opened on an event in three cases; the six our plans beat, in one.
- `on_screen {event, kind}` on a beat: what the viewer sees happen - kind contradiction (a belief broken), failure,
  payoff_first (the result first), anomaly (something odd), change (a state turns into another). An event that is a
  question is a line heard, not a thing seen.
- `HOOK_UNSEEN` (error): the hook has no on-screen event. Its question goes in `raises`, over the event.
- `TURN_SPOKEN_ONLY`: every `but` beat turns in words; let one go wrong on screen.
- `STORY_TOUR`: more than `TOUR_RUN_MAX` (4) beats in a row with neither `raises` nor `answers`.
- `stake {who, what_is_at_risk, closed_by}`: whose day, money, safety or time turns on it - the pitch's own,
  never one added to fit a list (2026-10-20: a drawn 'safety' put a food-safety aside into bread). `STAKE_MISSING`; `STAKE_NOT_CLOSED` when `closed_by` is not the last beat.
- `returns_to: <beat id>` on the last beat, the hook: the opening picture again, now understood (`CLOSE_NOT_RETURNING`).
- A story settled before these stands (`STORY_STAKE_UNSET`, story_core.GROUPS: each group of rules knows the stories
  written before it; a changed body answers to every group).
Bad hook: "have you ever wondered how a lift knows where to stop?" (a line). Good: "the lift doors open on a bare shaft
wall - it stopped between floors" (on_screen anomaly; raises: why did it stop there?; stake: personal - the rider late,
trapped; the last beat: the doors open level with the floor, returns_to the hook).

## Blind judging before the user (2026-10-20, `studio/story_judge.py`)
`story judge-pack` pairs each written direction with 3 reference narrations (the user's local study set,
`~/.config/studio/story_refs.json` `{refs: [{ref, narration}]}`, never in the repository): A and B in a drawn order,
nothing saying which is ours. Give `judge/brief.md` and the pair files to 3 judges who know nothing of the stories;
gather `[{judge, pair, winner: A|B, why}]` and `story judged --verdicts <file>`. A pair goes to its judges' majority; a
direction that wins most of its pairs won. One that lost is told `STORY_LOSES` with the judges' reasons, rewritten once
from them (`story add`, the same slot - the record keeps `rewrite.after`) and packed and judged again (only the
unjudged are packed); lost twice, it stays as judged. The sheet shows the winners and folds the rest; the user may
still pick any. Why: a lint reads fields, a judge reads the story as a viewer would - the four losses passed every rule.

## Directions: pitches written wide, judged blind, the user picks (2026-10-20, `studio/story_pitch.py`)
Why: the axis draw gave ten topics the same three directions (myth, result first, question), its "result first" slot
lost to the study set 8 times in 10, and a stake drawn from a list was forced onto bread as a food-safety aside. The
user: "llm의 창의성을 활용할 방법 고민해봐". The code no longer says what kind of story to write; it keeps the agent wide
and the choice blind.
1. `story pitch-brief` opens a round (`story/pitches/pNN/brief.md`): the request, every fact, example lenses
   (`library/pitch_lenses.json` - examples, never slots) and the user's story taste as a reference.
2. `story pitch-lenses --lenses <file>`: 5-8 angles this topic's facts can be seen from (`[{lens_id, question}]`); two
   asking one question are refused (LENS_REPEATED).
3. `story pitch-add --pitches <file>`, one lens per pass, a fresh start each time (a model asked for many at once
   returns one safe answer several times): 2-4 pitches a lens, 15+ in all, each `{pitch_id, lens, opening, question,
   stake}`; an opening that is a question is refused (HOOK_UNSEEN) - open on what is seen.
4. `story pitch-group --groups <file>`: the alike together (`{group_id: {label, pitch_ids}}`), every pitch once, 3+ groups.
5. `story pitch-judge-pack` → 3 judges who know nothing of them → `story pitch-judged --verdicts <file>`: each pitch
   meets 4 of other groups, each pair goes to its judges' majority, a pitch's score is the share it won.
6. `story directions`: the best pitch of each group, the 3 best groups; each slot's brief is its pitch (opening,
   question, stake) with the story rules. Refused before the tournament (STORY_PITCHES_UNJUDGED) - there is no draw.
7. `story add` checks the body names its slot's pitch (`pitch {pitch_id, line, group}`, STORY_PITCH_DIFFERS); then the
   directions are judged against the references (above), the sheet, the user's pick. The pick records the taste from the
   story itself (its hook's on-screen kind, its thread's kind).
Bad pitch: "opening: have you ever wondered how a lift knows where to stop?" (a line). Good: "opening: the lift doors
open on a bare shaft wall - it stopped between floors; question: why did it stop there?; stake: a rider late for work". A story drawn on the axes before (`direction`) still reads and lints.
`story sheet` warns STORIES_ONE_IDEA when two open the same way with the same run of beats. No direction fits: 1) their
mix (`story pick --body`); 2) a new pitch round - never a fourth draft of the same pitch.

## Pictures as variants
`concept brief --variants 3` draws each moment's pictures on `concept.CONCEPT_AXES` (size wide/medium/close, view
high/eye/low, place with_env/isolated) the same way; how the moment shows its parts stays the shot list's. A variant
(`concept add --variant size=..,view=..,place=..`) is a candidate until picked; the pick makes it the moment's frame
picture. Each `--variants` brief is a round (`concepts/<shot>/variants.json`); the pictures added for it are one set.
`concept check` reads the set (the frames reader, cached, nothing of the build changes) and says CONCEPTS_ONE_IDEA
when two read as one size, height and side: redraw one once (`concept add --variant <its axes> --redraw-of cNN`, the
redrawn one leaves the set; `REDRAWS_PER_ROUND` 1), then pick among what is there. The sheet compares what is read
and says CONCEPTS_UNREAD for the rest. A pick is per moment (the pick record's `moments`): of a moment's pictures the
latest decision shows - a picture added, a variant chosen, a moment picked. Pattern examples (`pattern show`) show a
different few per project, from the edges of the range too, each led by its structure - read for size and rhythm,
not copied as a scene.

## Taste
Every user pick between options (story, concept, take, redo) is kept in the local taste file as the chosen option's
axis values against those passed over, with their words (`pattern taste picks`). `pattern taste summary` turns them
into at most five lines (counts, leanings, passings-over, their latest words, the shot taste entries) - assembled by
code - that the launcher and slot 1 carry as "User taste:". The explore slot never follows it: a taste followed
everywhere makes every video one kind.

## The reel's pictures (`reel_flow.REEL_FLOW`; the rhythm from the project's reference, `reel_flow.RHYTHM`)
The rhythm numbers are read from the project's pattern library (`project.json` `pattern_library`; the pooled study set
when none is chosen) - `reel rules` prints each with the item it reads (`all.len_s.p75`, `hook.len_s.p75`, ...). A number
missing or weak in that library silences its rule ("no evidence in the reference"); a new reference needs no code. Where
the reference's reels differ in pace (a style library, one group a reel), no pooled cut length is stable; the rhythm
rules then read what most of its reels keep (`REELS_HOLD_LONG`, `REELS_MEDIAN_UNDER_LONG`: each reel's share of cuts at
the set's long cut, and its median under it - `patterns.REEL_CLAIMS`, tested over reels).
| Check | Warns when | Reference |
|---|---|---|
| ESTABLISH_MISSING | a shot after a `new_place` hand-over is neither wide (focus ≤ 0.5 of the frame tall) nor shows its surroundings (≥ 0.15) | re-establishing shots |
| ESTABLISH_GAP | over 40 % of the reel passes with no wide shot | wide shots return through a reel |
| SAME_PICTURE | the subject's silhouettes across a cut overlap ≥ 0.7 and the camera turned under 30° (not `continue`/`match_action`) | the 30-degree rule |
| OVERFILL_SHARE | over 25 % of shots fill the frame with their focus | 16 % (the flat reel 32 %) |
| STATIC_SHARE_LOW | under 30 % of shots (the climax left out) hold the camera still | 33-53 % |
| HEIGHT_FLIP | the camera's elevation jumps 60° or more across a cut | rule 6 |
| UNDERFILL_NO_SCALE | a focus under 0.2 of the frame tall with little around it | rule 5 |
| SHOT_HOLDS_UNSPOKEN | a cut longer than the reference's long cut holds longer than its median cut with nothing said or changing (an opening too: the voice goes on over it) | rule 7, `LONG_CUT_S` / `MEDIAN_CUT_S` |
| CLOSE_SHORT | the last shot is shorter than the median | the study set ends long (1.37 x) |
| CLIMAX_ONE_SHOT | the climax beat has one shot, where the reference's climax cuts run under its median cut (else silent) | rule 9, `CLIMAX_LEN_S` |
| CLIMAX_NOT_CLOSEST | the climax's focus is under the reel's top quarter of sizes | rule 9, the user's taste |
| PART_OCCLUDED | a shown part over 40 % hidden in front (median over its window; see-through by its opacity) | rule 12, bike reel |
| PART_LOW_CONTRAST | under 66 % of a key part's outline differs (CIE76 ≥ 10) on a coloured render | rule 13, bike reel |
| THREAD_OFF_SCREEN | over 25 % of shots, or over 3 in a row, show none of the story's thread | rule 14, study set 0.15 |
| PACE_SLOW | median cut over the reference's long cut, or the climax cuts slower than the median where the reference's climax cuts short | `LONG_CUT_S` (its p75) |
| PACE_FLAT | under 12.5 % of the cuts reach the reference's long cut (it holds a quarter of its cuts that long): the reel never holds a picture | `LONG_CUT_S` |
| BEATS_CROWD | more cuts than the reel's length over the mean cut of the reference's quicker quarter of reels: join beats, or make the reel longer | `REEL_CUT_S`, `REEL_QUICK_CUT_S` |
| CLOSE_RUN | over 4 close shots (focus > 0.66) in a row | re-establish after close-ups |
| ROLE_RUN | over 2 shots of one role in a row | 21 % repeats |

Read on the shot list (planned) and on the builds (`reel flow`, the storyboard brief, the clay page). Built: a shot's
size and fill from its frame report; SAME_PICTURE compares the subject's silhouette in the probe's id pictures across
the cut (last frame of one, first of the next) and the camera's direction there ([elevation, azimuth], an unknown one
is not the same); a camera rests when it travels under 2 % of its distance to what it looks at, turns (pan or tilt)
under 1° and zooms under 5 %; a shot not built breaks the comparisons beside it. A shot that departs on purpose names
the finding in its pattern departures, in lower case (`same_picture`, `overfill_share`, … - `reel_flow.DEPARTS` says
what each does to that shot only); a choice about the whole reel is `reel depart --code X --why … --user-words …` (a
delegated run: `--agent-note`). Warnings by default; the project's gate policy (`all-strict`, `policy.gates`) makes
them errors on the sheet and in `reel flow`.

## Direction rules (2026-10-19, `studio reel rules`)
The rules read the planned shot list and the built reel (`reel_flow.RULES`). Three stop and are never departed:
`HOOK_WEAK` (something happening in the first 3 s, the first cut by the reference's opening cut - `HOOK_CUT_S`, its
hook shots' p75: about 3 s in the pooled set, longer in the cutaway reference, whose first cuts move from the first frame -
the first shot a hook, built at least the reference's hook size; a travel opening lands within 3 s and is sized where it
lands), `KEY_CHANGE_UNPLANNED` (a story that turns or peaks marks the key change: `show` row `key: true`) and
`KEY_CHANGE_UNSHOWN` (at that moment its parts span at least 0.15 of the frame height and at most half is hidden).
Measured on the bike reel: parts read by eye at 0.18-0.49 across and 0.26-0.33 hidden, unread at 0.03-0.11 across and
0.55-0.62 hidden; outlines apart at 0.73-0.89 (a blue shirt, black pulleys), not at 0.33-0.59 (white on white, silver
on silver). How hidden is read: the probe renders each shown part alone at each probed frame and compares
(`frame_report.json` `occlusion`); contrast is read only on a coloured render of the version (look, review, final).
Every other finding is answered before the shot list is settled and before a look or final render: fixed, or departed
with why (12+ characters). `DIRECTION_UNRESOLVED` names what is open. The blocking rules and the answer apply to a reel
told as a story (the story layer); a project's gate policy (`look-first`, `policy.gates`) can make them warnings.
The shot list sheet ends with the rules (met / open / departed) and every departure's why.
Rule 15 (2026-10-20, `reel_flow.untold_lines`): every cut has a job in the story - it tells a beat (`story_beat_ids`, or
lines of a beat): what the last beat caused, what raises the next question, what makes the stake felt (`CUT_UNTOLD`).
It replaced a mechanism/world split of the cuts and a cap on mechanism seconds: the study set (295 shots, two
labellers agreeing on 0.94) spends 0-0.94 of its seconds on cuts showing how a thing works, and one reel at 0.88 beat
our plan - the share told nothing of quality, and the same thing is the world in one cut and the mechanism in the next.
Openings (`OPENING_SHORT`, `decisions.OPENING_LEN_MIN`): a shot that opens the subject runs at least 0.7 of the reel's
median shot, the voice going on over it - the study set's openings run 0.48-2.4 of their reel's median (p25 0.71, median
1.06) and its reels leave 0-4 % unsaid; no silence is asked (it replaced 1.5 s of silence on every opening, which a 90 %
spoken script could not leave in 9 of 10 planned reels; the user's call of 2026-10-20: decide it by the study set).
A line said across a cut (rule 7): it is in each shot it runs through - the last line of the shot it starts in, the first (`line_at_s` 0) of the next ones (`decisions.span_lines`); the shot it starts in carries its voice and `narration.continues_into`, edit build lays every voice at its shot's start on one track (never two at once) and keeps the caption up over the next picture; a shot with no words of its own is silence. Shot floors and `SHOT_HOLDS_UNSPOKEN` count the seconds the voice speaks over each shot (`decisions.speech_occupancy`).

## Clay reel and redo
`reel clay`: every built shot's Workbench clip (storyboard's layout animatic, seconds a shot), its number and lines
drawn on it, cut into `review/clay/reel_clay.mp4` (every clip fitted into the first one's frame and rate), one page with the flow;
a shot whose clip fails is named on the page (CLAY_SHOT_FAILED) and the others go on; captions run at most three rows,
the last cut with an ellipsis. `reel redo start --shot --user-words` draws
2-4 options of that shot (variants away from what it was drawn as, their words in every prompt); `--fixed size=close`
(`size=medium|close`: one of them) holds the axis values their words settle in every option (2026-10-18: "레버 클로즈업" was drawn as wide and medium
options - the draw moved away from the shot on every axis, the asked one too); `reel redo add
--option k --frame start|end (--image | --paint)`; `reel redo build` solves the camera to each option's pictures
(frames measure, frames apply) under the project lock, puts the shot, its pictures and their readings back and builds them side by side as storyboard takes; `reel redo pick
--option k --user-words` makes it the shot and its pictures the moments', recorded as taste.
