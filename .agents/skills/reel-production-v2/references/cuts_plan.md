# The cuts plan (studio2/cuts.py, studio2/schemas/cuts.schema.json)

Read `studio2 cuts rules --project P` and `studio2 grammar show` first: every value of a kind is a row there (a new kind is
a row, never a free word). The body: `look`, `label_height`, `places`, `point {says, proof {what, rows}, opening}`, `cuts`.

## A cut, field by field
| Field | What you decide | Checked by |
|---|---|---|
| `show_how {point, proof, way, picture}` | what the viewer learns, the rows that prove it, the showing way and the one not taken | SHOW_HOW_UNDECIDED, WAY_UNKNOWN, plan read |
| `place`, `scale` | where, at which of the grammar's scales | CUT_PLACE_UNKNOWN, SCALE_STEP_BROKEN |
| `cutaway.opens`, `what`, `view` | what is cut open and how, from where | CUT_VALUE_UNKNOWN, the picture check opens_shown |
| `cutaway.parts`, `labels`, `flow` | the parts shown (counts), the words on them, what flows where in which colour, and when a flow runs (`at`; the whole cut when left out) | CUT_LABEL_UNSHOWN, CUT_PART_UNSHOWN, CUT_FLOW_WINDOW |
| `cutaway.scale_step`, `camera` | closer/wider/same than the cut before; still, push, pull or travel by an amount; `ends` (the framing the move ends on, in words), `along` (a travel's way) | SCALE_STEP_BROKEN, CAMERA_UNDECIDED, CAMERA_AMOUNT_OFF (push/pull only) |
| `change {kind, what, at, parts, key, element, outline}` | what changes when; the key change; what moves and how it is drawn | CHANGE_*, CUT_ELEMENT_PASTED, KEY_CHANGE_UNPLANNED |
| `line_ids`, `line_at_s`, `duration_s` | the lines said over it (a line may run on into the next cut) and its length | span lines, SHOT_TOO_SHORT, LINE_OUTRUNS_SHOTS, PACE_* |
| `frames {first, middle?, last?}` | each picture in words | CHANGE_FRAMES_SAME |
| `video {holds, avoid}` | what must hold while it moves (positive sentences: a grip, a contact), what the video must avoid (nouns) | VIDEO_PROMPT_*, VIDEO_NEGATIVE_NOUNS (`paid_video.md`) |

## Opening and view: what makes a cut its own
Each cut opens the thing its point is about, at its own scale. Two cuts at one place differ by what is opened or by the
scale step - never only by a camera move or a label.

Bad (three cuts in a row): "the bay cut open, a worker at a tool" / "the bay cut open, the vehicle on the rail" / "the bay
cut open, dust beside the worker".
Good: "the whole bay in section, ceiling to sub-floor, the air falling through it" (room) → "one load port close: the
carrier's side cut away, the wafer edges" (machine, closer) → "the floor tile in section under the port, its holes going
through" (part, closer).

## The way: what the words claim happens is seen happening
Pick the showing way whose `shows` (library/showing_ways.json) is the kind of thing the cut's words claim, and let the
cut show it happening. Why: a label, a number or a still map of a happening is read as a diagram, not seen - the plan
reader asks it (`claim_shown`, PLAN_CLAIM_UNSHOWN), and a size way picked for a trip makes a map. Instead: a trip, a
route or a repetition is the going itself; a count of many is several instances shown, cut or crossing, not one with a
number beside it.

| The words claim | Shown by | Not by |
|---|---|---|
| how far or how often something goes | `follow`: legs in different places, there and back, the thing at one screen spot across cuts | a map or section with the route drawn and the count labelled |
| how small or large | `dive_scale`: the size crossed step by step | a number label on one view |
| that a state turns | `hold_change`: the turn on screen | a before picture with the after in words |
| how fast, how far or how often it goes, felt from inside | `ride`: first-person from the traveller (view `rider`, camera `travel` along its way), a leg per cut | the traveller watched small from far off, or one cut with every leg in it |

Bad: over "a letter passes nine sorting halls in two days" - a country map from above, a red line joining nine dots,
labels "9곳" and "2일"; the camera pulls back over it.
Good: the same line over three cuts of about 1.5 s - the letter drops off a belt into a bin in one hall, rides a chute
in another, is lifted onto a third belt; the letter at one spot of the screen at each cut, the halls changing around it.
Bad: over "your blood goes round your body a thousand times a day" - the body in section with the vessels traced and
"1,000회" on it.
Good: one cell rides out of the heart, through a leg, back in, and out again - the second loop faster - the count a
label on the cell, not the picture.

## A ride: the trip from the traveller's seat
`ride` fits when the words claim a speed, a length or a repetition of a going and the viewer should feel it. `follow`
watches the trip from outside; a ride is the trip seen from the traveller. Each leg is one cut with `view: rider`,
`camera: {move: travel, amount, along}`, and nothing else moving (`change.kind: none`). Its proof is `camera`. A long or
repeated trip is two or more legs in different places, at steps of scale that chain, with the vanishing point at the same
spot of the screen. A speed change comes from the edit, never from the video model. Its picture is the way ahead, open
down the middle of the frame, with things close on both sides to stream past.

Bad: over "a parcel crosses the country overnight", one 4 s cut of the hub from above, the parcel a dot that rides three
belts and two chutes.
Good: two legs of about 2 s, both from the parcel: belt rollers flick past underneath as it runs down a long belt (the
hall), then it drops down a chute toward the bright mouth of a van (one place closer).

## Rhythm: lines across cuts
`studio2 cuts rules` gives the reference's cut lengths per role (targets) and the claims the flow checks (the median cut
under the long cut). A sentence longer than its picture runs on: the next cut lists the same line first, `line_at_s` [0, ...]
when it has lines of its own after it; the line is said in full across them (LINE_OUTRUNS_SHOTS otherwise).

Bad: 8 lines → 8 cuts of 5-6 s, each holding one picture while its sentence ends.
Good: 8 lines → 13-15 cuts of 2-3.5 s; a 5 s sentence over two views (the room, then the part it names).

## What moves (change.element.drawn)
| drawn | for | in the free preview | in the paid video |
|---|---|---|---|
| `picture` | a thing of the scene (a box, a vehicle, a door) | painted at its start (first) and end (last, an edit); the preview crosses | the model moves it |
| `speck` | dust, a drop, a signal: a mark | a glowing speck along its located path | drawn over the video |
| `sprite` | only something at most 0.03 of the frame tall | its own picture keyed and moved | drawn over the video |

## The key change and the hook
- One cut whose change is the reel's point carries `change.key: true`; its window starts at the moment.
- The first cut is the hook (`role: hook`), cut within the reference's opening length, something changing in its first 3 s.

## Answering a finding
Fix it in the plan, or add `departs: [{item: <code in lower case>, why: <a sentence>}]` on the cut it is about (a reel
choice: `python -m studio reel depart --code CODE --why ...`). A finding from the picture reader (SAME_PICTURE,
PICTURE_REPEATS) is answered by #1: what one of the cuts opens, or its scale step.
