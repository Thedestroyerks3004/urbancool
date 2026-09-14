# Urban Cool (Chennai) — Data Pipeline Status Report (historical)

> **Point-in-time snapshot.** Several scripts named below (early single-shot fetchers,
> the zonal 300m/100m/50m/30m model iterations, `fetch_worldcover_10m.py`) were later
> superseded by the final monthly pipeline and the native-10m model, and have since been
> removed from `pipeline/` to keep the repo to what the running app actually uses. See
> the [project root README](../README.md) for the current pipeline file list.

Two parallel tracks have been built and run with real fetch/validate/decide code (no simulated results). This report consolidates both as of the current session.

---

## Track A: 150m analysis grid pipeline (original spec)

Location: `D:\Projects\UC\pipeline\` | Window: narrowed from the originally requested 2015-2026 to **2019-2026** (2015-2018 documented as a real Sentinel-2 coverage gap, not silently dropped)

| # | Dataset | Fetch status | Validation result vs. threshold | Decision | Reason |
|---|---|---|---|---|---|
| 1 | NDVI (Sentinel-2) | Real fetch, 425 scenes | 100% of 2019-2026 seasons met minimum cloud-free scene count (required 70%) | **KEEP** | Real 150m composite, 100% valid pixels, NDVI in-range |
| 2 | LULC (Sentinel-2 + Random Forest) | Real fetch, 3-class OSM reference (600/585/369 polygons) | 76.1% held-out accuracy (required 75%) | **KEEP** | Passes, but by a narrow 1.1-point margin |
| 3 | Water body (Sentinel-2 NDWI) | Real fetch + calibration sweep | Best achievable IoU 0.199 vs. reference marsh boundary (required 0.60) | **DROP** | NDWI cannot resolve the vegetated Pallikaranai marsh even after full threshold calibration — confirms the earlier resolution-honesty audit's prediction |
| 4 | LST (Landsat 8/9, 150m grid) | Fetch was in progress when this track was paused in favor of the stricter 10m-only track (see Track B) | Not completed in this track | — | Superseded by Track B's findings before completion |
| 5-10 | Air temp proxy, population, demographic vulnerability, building footprint, road network, wind/ERA5-Land | Not yet built | — | — | Deprioritized once the 10m-only requirement was issued |

---

## Track B: strict 10m-or-finer track (current requirement — no coarser substitutes)

Location: same `pipeline/` folder, new scripts (`fetch_sentinel2_10m_native.py`, `fetch_worldcover_10m.py`, `fetch_building_footprints.py`, `fetch_lst_10m_downscaled.py`, `check_resolution_disqualifications.py`)

| Layer | Fetch status | Validation result vs. threshold | Decision | Reason |
|---|---|---|---|---|
| Sentinel-2 native bands (B2/B3/B4/B8 → NDVI) | Real fetch, 158 qualifying scenes, 16-tile mosaic | 100% valid pixels (required 90%), NDVI in-range | **KEEP** | Genuinely native 10m, no upsampling anywhere in the chain |
| ESA WorldCover v200 | Real fetch via GEE | Confirmed native 9.28m; 62.7% agreement vs. independent OSM reference points (required 60%) | **KEEP** | Passes, narrow 2.7-point margin |
| Building footprints (Microsoft Global ML) | Real live download, 66.9MB across 2 quadkey tiles | 747,092 footprints; density 404/km² (plausible); 13 invalid geometries out of ~747K | **KEEP** | Vector precision exceeds 10m; dataset URL had moved and was corrected mid-session |
| NDBI (Sentinel-2 SWIR B11/B12) | Real EE metadata check | Confirmed native 20m (required ≤10m) | **DROP** | Physically disqualified — not fetched further, no fabricated upsampling |
| Elevation/DEM (Copernicus GLO-30) | Real EE metadata check | Confirmed native 30.9m (required ≤10m) | **DROP** | No public DEM at 10m or finer exists for this region |
| LST — ECOSTRESS (primary) | Blocked | No NASA Earthdata credentials available | **DROP** | Fetch-blocked, not a resolution failure |
| LST — Landsat backup + TsHARP sharpening | Real fetch (95 candidate / 36 cloud-free scenes) + real regression (2,069,440 paired pixels) | R² = 0.179 (required ≥0.20); slope physically backwards (positive, LST rising with NDVI) | **DROP** | Relationship too weak to justify manufacturing 10m thermal detail; likely weakened by using multi-year median composites instead of per-date TsHARP |

**Net result for Track B: no 10m-native or defensibly-sharpened LST exists.** Three independent paths were tried (ECOSTRESS access, Landsat+TsHARP) and none cleared a legitimate bar.

---

## What is currently usable, right now, at genuine 10m

- Native 10m Sentinel-2 optical bands + NDVI
- ESA WorldCover 10m land cover
- Building footprints (vector, sub-meter precision)

## What is missing and unresolved

- **10m LST** — no path currently succeeds without either (a) NASA Earthdata credentials for real ECOSTRESS, or (b) retrying TsHARP per individual Landsat scene rather than on a multi-year composite (untried — flagged as the next thing to attempt if you want to keep pushing on this before falling back to a coarser LST grid).
- **NDBI / built-up index** — cannot exist at 10m from Sentinel-2's own SWIR bands; would need a different 10m-native SWIR-class sensor, none identified.
- **10m DEM** — does not exist as open data for this region at all.
- **Track A datasets 5-10** (air temperature proxy, population, demographic vulnerability, road network, wind) — not yet attempted in either track; population/demographics in particular are inherently coarser than 10m at the source (WorldPop 100m, Census polygons), so they would need their own resolution-honesty ruling before fetching.

## Immediate open question for you

The Effectiveness Score's ΔLST numerator has no 10m-compliant data source right now. Do you want me to (1) attempt the per-scene TsHARP retry, (2) wait for NASA Earthdata credentials, or (3) accept LST at a coarser grid as a stated exception while keeping everything else at 10m?
