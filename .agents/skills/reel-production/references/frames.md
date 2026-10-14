# Frame pictures — the first and last picture of a shot, and the camera that reproduces them

`studio/frame_targets.py`, `concept add --frame`, `camera.move` type `frame_to_frame`. Why (2026-10-11): the rocket
reel's 25 storyboards held the booster upright in the middle of the frame at eye level; six shots in a row were one
picture. The shot list said "just above the waves, the booster drops into frame from the top" and nothing could read
words. Its s12 "below the falling booster looking up" was built at -2° because the steep solve sank under the sea and
the angle was dropped to pass. The user: "샷별로 첫 장면과 마지막 장면을 그림으로 그리고 그사이를 움직임으로 메꾸고,
카메라 움직임까지 더해서 역동적으로".

## Order (after the shot list is approved, before the storyboard)
1. **Two pictures per shot** with your built-in image tool: the first moment (`--frame start`) and the last
   (`--frame end`); a third only for a moment that must land (`--frame key --at 0.6`: the touchdown). Write each prompt
   with `studio concept brief --shot s01 --frame start|end|key [--at]` (2026-10-13): the shot's lines and job, how this
   moment shows its parts - the shot list's `show` (exterior, section, cutaway, interior, explode, xray, transparency) -
   and, for a start picture, the shot before's last picture and hand-over; attach what it lists. A section moment is
   drawn as a section from the start - the camera never turns to find an inside. A beat
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

## How the moment shows (2026-10-13)
- The reading says `how` the picture shows the subject and which parts it shows only because it is `opened`; `frames
  mark --how section --opened booster/engine_00` corrects it. `frames apply` refuses a picture drawn another way than
  its moment's show (`FRAME_SHOW_DIFFERS`): draw it again from the brief, or change the shot list.
- The build opens the subject for that moment before the camera is solved (`show_report.json`): a cutter whose face
  looks at the picture's camera through the middle of the deepest opened part (opening while the camera moves from the
  moment before, shutting before a later outside moment), an explode of what is in front, or a see-through skin over
  it - every layer in front (skin, the tank under it, a sleeve), not only the first. Once the cuts are built each
  opened part is measured from the picture's view: one still more than half hidden stops the build before the camera
  (`SHOW_CUT_MISSES`, broken; `checked` in the report).
