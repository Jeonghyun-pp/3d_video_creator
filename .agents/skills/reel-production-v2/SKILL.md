---
name: reel-production-v2
description: >
  Make a short vertical Korean cutaway explainer reel the v2 way (python -m studio2): each cut planned as a cutaway, drawn
  as pictures by the free image tool, moved and marked locally into a free preview, put to the user on an approval sheet,
  then (only with their words) moved by a paid video model.
  Triggers on: "v2 reel", "studio2", "generation-first reel", "approval sheet", "/reel-production-v2", a request for a
  cutaway explainer reel made from generated pictures.
  Do NOT trigger for: Blender scenes, clay builds, camera rigs or hybrid restyles (that is .agents/skills/reel-production,
  v1), nor for editing v1 projects.
---

# Reel production v2

A v2 reel must look like it belongs to the user's reference series cut by cut and as a whole, and a viewer who does not
know the plan must learn each cut's point from its picture. Every picture, opening, scale step, camera and length is
yours to decide from the story and the reference; the checks below are the floor, never the target.

Work from the repository root with `../.venv/bin/python -m studio2 ...` (v1's `python -m studio` for the story layers, the
voice and the edit). Projects keep v1's layout, so v1's readers and edit work on them.

<HARD-GATE>
Paid video: do not run `studio2 video make` for a cut until the user saw the approval sheet (`studio2 sheet build`,
review/sheet/index.html) and their own words were recorded on it (`studio2 sheet approve --user-words "<verbatim>"
[--cuts <cuts they asked to change>]`). The approval binds to exactly that request; a changed picture, prompt or length
needs a new sheet. A run without its user (STUDIO_UNATTENDED) records no user's words at all - stop at the sheet.
No exceptions.
</HARD-GATE>

---

## Non-Negotiable Rules
These apply to every cut. Violations are what made the first dev preview wrong (2026-10-26). Measurements and history:
`references/rationale.md`.

<CRITICAL>
**#1 — Each cut opens its own subject, from its own view**: plan in the cuts layer what the cut opens (`cutaway.opens`,
`what`), the scale step from the cut before and the view - before any picture. Why: the dev preview's planned "sections"
came out as one frontal view of the same bay in 5 of 8 cuts; the viewer saw one picture again and again. Instead: when two
cuts show the same place, change what is opened (a different part cut open) or the scale step (one closer or wider).

Bad `what`: "the cleanroom bay cut open in section, a worker at a tool" (again, for the third cut)
Good `what`: "one load port close: the carrier's side cut away so the 25 wafer edges show, the floor tile below cut in section"

**#2 — Words and marks never live in the pictures**: every picture prompt says no text, no letters, no labels; labels,
flows, specks and outlines are drawn over the pictures by `studio2 motion render` (and over the paid video by
`studio2 video composite`). Why: generated letters are garbled; a mark painted in cannot be moved or named. Instead: name
parts in `cutaway.labels` and draw flows in `cutaway.flow`.

