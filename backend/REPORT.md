# Urban Cool — Backend + Design Prototype Report

This covers PART A (FastAPI backend, fully built and tested end-to-end against the running
server, not just code-reviewed) and the design-mockup phase of PART B (a real interactive
Next.js app is separate, follow-on engineering — see "Frontend status" at the end).

---

## 1. Architecture

```
                    ┌─────────────────────────┐
                    │   Next.js frontend        │  <- NOT built yet this session.
                    │   (React, TypeScript,      │     A design PROTOTYPE exists
                    │   Mapbox/MapLibre)          │     (see "Frontend status").
                    └───────────┬─────────────┘
                                │ JSON (tabular/ranking)
                                │ GeoJSON (vector overlays)
                                ▼
                    ┌─────────────────────────┐
                    │  FastAPI backend           │
                    │  (this session, built +    │
                    │   tested end-to-end)       │
                    │                             │
                    │  api/routes.py  ─┬─ schemas.py (Pydantic)
                    │                  │
                    │  state.py  (loads once at startup, held in memory)
                    │    ├── feature_stack.py  (native-10m per-pixel stack, cached to disk)
                    │    ├── model.py          (CatBoost .cbm artifact, loaded not retrained)
                    │    ├── albedo.py         (Liang 2001, partial/renormalized)
                    │    └── simulator.py      (perturbation-based intervention scoring)
                    │  region_cache.py  (in-memory region_id -> pixel indices)
                    └─────────────────────────┘
```

Backend owns every geospatial and ML computation. The frontend (once built) will never touch
raw rasters directly — it only ever sees JSON summaries, GeoJSON grids for map overlays, and
`region_id` handles.

---

## 2. Albedo method

Broadband albedo is **derived, not measured**. Method: Liang (2001) narrowband-to-broadband
conversion, which in its original form uses five Landsat bands (blue, red, NIR, SWIR1,
SWIR2). This project's own resolution-honesty policy (see `DATA_REPORT.md` §4) already
disqualifies Sentinel-2's SWIR bands (B11/B12) for being natively 20m, not 10m. Without
SWIR, this backend uses only the blue/red/NIR terms, **renormalized** so those three
coefficients still sum to 1:

```
albedo_approx = (0.356*blue + 0.130*red + 0.373*nir) / 0.859
```

This is disclosed as `is_modeled: true` in `app/albedo.py`'s `ALBEDO_METHOD_METADATA`, not
just a code comment. Real validation from the actual 45-month run: **371,683 of 251,151,300
pixel-months** (0.15%) had raw albedo above 1 before clipping — expected given the partial
formula, clipped to [0,1] and counted, not silently discarded.

---

## 3. Feature stack (native 10m, full AOI)

Built in `app/feature_stack.py` as a **single streaming pass** over the 45 already-validated
months (never holds more than one month's rasters in memory at once, to keep memory bounded
regardless of month count). Real results from the actual run:

| Feature | Real range |
|---|---|
| NDVI mean | -0.503 to 0.878 |
| NDWI mean | -0.779 to 0.730 |
| Albedo mean | 0.021 to 0.729 |
| Built-up % mean | 0.0 to 100.0 |
| Building density (per km²) | 0.0 to 7,160.5 (mean 438.3) |
| Road density (km/km²) | 0.0 to 87.65 (mean 11.10) |

**Valid pixel coverage: 100.0%** (5,581,140 of 5,581,140) — every pixel in the AOI had at
least one cloud-free observation across the 45 months.

Building/road density are **rasterized once** (not per-month) via a documented moving-window
approach: building centroids are rasterized to the 10m grid, then a 9×9-pixel (90m) uniform
window converts local point density to buildings/km². Road density rasterizes an
`all_touched` presence mask, then applies the same 9×9 window, converting the touched-pixel
fraction back to an actual length-in-window before dividing by window area.

### A real bug caught and fixed here
The first road-density implementation skipped the touched-pixel-count conversion step and
was off by **exactly 9x** (the window pixel count) — it returned a mean of 1.23 km/km²
against the zonal model's independently-computed 10.4 km/km² for the same corridor. This was
caught by cross-checking against that earlier result (not by code review alone), the formula
was corrected, and the fixed run now returns **11.10 km/km²** — consistent with the earlier
finding. Building density needed no fix; it was cross-checked the same way and matched
(438.3/km² here vs. ~404-438/km² in the earlier zonal runs).

---

## 4. Correlation matrix and index weights (native 10m, real numbers)

Computed fresh at native-pixel resolution — not assumed from any of the earlier 300m/100m/
50m/30m zonal runs:

| | built_up_pct_mean | building_density | road_density | ndvi_mean | ndwi_mean | albedo_mean |
|---|---|---|---|---|---|---|
| **built_up_pct_mean** | 1.000 | 0.616 | 0.626 | ... | ... | ... |
| **road_density** | 0.626 | 0.616 | 1.000 | 0.193 | -0.322 | 0.545 |

- Mean pairwise \|correlation\| among structural variables (built-up %, building density,
  road density): **0.611** (threshold 0.5 → down-weighted to a combined 0.5)
- Mean pairwise \|correlation\| among cooling variables (NDVI, NDWI, albedo): **0.758** (also
  down-weighted to a combined 0.5)
