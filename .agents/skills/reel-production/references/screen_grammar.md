# Screen grammar: how well-made reels use the frame, as data (v0, 2026-10-09)

What this is: one vocabulary for describing any shot of any video - a study clip or our own render - by what it
does on screen, never by what it is about. A study set is measured in it; the measurements become patterns with their
support (how many sample groups, how many shots: library/patterns, studio/patterns.py); Astra reads them, picks what
fits the project the user and Astra agreed (aspect, length, subject, direction), and records why. Nothing here assigns
a clip a purpose ("these for rhythm, those for camera"): every clip is measured on every item, and the choice is Astra's.

Why (2026-10-09): shot design was Astra's guess - screen targets with no basis, camera timing from one clip - and
"looks well made" lived in no number. A study set was read at 0.25 s before writing this list; every item below was
needed to describe it, and one shot from each of six sample groups fills it without a new item (section 7).

## 1. Principles
1. **Function, not content.** "A known-size thing in frame at 0.3 of the height", not "a child"; "the surroundings
   dim and the subject is outlined", not "the dam is highlighted".
2. **Relative to the content rect.** Positions and sizes are shares of the rectangle the picture plays in (section 2),
   so a square, a vertical or a window-in-frame layout reads the same. The aspect is the project's, chosen in
   conversation; Astra maps shares onto it.
3. **Time in two units.** Shares (of the shot, of the reel) for structure; seconds only for rates people perceive in
   seconds (how often the picture changes, how long a subtitle chunk stays). No item depends on the reel's length.
4. **Closed vocabularies with definitions.** A value outside the list is `other` plus a note; a new value enters only
   when it is seen in three or more sample groups (section 5).
5. **Measured the same way on both sides.** A study clip and our render go through the same estimators; our id pass
   is used only to measure the estimators' error (calibration), never as a better ruler for our side.

## 2. Frame of reference
- `canvas.layout`: `full_bleed` (the picture fills the frame) | `window` (the picture plays in a sub-rectangle, with
  bands around it).
- `canvas.content_rect`: [x0, y0, x1, y1] of the picture in canvas shares (T1: rows/columns that stay static and
  flat across the reel).
- `canvas.bands`: list of {`kind`: `title` | `subtitle` | `logo` | `blank`, rect} outside the content rect.
All composition items below are in content-rect shares (0,0 top-left; 1,1 bottom-right).

## 3. Measurement tiers
| Tier | How | Trusted for |
|---|---|---|
| T1 signal | deterministic on every frame (ffmpeg, pixel statistics) | cuts, shot lengths, motion energy envelope, static bands |
| T2 geometry | Blender-side numpy on every frame (global motion: translation, scale, rotation, parallax residual) | camera move class, magnitude, timing |
| T3 vision | Codex labeller, fixed prompt, keyframes (start, 1/3, 2/3, end, plus T2 events), run twice | roles, boxes, reveal, graphics, scale anchors, depth layers |
An item whose two T3 runs agree on fewer than 0.7 of shots is `weak`: reported, never a target, its definition revised.

## 4. Items
### A. Edit (per shot, T1)
| id | definition | unit |
|---|---|---|
| `shot.len_s` | cut to cut | s |
| `shot.len_share` | of the reel | share |
| `shot.position` | start time / reel length | share |
| `shot.transition_in` | `cut` `dissolve` `dip` `whip` `match` (shape or motion continues across) `zoom_through` (a push carries into the next shot) `flash` | vocab |
| `shot.cut_during` | the outgoing shot at the cut: `hold` (still) or `move` | vocab |
| `edit.visual_change_rate` | changes a viewer sees per second inside the shot: cuts + graphic appear/disappear + subtitle chunk changes | 1/s |