**#3 — A thing that belongs to the scene is painted in the pictures**: a box, a vehicle, a door is `change.element.drawn:
picture` (where its path begins in the first picture, where it ends in the last, an edit); `sprite` only for a thing at most
0.03 of the frame tall (`CUT_ELEMENT_PASTED` refuses more); a speck is a mark. Why: c1's and c8's pasted boxes (0.12, 0.26)
sat on the picture without its light, perspective or what stands in front.

**#4 — The rhythm is the reference's, and lines run across cuts**: read `studio2 cuts rules` before writing cuts; aim at the
reference's cut lengths; when a picture is shorter than its sentence, let the line run on into the next cut (the next cut's
`line_ids` starts with it, `line_at_s` from 0). Why: one cut per line made a 5.3 s median against the reference's 2.4 s.
Instead of stretching a cut to its sentence, cut the sentence's picture into two views.

**#5 — Mark the key change**: the change that is the reel's point carries `change.key: true` (v1 rule 11, read from the cuts:
`KEY_CHANGE_UNPLANNED`); the opening cut changes something within its first 3 s (`HOOK_WEAK`). Why: a reel whose turn has
no marked moment shows its point nowhere close (the bike reel, 2026-10-19: the chain's jump had no cut that showed it), and
an opening where nothing happens loses the viewer. Instead of a wide opening that holds, open on the event itself.

**#6 — Fix the work, never the check** — when a cut fails a check, in order:
  - 1st: redraw it from its plan - change what it opens or its scale step (`cuts write`, then `frames make`);
  - 2nd: make the failing picture again with the reason (`frames make --frame F --reason "..."`, at most 2 tries);
  - 3rd: change the plan of the cut and its neighbours (a different showing way, the line run across two cuts);
  - last: depart with why (`departs` on the cut, `reel depart`) - only for a flow finding meant on purpose;
  - forbidden: a camera, label or timing tweak on pictures that failed a picture or style check (the verdict stays with the
    pictures), editing a check's numbers, writing the user's words.
</CRITICAL>

General rules (the default tier):
- Readers run at most 2 at a time on the machine (`pool.slot`; STUDIO_READERS) - never start more by hand.
- Labels and captions are drawn at the reference's measured letter height: the cuts layer's `label_height` and the style's
  `captions.letter_height` (studio2 project init sets it from the reference's subtitles), shown against the reference's
  middle half (`LABEL_SIZE_OFF`, `CAPTION_SIZE_OFF`).
- Open every page, clip and strip yourself before calling a stage ready for the user.
- Record your own choices (`python -m studio decide note --topic --choice --why`); ask the user only for their words on the
  sheet, the motion review of a paid clip, spending above the approved total, and frozen code.

→ Full rules: `references/index.md` routes to the module of each phase.

---

## Phase 0 — Project and story
### Goal
A v2 project whose brief, facts, story and script are settled.
### Do
1. `studio2 project init --id <id> --brief <brief.json>` (new), or `studio2 project copy-story --id <id> --from <project>` (a
   settled story kept); v1's ladder for brief/facts/story/script when they are new.
2. `studio2 project status` - every story layer decided before cuts.

## Phase 1 — The cuts plan
### Goal
Every cut planned as a cutaway, its rhythm read from the reference, its findings answered - before any picture.
### Do
1. `studio2 cuts rules --project P` (targets) and `studio2 grammar show` (what each value means).
2. Write the cuts body (`references/cuts_plan.md`): point, places, look, `label_height`, each cut's show_how, place, scale,
   cutaway, change (key, element drawn), lines (runs across cuts), length, frames in words.
3. `studio2 cuts lint`, then `studio2 cuts write --agent-note "..."` (delegated) or the ladder with the user's words.
4. `studio2 cuts read` (the plan reader) - answer its findings in the plan.
### Gate
`cuts lint` has no error; every warning is fixed or departed with why (`DIRECTION_UNRESOLVED` stops otherwise).

## Phase 2 — Pictures
### Goal
Each cut's first (and middle, last) picture, checked, picked by you, its marks located and judged.
### Do (in cut order: a later cut takes an earlier cut's first picture as its place reference)
1. `studio2 frames make --cut C` → `studio2 checks run --cut C` → `studio2 frames pick --cut C --frame F --try N --why "..."`.
2. `studio2 locate --cut C` (overlay judged; `--reason` to locate again).
3. After every first picture is picked: `studio2 flow same --project P` (which cuts show one view of one place) - a group is
   answered by #1 before going on.
→ `references/pictures.md`

## Phase 3 — Free preview and its readers
### Do
1. `studio2 motion render --cut C` for each cut; `studio2 motion stills --cut C` and look at the strip.
2. The scratch voice (`python -m studio audio build --shot C`), then `studio2 reel preview`.
3. `studio2 reel see`, `studio2 reel judge`, `studio2 style reel`, `studio2 flow built` - fix what they find (#6).
### Saturation (stop iterating when all hold)
- no cut fails a floor: picture checks, style (when library/style_check.json says floor: true), the reel flow's stop codes;
- reel see reads every cut's point at least partly and the reel's;
- every remaining finding is answered with why.
→ `references/readers.md`

## Phase 4 — The approval sheet (stop here in a run without the user)
1. `studio2 sheet build` → open review/sheet/index.html yourself (pictures, lines, motion, preview clips, costs, total).
2. Show it to the user. Their answer: `studio2 sheet approve --user-words "<verbatim>" [--cuts <the cuts they ask to change>]`
   - every cut they name for change is reopened: redraw it with their words as the reason, rebuild the sheet, show it again.

## Phase 5 — Paid video (approved cuts only)
1. `studio2 video make --cut C` (veo-3.1 image-to-video from the approved first picture; never above the approved price).
2. `studio2 video check --cut C` (advice), `studio2 video composite --cut C` (our marks over it).
3. Show the person the marked clip; `studio2 video review --cut C --user-words "<their words>" [--reject]`. Only a passed
   clip becomes the cut; then v1's edit for the final.
→ `references/paid_video.md`

---

## Quick Reference: thresholds (read from code and the library, never remembered - check with the command)

| Thing | Value | Where it is read |
|---|---|---|
| A sprite's largest size | 0.03 of the frame height | `studio2/cuts.SPRITE_MAX` (`CUT_ELEMENT_PASTED`) |
| Picture tries with a reason | 2 after the first, then a written finding | `studio2/frames.RETRIES` (`TRIES_SPENT`) |
| Locating tries | 2 after the first | `studio2/locate.RETRIES` |
| Something happens / first cut | within 3 s | v1 `reel_flow.HOOK_WINDOW_S` (`HOOK_WEAK`, stop) |
| Median cut | under the reference's long cut (4.6 s, 78 % of its reels) | `studio2 cuts rules` (`PACE_SLOW`) |
| Repeats | 3+ cuts within 4 of one view (read from pictures) | `PICTURE_REPEATS`, `SAME_PICTURE` |
| Label letters | the reference's middle half 0.023-0.045 of the frame height | `LABEL_SIZE_OFF` (advice) |
| Caption letters | the reference's middle half 0.030-0.034 | `CAPTION_SIZE_OFF` (advice) |
| Style score | the floor library/style_check.json holds (none until calibrated) | `studio2 style cut/reel` (`STYLE_OFF`) |
| Readers at once | 2 | `studio/pool.READERS_MOST` |
| Paid video | veo-3.1 i2v, $0.20/s, billed 4/6/8 s | `routing.MODELS`, `fal_client.PRICING` (frozen) |

## Machine checks (run them; read their output, then look)

| # | Check | Severity |
|---|---|---|
| 1 | `cuts lint`: unknown values, labels on parts not shown, scale steps, lines and lengths, CUT_ELEMENT_PASTED, CHANGE_* | ❌ Error |
| 2 | Reel flow on the cuts: HOOK_WEAK, KEY_CHANGE_UNPLANNED/UNSHOWN, DIRECTION_UNRESOLVED, REEL_FLOW_UNMADE | ❌ Error |
| 3 | Picture checks rows marked floor in library/picture_checks.json (`PICTURE_FLOOR` refuses the pick) | ❌ Error |
| 4 | Located marks judged off (`PLACES_OFF` refuses the clip) | ❌ Error |
| 5 | Style floor when calibrated (`STYLE_OFF` refuses the sheet) | ❌ Error |
| 6 | PACE_*, SAME_PICTURE, PICTURE_REPEATS, ESTABLISH_*, PLAN_*, LABEL/CAPTION_SIZE_OFF, uncalibrated checks | ⚠️ Warning: fix or depart with why |

Handling: ❌ → fix by #6 and run again. ⚠️ → fix, or answer with why on the cut (`departs`) or the reel (`reel depart`).
