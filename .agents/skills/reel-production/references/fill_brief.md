# Fill briefs — what fills a shot is decided by its topic, with the user

Code: `studio/fill.py` (propose / revise / approve / show, lint, sheet), `studio/blender_ops/fill_brief.py`
(`declare_levels`, apply, gates), `env_fill_core.level_layout`; tests `tests/test_fill_brief.py`, smoke
`tests/studio/fill_smoke.py`. Read before filling any level, floor, platform or interior a shot shows.

## The three roles
| role | is | made from | checked |
|---|---|---|---|
| `subject` | what the shot explains (columns and their rebar, sprinkler mains, a machine) | verified exemplar or project spec (fidelity) | on screen, not buried |
| `identity` | the fewest cues that say where we are (screen doors, one train) | kit exemplar, ≤ 2 kinds per level | a seen level has subject or identity |
| `ambient` | life (people, a few cars) | density ≤ 4 / 100 m² | never covers the subject |

Every item has `why` (traced to the request, narration, a reference or the user's words) and `source` (agent / user).
Layouts: `along_edge` (edge inner/outer = nearer/farther from the level centre, near/far = the short ends),
`line_across`, `grid`, `cluster`, `density`; `facing` along / face / inward / random; `height_m` for things mounted
above the floor (ceiling mains, hanging signs).

## Flow (HITL)
1. Draft the brief from the request, narration and references: topic first, then per level the subject, the identity
   cues, a little ambient. Put what you left out in `excluded` with the reason.
2. `fill propose --project P --shot S --brief draft.json` → sheet `fill/<shot>/brief.md` (levels, roles, reasons,
   which elements the library has, **missing** ones with the closest exemplars).
3. Show the sheet and ask what to add, remove or change. Record the user's words verbatim:
   `fill revise --user-words "<their words>" --add role:element@level[:layout[:count]] --remove element`, and for any
   other value (count, pitch, position, edge, facing, a level's note or void) `--ops '[{"op": "set", "path":
   "/levels/0/items/1/pitch_m", "value": 12}]'` (same grammar as storyboard `set`; a key the item's layout does not read
   is refused, e.g. `count` on a density fill).
4. `fill approve --user-words "<their words>"` — bound to the brief's hash. Builds may use a proposed brief (warning);
   renders and paid generation refuse `FILL_BRIEF_UNAPPROVED` / `FILL_BRIEF_STALE`.
5. Missing elements are modelled like any exemplar (spec data → fidelity → `subject promote`; examples/kits/*), never
   placed as stand-in boxes.

Authors declare levels only (`fill_brief.declare_levels([{level_id, z, rects, obstacles}])`); a structure whose own
parts the brief supplies (e.g. columns) leaves them out on those levels.

## Gates (fill_report.json, on the baked camera)
- `FILL_LEVEL_EMPTY` — a level the camera sees has no subject or identity copy. Fix: fill it from the topic, or
  declare `void: true` with a `note` (an empty level must be a decision).
- `FILL_SUBJECT_HIDDEN` — nearer identity/ambient copies cover > 40 % of samples of > 30 % of the subject copies.
  Fix: lower the density, move the background (`at`, `edge`, `height_m`), never shrink the subject.
- `FILL_OFF_BRIEF` — a fill host that no brief item explains.

Bad: "it is a station, so fare gates, kiosks, benches" — a catalogue that buries the columns the story is about.
Good: topic "the columns carrying the GTX-A platform": subject `rebar_column` along the atrium edge of B1–B5,
identity `platform_screen_doors` and one `train_car` on the platform level, people at 0.5/100 m²; fare gates
excluded because they hide the columns — then the user's words decide what changes.
