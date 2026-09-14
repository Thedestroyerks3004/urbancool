# Urban Cool (Chennai) — Complete Data Report

**Generated:** 2026-09-13 (updated same day — added §5, the Heat Vulnerability Model runs)
**Project:** AI-based urban heat mitigation system, Chennai Metropolitan Area
**Current study area:** Anna University → OMR-ECR corridor
**Bounding box:** 80.15°E–80.30°E, 12.75°N–13.05°N (single Sentinel-2/ECOSTRESS tile: 44PMV, UTM zone 44N)
**Requested timeframe:** 2021-01-01 to 2026-08-31 (monthly cadence)
**Resolution policy:** 10m-or-finer required; no downscaling, no interpolation, no coarser silent substitutes. Any exception is disclosed explicitly, never presented as native.

This report supersedes all earlier interim status reports in `unnecessary/data-audit/`. It reflects the actual, verified state of every file on disk as of this writing — every dataset below was independently opened and health-checked, not assumed from historical logs.

---

## 1. Study area history (for reproducibility)

The AOI was narrowed once during this project, from an original Chennai-wide box (79.999–80.301°E, 12.700–13.201°N, spanning two Sentinel-2 tiles 44PLV/44PMV) down to the current Anna University–OMR-ECR corridor box, which fits entirely within one tile (44PMV, confirmed 100% coverage). All data reported below is under the **current, narrower AOI** unless stated otherwise. Data computed under the old AOI was deleted during this project (~463MB removed) as it was no longer applicable.

---

## 2. Kept datasets

### 2.1 Sentinel-2 native-10m optical bands (B2 Blue, B3 Green, B4 Red, B8 NIR)
- **Status:** ✅ KEEP — 45 of 68 months
- **Provider:** ESA/Copernicus, Sentinel-2 L2A (`COPERNICUS/S2_SR_HARMONIZED`)
- **Access method:** Google Earth Engine (authenticated project `urban-heat-mitigation-502708`)
- **True native resolution:** 10m — B2/B3/B4/B8 are natively sensed at 10m, no upsampling in the chain (SWIR B11/B12 deliberately excluded — see §4)
- **Method:** one real single-date scene per month (the least-cloudy available for that month), not a multi-date blended composite. Selected by Sentinel-2's own `CLOUDY_PIXEL_PERCENTAGE`, then independently re-verified via a real SCL-band pixel count over the AOI before acceptance.
- **Validation thresholds:** ≥80% independently-computed cloud-free pixels over the AOI; ≥90% valid (non-nodata) pixels; NDVI computed from the bands must fall in the physically valid [-1, 1] range.
- **Kept months:** 2021-03, 05, 06, 09, 10, 12; 2022-02, 03, 04, 05, 06, 08, 12; 2023-01 through 10; 2024-03, 04, 08, 09, 10; 2025-02 through 10, 12; 2026-01 through 07 (45 total)
- **Dropped months (23):** genuine monsoon-driven cloud cover, concentrated Oct–Jan each year (e.g. a 4-month gap Nov 2023–Feb 2024)
- **File locations:** raw `data/raw/sentinel2_10m_monthly_least_cloudy/YYYY-MM/`, validated `data/validated/sentinel2_10m_monthly_least_cloudy/YYYY-MM/sentinel2_10m_least_cloudy_YYYY-MM.tif`
- **Size:** 1.5GB raw + 1.5GB validated (duplicate copies)
- **Health check:** 45/45 files open cleanly, correct dimensions, non-empty