- Outside, a part something ENCLOSES (a housing: in front of it and behind it on the same line, or holding its whole
  box) stops the build (`SHOW_NEEDS_OPENING`); opened but still hidden, `SHOW_NOT_OPENING` names what is in front. A part
  behind a sibling or something beside it (a leg, the next engine, a pillar) lets the camera go round it; the report
  and the sheet say `moved_because`. Hidden is judged against what any side reaches of the part (`exposable_share` of
  the subject's exposure survey, when fresh): a bulb whose foot sits in its socket is not hidden from the side. A small
  part is shown by coming closer (`size_scale`, up to 1.33x), one off frame by moving the framing (`place_shift`).
- Before the build: `frames apply` keeps a part off a moment the shot list shows it at another moment; refuses a
  must-see part that even 1.33x closer cannot show at its `min_px` (`MUST_SEE_UNREACHABLE`: its thinnest side from the
  survey's box times the picture's px/m); warns when an outside moment's view hides a part (`SHOW_VIEW_HIDES`: the
  survey's share from the nearest direction, with the nearest view that shows it and the ways to open). The shot-list
  lint warns the same for a shot whose pictures set its views, and when one must-see part encloses another at one
  moment (`MUST_SEE_NESTED`).
- The sheet no longer judges the angle by degrees: it says when the camera moved and why, and `FRAME_VIEW_STALE`
  when a picture was read again after `frames apply` (run it again).

## What a reading becomes
| read on the picture | becomes | rule |
|---|---|---|
| whole_box | `height_share`, `centre` (may be outside 0..1) | the whole subject places the camera: a booster cut by the top edge is solved with its centre above the frame |
| subject_box + cut_edges | the check | what the built moment must show: centre within 0.08, height ×0.75-1.33, the same edges cut |
| horizon_v | `view.elevation_deg` | a level horizon at v means the camera is pitched by atan((v - 0.5)·2·tan(half vertical view)); low horizon = looking up |
| view_height + view_deg (no horizon) | `view.elevation_deg` | up = negative (camera below), down = positive |
| lens wide / normal / tele | 24 / 35 / 70 mm | full-frame equivalent |
| side | (said, not used) | a cylinder looks alike from every side: the azimuth stays the shot's unless marked |
| subject_tilt_deg | `tilt_deg` -> camera roll | the subject's long axis on the picture from vertical (+ top leans right); the camera rolls (up to 30°) so the built frame leans the same; the move's `params.axis` names the long axis (default z); the built lean is the final camera's view of that axis (`built.tilt_deg`), not the pose the move asked for |
| shows | that moment's `must_see` | the parts the picture clearly shows, from screen.details and the spec's features; each must be a screen.details entry (`FRAME_SHOWS_UNDECLARED`) |

## The picture as drawn (2026-10-11)
<CRITICAL>
Apply each picture as it is drawn: its size, place, cut edges, angle, lean and the parts it shows. The camera
reproduces the first and the last picture; the move between them is the only thing left to choose. Why: the user,
after s06's last picture (the pipe reaching three engines, upright) was built as a 15 deg-leaning tank with no engine
in frame and three departures recorded: "그냥 처음 샷이랑 끝 샷을 각각 보고, 그대로 적용한다음 카메라 움직임을 정하면
안되는거야?"
</CRITICAL>

When a moment does not come out as drawn - in order:
  1. the camera: angle, lens, roll (`frames apply` rolls to the picture's lean; `frames mark --tilt-deg` corrects it);
  2. the scene at that moment: where the subject is, its height, its pose (the author script - a booster flying 15
     deg over can be flown upright for the explanatory moment);
  3. redraw the picture for the model and scene as they are (your image tool, the world reference attached).
  Kept different on purpose (`frames depart --why`): at most 2 items per picture, never the viewpoint (angle,
  tilt), never a must-see part; more, or the viewpoint, only with the user's words (`--user-words`,
  `FRAME_DEPART_LIMIT`). A picture past that is `FRAME_PICTURE_ABANDONED` on the build, the board and the brief.
  Forbidden: covering a difference with departures; dropping what the picture shows from must-see.

Bad (s06, 2026-10-11): last picture - the pipe meeting the engines, upright; built - a leaning tank, engines cut,
departures size + place + edges, sheet "matches".
Good: `frames mark --concept c02 --shows booster/lox_tube booster/engine_00 --tilt-deg 0`, screen.details gains
`booster/engine_00 {min_px: 20, why: where the oxidizer arrives}`, `frames apply` -> the camera rolls 15 deg and
frames the pipe down into the engines; the brief's picture column reads 일치.

## The whole frame, and drawing over the model (2026-10-12)
Every thing a picture shows is read (`objects`: the shot's world elements by id, anything else as "other: ...", with its
box, the edges it runs past and its state) and compared with the build thing by thing (`FRAME_LAYOUT_MISS` below 0.7 of
the frame's things as drawn): in both, same place (box overlap 0.5) and the same edges cut; only in the build (0.10 of
the frame tall or more - a second stage nobody drew); only in the picture (a side booster the scene does not have).
Why: s03's booster sat exactly as drawn while the stack above it ran off the top; only the subject was measured.

When a picture shows what the scene cannot be - in order:
  1. the camera (angle, lens, roll), then the scene at that moment (where the subject is, its pose) - the scene is the
     truth where the facts say so (a rocket on the pad carries its second stage; its legs are folded there);
  2. **draw the picture over the model**: give your image tool the built frame of that moment (the storyboard sheet's
     `frame_NNNNNN.png`, or a workbench lit preview at the shot camera) and the original picture; ask it to keep every
     shape, place, edge and the viewpoint of the built frame and to carry the original's light, materials and mood.
     `concept add --frame end --image painted.png --derived-from <that built frame>` then `frames derive --concept cNN`:
     the reading comes from the build itself (exact; no labeller). The original stays on file as the intent
     (`derived_from.supersedes`) and shows on the brief beside it; the user approves the painted one as the target.
  A picture no camera can take (s21: 85 deg down, the fins invisible from there) goes straight to 2 from the nearest
  view that builds. Forbidden: keeping a picture that draws what the scene does not have and calling the frame a match.

Bad: s03 - the first stage alone with fins at the top, built on the pad under its second stage: target "matches",
the user sees a rocket cut at the top.
Good: s03 painted over its v0009 frame: the full stack, cut where the build cuts it, the sunset of the first picture;
layout 1.0, review yes x4.

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
| `FRAME_TARGET_MISS` | a pictured moment built as another composition (place, size, cut edges, up vs down, lean > 8°, a shown part too thin) | taste |
| `FRAMING_REPEATED` | a cut joins two frames of one composition (centre ≤ 0.05, size ≤ ×1.15, angle ≤ 15°) | taste (board) |
| `FRAME_PATH_BLOCKED` | between the pictures more than 30 % of the focus hides behind something else | taste |
| `FRAME_UNSOLVED` | no view within 36° of a picture works, even at 14 mm (each view's reason listed) | build error |
| `FRAME_BELOW_SUPPORT` | every pose the solve reached stands under, or within the margin (a quarter of the subject's height, 0.05-0.5 m) of, the top of what the subject stands on (scene role `support`) - a step that lands there is halved twice first; also a path sample between the pictures | build error |
| `FRAME_UNREACHABLE` | (a lens's reason) the picture's whole box spans where the subject's long axis vanishes from that view, and the picture gives no cut to place instead (vanishing point and whole box in the reason) | lens / view reason |
| `FRAME_SIZE_CYCLE` | (a lens's reason) the size swings between two depths and never reaches the picture's: what is seen of the focus changes there (bracket in the reason) | lens / view reason |
| `SUBJECT_SINKS` | a subject the shot moves goes more than 2 cm under the top of its support on some frame (depth, object, frame in `support_report.json`) | broken |
| `CAMERA_IN_ATMOSPHERE` | a picture's pose inside a fog, smoke or beam volume (scene role `atmosphere`; a cloud of instanced specks counts as the region it fills, a closed mesh by crossing parity, a piece under 1 m across is a speck) or within 1 m of it is refused like one under the support; a volume that also holds the subject is its air (a hall's fog) and allowed; every pose refused for it: the frame fails with this code. Volumes still block no ray | broken (pose) |
| `ATMOSPHERE_COVERS` | at a picture, the final camera sees a volume nearer than the subject over more than 0.5 of the frame, or more than 0.3 of the focus only through one (`built.atmosphere`); also the path between the pictures passing through one | taste |
| `FRAME_READING_STALE` | before the build: the pictures were read for other subject words than the shot's now (words given by hand with `--subject` are kept) - `frames measure`, then `frames apply` | broken |
| `FRAME_READING_PARTIAL` | before the build: things of the shot (around it, or in its frame) were linked after the reading - no box, so their size and side go unjudged; `frames measure` reads again when the store recorded what it asked; a store from before that only says so (`--force` to re-read) | taste |
| `FRAME_TARGET_STALE` | `move.target` / `parts` is another thing than the reading measured as the subject (before the build and at `frames apply`) | broken |
| `FRAME_SUBJECT_MISREAD` | before the build: a picture's subject box is 2× the box the same reading gives the target itself; after it: an uncut box with nothing in front (`fore` null) more than 2× as long for its width as the framed thing can look from any side (`spans_m`) - the reader boxed more than the camera frames | broken |
| `FRAME_SIDE_MISS` | a thing around the subject stands left of it in the picture and right of it in the build (or the other way); a thing spanning the subject's centre has no side | taste |
| `SUBJECT_END_ON` | at a picture with a lean, the final camera sees the subject's long axis within 45° of the line of sight: the lean is not compared (`built.sight_deg`) | taste |
| `MOVING_SUBJECT_OFF` | a subject travelling more than 5 % of its length is built away from where its solved pose frames it: its points, nothing hidden, through the built camera against the pose (`built.box` / `built.posed_box`; centre > 0.08, height outside ×0.75-1.33) | taste |
| `FRAME_LAYOUT_MISS` | the frame's things as drawn below 0.7 (place, edges, added or missing things) | taste |
| `FRAME_REVIEW_DIFFERS` | an eye said no to objects, extent, state or viewpoint (`frames review`) | taste (brief) |
| `FRAME_PICTURE_ABANDONED` | a picture kept different in more than 2 items, or in its viewpoint, without the user's words | taste |
| `FRAME_TILT_UNREACHED` | the picture's lean needs more than 30° of camera roll (an upright subject), or no side of a lying subject shows it (its lean sets the azimuth, `azimuth_from: tilt`): pose the subject nearer it | taste |
| `FRAME_SHOWS_UNDECLARED` | a picture shows a part screen.details does not name | apply error |
| `FRAME_DEPART_LIMIT` | a third departure, or the viewpoint, without the user's words | depart error |

How the solve reaches a picture (A4, 2026-10-14; each frame row says `mode` and `engaged`):
- The picture's cut reaches the solve: `frames apply` writes `cut_edges`, `width_share`, `side` and `segment` (where
  each cut frame edge crosses the subject, a share of its length from the end shown at the top); a frame written before
  rebuilds them from its edges and whole box (`camera_moves_core.frame_segment`) - no new reading needed.
- `mode`: `plain` (shown whole: a band - running past the sides - only when the picture cuts both sides; else its width
  caps it), `height`/`width` (an edge pair), `whole` (the whole box read for the picture), `segment` (the whole box spans
  the long axis's vanishing point - no distance shows it - so the cut stations are placed), and an axis with no edge
  is placed by the whole box's centre (`engaged: place_whole`).
- When placing a whole-mode axis that has no edge does not converge, it is left to find its own place (a long band
  whose span centre swings with perspective), accepted only when it still runs past the sides the picture cuts there.
- When every seed's first pass fails to converge, a picture is solved again with rescues, `engaged` starting with
  `rescue` (row `pass: rescue`) and naming each that acted: `cap` (a step travels at most
  half the depth), `clip` (a few points behind the camera are clipped, not fled), `cycle` (a size swinging between
  depths is bracketed), `edge_depth` (a single edge's depth from its nearest points' median), `backtrack` (a step under
  the support is halved). A solve that worked before works the same, `engaged: []`.
- A lying long subject (its long axis more than twice the next, lying): the picture's lean sets where the camera
  stands around it, unrolled (`azimuth_from: tilt`); the lean shows from two azimuths half a turn apart, and the one
  nearest the side the shot holds is taken.
- Not supported (recorded): reveal cutters that open a hole in a support are applied after the camera is solved, so a
  frame_to_frame camera diving through a reveal hole in a support is refused.

The things around the subject (the world's set and context elements linked to scene ids - the landing ship) are
read on the picture too; at each pictured moment one more than ×0.6-1.6 its drawn height is a `FRAME_TARGET_MISS`
("the landing ship 0.45 of the frame tall, the picture 0.12"). In order: make the subject smaller at that moment
(the camera stands back), then raise the camera, last redraw the picture.

A miss is read, not silenced: in order, re-read the picture (`frames mark`), then the scene (where the subject is at
that moment), then the move's path or anchor; last, change the picture with the user.
