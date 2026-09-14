# Urban Cool

An AI-based urban heat mitigation tool for the **Anna University – OMR–ECR corridor,
Chennai** (80.15°E–80.30°E, 12.75°N–13.05°N). It maps a native-10m Heat Vulnerability
Index from real satellite/vector data, explains what drives a drawn region's score, and
simulates the effect of concrete interventions (tree cover, cool roofs, cool pavement,
green space, water features, etc.), ranked by real modeled effectiveness.

Every number in the app is either measured (from Sentinel-2, Landsat, ECOSTRESS, OSM,
WorldPop) or a disclosed, documented planning assumption — never fabricated or silently
interpolated. See [`DATA_REPORT.md`](DATA_REPORT.md) for the full data provenance and
resolution-honesty audit.

## What it does

1. **Draw a region** on the map (bounded to the study area).
2. **See its Heat Vulnerability Index** (0–100) at native 10m resolution, plus every
   underlying metric (NDVI, NDWI, albedo, built-up %, building/road density, built-up
   trend) as its own toggleable layer.
3. **Read a SHAP-based explanation** of which features drive that region's score.
4. **Simulate interventions** — all 7 are scored for the drawn region in one pass and
   ranked by mean improvement, with a before/after map toggle and a ranked-locations
   table showing where each intervention would help most.

## Architecture

```
                     ┌───────────────────────────┐
                     │   Next.js frontend          │
                     │   (MapLibre GL, React Query) │
                     └──────────────┬────────────┘
                                    │ REST + GeoJSON
                                    ▼
                     ┌───────────────────────────┐
                     │   FastAPI backend            │
                     │                               │
                     │   api/routes.py  ── schemas.py │
                     │   state.py (loads once at startup, held in memory)
                     │     ├─ feature_stack.py  (native-10m per-pixel stack, cached)
                     │     ├─ model.py          (CatBoost .cbm, loaded not retrained)
                     │     ├─ albedo.py         (Liang 2001, renormalized)
                     │     ├─ simulator.py      (7 intervention perturbations)
                     │     └─ grid_utils.py     (vectorized GeoJSON grid building)
                     │   region_cache.py  (in-memory region_id -> pixel indices)
                     └──────────────┬────────────┘
                                    │
                                    ▼
                     ┌───────────────────────────┐
                     │   pipeline/                   │
                     │   (Sentinel-2, Landsat/ECOSTRESS│
                     │    LST, OSM, WorldPop fetch +   │
                     │    validation scripts)          │
                     └───────────────────────────┘
```

The backend owns every geospatial and ML computation; the frontend only ever sees JSON
summaries, GeoJSON grids for map overlays, and `region_id` handles — it never touches a
raw raster.

## Running it

**Single command** (from the project root):

```bash
./run.sh          # bash / macOS / Linux / Git Bash on Windows
```
```powershell
.\run.ps1         # PowerShell on Windows
```

Either script frees ports 8000/3000 if already in use, starts the backend (loading the
cached feature stack + trained model into memory), waits for a real health check to pass,
starts the frontend, waits for it to respond, then tails both logs until you press
Ctrl+C — at which point it stops both cleanly.

- Backend: http://127.0.0.1:8000 (interactive API docs at `/docs`)
- Frontend: http://localhost:3000

### Running the pieces by hand

```bash
# Backend
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

The frontend reads the backend URL from `NEXT_PUBLIC_API_BASE_URL` in
`frontend/.env.local` (defaults to `http://127.0.0.1:8000`).