### B. Camera (per shot, T2; envelope features reuse motion_style)
| id | definition | unit |
|---|---|---|
| `camera.move` | dominant move: `static` `push` `pull` `pan` `tilt` `orbit` `truck` (sideways) `pedestal` (up/down) `roll` `handheld` `zoom` `compound` | vocab |
| `camera.magnitude` | total screen translation (share of content width) and scale ratio end/start | share, ratio |
| `camera.envelope` | motion_style features: share of motion in the first 30 %, peak position, end-hold share | shares |
| `camera.target` | the move goes `toward_subject` `away` `across` `around_subject` `none` | vocab |
| `camera.view_height` | `down` (looking down > 20 deg) `level` `up` | vocab |
| `camera.roll_deg` | horizon tilt | deg |

### C. Composition (per keyframe, T3 boxes, same formulas as screen_core.METRICS)
| id | definition | unit |
|---|---|---|
| `subject.box` | the thing the shot is about (one box; a group if several) | rect |
| `subject.center_x/y`, `width_share`, `height_share`, `area_share`, `edge_margin`, `thirds_distance` | from the box, as `shot.screen.targets` names them | share |
| `frame.void_share` / `frame.void_edges` | empty sky, flat backdrop, black or blank; which edges it touches | share, edge list |
| `frame.depth_layers` | how many of foreground / middle / background hold something | 0-3 |
| `frame.scale_anchor` | a thing of known size and its kind: `person` `hand` `vehicle` `door` `furniture` `tool` `none` | vocab + height_share |
| `frame.subject_count` | separate things competing for attention | count |

### D. Change inside the shot (T2 + T3)
| id | definition | unit |
|---|---|---|
| `change.kind` | what moves: `camera_only` `object_motion` `state_change` (fills, opens, breaks, grows) `none` | vocab |
| `reveal.method` | `section_cut` `explode` `transparency` `isolate` (surroundings dim or grey, subject kept) `zoom_scale` (into a smaller scale) `real_to_diagram` (a real view turns into a diagram or cutaway) `time_lapse` `xray` `none` | vocab |
| `reveal.at` | when the reveal starts | share of shot |

### E. Graphics over the picture (T3; engine kinds in brackets)
| id | definition | unit |
|---|---|---|
| `graphic.kind` | `arrow` [arrow] `outline` [outline] `highlight` [highlight] `dimension` [dimension] `path_trace` [draw_line] `pin` (map marker; no engine kind) `label` (text tied to a point) `header_word` (a big word naming the step; no engine kind) `counter` (a number counting) | vocab |
| `graphic.count` | graphics on screen at the busiest keyframe | count |
| `graphic.appear_at` | first appearance | share of shot |
| `graphic.anchored` | follows a thing in the picture (true) or fixed on screen | bool |

### F. Subtitles (T3 boxes + T1 change detection)
| id | definition | unit |
|---|---|---|
| `subtitle.center_y` | of the subtitle box | share |
| `subtitle.lines` / `subtitle.chars` | visible at once | count |
| `subtitle.chunk_s` | how long one chunk stays | s |

