# Camera journey — where the camera goes, when it arrives, what can be followed

Read when: writing or revising the shot list (journey, each cut's part, settle_at, recipe - after its point and each
cut's show_how, SKILL's decision chain), a fly-in through a building,
or a refused pick / finding coded `JOURNEY_*`, `KEY_BEFORE_SETTLE`, `KEY_CHANGE_MOVING`, `DEAD_TAIL`, `CAMERA_JERK`,
`CAMERA_PASS_THROUGH`, `JOIN_REVERSED`, `HANDOVER_BROKEN`. You direct (the journey, the roles, why); the engine holds the
floors. Why (2026-10-25, the user, after a stadium fly-in: "어떻게 하면 그런 연출을 하고, 그 연출을 구현할수 있을지"):
each cut's camera was chosen alone, the key change came while the camera still moved, and a fast fly-in was blocked
for its speed while harsh lurches passed.

## Order of decisions (in the shot list)

0. **What it proves comes first** (`point`, each cut's `direction.show_how`; SKILL's decision chain,
   `references/showing_ways.md`). The journey is where the rows that prove each cut's point are best seen, at the scale
   they are seen at - it follows from what the reel explains the inside of, never the other way round. A reel that
   explains one box stays at the box; a reel whose point is a size the viewer cannot picture crosses scales
   (`dive_scale`); a reel that follows a cause goes where the cause goes (`follow`). A flight is a means of those two
   ways, never a look chosen for itself (`MOVE_NOT_OF_WAY` when a cut's recipe is not one its way delivers;
   `INTENT_MISMATCH` when a travel cut arrives at neither its focus, a part it shows nor what its lines speak of).
1. **Journey** (`journey: [{place, scale, why, passable?}]`): the places the camera goes through in order - a world
   element (or a place's name for a cut that only shows or rests there), its scale (`outside`, `building`, `room`,
   `object`, `inside`) and why. One place with why when the story stays there; nothing forces the camera to fly.
2. **Each cut's part** (`direction.journey: {from, to, role, why}`): place numbers it goes from and to (the same twice
   to stay) and its role - `travel` (goes through places), `show` (shows what is at a place), `rest` (lets the reel
   breathe).
3. **When the camera is still** (`direction.move.settle_at`, 0-1): the camera is on its framing from then on (eased in,
   eased out, still after). The key change starts at or after it (`KEY_BEFORE_SETTLE` is an error).
4. **The recipe** (`direction.move.recipe`): for a travel cut into a building, `fly_in` (below); otherwise the feel the
   scene needs (`library/camera_recipes.json`).

| Code | When | Severity |
|---|---|---|
| `JOURNEY_UNSAID` | no journey, or a cut without `direction.journey` | error (shot list stays a draft) |
| `JOURNEY_PLACE_UNKNOWN` | a cut names a place number the journey lacks | error |
| `JOURNEY_PLACE_UNBUILT` | a travel cut passes a place the 3D does not build (a world element with a blockout, scene ids or a Blender shape) | error |
| `JOURNEY_JUMP` | a cut starts past the next place from where the cut before ended, without `handover: new_place` | flow finding: fix or `pattern.departs {item: journey_jump, why}` |
| `ESTABLISH_MISSING` | a cut starts at a place the cut before did not end at, and does not open wide | flow finding |
| `KEY_BEFORE_SETTLE` | the key change (a key show row, a change on its parts) starts before `settle_at` | error |

## Flying in (recipe `fly_in`, move `fly_to`)

- The camera comes down one line of sight from above the cut's first journey place to its subject (`frame.focus`),
  framed at `height_share`, from `azimuth_deg` and `elevation_deg` (both yours; a level line never passes a roof).
  The destination is ahead the whole way and shows through each opening.