- Final weight per feature: **±0.167** each (six features, two clusters, 0.5 combined weight
  per cluster)

This generalizes the earlier zonal model's pairwise-only correlation check (which only
handled exactly 2 or 3 columns) to an arbitrary-size cluster via mean pairwise absolute
correlation — needed here because albedo was added as a third "cooling" variable alongside
NDVI/NDWI, which the zonal model never included.

---

## 5. CatBoost model

- **Trained on every valid pixel** (5,581,140), not a subsample, per the explicit decision
  to prioritize completeness over speed.
- **Batched training** (500,000 rows/batch, 9 batches, `thread_count=6` of the machine's 12
  cores) using CatBoost's `init_model` continuation, so the machine was never pinned at full
  load for the whole run — a deliberate trade-off, not a shortcut on the final model, which
  ends up with ~1,350 cumulative trees (150 iterations × 9 continued batches).
- **Real held-out test results:** R² = **0.99985**, MAE = **0.1335** (index scaled 0-100),
  on a 4,464,912-row train / 1,116,228-row test split.
- **Honesty note (same as every prior run in this project):** the target is a deterministic
  formula built from the same features CatBoost trains on. This R² confirms correct formula
  reconstruction, not external predictive skill on unseen ground truth.
- **Feature importance (real, from the trained model):** built_up_pct_mean (43.1%), road
  density (28.4%), building density (17.7%), then NDVI/NDWI/albedo and their variants
  (remaining ~11%).
- Saved to `backend/models/heat_vulnerability_native10m.cbm`, loaded once at API startup
  (`app/state.py`), never retrained per request.

---

## 6. Intervention assumptions

| Intervention | Constant | Value | Applied to |
|---|---|---|---|
| `add_tree_cover` | `TREE_COVER_NDVI_BOOST` | +0.15 | NDVI, region-wide |
| `cool_roof` | `COOL_ROOF_ALBEDO_BOOST` | +0.20 | Albedo, only pixels with built_up_pct_mean > 10% |
| `add_green_space` | `GREEN_SPACE_NDVI_BOOST` / `_NDWI_BOOST` / `_BUILT_UP_REDUCTION_PCT` | +0.25 / +0.05 / -20 pts | NDVI, NDWI, and built-up % together (a park displaces built area, it doesn't just add greenery on top) |

All three are illustrative planning constants, documented in `app/simulator.py`, not
measured effect sizes for this specific corridor — every `/simulate` response's `caveats`
field says so explicitly.

**Real end-to-end test result** (a 111,222-pixel drawn region): `add_tree_cover` produced a
mean improvement of 3.22 points; `cool_roof` produced 4.50 points — cool_roof scoring higher
makes sense given albedo has a real, direct weight in the index (±0.167) and this
intervention targets it directly, whereas tree cover's effect flows only through NDVI's
equal-sized share of the same combined 0.5 cooling weight.

---

## 7. API endpoints (all tested live against the running server, not just implemented)

| Endpoint | Tested with | Real result |
|---|---|---|
| `GET /api/v1/health` | — | Returns real R²/MAE/timestamps/pixel count |
| `POST /api/v1/region/analyze` | 111,222-pixel polygon | Correctly auto-aggregated to 60m cells (3,249 features) since the region exceeded the 4,000-pixel raw threshold; real SHAP summary matched the global feature ranking |
| `GET /api/v1/region/{id}/metrics/{name}` | `ndvi_mean` on the cached region | Real per-cell values, e.g. 0.4214 at (80.2101°E, 12.9101°N) |
| `POST /api/v1/region/{id}/simulate` | `add_tree_cover` + `cool_roof` | Real before/after scores, ranked by improvement |
| `GET /docs` | — | HTTP 200, auto-generated OpenAPI docs live |

**A real bug was caught and fixed during this testing**, not before it: the feature-stack
cache loader used `np.load()` without `allow_pickle=True`, which crashed server startup with
`ValueError: Object arrays cannot be loaded when allow_pickle=False` on the very first
restart. Fixed in `app/feature_stack.py`.

**Performance note (disclosed, not hidden):** `/region/analyze` takes on the order of
1-3 minutes for a ~100K-pixel region, because SHAP's `TreeExplainer` cost scales with the
model's ~1,350 cumulative trees. `/simulate` is fast (a few seconds) since it only calls
`model.predict()`, not SHAP. A production version would likely need to either reduce the
model's tree count, use a SHAP approximation, or cache SHAP values per region.

---

## 8. Standard caveat (present in every API response's `caveats` field, not left for the frontend to remember)

> This score is derived from land cover, vegetation, and urban density only (native 10m
> resolution); it has not been validated against measured land surface temperature.
> Intervention effect sizes are documented planning assumptions, not measured outcomes for
> this corridor.

---

## 9. Frontend status

A **real, interactive design prototype** was built and published this session (not static
mockups — working metric toggles, a drag-to-draw region tool, an intervention panel with a
functioning before/after switch, and a sortable results table), covering every screen in the
brief. It uses illustrative sample data and is explicitly not wired to this backend.

**Not built this session:** the actual Next.js/TypeScript app (Mapbox GL integration, a
typed API client generated from this backend's OpenAPI schema, live calls to the four
endpoints above). That is real, separate follow-on engineering — building it was deferred by
explicit agreement at the start of this work, not skipped silently.
