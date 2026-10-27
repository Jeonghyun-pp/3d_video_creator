# Readers (the preview's, the style's, the same view's)

All readers are `screen_grammar.codex_json`, at most 3 at a time on the machine (pool.slot; STUDIO_READERS).

| Reader | Command | Reads | Its findings go to |
|---|---|---|---|
| Blind viewer | `studio2 reel see` | the preview without words: what each cut and the reel teaches | the cut's plan or pictures |
| Reel watcher | `studio2 reel judge` | the preview: hook, change seen, rushed, dull, repeats | the plan (lengths, views) |
| Style | `studio2 style cut --cut C`, `studio2 style reel` | each cut's strip against the reference's own cuts; one frame of each cut against them | redraw from the plan (#6) |
| Same view | `studio2 flow same` | the cuts' first pictures on one sheet: which show one view of one place | what is opened / the scale step |
| Reel flow | `studio2 flow plan`, `studio2 flow built` | v1's 16 direction rules read from the cuts and their located pictures | the plan |

## The style verdict
It scores a cut 0-10 against the reference's cuts; the pass score and whether it is a floor are calibration's
(library/style_check.json: held-out reference cuts must pass, v1 clay and plain photoreal views must not, on every run).
A failure is kept for the cut's pictures: a new clip of the same pictures gets the same verdict back. Read its `further`
sentence - it says what to change in the plan.

## When the studio asks you to read or paint (OUTSIDE_MODEL_ASKED)
Where the sandbox denies the network the studio writes a request file and stops. Hand the request file to a fresh subagent
with exactly this, then `python -m studio ask answer --request <file> --answer <its file> --model <its model> --by <who answered>`:

```
You are a reader for a vertical explainer reel's checks.
## Your Assignment
- request_file: {request_file}
## Instructions
1. Open the request file; look at every image it names; answer the question in it from the images only.
2. Write your answer as JSON that validates against the schema in the request file.
## Output Format (return EXACTLY this structure)
{"answer_file": "<path of the JSON you wrote>"}
## Rules
- Do NOT answer from the plan, the line or what the cut is meant to show - only from the pictures.
- Do NOT write or change any other file; do NOT soften an answer to let a cut pass.
```
