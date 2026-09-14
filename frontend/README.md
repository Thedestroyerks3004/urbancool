# Urban Cool — Frontend

Next.js 16 (App Router) + React 19 + MapLibre GL + TanStack Query + Tailwind frontend
for the Urban Cool heat vulnerability tool. See the [project root README](../README.md)
for the full architecture, the backend API, and the data pipeline behind this app.

## Running

Requires the backend running at `http://127.0.0.1:8000` (or whatever
`NEXT_PUBLIC_API_BASE_URL` in `.env.local` points to — see `.env.local.example` if
present, otherwise it defaults to `http://127.0.0.1:8000`).

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

From the project root, `../run.sh` or `..\run.ps1` starts both the backend and this
frontend together with a single command.

## Structure

- `app/page.tsx` — top-level state and wiring (draw → analyze → metric/simulation view).
- `components/MapView.tsx` — MapLibre map: AOI-locked viewport with a visible boundary,
  drag-to-draw, and the data-driven metric/simulation circle layer.
- `components/MetricTogglePanel.tsx`, `Legend.tsx` — the 8 real metric layers.
- `components/InterventionPanel.tsx` — ranked intervention list + before/after toggle +
  sortable top-locations table.
- `components/ExplanationPanel.tsx` — SHAP-based score explanation.
- `lib/api-client.ts` — typed fetch wrapper over the backend's REST API.
- `lib/api-types.ts` — **generated**, not hand-written. Regenerate after any backend
  schema change:
  ```bash
  npx openapi-typescript http://127.0.0.1:8000/openapi.json -o lib/api-types.ts
  ```
- `lib/metrics.ts` — metric and intervention display metadata (labels, color scales);
  the IDs must match the backend's `FEATURE_NAMES` / `INTERVENTION_ASSUMPTIONS` keys.
- `scripts/` — Playwright end-to-end checks driven against a real running instance
  (not unit tests): `smoke-test.mjs` (draw → analyze → simulate flow), `final_check.mjs`.

## Type-checking

```bash
npx tsc --noEmit
```

## Notes

- `MapView`'s data source is a MapLibre GeoJSON source processed by MapLibre's own
  worker — `maplibre-gl` is pinned to `4.7.1` because newer versions' ESM
  module-worker loading silently fails to initialize under Next.js's Turbopack bundler
  (the map renders the basemap but no data layer ever paints). Don't bump this
  dependency without re-verifying that a drawn region's grid actually renders.
- The analyze response only ships the default (Heat Vulnerability) layer. Any other
  metric is fetched on demand via `/metrics/{name}` and cached client-side by React
  Query, so switching metrics after the first fetch is instant with no repeat request.
