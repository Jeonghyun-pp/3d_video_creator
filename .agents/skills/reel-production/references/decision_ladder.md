# Decision ladder — settle the video with the user before expensive work

`studio/decisions.py`: brief → facts → script → world → shot list → look (per shot: the fill brief, `fill_brief.md`).
A project is on the ladder once its first layer is proposed (`decisions/ladder.json`); from then on builds need the
shot list, renders need the shot list (look/final also the look), paid generation needs the shot list and look,
final voice needs the script, candidates and delivery need facts and script — approved, fresh and not drifted.

## Delegated runs (no ladder)
When the user hands the run over ("decide it all yourself", a test run overnight), their words are recorded as
`project.delegation` - by `project delegate --user-words "…"`, or by the launcher's `--delegate "…"` (claimed by the
project the run creates). Then: open no sheets; decide what is not on SKILL's ask-only list and record each choice with
`decide note --topic --choice --why [--evidence]` (`decisions/agent_log.jsonl`, read by the report and run metrics, never
by a gate). The ladder stays opt-in: proposing a layer later puts the project back on it.

## One layer, every time
1. Draft the body (schema: `schemas/studio-v1/decision.schema.json`) from what the user said and what you found.
2. `decide propose --layer L --body draft.json` → read the sheet (`decisions/sheets/L/rNN/sheet.md`) and show it.
3. Ask what to add, remove or change. Put their words in verbatim: `decide revise --layer L --user-words "…" --ops ops.json`
   (`[{"op": "set|add|remove", "path": "/json/pointer", "value": …}]`; a path outside the schema is refused). The new
   sheet lists "what you asked → what changed": show it.
4. `decide approve --layer L --sheet rNN --user-words "<their words>"` — only the latest sheet, only their words.
5. Next layer. A changed parent makes every layer below it stale: show those sheets again.

## Layers
| Layer | Body | Approval projects into | Lint (errors block approval) |
|---|---|---|---|
| brief | topic, audience, length_s, key_message, subject_mode, place (where it happens, in words: read by `generate backdrop-review --shot`), references, must_include/avoid | project.json brief, output.target_seconds | schema |
| facts | sources, claims (each claim → source_ids) | sources.json | claim without a listed source; a number about a real subject needs 2 sources |
| script | lines (text + claim_ids, or illustrative) | — | unknown claim; an illustrative line with a digit; spoken length vs brief x speech share is a warning (SCRIPT_DENSE) |
| world | kind, place, time_light, inhabitants, beats (pictures of the minute), elements (function, requirements, owner {shape, look}), reference | world.json (shots lit from time_light at build) | every line in a beat; pictures present; CONTEXT_MISSING; WORLD_REFERENCE_MISSING; OWNER_CONFLICT (`world.md`) |
| shotlist | shots (shot_id, purpose, line_ids, duration_s, route_features, role, pattern, elements, beat_ids, direction {camera, action, depth, scale_step, transition, reveal}, line_at_s) | shots and timeline, narration + sentence_claims, `screen.role` and `screen.pattern_departs` | every line in exactly one shot; line_at_s in order inside the shot; duration vs brief is a warning; REVEAL_UNPLANNED, PACING_DENSE |
| look | preset, per_shot | shots' render.look_preset | unknown preset or shot |

## Time to look (2026-10-12)
<CRITICAL>
Words fill about 72 % of the reel, never all of it (`SPEECH_SHARE`, per project `audio.speech_share`): the script is sized
to `brief.length_s x speech share` (`SCRIPT_DENSE` above it), and every shot leaves at least 0.8 s with nothing said - 2.0 s
where it opens something (`PACING_DENSE`). Place the lines inside a shot with `line_at_s` (seconds, one per line): the
voice puts each line there and the gap is silence (`audio.place_segments`; words are never cut - a line asked to start
while the one before speaks is `TIMING_CONFLICT`). Pause where the picture explains: after "속을 열어 보면" while the
section opens, not in the middle of a sentence.
Why: the user, 2026-10-12, on a 60 s reel whose 25 lines took 63 s: "하나의 내용(문장)을 설명할때 컷이 좀더 길지 않나? ...
훅훅 넘어가는 느낌". The old check asked the words to fill the reel; the reference's own pauses cannot be measured (its music
bed never drops), so 0.72 is the user's taste, to be tuned on the rough cut.
</CRITICAL>
Bad: 25 lines in 64 s, each 2.6 s, words end to end; a merged shot whose three lines run on while its section opens.
Good: 88 s for the same 25 lines; s05 `line_at_s: [0, 4.5, 8.0]` - "속을 열어 보면 ..." then the shell opens in silence,
"산화제는 ..." as the camera goes down the pipe, a pause on the pipe, "내려올 연료를 ..." at the fuel layer.