## Backend API

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/health` | GET | Model version, R²/MAE, training pixel count, cache timestamps |
| `/api/v1/region/analyze` | POST | Score a drawn GeoJSON polygon: heat vulnerability grid, metric summaries, SHAP explanation |
| `/api/v1/region/{region_id}/metrics/{metric_name}` | GET | Fetch any single metric's grid for an already-analyzed region (lazy-loaded by the frontend to keep payloads small) |
| `/api/v1/region/{region_id}/simulate` | POST | Score one or more interventions against the region's real feature values |

Full interactive docs (request/response schemas) are always available live at
`/docs` once the backend is running.

### Model

- **CatBoost regressor**, trained on every valid pixel in the AOI (5,581,140 rows, no
  subsampling), batched training via `init_model` continuation to bound memory/CPU.
- Held-out test: **R² = 0.99985, MAE = 0.1335** (index scaled 0–100). This confirms the
  model correctly reconstructs its target formula from the same features it trains on —
  it is not a claim of predictive skill against independently measured ground truth (the
  index has not been validated against measured land surface temperature; see the
  caveat every API response carries in its `caveats` field).
- Feature stack is native 10m resolution across the full AOI, built as a single
  streaming pass over 45 validated months (`backend/app/feature_stack.py`), cached to
  disk so the API loads it once at startup rather than rebuilding it per request.

### Intervention simulator

Seven interventions, each a documented, named perturbation of real feature values (see
`backend/app/simulator.py` for exact constants and rationale) — not measured effect
sizes for this specific corridor:

| Intervention | What it perturbs |
|---|---|
| Add Tree Cover | NDVI, region-wide |
| Cool Roof | Albedo, on meaningfully built-up pixels |
| Add Green Space | NDVI + NDWI up, built-up % down (displaces built area) |
| Green Roof | NDVI + albedo, smaller gain than ground-level tree cover (roof footprint only) |
| Cool Pavement | Albedo, only on pixels with meaningful road density |
| Water Feature | NDWI + NDVI up, built-up % down (retention pond + riparian planting) |
| Reduce Density | Building density + built-up % down, NDVI up (long-horizon redevelopment scenario) |

Every `/simulate` call scores all seven for the drawn region in one request; the
frontend ranks them by mean Heat Vulnerability improvement across the region.

## Frontend

Next.js 16 (App Router) + React 19 + MapLibre GL + TanStack Query + Tailwind.

- `components/MapView.tsx` — the map: OSM basemap, a locked-to-AOI viewport with a
  visible boundary outline, drag-to-draw, and a GPU-side data-driven circle layer for
  whichever metric or simulation overlay is active.
- `components/MetricTogglePanel.tsx` / `Legend.tsx` — switch between the 8 real metric
  layers (Heat Vulnerability, NDVI, NDWI, albedo, built-up %, building density, road
  density, built-up trend).
- `components/InterventionPanel.tsx` — the ranked intervention list, before/after map
  toggle, and a sortable top-locations table.
- `components/ExplanationPanel.tsx` — the SHAP-based "why this score" summary.
- `lib/api-client.ts` / `lib/api-types.ts` — a typed API client generated from the
  backend's own OpenAPI schema (`npx openapi-typescript <backend-url>/openapi.json -o
  lib/api-types.ts`), so a backend schema change that isn't reflected here fails to
  typecheck rather than silently mismatching at runtime.

## Data pipeline

`pipeline/` holds the fetch/validate scripts behind every feature (Sentinel-2 10m
monthly least-cloudy composites, Landsat 30m and ECOSTRESS LST, OSM building/road
vectors, WorldPop). Each script prints its own real KEEP/DROP verdict against a named
threshold in `pipeline/common/thresholds.py` — nothing is assumed to have worked
silently. See [`DATA_REPORT.md`](DATA_REPORT.md) for the full dataset-by-dataset
report (sources, resolution honesty, temporal coverage, what was dropped and why) and
[`data-audit/`](data-audit/) for the earlier feasibility and pipeline-status audits.

## Known limitations (disclosed, not hidden)

- The Heat Vulnerability Index is derived from land cover, vegetation, and urban
  density only — it has **not** been validated against measured land surface
  temperature.
- Intervention effect sizes are illustrative planning constants grounded in general
  urban-heat-mitigation literature, not measured outcomes for this specific corridor.
- The region cache (`backend/app/region_cache.py`) is an in-memory prototype — region
  IDs are lost on backend restart. A production deployment would persist this.
- Drawing a very large region (close to the full AOI) is inherently slower and heavier
  than a focused neighborhood draw, since it means scoring millions of real pixels.
