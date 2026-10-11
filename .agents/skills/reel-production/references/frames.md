# Frame pictures — the first and last picture of a shot, and the camera that reproduces them

`studio/frame_targets.py`, `concept add --frame`, `camera.move` type `frame_to_frame`. Why (2026-10-11): the rocket
reel's 25 storyboards held the booster upright in the middle of the frame at eye level; six shots in a row were one
picture. The shot list said "just above the waves, the booster drops into frame from the top" and nothing could read
words. Its s12 "below the falling booster looking up" was built at -2° because the steep solve sank under the sea and
the angle was dropped to pass. The user: "샷별로 첫 장면과 마지막 장면을 그림으로 그리고 그사이를 움직임으로 메꾸고,
카메라 움직임까지 더해서 역동적으로".

## Order (after the shot list is approved, before the storyboard)
1. **Two pictures per shot** with your built-in image tool: the first moment (`--frame start`) and the last
   (`--frame end`); a third only for a moment that must land (`--frame key --at 0.6`: the touchdown). Prompt each from
   the shot's `direction` (camera, action, depth front/middle/back) and attach the world reference picture. A beat
   picture that already shows the moment is reused, not redrawn: `concept add --shot s01 --frame end --from-beat b1/p01`.
2. **Read them**: `frames measure --shot s01` (one Codex call for the new pictures, cached by hash) →
   `concepts/s01/frames_check.png`: the visible box (yellow), the whole subject (orange, may run off the frame), the
   horizon (cyan), and the reading (up/down and how steep, lens). **Look at it.** A misread picture is corrected, not
   accepted: `frames mark --shot s01 --concept c02 --box … --whole … --horizon 0.78 --view-deg -25`.
