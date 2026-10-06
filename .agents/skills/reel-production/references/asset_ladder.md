# Asset ladder (in order)

1. **Local library** — `asset search --offline`; pinned versions.
2. **Poly Haven / ambientCG** — `asset search` with `"providers": ["polyhaven", "ambientcg"]`; CC0, studio marks them `cleared`. Models, textures, HDRIs.
3. **CAD factory** — `asset generate --spec <json> [--prepare]` for standard parts (ISO bolts/nuts/washers, EN sections, KS rebar). Exact to the table; dimension tables need human verification against the standard before publication.
4. **Image → 3D (paid)** — `asset image3d-review --project P --image --asset-id --real-dimension longest=0.30` (sheet, no call) → show it → `asset image3d --project P --review <id> --user-words "<their words>" --allow-paid --max-usd 1` (Rodin 2.5, inside the project budget). Registered `review_only` + `ai_generated`; the unseen back is invented. Needs `asset approve` (human) before any final render.
5. **Hand modelling** in the author script.

Caller JSON can never mark a file `cleared`; only studio adapters can. Hunyuan 3D models are blocked (licence excludes South Korea). Free assets lacked elevator systems, steel connections, space frames, curtain walls, tunnels and Korean landmarks in the 2026-10-03 survey — plan CAD or modelling for those.
