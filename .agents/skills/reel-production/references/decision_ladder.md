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
| script | lines (text + claim_ids, or illustrative) | — | unknown claim; an illustrative line with a digit; spoken length vs brief x speech share is a warning (SCRIPT_DENSE); SCRIPT_CHOPPY |
| world | kind, place, time_light, inhabitants, beats (pictures of the minute), elements (function, requirements, owner {shape, look}), reference | world.json (shots lit from time_light at build) | every line in a beat; pictures present; CONTEXT_MISSING; WORLD_REFERENCE_MISSING; OWNER_CONFLICT (`world.md`) |
| shotlist | shots (shot_id, purpose, line_ids, duration_s, route_features, role, pattern, elements, beat_ids, direction {camera, action, depth, scale_step, transition, reveal}, line_at_s) | shots and timeline, narration + sentence_claims, `screen.role` and `screen.pattern_departs` | every line in exactly one shot; line_at_s in order inside the shot; duration vs brief is a warning; REVEAL_UNPLANNED, PACING_DENSE (reveal shots) |
| look | preset, per_shot | shots' render.look_preset | unknown preset or shot |

## How the words run (2026-10-12)
<CRITICAL>
Say one whole thought per line, and keep talking: the user's reference fills about 90 % of a reel with words (91 %, 87-98 %),
at 7.4 characters a second, in sentences of about 23-26 characters (3-3.5 s each); it hardly ever pauses. Size the script to
`brief.length_s x speech share` (`SCRIPT_DENSE` above it); a script whose median line is under the reference's short
sentences (22 characters as learned; 19 without a profile) is `SCRIPT_CHOPPY` - join a line that ends in a comma to the
next, unfold a bare phrase into a sentence. Pause only where a shot opens something: `direction.reveal` shots leave at
least 1.5 s with nothing said (`PACING_DENSE`), placed with `line_at_s` (seconds, one per line; the voice moves each line
there and the gap is silence, `audio.place_segments`; a line asked to start while the one before speaks is `TIMING_CONFLICT`).
The reference's numbers: `pattern pacing learn` (its reels transcribed on this machine, mlx-whisper, docs/SETUP.md).
Why: the user, 2026-10-12, on a 64 s reel of 25 lines: "하나의 내용(문장)을 설명할때 컷이 좀더 길지 않나? ... 훅훅 넘어가는
느낌", then asked that the reference's sentences be counted and its scenes timed. A first plan guessed the reference paused (72 % words,
silence between lines); counted, it does not - its lines are longer: 23 characters against ours 17.6, so each thought stays
on screen ~3.1 s instead of 2.6 s.
</CRITICAL>
Bad: "1단은 로켓 값의 대부분이라," as a line with its own picture; or 28 % of the reel silent between lines.
Good: "1단은 로켓 값의 대부분이라, 버리지 않고 다시 쓰려면 세워서 내려야 합니다." one picture, ~4 s; words running on across
the cuts; 1.5-2 s of silence while the tank section opens.

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
