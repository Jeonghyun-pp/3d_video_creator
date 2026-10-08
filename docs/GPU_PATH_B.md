# Generation path B: open-weight models on a rented GPU (design, 2026-10-08)

Path A sends every paid request to fal. Path B runs open-weight models (mask- and control-conditioned video and image
models) on a rented GPU. Nothing below is built yet; this is what has to be decided first and the shape the code
keeps ready for it.

## Decisions the user makes before any code runs
| Decision | Options / notes |
|---|---|
| Provider | RunPod or Vast.ai (RTX 5090 32 GB, about $0.3-0.7/h; A100 80 GB for long clips) |
| Account, payment, key | the user's own; the key lives in the environment like FAL_KEY, never in the repo |
| Spending cap | per hour and per month; the paid ledger records GPU time like a fal call |
| Models | mask-conditioned video (Wan 2.2 VACE: depth + masks), image fill (FLUX Fill); each licence checked - models whose licence excludes Korea stay blocked (BLOCKED_PREFIXES) |
| Storage | 50-100 GB of weights; a persistent volume or a re-download per session |
| What leaves the machine | control passes, masks and prompts only; reference photos and videos never (licence local_only) |

## Shape kept ready in the code
- A request is already provider-neutral: `generative/inputs.py` builds the control passes (depth, clay, canny,
  normal, lines, id) and `generative/keep.py` the per-part masks (`keep`, `generate_only`); `clip.build_arguments`
  is the only fal-specific step.
- Path B adds a provider row (`routing.MODELS[model].provider = 'local_gpu'`) whose arguments are the same inputs
  plus the masks as files, a runner that starts the pod, uploads, runs, downloads and stops it, and the same
  review sheet, user words, budget and ledger as fal (`fal_client.paid_call` generalised to a provider).
- QA does not change: structure, parts, light, keep and the reference critique judge a take whatever made it.
