# Chennai UHI three-stage geospatial pipeline

Reproducible extractor → validator → cleaner for a citywide urban heat island
study of **Chennai, Tamil Nadu, India**.

**Nothing from the extractor is analysis-ready.** Only the CLEANER’s outputs for
layers that **PASSED** every VALIDATOR check count as clean data.

## Project rules

| Constraint | Implementation |
|---|---|
| Time window `2024-01-01` → present | `present_date_utc()` at runtime; never a hardcoded end date. Source lag recorded as `last_available_date`. |
| Full Chennai extent | Authoritative AOI from GADM (fallback: OSM Nominatim admin boundary). Polygon clip, not bbox-only. |
| ≤30 m rasters, shared grid | `ReferenceGrid` locked once in EPSG:32644; finer natives (e.g. WorldCover 10 m) kept finer with aligned origin. |
| CRS EPSG:32644 | Cleaner reprojects everything; final outputs never left in 4326. |
| No building height / H–W | Explicit design exclusion + coverage gate (default 90%). Geometry proxies: footprint density, compactness, street width. |
| One sensor family per variable | Landsat 8/9 C2 L2 only for LST/NDVI/NDBI — no MODIS blend. Cross-sensor check fails mixed families. |

## Stages

### 1 — EXTRACTOR
Fetches and logs (JSON) every pull: source, query params, resolved URL, timestamp, data dates.

- **LST / NDVI / NDBI** — Landsat 8/9 Collection 2 L2 via Microsoft Planetary Computer STAC; monthly median composites with `QA_PIXEL` cloud/shadow mask
- **DEM + slope** — Copernicus GLO-30 (static, once)
- **Buildings / roads** — pinned Geofabrik OSM `.pbf` + `.md5`
- **Land cover** — ESA WorldCover
- **Population** — WorldPop closest release year (may fail Stage 2 if native res > 30 m)
- **Air temperature** — Open-Meteo ERA5 archive for **exact** Landsat acquisition dates

### 2 — VALIDATOR
Every layer gets: provenance allow-list, checksum (or explicit `SKIPPED`), temporal coverage (gap months listed individually), spatial coverage vs 90% threshold, CRS/resolution audit, cross-sensor consistency, statistical sanity. Any `FAIL` → `data/rejected/`.

### 3 — CLEANER
Passed layers only → EPSG:32644 → shared grid → AOI polygon mask → small-gap nearest fill (r≤3 px) → `chennai_{var}_{unit}_{yyyy-mm}_{res}m.tif` + `_meta.json` sidecar → `CLEAN_DATASET_SUMMARY.json`.

## Work Completed and Remaining

### Completed so far

- Stage 1 extraction is complete. The pipeline has recorded provenance for the Chennai AOI, Landsat LST/NDVI/NDBI, DEM, OSM buildings and roads, population, land cover, and Open-Meteo air-temperature inputs.
- Stage 2 validation is complete for the current data version. Provenance, allowlists, checksums, dates, AOI coverage, CRS, resolution, sensor consistency, structure, and value-range checks are implemented. Products below the 90% coverage threshold are rejected rather than silently repaired.
- Stage 3 cleaning is complete. Accepted products are reprojected to `EPSG:32644`, aligned to the locked 30 m grid, clipped to the Chennai polygon, gap-filled only within the configured three-pixel limit, and written with metadata sidecars.
- The modeling manifest is locked to 22 common healthy months and records 66 monthly raster references, six static layers, and the required weather joins.
- Stage 4 has a working feature-builder implementation and a 150 m analysis-cell grid. A complete-case feature table is available with 194,718 rows across 22 months and zero nulls in required fields; 99,312 incomplete cell-month rows were excluded without imputation. The full remote rebuild remains unfinished.

### Implemented modules