## Inside and across cuts, in the shot list (2026-10-12)
<CRITICAL>
Decide where the camera goes inside, and plan it as a move. Read the script for the lines that say what is inside (tanks,
a pipe, a mechanism, rooms) and give those shots `direction.reveal` (`section_cut`, `xray`, `transparency`, `explode`) and
a camera that enters - "push into the side as the shell opens", "down the pipe to the engines". If the script never says
what is inside and the subject has one worth seeing, propose a line that does (the script is the user's to approve). A shot
list with no reveal while the user's reference opens its subjects is `REVEAL_UNPLANNED`; say why in `pattern.departs`
(item `reveal_method`) when there is truly nothing inside to show.
Why: the user, 2026-10-12, watching the rocket reel: "로켓을 뚫고 들어가서 구조를 보여준다거나 이런 장면이 없어서
심심한 이유인가?" and "2는 llm이 알아서 판단해야지. 전체 내용에 내부 구조 얘기가 없으면 추가해야". The user's
reference opens a section in 39 % of its shots; the rocket's plan said "push into the side" at s05 and every shot was
still built as a move between two pictures, so nothing ever entered.
</CRITICAL>
- A line is not a shot. Neighbouring lines whose plan says "continue" or "match" can be one camera (`camera.move.then`
  carries a move on through further points): one unbroken camera is one shot (`references/camera_rig.md`).
- The brief measures every cut (`카메라 움직임`: 이어짐, 멈췄다 출발, 움직이다 뚝, 방향 꺾임, 반대로 튐); `JOIN_REVERSED`
  and `JOIN_STOPPED` per cut, `JOINS_ROUGH` for the reel against the reference's cuts. The user's reference settles: at
  its cuts both sides are still 33 %, it stops into the cut 25 %, starts from a hold 23 %, and changes a moving camera's
  direction only 13 % (reverse 3 %). The rocket reel's first build changed direction at 21 of 24 cuts (reverse 9).

Bad: 25 lines, 25 shots, each a move between its own two pictures; "push into the side" planned and a slide built.
Good: s05-s07 one camera - push into the side as the section opens, down the centre pipe, settle on the thin fuel layer
(`section_push` + `then`, `direction.reveal: section_cut`); the brief shows 뚫고 들어가기 and the cuts either side 이어짐.

Bad: build the station and render a look frame because the user said "make a fire-system reel".
Good: brief (1 turn) → facts with 3 sourced claims and 2 illustrative lines → script → 5-shot list → their words on each
sheet → then build.

Rounds: after 3 revisions of one layer without approval the sheet warns `DECISION_ROUNDS` — ask a framing question
("what should the viewer remember?") instead of another variation. Unattended runs propose and stop at the first
unapproved layer; they never approve.

Existing projects: `decide adopt` drafts every layer from the current contracts as proposed; approve them in order.

## The rough cut (`cut` layer, 2026-10-09)
After the storyboards and the look, build the rough cut (`edit build --profile rough`) and show it whole.
- Body: `{candidate_id, versions: {shot: scene_version}, note?}`, proposed and approved like any layer.
- Final renders (`render_final`) and paid generation (`generate`) need it.
- A shot rebuilt afterwards drifts it (DECISION_DRIFT): show the new cut.
- Why: the user should see motion and flow before money goes to finals.