3. **Write the move**: `frames apply --shot s01 [--follow 0..1] [--path auto|straight|arc] [--pace subject|timing]
   [--must-see id ...]`. The camera moves while the subject moves (the user, 2026-10-11: "대상이 움직임과 동시에
   카메라가 움직이면 동작인 효과를 살림과 동시에 문제 해결도 가능"):
   - `follow` (default 0.5): the share of the subject's travel the camera shares. 0 keeps its place and the subject
     crosses the frame; 1 rides with it; between, both move - the most motion on screen.
   - `path auto` (default): an arc around the subject when the two poses look from different sides (> 5°), else a line.
   - `pace subject` (default): the camera moves as fast as the subject travels - it hurries while the booster falls
     and settles as it slows; a subject that barely moves (< 5 % of its size) leaves it to the move's timing.
   - `must-see`: the screen.details parts the last picture must show at their `min_px` (default: all of them - a shot
     ends on what it explains); `--must-see` with nothing turns it off.
   Style, timing, guards and parts carry over from the old move. List what the camera must stay out of in
   `move.guards.clearance_ids` with `min_clearance_m`: the sea, the deck, **the flame stand-ins**.
4. **Build** (`shot build`): for each picture the view nearest it that works is chosen on the scene as it stands at
   that moment - out of the guarded volumes, at most 30 % of the focus behind something else, every must-see part
   at its size - trying 35 nearby views (azimuth ±12/24/36°, elevation ±10/20°), the lens widening down to 14 mm in
   each. `camera_move_report.json` `frames[]`: the chosen view and its offset from the picture, every rejected view
   with its reason, blocked share, must-see px. Between the pictures the joined path is checked every 10 frames:
   inside a guarded volume stops the build, the focus hidden is `FRAME_PATH_BLOCKED`.
5. **Storyboard** (`storyboard propose`): the sheet adds every picture above the built frame of the same moment, with
   both boxes and what differs in words. Approval binds the pictures' hashes (`DECISION_STALE` when one changes).

## What a reading becomes
| read on the picture | becomes | rule |
|---|---|---|
| whole_box | `height_share`, `centre` (may be outside 0..1) | the whole subject places the camera: a booster cut by the top edge is solved with its centre above the frame |
| subject_box + cut_edges | the check | what the built moment must show: centre within 0.08, height ×0.75-1.33, the same edges cut |
| horizon_v | `view.elevation_deg` | a level horizon at v means the camera is pitched by atan((v - 0.5)·2·tan(half vertical view)); low horizon = looking up |
| view_height + view_deg (no horizon) | `view.elevation_deg` | up = negative (camera below), down = positive |
| lens wide / normal / tele | 24 / 35 / 70 mm | full-frame equivalent |
| side | (said, not used) | a cylinder looks alike from every side: the azimuth stays the shot's unless marked |

## Rules
- **The picture gives composition, never shape.** The booster's legs, fins and proportions come from the Blender model;
  a picture with five legs is still a good picture of where the booster sits. Why: an image model draws plausible
  rockets, not this one (subject_fidelity).
- **Stay near the angle the picture asks for, and say how far you moved.** A steep view under the sea is solved wider
  and closer first (24 → 18 → 14 mm); a view the subject hides from, or that hides what must be seen, turns by the
  smallest step that works, and the report and sheet say so ("24° right of the picture"). When no view within 36°
  works the build stops (`FRAME_UNSOLVED`, each view's reason). Then, in order: raise or move the subject in the scene
  (it is falling - meet it higher), take a must-see part off that moment, last a different picture. Forbidden:
  flattening the angle silently, or dropping a must-see part to make the build pass.
- **Why the search exists** (2026-10-11): s12's last picture put the camera straight under the booster - in the
  flame stand-ins, nothing but orange showed; s15's last picture matched size and place to the number while the
  hydraulic arms it explains hid behind the nozzle. A picture is drawn without the scene; the scene decides.
- **Movement is the dynamism, not off-centre gimmicks.** The study reels keep the subject near the middle across
  (center_x p25-p75 0.50-0.53, all roles); what changes is size through the shot (subject_growth), travel across the
  frame (subject_travel), the camera's speed and looking up (5-14 %). A first and a last picture that differ in size,
  height in frame, angle or what is cut give the move; two pictures of the same framing give a held shot - say so.
- **Section shots keep the flight.** A cut through the booster is drawn on the booster in flight - leaning body, flame
  below, sky behind - and the camera pushes into the cut. Why: "로켓 외부만 보여주면 그냥 영상 하나에 나레이션만 있는
  것처럼"; a section on a grey void reads as a diagram pasted in.
- **Neighbours differ.** The last picture of a shot and the first of the next differ in place, size or angle unless the
  hand-over says match cut (`FRAMING_REPEATED` on `storyboard board`).

Bad: s01 "rushing toward the ship, the booster drops in from the top" built as `macro_push` with `focus {height_share
0.78, centre [0.51, 0.43]}` at elevation -2° - the booster stands centred and only grows; s12 "looking up" at -2°.
Good: s01 start picture: sea at the bottom third, the ship small at centre, a flame entering at the top edge (whole box
above the frame); end picture: the booster half the frame, upper middle, horizon low (camera low looking up) →
`frames apply --anchor world`: the camera rushes low over the water while the booster drops into the frame.

## Checks
| code | when | kind |
|---|---|---|
| `FRAME_TARGET_MISS` | a pictured moment built as another composition (place, size, cut edges, up vs down) | taste |
| `FRAMING_REPEATED` | a cut joins two frames of one composition (centre ≤ 0.05, size ≤ ×1.15, angle ≤ 15°) | taste (board) |
| `FRAME_PATH_BLOCKED` | between the pictures more than 30 % of the focus hides behind something else | taste |
| `FRAME_UNSOLVED` | no view within 36° of a picture works, even at 14 mm (each view's reason listed) | build error |

The things around the subject (the world's set and context elements linked to scene ids - the landing ship) are
read on the picture too; at each pictured moment one more than ×0.6-1.6 its drawn height is a `FRAME_TARGET_MISS`
("the landing ship 0.45 of the frame tall, the picture 0.12"). In order: make the subject smaller at that moment
(the camera stands back), then raise the camera, last redraw the picture.

A miss is read, not silenced: in order, re-read the picture (`frames mark`), then the scene (where the subject is at
that moment), then the move's path or anchor; last, change the picture with the user.
