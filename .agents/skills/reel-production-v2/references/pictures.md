# Pictures (studio2/frames.py, prompts.py, checks.py, locate.py, images.py)

## Order
Pictures are made in cut order: a cut at a place an earlier cut showed takes that cut's picked first picture as its place
reference (and the prompt says how its view differs: "NOT the reference's view"); every other cut takes the reel's first
picture as its style reference. The middle and last pictures are edits of the picked first that change only the change.

## A picture that will be moved
The video model moves what the first picture gives it. The writer asks for the moving thing (`change.element` drawn
picture) large and clear in the frame, with open room ahead of it the way it will move. A travel asks for the way ahead
open down the middle, with things close on both sides. Why: a small far subject smears (fab c04's distant box blurred
into the rail), and a subject at the frame's edge has nowhere to go. When you pick, prefer the try where the moving thing
is biggest and has the most room ahead. A ride's first picture (view `rider`) shows the way ahead, not the traveller: at
most its front edge at the bottom of the frame.

## Make, check, pick
1. `studio2 frames make --cut C` makes what is not made yet (an edit waits for its first to be picked).
2. `studio2 checks run --cut C` asks the table's questions (library/picture_checks.json). A row marked floor refuses the pick
   (PICTURE_FLOOR); the others are advice to weigh.
3. `studio2 frames pick --cut C --frame F --try N --why "<what the picture shows that the cut needs>"`.
4. A wrong picture: `studio2 frames make --cut C --frame F --reason "<what was wrong>"` - at most 2 more tries
   (TRIES_SPENT), then write up what keeps failing and change the plan.

Bad reason: "make it better". Good reason: "the carrier is closed - its side must be cut away so the wafer edges show".

## Many separate things
A part the plan says is many loose things - a `count` of 2 or more, a number before its noun in its words or the show rows
("25 thin wafers"), or a word of the row's `many` ("stack of", "rows of") - is checked to read as separate items
(`count_separate`, PICTURE_ITEMS_FUSED): located on the try, cut out, counted three ways by a narrow reader, the middle
count over the planned number. Why: fab c03's approved first picture showed its 25 wafers as one striped cylinder; the paid
video kept it, and no check looked before the sheet. Instead of "a stack of wafers", write the gap into the words.

Bad part words: "the stack of thin round silver wafers in the box's slots" (drawn as one ribbed block).
Good: "the 25 thin round silver wafers in the box's slots, each held apart by a clear gap" (drawn as plates over air).

It is advice (calibrated 2026-10-27 on 9 labelled pictures, 2 runs: goods 0.72-1.0, bads 0.6-1.0, limit 0.66): the reader
counts the stripes of a fused stack as items (c03's cylinder read 15 of 25, a fused dev c03 read 22). So look at the
crop yourself before you pick a picture whose many things touch; a value under the limit means few read apart. A grid on
one surface (chips on a wafer) is not "many loose things" and is not checked. Put a number in the words, not `count`,
where the exact number is beyond a reader's count (`count` asks it exactly: count_right, a floor).

## Locate
`studio2 locate --cut C` returns boxes and points for the parts, the flows' ways, the moving path and the focus, draws them
on the picture and has a second reading judge them. Off marks: `--reason "<which mark, where it should be>"`.

## When the image tool stops
A quota or a pay prompt stops at once and is never retried (IMAGE_QUOTA, IMAGE_PAYMENT): report it, the user decides. A
refusal is tried twice, then rewrite what the request asks.