### G. Role (T3; a label to group patterns, not content)
`hook` (the reel's first seconds: a reason to keep watching) `establish` (where/what, often a real or familiar view)
`explain` (shows how something works) `reveal` (opens what was hidden) `compare` `consequence` (what happens because)
`close`. Narrative reels use the same words (establish, consequence); a shot may carry one role.

### H. Flow (derived across shots)
| id | definition |
|---|---|
| `flow.scale_step` | next shot `closer` / `same` / `wider`, and the ratio of subject height_share |
| `flow.move_hold` | sequence of moving / still shots (run lengths) |
| `flow.role_next` | role transitions (from -> to counts) |
| `flow.real_to_inside` | a familiar real view followed within two shots by the inside of the same thing |

## 5. Keeping the vocabulary general
- **N+1 test** at every vocabulary change: describe one shot from a sample group not used to write the change. If it
  needs a new item or value, the change was a case list - split it further.
- **New values** need three or more sample groups (clips made the same way). **Leave-one-group-out:** a pattern that
  changes materially when one group is removed is `weak` (one maker's style, or one subject's).
- **No subject nouns** in item ids, values or pattern text (a lint rejects them).
- **Support, not purpose:** a pattern records how many groups and shots support it; it never records what it is "for".

## 6. The open layer: the list measures, pictures design
The items are a ruler for machine judgement; they are not what Astra looks with. So that the list never narrows what
is seen:
1. **Free notes.** The labeller also writes, per shot, one or two sentences: why the shot works, and anything it sees
   that no item describes (light that builds toward the subject, a colour change at the reveal, sound-picture sync...).
2. **Discovery.** Notes are clustered across clips; a concept that recurs in three or more sample groups is proposed as a
   new item (with the shots it came from), passes the N+1 test, and is then measured like the rest. The vocabulary
   grows from the data, not from whoever wrote this file.
3. **Design by example.** When Astra designs a shot it gets example shots, not only numbers: keyframe crops, their
   notes (kept local beside the study set), and the distributions with their support. It looks at the pictures and decides how to carry them into this
   project; numbers are for checking afterwards.
4. **Differences in pictures.** Our render beside the closest example shots; what differs that no item explains is
   written down (as visual_review does), and recurring differences are proposed as items the same way.
Astra may also propose an item when its own work shows a gap; it enters by the same rule (three groups, N+1).

### Proposed items (discovery, 2026-10-10; not yet measured - each needs a definition, an estimator and the N+1 test)
Concepts that recur in the labeller's notes in 4+ sample groups and that no item describes:
| proposal | what the notes say | support | estimator idea |
|---|---|---|---|
| `colour.accent` | one saturated accent colour (red, cyan, orange) marks the subject, a route or an edge against a muted picture | 278 notes, 7 groups | T1: share and hue of high-saturation pixels; inside vs outside the subject box |
| `light.emphasis` | glowing edges, translucent surfaces showing what is beneath | 65 notes, 7 groups | T3 value of `graphic.kind` or `reveal.method`, or T1 local bloom |
| `medium.visualised` | particles, streams or light making a flow or process visible | 55 notes, 7 groups | T3 value of `change.kind` |
| `subject.contrast` | the subject reads against a plain, dark or bright ground (silhouette) | 38 notes, 7 groups | T1: luma/colour contrast between the subject box and its surround |

## 8. Calibration (2026-10-10)
Eight 2 s renders with a known camera (static, push, pull, zoom, pan, truck, pedestal, orbit) and a known subject box
(projected bounds), measured with known cuts (`grammar measure --cuts`: our own edit knows them):
- labeller move names 8/8 in both runs; T2 axis right where one axis dominates (pedestal and orbit read `compound`,
  which is why the labeller names the move);
- subject box from the labeller: height error median 0.004, p90 0.012 of the frame; centre error under 0.003 (a simple
  solid on a plain ground - complex subjects are less exact);
- fixed while calibrating: a flat sky above a horizon was read as a title band (a band now needs a fixed straight
  edge); a locked-off still shot read as `unknown` (now `still`);
- boundary detection is not calibrated here: cuts between views of one plain scene score low (0.05-0.21) - for our
  renders the edit's own cut list is used, for study clips the hand-labelled check stands (91 %).

## 9. Fill test (2026-10-09, one shot from each of six sample groups, read from 0.25 s contact sheets)
| group | layout | camera | reveal | graphics | subtitle | role | other |
|---|---|---|---|---|---|---|---|
| G1 | full_bleed | static | real_to_diagram at 0 | arrow x2, label | centre, 1 line | explain | prior shot is the real fixture (flow.real_to_inside) |
| G2 | full_bleed | push (slow) | section_cut | none | upper centre, 2 lines (two languages) | explain | flow.scale_step: wider than the shot before |
| G3 | window (title band top, subtitle below) | push toward_subject | none | none | below window | establish | scale_anchor person, depth_layers 3 |
| G4 | full_bleed (square) | static | isolate at 0.4 | outline, header_word | centre | explain | void none (terrain block, sides cut) |
| G5 | full_bleed | static | none | none | centre, small | consequence | change.kind state_change |
| G6 | full_bleed | push (slow), view_height down | none | arrow x4 (appear 0.3) | centre | explain | path-shaped subject |
Values were read by eye from the sheets; camera entries are approximate until T2 measures them. Every shot filled
without a new item. Seen and kept as values: window layout (G3), header_word and isolate (G4), real_to_diagram (G1).
Camera moves inside shots are small in all six; most change comes from cuts, graphics and subtitles
(`edit.visual_change_rate`) - recorded as data, not as a rule.

## 10. Using patterns (2026-10-10)
- **One role vocabulary** for shot lists, shots and patterns (4.G). Older shot-list names map: detail -> explain,
  outro -> close, transition -> no section of its own (reads 'all').
- **Read:** `studio pattern show --role <role> [--project P] [--library NAME]` - strong items as ranges, weak items with
  their reasons, and example shots (keyframe strips and notes) where this machine keeps them; elsewhere numbers only.
  The library is the project's `pattern_library`, else the only one in `library/patterns`.
- **Record:** the shot list's `pattern` per shot - what is carried (mapped to this aspect, length and subject) and what
  is left on purpose; departures reach `shot.screen.pattern_departs`. Ladders adopted from 2026-10-10 warn
  `PATTERN_UNREAD` for a shot with a role and no record (advice).
- **Compare:** one function (`patterns.compare`) for the storyboard sheet, the build (`SCREEN_PATTERN_OFF`: strong items
  outside p10-p90 with no declared departure; taste) and `qa collect` (`visual_checks.patterns`: the cut measured at
  its own cuts with machine items only, plus each shot's composition; the reel's mix of camera moves).
- **Our side is measured on the frame probe's id-pass subject box**, through the same formulas as the study clips'
  labelled boxes. Calibration (section 8) put the labeller's box within 0.004 of the true box height, so the two
  rulers agree; complex subjects may differ more, and a box overstates a non-rectangular subject on both sides.
- Patterns are never written into `screen.targets` automatically; targets stay the words the user fixed.
- **Focus:** our side measures what the shot is about - `screen.subject`, else declared key parts, else the parts screen
  targets measure (each gets its own id-pass class); with none, the probe's whole subject class, noted FOCUS_UNDECLARED
  when it fills the frame. `subject_travel` (how far the focus box moves across the shot) is measured the same way on
  study clips (first to last keyframe) and our shots (first to last probe frame).
- **The record lives on the shot:** `screen.pattern_carried` / `pattern_departs` (the shot list's `pattern` lands there
  on approval; delegated runs write it directly). A shot with a role and no record is `PATTERN_UNREAD`.
- **Shot length is compared in seconds** (`len_s`). A share of the reel is never compared: it depends on how many shots
  the reel has (the first comparison run, 2026-10-10: a 5-shot reel read every shot as outside study clips of 20-50).
- **Taste** (`studio pattern taste add|list|remove`, local `.studio/taste.json`): the user's preferences in the same items,
  per section - `prefer higher|lower` for a number (the user's side of the median), values for a category. Each entry
  carries the user's words and is read back to them before it is added ("I read that as: hook shots keep the subject
  large - right?"). `pattern show` lists the taste beside the ranges; the sheet marks each item met / not met. Taste is
  never a warning; where it and the patterns disagree, the taste wins unless the shot says why.
- **Framing (2026-10-10):** the focus is the explained structure (a detail inside it keeps its own `min_px`;
  `FOCUS_IS_DETAIL` when `screen.subject` lists only details). On the settled frames (the last 40 %) the focus's union box
  is judged: touching one edge of an axis = cut, all four = fills the frame, a measured part at 0 px = off screen
  (`FOCUS_FRAMING`, taste); touching both edges of one axis is a band running across and is fine. Fit with the move's
  `fill`/`detail_fill` = the role's `height_share` median, then correct by measured/target.