| Module | Responsibility |
|---|---|
| [`src/chennai_uhi/config.py`](src/chennai_uhi/config.py) | Load and validate YAML configuration. |
| [`src/chennai_uhi/aoi.py`](src/chennai_uhi/aoi.py) | Resolve, store, and validate the Chennai area of interest. |
| [`src/chennai_uhi/grid.py`](src/chennai_uhi/grid.py) | Define the locked 30 m reference grid in `EPSG:32644`. |
| [`src/chennai_uhi/extractor/`](src/chennai_uhi/extractor/) | Extract Landsat, DEM, land cover, OSM, population, and weather data with manifests and logs. |
| [`src/chennai_uhi/validator/`](src/chennai_uhi/validator/) | Run standard and standalone validation checks and produce rejection reports. |
| [`src/chennai_uhi/cleaner/`](src/chennai_uhi/cleaner/) | Reproject, align, mask, gap-fill, and summarize validator-passed products. |
| [`src/chennai_uhi/cli.py`](src/chennai_uhi/cli.py) | Expose the pipeline stages through the `chennai-uhi` command. |
| [`scripts/build_stage4_features.py`](scripts/build_stage4_features.py) | Build the 150 m cell grid and assemble the modeling feature table. |
| [`scripts/build_modeling_manifest.py`](scripts/build_modeling_manifest.py) | Create and validate the locked modeling manifest. |
| [`scripts/build_extractor_report.py`](scripts/build_extractor_report.py) | Build extractor inventory and provenance reports. |
| [`tests/test_core.py`](tests/test_core.py) | Cover core configuration, grid, and validation behavior. |

### What needs to be done next

1. Finish the full Stage 4 remote retrieval and compare it with the complete-case table.
2. Validate the final table for the exact manifest month set, deterministic cell IDs, expected row scope, grid geometry, CRS, scene-date joins, required columns, null rates, and coverage exclusions.
3. Keep the synthetic population label on every affected row and document the excluded cell-months.
4. Train and evaluate separate models for each typology. Record metrics, feature importance, and uncertainty rather than producing one unqualified city-wide model.
5. Run SHAP analysis and generate cell-level predictions and heat-risk rankings.
6. Produce the final decision-support deliverables, including maps, tables, methodology, limitations, and reproducibility details.

Model training, SHAP analysis, prediction, ranking, and final deliverable generation have not started yet. See [`STATUS_REPORT.md`](STATUS_REPORT.md) and [`DATA_STATUS_REPORT.md`](DATA_STATUS_REPORT.md) for the detailed current state and validation evidence.

## Setup

```bash
cd chennai_uhi_pipeline
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
# Recommended for OSM PBF:
pip install pyrosm
```

Network access is required for Planetary Computer, Geofabrik, GADM/Nominatim, WorldPop, and Open-Meteo.

## Run

```bash
# Full sequence
python scripts/run_pipeline.py all -v

# Or stage-by-stage
python scripts/run_pipeline.py extractor -v
python scripts/run_pipeline.py validator -v
python scripts/run_pipeline.py cleaner -v

# Validate existing raw files without running the extractor
python scripts/run_pipeline.py standalone-validator
```

After install: `chennai-uhi all -v`

## Outputs

```
data/
  aoi/           chennai_boundary.geojson + reference_grid.json
  raw/           extractor products (not for analysis)
  rejected/      layers that failed any validator check
  clean/         analysis-ready GeoTIFFs + *_meta.json + CLEAN_DATASET_SUMMARY.json
  logs/
    extractor_fetch_log.json
    extractor_manifest.json
    validation_report.json
    standalone_validation_report.json
    STANDALONE_VALIDATION_REPORT.md
    validator_passed_manifest.json
    cleaner_summary.json
```

## Current exclusions

- **Building height / H–W ratio** — prior validation: &lt;5% real OSM height tags; Microsoft height layer placeholders for this region. Logged in validator design exclusions and cleaner summary.
- **Any layer &lt;90% real coverage** over the full Chennai polygon — excluded (not gap-filled to look complete).
- **WorldPop (~100 m)** — fails the raw ≤30 m resolution audit unless a finer population product is configured.

## Config

- `config/settings.yaml` — CRS, grid, coverage threshold, sources, gap-fill
- `config/allowlist.yaml` — authoritative hosts per data type
