# AI technical visualization studio — agent entry point

The production agent lives in this repository. For any scene or reel work (creation, revision, resumption):
1. Read `.agents/skills/reel-production/SKILL.md` (rules, phases, machine checks) and `references/index.md` for the modules of the current phase.
2. Read `docs/ENGINE_PROGRESS.md` (where the engine stands, open issues) and `docs/BUILD_REPORT.md` (measured capability). The build plan `docs/AGENT_BUILD_PLAN.md` is design; the report takes precedence.
3. Run tools with `.venv/bin/python -m studio ...` from this directory. New machine: `scripts/bootstrap.sh` first (`docs/SETUP.md`).

Where work goes:
- `projects/` (gitignored): working projects, renders, reference analysis. Reference videos stay local, never committed or sent anywhere.
- `examples/`: tracked, reproducible inputs (author scripts, shot/style contracts) — e.g. `examples/samsung_cutaway`.
- `library/`: verified exemplars, assets, fonts and learned styles (numbers and hashes only).
- New validation work goes in `projects/harness_validation/`. Projects made by other sessions are read-only unless the user targets them.

The requested orchestrator is Astra: `scripts/reel_agent.py` requests `gpt-6-astra` explicitly; this file does not change the current chat model. Use the host's available agent tools when native CLI access is unavailable, and disclose the actual limitation.