- **Only declared slabs open.** Name the slabs it may pass in the journey place's `passable` (scene ids: the hall's roof,
  the bay's ceiling filters). Where the line crosses one, the engine cuts an opening that grows from the move's start and
  is fully open a quarter second before the camera arrives (`studio/blender_ops/passage.py`, a reveal bound to the
  camera's own cue). Any other crossing stops the build: `CAMERA_PASS_THROUGH` names the slab. Never switch the
  pass-through guard off; declare the slab where the journey truly goes through it, or fly another way.
- Timing is `settle_at` with `cruise_share` (0.2-0.6): eased in, an even cruise, eased out - eased in the subject's size
  on screen (log distance), so the last metres do not balloon.
- Give it time: in the fly-in smoke a 2.8 s way down from above a roof to a 0.4 m box read jerk 1.40 (under 1.8); the
  same way crammed into 1.8 s read 2.45 (`CAMERA_JERK`, no reason passes).

Bad: `{recipe: dig_in, key_at: 0.5}` on the opening cut while the box lid lifts at 0.4 - the camera creeps, then rushes
at the box as the lid moves (a lurch, and the lift reads as the camera's).
Bad (2026-10-26): an opening cut flies from outside a building through its roof onto the object, its `move.why` "the
reel's journey in one flight", while its beat's picture is a close moment at a hand - the flight is the reel's tour, not
this cut's proof (the plan reader called it decoration 4 of 4).
Good - the journey follows from the point, three reels, three answers:
- point "a pollen grain is far smaller than the bee that carries it" (`dive_scale`): journey `[field (outdoor), flower
  (object), bee's leg (inside), grain (inside)]`, each a place built at its own scale and joined by `scale_through`; the
  cuts push in place by place and the last holds while the grain drops (`hold_change`).
- point "the water, not a pump, lifts the boat" (`cut_open`): journey `[lock (object)]` with why "the story happens inside
  one chamber"; a cut holds on the lock's opened side while it fills.
- point "the city's tap water comes all the way from the river through one tunnel" (`follow`, the way along is the proof):
  journey `[intake (outdoor, passable [intake/grate]), tunnel (inside), tap (object)]`; s01 about 4 s,
  `{from: 0, to: 2, role: travel}`, `fly_in` with `settle_at: 0.65` (lands at 2.6 s, inside the opening's 3 s;
  `HOOK_WEAK` sizes it where it lands), `cruise_share: 0.4`, `elevation_deg: 55`, the tap's water starting at 0.7-0.95 -
  the camera arrives, then the water runs. This one is good only where the cut's own point is the path; its show_how
  says so.

## What is measured on every take (camera_dynamics.json)

| Measure | Value | Floor |
|---|---|---|
| `follow.jerk`: largest change of 0.25 s screen motion between neighbouring windows, per s | at or past 1.8 | `CAMERA_JERK` - hard: no reason passes (`SHOT_FLOOR`; only the user's words) |
| `comfort`: fastest 0.5 s scale / travel / turn | past 0.6 / 0.4 / 3 deg | `CAMERA_TOO_FAST` - a reason (`--because`), read with the picture |
| `key_moving`: camera moving a window or more of the key change | - | `KEY_CHANGE_MOVING` - a reason |
| `dead_tail`: still and silent after the last change, words and camera motion | over 1.7 s | `DEAD_TAIL` - a reason, or `direction.rest` in the shot list |

A long cut its words and changes fill end to end is not slow: it leaves `PACE_SLOW`'s median, and `SHOT_HOLDS_UNSPOKEN`
counts only what neither words nor a change fill.

## Joining cuts

- A cut the one before hands over on (`continue`, `match_action`) may leave its `azimuth_deg` unsaid: it starts looking
  the way that cut's planned move ends (said as `carried`). Say another azimuth when the scene needs it.
- Built, a join that snaps back (`JOIN_REVERSED`) or breaks a planned continue (`HANDOVER_BROKEN`) is a flow finding:
  fix it (carry the direction, settle before the cut, one camera with `camera.move.then`) or depart with why
  (`screen.pattern_departs` / `reel depart`), read with the picture.

## The watchers

`harsh` is motion an eye cannot follow - a lurch (a burst from still, a lunge from the whole thing to one detail between
neighbouring frames, a dead stop, a shake, a swing) or no clear destination; a destination in view does not excuse a
lurch, and a fast, even flight toward a place in view that eases in and stops is not harsh. `lost`: a viewer cannot
tell where the camera is or what to look at. Both are findings (reel answer takes them). Read 4 times on 10 fly-through
segments and 15 camera-harsh clay takes: fly-throughs passed 0.975, harsh caught 1.0 (the old question: 0.925 / 0.78).
