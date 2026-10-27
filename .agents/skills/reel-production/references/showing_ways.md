# Showing ways — how a cut shows what its viewer must learn

Read when: writing a shot list's `point` and each cut's `direction.show_how` (before its journey and motion), choosing a
cut's way, or a finding coded `POINT_*`, `SHOW_HOW_UNDECIDED`, `WAY_UNKNOWN`, `MOVE_NOT_OF_WAY`, `PICTURE_DROPPED`,
`INTENT_MISMATCH`, `PLAN_*`, `PROOF_NOT_CARRIED`, `OPENING_UNDONE`, `POINT_UNSEEN`, `REEL_POINT_UNSEEN`,
`SHOT_REPLAN_NEEDED`. Why (2026-10-26): a reel's fields were all filled and none followed from the one above - an
opening flight where the beat's picture was a close moment at a hand, a climax whose failure never showed. The ways are a table so you can
choose among them; the table names what each needs so you choose one the engine can make.

## The chain (shot list)

| Step | Field | You decide | Held by |
|---|---|---|---|
| 1 | `point.says` | the one thing the reel proves (from the story's stake) | `POINT_UNDECIDED` (error) |
| 2 | `point.proof {what, rows}` | the scene that proves it, as rows the list makes: `shot/change_id`, `shot/show@<at>`, `shot/frame` | `POINT_UNSHOWN` (flow) |
| 3 | `point.opening {does, why}` | what the first cut does and why | `OPENING_UNDONE` (plan read) |
| 4 | beat `picture_idea` → `show_how.picture` | keep it (`"beat"`) or leave it with why (`{departs}`) | `PICTURE_DROPPED` (flow); the why read with the clay |
| 5 | `show_how.point`, `show_how.proof` | what this cut's viewer knows when it ends; its own rows that show it | `SHOW_HOW_UNDECIDED` (error) |
| 6 | `show_how.way {id, rejected {id, why}}` | a way below, and one you did not pick with why | `WAY_UNKNOWN` (error) |
| 7 | `direction.journey` | where and at what scale the proof is best seen | `INTENT_MISMATCH` (flow) |
| 8 | `direction.move` | the motion that delivers the proof; `why` says how | `MOVE_NOT_OF_WAY`, `INTENT_MISMATCH` (flow); `move.why` read with the clay |

`INTENT_MISMATCH` thresholds: `key_at` within 0.09 of the cut of a `key: true` show row (half the widest gap between the
watcher's frames - both land on one judged frame) or inside a change of the key part; `reveals` shares a word with the
ids or own words of what the cut shows or changes; a travel cut's last place is its focus, a part it shows or what its
lines speak of. Flow findings are fixed or departed: the cut's `pattern.departs {item: <code lower case>, why}`,
`studio reel depart` for the reel - read with the picture before a coloured render.

## The ways (`library/showing_ways.json`)

| id | shows | recipes |
|---|---|---|
| `dive_scale` | a size the viewer could not picture, crossed step by step | dig_in, go_inside, fly_in |
| `cut_open` | the inside of a thing: structure, layers, what sits where | go_inside, dig_in, watch, pass_by, look_up |
| `follow` | a cause and its path | ride_along, pass_by, fly_in, against |
| `ride` | a trip's speed, length or repetition felt from the traveller's seat | ride_along, pass_by, fly_in |
| `side_by_side` | a comparison held where the eye can go between them | watch, pass_by, look_up |
| `hold_change` | a change happening; the camera stays | watch, dig_in, look_up |
| `reveal_turn` | what the viewer assumed is not so | look_up, against, pass_by, dig_in, go_inside |
| `glance` | a short recognition, read at once | watch, pass_by, dig_in |

Read each row's `needs` and `example` before you pick. A way the table lacks is a new row (id, shows, recipes from
`library/camera_recipes.json`, needs, an example in our own words) - nothing else changes. Across scenes of different
real size, `dive_scale` needs a hand-over at the same screen point; where the engine cannot yet make it, choose another
way and say so in `rejected.why`.

## Checks after the plan, in order

1. `studio plan read` (words, a model; cheap): per cut `derived` (place and motion follow from the proof) and
   `stronger` (another place, scale or way proves it better); for the reel `proof_carried` and `opening_done`. Flow
   findings `PLAN_NOT_DERIVED`, `PLAN_STRONGER`, `PLAN_CLAIM_UNSHOWN` (what the words claim happens stood in for by a label, a number or a still map), `PROOF_NOT_CARRIED`, `OPENING_UNDONE` - no floor. A render waits until
   each cut's entry was read as it stands (`PLAN_UNREAD`). Calibrated on an earlier list (4 readings): its opening flight
   read as decoration 4/4, two well-chained cuts derived 4/4, the climax's proof missing its change 4/4.
2. `reel clay` then `reel see` (the picture, the real test), before `reel judge`: a viewer shown each cut without
   words, captions or the plan writes what it sees, what changes and what it learns; a reader of words alone matches
   that with each cut's point. `POINT_UNSEEN` is a floor (calibrated: reference cuts with their own lines 10/12 not
   unseen, crossed with another reel's lines 11/12 unseen, both runs; about 1 cut in 6 whose line a picture hardly
   carries reads unseen) - the cut goes back to its plan: `SHOT_REPLAN_NEEDED` until the list plans its `show_how`,
   `show`, `change` or `journey` anew, then a pick. `REEL_POINT_UNSEEN` is advice (no reference bar). A render waits
   until its cut was seen as it is (`REEL_UNSEEN`).
3. You may send a cut back to its plan yourself: `studio plan reopen --shot S --why "..." --try "..."`.

A point not seen - in order:
  1. what is shown: a change that makes the point, a mark on it, open the thing;
  2. where and at what scale: closer, inside, a thing of known size beside it;
  3. the way;
  last: the motion.
  Forbidden: another camera take on the same plan; a departure that restates the finding; a point rewritten to match
  what the viewer happened to see.

## Examples (in our own words, from the cutaway reference)

- `cut_open`: a toilet seen from above becomes a section of the bowl - the curved water path and the water left in it
  stand on the cut face while the flush arrow runs along it.
- `hold_change`: water rises round a float in a pit with the camera still; the float lifts, a valve shuts - cause and
  effect in one frame.
- `follow`: water leaves a flat through the slab and the camera slides with it down the sloping pipe to the riser.
- `ride`: from a red blood cell, a narrow capillary's walls rush past close; cut, it is swept down the wide aorta among
  other cells - two legs, the way ahead at the same spot of the screen. (v2: view `rider`, camera `travel` along its way;
  v1: `ride_along` with the camera at the traveller.)
- `side_by_side`: one building front framed the same while the years pass over it - only the wear moves.
- `reveal_turn`: the view rises while the street turns see-through and a second road appears under the first.
- `dive_scale`: from a whole street into a round opening and down the pipe it leads to - the depth is felt because the
  street was there a second before.
