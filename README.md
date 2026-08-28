# Chennai UHI three-stage geospatial pipeline

Production-grade extractor → validator → cleaner for a citywide urban heat island
study of **Chennai, Tamil Nadu, India**.

**Nothing from the extractor is analysis-ready.** Only the CLEANER’s outputs for
layers that **PASSED** every VALIDATOR check count as clean data.

## Hard constraints (enforced in code)

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

## Deliberate exclusions

- **Building height / H–W ratio** — prior validation: &lt;5% real OSM height tags; Microsoft height layer placeholders for this region. Logged in validator design exclusions and cleaner summary.
- **Any layer &lt;90% real coverage** over the full Chennai polygon — excluded (not gap-filled to look complete).
- **WorldPop (~100 m)** — fails the raw ≤30 m resolution audit unless a finer population product is configured.

## Config

- `config/settings.yaml` — CRS, grid, coverage threshold, sources, gap-fill
- `config/allowlist.yaml` — authoritative hosts per data type
