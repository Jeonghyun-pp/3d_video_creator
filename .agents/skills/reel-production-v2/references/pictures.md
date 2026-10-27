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

## Locate
`studio2 locate --cut C` returns boxes and points for the parts, the flows' ways, the moving path and the focus, draws them
on the picture and has a second reading judge them. Off marks: `--reason "<which mark, where it should be>"`.

## When the image tool stops
A quota or a pay prompt stops at once and is never retried (IMAGE_QUOTA, IMAGE_PAYMENT): report it, the user decides. A
refusal is tried twice, then rewrite what the request asks.
