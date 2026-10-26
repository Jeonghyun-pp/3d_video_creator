# The approval sheet and the paid video (studio2/sheet.py, video.py)

## The sheet
`studio2 sheet build` makes review/sheet/<rev>/index.html (and review/sheet/index.html, the latest): per cut its first,
middle and last pictures (a frame of the preview where it has no such picture, said so), its line, what the viewer learns,
the motion in words, the free preview clip, the paid model, billed seconds and cost, the exact prompt; the reel's total
with the budget and what is spent. A cut failing the calibrated style floor is refused (STYLE_OFF).

<CRITICAL>
The sheet shows every cut, its whole line and the exact prompt the model would get - never a summary. The user's answer is
recorded verbatim: `studio2 sheet approve --user-words "<exactly what they said>"`. A cut their words ask to change goes in
`--cuts` (reopened, never approved); words that name a cut left out of `--cuts` are refused. Never write words for them.
</CRITICAL>

A reopened cut: `studio2 frames make --cut C --frame first --reason "<what the user asked>"` (or `cuts write` when the plan
changes), `motion render`, `sheet build`, show it again. An approval holds while the request is unchanged (the first
picture, the prompt, the length, the arguments).

## The paid video
1. `studio2 video make --cut C`: veo-3.1 image-to-video (`fal-ai/veo3.1/image-to-video`, $0.20/s, billed 4/6/8 s) from the
   approved first picture through v1's generative/clip.py; never above the approved price; made once per request.
2. `studio2 video check --cut C`: three frames of the video against the approved pictures and the line (motion matches the
   line, the middle near the approved middle, nothing morphs, it ends near the last). Not calibrated on real takes: advice
   for the person.
3. `studio2 video composite --cut C`: the marks located again on the video's frames (every second, both ends), judged on the
   middle frame, followed between them and drawn over every frame.
4. Show the person the marked clip; `studio2 video review --cut C --user-words "<their words>"` (`--reject` keeps the free
   clip). Only then is it the cut's clip in v1's edit.

## Cost and models
Image-to-video holds only the first picture. First-last-frame and cheaper models on fal (with price evidence) are listed as
candidates in the build plan; adding one needs a `fal_client.PRICING` row - frozen code: ask the user for their freeze words.
Veo 3.1's fal endpoints say they are deprecated on 2026-11-17.

Over the remaining budget — in order: 1) fewer paid cuts (the key change and the hook first; the rest stay free previews),
2) a cheaper model (the user's freeze words for its price row), 3) the user's new cap. Forbidden: splitting a request to
pass under a cap, or paying without the words.