### 2.2 NDVI, monthly (derived, no new fetch)
- **Status:** ✅ KEEP — 45 of 45 available months (100% of what's fetchable given §2.1)
- **Derivation:** computed directly from the already-validated Sentinel-2 bands above (NIR−Red)/(NIR+Red); no additional Earth Engine calls
- **Validation:** physical range check (−1 to 1) per month; all 45 passed
- **File location:** `data/validated/ndvi_10m_monthly/ndvi_10m_YYYY-MM.tif` (flat structure, one file per month)
- **Size:** 794MB

### 2.3 Water body / NDWI, monthly (derived, no new fetch)
- **Status:** ✅ KEEP — 45 of 45 available months
- **Derivation:** (Green−NIR)/(Green+NIR) from the same validated bands
- **Validation:** physical range check (−1 to 1); all 45 passed
- **File location:** `data/validated/water_ndwi_10m_monthly/ndwi_10m_YYYY-MM.tif`
- **Size:** 791MB
- **Note:** this supersedes an earlier Track-A attempt (150m grid, old AOI) that was formally DROPped — even after a full threshold-calibration sweep, IoU against a reference marsh boundary topped out at 0.199 against a required 0.60. That earlier result is preserved in `unnecessary/data-audit/pipeline-status-report.md` for reference; it does not apply to the current native-10m, single-date approach, which was not re-validated against a reference marsh boundary and should be treated as an uncalibrated NDWI signal.

### 2.4 Land Use / Land Cover (LULC), monthly, native-10m classifier
- **Status:** ✅ KEEP — 45 of 45 available months
- **Method:** Random Forest classifier trained on native-10m bands only (B2, B3, B4, B8 — **not** the 6-band version used earlier in this project, which included disqualified 20m SWIR bands)
- **Training data:** OpenStreetMap reference polygons (built_up, vegetation, water tags) sampled for the current AOI, real band values pulled via Earth Engine `sampleRegions`
- **Classifier accuracy:** real held-out test-set accuracy, must be ≥75% (project threshold) — passed
- **Classes:** built_up (0), vegetation (1), water (2)
- **Applied to:** each of the 45 months' real band rasters individually (not one classifier applied to a single composite)
- **File location:** `data/validated/lulc_10m_monthly/YYYY-MM/lulc_10m_YYYY-MM.tif`
- **Size:** 32MB

### 2.5 Land Surface Temperature (LST) — two separate, disclosed-resolution sources

**This is intentionally NOT one blended dataset.** Landsat and ECOSTRESS have different true native resolutions (30m vs 70m) and are kept as two separate file sets so no user of this data can accidentally treat them as uniform-resolution.

#### 2.5a Landsat 8/9 Collection 2 Level-2 LST
- **Status:** ✅ KEEP — 20 of 68 months
- **Provider:** USGS/NASA, accessed via Google Earth Engine (`LANDSAT/LC08/C02/T1_L2`, `LANDSAT/LC09/C02/T1_L2`)
- **True native resolution:** 30m (the Collection 2 Level-2 distributed Surface Temperature product; the TIRS sensor itself senses at 100m — USGS co-registers to 30m using the reflective bands, a registration choice, not new thermal information — disclosed here, not hidden)
- **Method:** per month, all Landsat 8+9 candidate scenes ranked by AOI-clipped cloud-free percentage (computed from the real QA_PIXEL band over the AOI, not whole-scene metadata); each candidate's LST band is masked pixel-by-pixel for cloud/cirrus/cloud-shadow/dilated-cloud (QA_PIXEL bits) *before* any statistic is computed; if a candidate fails validation, the next-best candidate is tried automatically
- **Validation thresholds:** ≥80% AOI cloud-free; ≥90% valid pixels after masking; LST in [15°C, 55°C] plausible range
- **Kept months:** 2021-02, 03, 12; 2022-05, 10; 2023-02, 03, 04; 2024-01, 03, 09; 2025-02, 03, 06, 07, 12; 2026-03, 04, 05, 06 (20 total)
- **2026-07 and 2026-08 dropped** with an explicit flag that USGS Collection 2 processing latency is a plausible contributing factor for these most-recent months, not necessarily a confirmed cloud gap
- **File location:** `data/validated/lst_30m_monthly/YYYY-MM/lst_30m_YYYY-MM.tif`
- **Size:** 38MB validated, 84MB raw

#### 2.5b ECOSTRESS L2T LSTE V2
- **Status:** ✅ KEEP — 29 of 68 months
- **Provider:** NASA/JPL, accessed via Google Earth Engine (`NASA/ECOSTRESS/L2T_LSTE/V2`)
- **Access history:** originally specified as the project's PRIMARY LST source, initially believed blocked because it is normally distributed via NASA Earthdata/AppEEARS, which requires a signup this environment does not have. **Mid-project, this exact product was found to also be hosted as a public, free, no-signup Earth Engine asset**, reachable through the same already-authenticated GEE connection used for everything else in this project. No NASA Earthdata account was ultimately needed.
- **True native resolution:** 70m (confirmed directly from the image's own Earth Engine projection metadata, `nominalScale()`)
- **Method:** same per-month, rank-then-validate-then-fallback-to-next-candidate pattern as Landsat, but tuned to this product's own bands: cloud masking via ECOSTRESS's own `cloud` band (not a bit-mask QA band like Landsat), ranking metric corrected mid-development after it was found to be misled by ECOSTRESS's fixed-tile granule structure (a granule can be 100% cloud-free by its own internal statistic while covering almost none of the AOI — fixed by requiring the ranking statistic to also account for whether the LST band has real data present, not just whether it's cloud-free where data exists)
- **Downloaded in each scene's own native UTM CRS (EPSG:32644) at 70m** — deliberately not reprojected to lat/lon, to avoid any resampling
- **Validation thresholds:** identical structure to Landsat (≥80% AOI-adjusted clear coverage, ≥90% valid pixels, 15–55°C plausible range)
- **Kept months:** 2021-01, 02, 03, 06, 08, 09, 11; 2022-01, 10, 11, 12; 2023-03, 04, 06, 10, 12; 2024-10, 11, 12; 2025-01, 02, 03, 04, 07, 11; 2026-01, 03, 04, 06 (29 total)
- **File location:** `data/validated/lst_70m_ecostress_monthly/YYYY-MM/lst_70m_ecostress_YYYY-MM.tif`
- **Size:** 4.8MB validated, 5.5MB raw

#### 2.5c Combined LST coverage picture
| Metric | Count |
|---|---|
| Landsat-only real coverage | 20 / 68 months (29%) |
| ECOSTRESS-only real coverage | 29 / 68 months (43%) |
| Either source real (union) | **38 / 68 months (56%)** |
| Both sources agree | 11 months |
| ECOSTRESS filled a month Landsat couldn't | 18 months |
| Landsat covered a month ECOSTRESS couldn't | 9 months |
| Neither source cleared validation | 30 months |

**Cross-cutting caveat for anyone using LST downstream:** only **25 of the 68 months** have *both* real optical (Sentinel-2/NDVI/NDWI/LULC) *and* real LST (either source) in the same calendar month. A combined per-month Heat Vulnerability / Effectiveness Score that requires every layer to align in the same month is therefore only computable for those 25 months, not the full 45 or 68. Recommended approach (not yet implemented): decouple LST's own trend/climatology from the slower-changing structural layers (LULC, buildings, roads) rather than forcing calendar-month alignment across all layers.

### 2.6 Building footprints
- **Status:** ✅ KEEP
- **Provider:** Microsoft Global ML Building Footprints (live dataset, URL confirmed current via the repository README at fetch time — Microsoft periodically relocates the index file)
- **Access method:** direct HTTPS download, no signup, quadkey-tiled (`bfppub.blob.core.windows.net`)
- **Format/resolution:** vector polygons; footprint precision reflects Microsoft's own source imagery, whose exact GSD is not published — genuinely finer than the 10m bar, not a grid-comparable number
- **Real count:** 747,092 buildings fetched for the AOI (originally fetched under the old, larger AOI; confirmed to fully contain the current narrower AOI, so no refetch was needed)
- **Validation:** density sanity check (404 buildings/km² at original scope), 13 invalid geometries out of 747,092 (0.002%, negligible)
- **File location:** `data/validated/building_footprints/building_footprints.geojson`
- **Size:** 262MB

### 2.7 Road network
- **Status:** ✅ KEEP — complete and truncation-free
- **Provider:** OpenStreetMap, via Overpass API (mirror: `maps.mail.ru`, with `overpass-api.de` and `overpass.kumi.systems` as fallbacks)
- **Format/resolution:** vector polylines, sub-meter geometric precision
- **Method:** fetched per road class (motorway/trunk/primary/secondary/tertiary/residential/unclassified/service/living_street), each class queried across the AOI split into recursively-subdividing quadrants — subdivision continues wherever Overpass's per-query result cap (5,000 elements) would otherwise silently truncate results, up to 4 levels deep
- **Real count:** 40,114 segments, 5,768km total length, density 10.4 km/km²
- **Validation:** minimum segment count, plausible density ceiling, zero invalid geometries found
- **History:** an earlier version of this fetch (27,241 segments) silently truncated residential/service roads at a hard-coded cap; this was caught, fixed with recursive subdivision, and confirmed truncation-free (`grep` for "STILL AT CAP" returns zero matches in the final run)
- **File location:** `data/validated/road_network/road_network.geojson`
- **Size:** 11MB

---

## 3. Formally dropped datasets (with real validation numbers)

| Dataset | Verdict | Real result |
|---|---|---|
| NDBI / built-up index (Sentinel-2 SWIR B11/B12) | DROP | Earth Engine confirms B11 native resolution = 20m via `projection().nominalScale()` — physically disqualified under the 10m-or-finer rule; not fetched further |
| Elevation / DEM (Copernicus GLO-30) | DROP | Confirmed native resolution = 30.92m; no public DEM finer than 30m exists for this region |
| Water body via JRC Global Surface Water (Track A, old AOI, 150m grid) | DROP | Best-achievable IoU after a full NDWI-threshold calibration sweep = 0.199 against a required 0.60 — the vegetated Pallikaranai marsh systematically defeats optical water detection at this scale (see `unnecessary/data-audit/` for full detail; superseded by the un-validated native-10m NDWI in §2.3) |
| LST via Landsat + TsHARP thermal sharpening to 10m | DROP | Real regression against 2,069,440 paired pixels: R² = 0.179 (initial full-window attempt) / 0.179 (retried), against a required 0.20 — the vegetation-temperature relationship in this corridor is too weak to justify manufacturing 10m thermal detail from a coarser source |
| Population density | DROP (no fetch attempted) | WorldPop is 100m-native and itself a modeled/dasymetric surface (not a direct measurement); no genuine 10m population source exists publicly. Not fetched under the 10m-only, no-downscaling policy. |
| Demographic vulnerability | DROP (no fetch attempted) | Same constraint as population; Census data is ward-polygon-level, not gridded at all |
| Wind speed/direction | DROP (no fetch attempted) | ERA5-Land is ~9km native (itself downscaled from a ~31km atmospheric model); no genuine 10m wind source exists publicly |
| Air temperature (ground-truth proxy) | DROP (no fetch attempted) | Would require the same LST downscaling already shown to fail (R²=0.179), or IMD/NOAA station regression already flagged in the earlier resolution-honesty audit as a weak validation (2-3 stations); not attempted under the no-downscaling policy |

---

## 4. Resolution-honesty summary

| Layer | Disclosed native resolution | Genuinely native, or disclosed exception? |
|---|---|---|
| Sentinel-2 optical / NDVI / NDWI / LULC | 10m | Genuinely native |
| Landsat LST | 30m (distributed product; sensor itself is 100m) | Disclosed distribution-vs-sensing distinction |
| ECOSTRESS LST | 70m | Genuinely native |
| Building footprints | Vector, sub-meter | Genuinely finer than 10m |
| Road network | Vector, sub-meter | Genuinely finer than 10m |

No dataset in the kept set is a downscaled, interpolated, or resampled product presented as native. Every KEEP decision above is backed by a real, printed, threshold-compared number generated by code that actually fetched and inspected the underlying pixels — none of it is assumed or estimated.

---

## 5. Heat Vulnerability Model (structural/vegetation-only, LST excluded)

Built on top of the datasets in §2 (Sentinel-2 NDVI/NDWI, LULC, building footprints, road network). LST was deliberately excluded from this model — it is a separate, later research question, not because LST data doesn't exist (§2.5 shows it does, for 38/68 months). Every run below computed a 0–100 Heat Vulnerability Index per zone from a weighted formula (built-up %, building density, road density push the score up; NDVI, NDWI pull it down), then trained a CatBoost regressor to explain/smooth that index and rank feature importance via SHAP.

**This was run four times, at four different zone sizes, to explicitly test how zone size changes the input correlations and the resulting index** — nothing was assumed to carry over from one resolution to the next; each run recomputed its own correlation matrix from scratch.

### 5.1 Real results across all four resolutions

| Zone size | Real zone count | Structural correlation (built-up %, building density, road density) | NDVI/NDWI correlation | CatBoost test R² | CatBoost test MAE |
|---|---|---|---|---|---|
| 300m | 6,036 | 0.818 | −0.982 | 0.9992 | 0.48 |
| 100m | 54,024 | 0.720 | −0.978 | 0.9994 | 0.39 |
| 50m | 215,961 | 0.622 | −0.975 | 0.9993 | 0.36 |
| 30m | 599,959 | 0.528 | −0.973 | 0.9993 | 0.37 |

**Two genuine, honest findings from running this at four scales instead of assuming one:**
- The three structural variables become *less* correlated as zones get finer (0.818 → 0.528) — a real physical effect, since individual buildings and roads decouple from each other at fine granularity, while at coarse granularity they average together. At 30m the correlation is now right at the edge of the 0.5 threshold used to decide whether to down-weight these variables.
- NDVI and NDWI stayed almost perfectly anti-correlated (~−0.97 to −0.98) at every single scale tested — this is a stable, scale-independent property of this corridor (in a landscape with little open water, the NDWI formula used here behaves mostly as an inverted greenness signal, not a true independent water-content measurement), not an artifact of any one aggregation size.

At every resolution, `built_up_pct_mean` was the single strongest driver of the score (40–45% of CatBoost's feature importance), followed by building density and road density.

**Honesty note on R² (applies to all four runs):** the target (the Heat Vulnerability Index) is a deterministic formula built directly from features CatBoost also trains on. An R² this high (~0.999 at every resolution) confirms CatBoost correctly reconstructed a known formula from its own inputs — it is a self-consistency check, not evidence of predictive skill on external, unseen ground truth.

### 5.2 Zone size reasoning (not assumed, tested/confirmed each time)
- **300m** was chosen over the originally-suggested 30m for that phase because 30m would have produced ~600,000 zones — impractical as a one-row-per-zone interpretable table at the time.
- **100m and 50m** were explicit user-requested rebuilds "at higher resolution," each confirmed against a 9-pixel-minimum stability floor before running (100 px/zone at 100m, 25 px/zone at 50m).
- **30m** was requested after a further ask for "10m" was flagged as producing ~5.5 million zones (1 pixel per zone, violating the stability floor entirely, and likely to exhaust memory or hang on the road-overlay and choropleth-render steps); 30m was accepted as the finest resolution that still respects the literal 9-pixel-minimum floor from the original spec. It ran successfully at 599,959 zones with only 4 zones falling below half the pixel-count safety margin.

### 5.3 Real bugs caught during this work (fixed before finalizing, not swept under the rug)
- **NDVI/NDWI correlation mislabeling:** the first weighting pass only checked for strong *positive* correlation between NDVI and NDWI and mislabeled a −0.982 correlation as "independent cooling mechanisms" — factually wrong for a value that strongly correlated (just inverted). Fixed to check `abs(correlation)`; the actual weight values were unaffected, only the narrative reasoning was corrected.
- **LULC trend computation was not vectorized:** an early version of the 100m/50m/30m scripts looped once per zone (up to ~600,000 times) doing a full-table pandas scan each time to compute the built-up-percentage trend slope — this stalled visibly at the 100m scale. Rewritten to a single vectorized groupby-based linear-regression computation; re-verified it produced identical zone counts and pixel statistics after the fix, confirming no results changed, only speed.
- **Choropleth basemap provider issues (two, both caught by actually viewing the rendered image, not just checking for a "success" log line):** the first render used OpenStreetMap's own raw tile server and returned real HTTP 200 responses that were actually "Access blocked — not following OSM's usage policy" placeholder tiles, stitched into the map as if they were real. A second attempt with CartoDB Positron returned tiles watermarked "API KEY REQUIRED." Both were caught by rendering and re-inspecting the image, not assumed to have worked. Settled on Esri World Street Map (verified via direct HTTP requests to return genuine ≥9KB image tiles before committing to it), which rendered cleanly at all four resolutions.

### 5.4 Outputs
Each resolution has its own complete, independent output folder — nothing is overwritten between runs:

| Resolution | Folder | Feature table | Monthly trend CSV | Scored GeoJSON |
|---|---|---|---|---|
| 300m | `model_output/` | `zone_features_spatial.csv` | `zone_month_optical.csv` | `zone_heat_vulnerability_map.geojson` |
| 100m | `model_output_100m/` | `zone_features_spatial_100m.csv` | `zone_month_optical_100m.csv` (453.6MB) | `zone_heat_vulnerability_scores_100m.geojson` |
| 50m | `model_output_50m/` | `zone_features_spatial_50m.csv` | `zone_month_optical_50m.csv` | `zone_heat_vulnerability_scores_50m.geojson` |
| 30m | `model_output_30m/` | `zone_features_spatial_30m.csv` (151.9MB) | `zone_month_optical_30m.csv` (**5.27GB**) | `zone_heat_vulnerability_scores_30m.geojson` (283.6MB) |

Each folder also contains its own `feature_importance_report*.csv`, `heat_vulnerability_choropleth*.png` (static map, real Esri basemap), and `heat_vulnerability_report*.md` (full writeup with that run's own correlation matrix and weight rationale).

**Explicit caveat, carried through every one of these outputs:** *this score is derived from land cover, vegetation, and urban density only; it has not been validated against measured land surface temperature.*

---

## 6. Known open items

1. **LST/optical month misalignment** (§2.5c) — only 25 of 68 months have both; the Effectiveness Score formula needs a decision on how to handle this (decouple cadences, recommended, vs. restrict to the 25-month intersection).
2. **NDWI in the current native-10m track has not been re-validated against a reference boundary** — the 0.199 IoU failure applies to the earlier 150m/old-AOI attempt; the current 45-month NDWI series is a real, physically-valid signal but its accuracy against ground truth (especially over the Pallikaranai marsh, if it falls within this narrower AOI) has not been independently re-checked.
3. **2026-07 and 2026-08 Landsat LST** are flagged as possibly latency-affected rather than confirmed cloud drops — worth re-running later once USGS Collection 2 processing catches up.
4. All disk paths above are absolute under `D:\Projects\UC\data\`; this report itself lives at `D:\Projects\UC\DATA_REPORT.md`.

---

## 7. Full folder map

> Folder map as of this report's writing. `pipeline\` and `data-audit\` have since moved
> under `unnecessary\` (see the project root README) to keep the main tree to just the
> running app; their contents are unchanged. `data\raw\` has since been removed (it was
> write-only staging, nothing reads it) -- only `data\validated\` remains.

```
D:\Projects\UC\
  DATA_REPORT.md                              <- this file
  unnecessary\
    data-audit\
      resolution-honesty-audit-2026.md        (earlier planning-stage audit)
      pipeline-status-report.md               (earlier interim status, partially superseded by this report)
    pipeline\                                  (all fetch/validate/decide/model scripts, one per dataset or model run)
  data\
    raw\        <- pre-validation downloads, kept for traceability
    validated\  <- only KEEP-decision outputs land here
      sentinel2_10m_monthly_least_cloudy\YYYY-MM\
      ndvi_10m_monthly\
      water_ndwi_10m_monthly\
      lulc_10m_monthly\YYYY-MM\
      lst_30m_monthly\YYYY-MM\
      lst_70m_ecostress_monthly\YYYY-MM\
      building_footprints\
      road_network\
  model_output\        <- Heat Vulnerability Model, 300m zones (§5)
  model_output_100m\   <- Heat Vulnerability Model, 100m zones (§5)
  model_output_50m\    <- Heat Vulnerability Model, 50m zones (§5)
  model_output_30m\    <- Heat Vulnerability Model, 30m zones (§5)
```
