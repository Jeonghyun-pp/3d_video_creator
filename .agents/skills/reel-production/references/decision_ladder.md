# Decision ladder — settle the video with the user before expensive work

`studio/decisions.py`: brief → facts → script → shot list → look (per shot: the fill brief, `fill_brief.md`).
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
| script | lines (text + claim_ids, or illustrative) | — | unknown claim; an illustrative line with a digit; length vs brief is a warning |
| shotlist | shots (shot_id, purpose, line_ids, duration_s, route_features, role) | shots and timeline, narration + sentence_claims | every line in exactly one shot; duration vs brief is a warning |
| look | preset, per_shot | shots' render.look_preset | unknown preset or shot |

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
