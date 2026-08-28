# Chennai UHI Pipeline - Data and Logs Status Report

**Report date:** 2026-08-28  
**Project:** Chennai Urban Heat Island Pipeline  
**Study area:** Chennai, Tamil Nadu, India  
**Root:** `D:\UrbanCool\new\chennai_uhi_pipeline`

This report records the current state of the data directories, validation and cleaning logs, locked modeling manifest, and Stage 4 output attempts. It is separate from the broader project status report.

## Current Overall State

| Area | Status |
|---|---|
| Raw extraction artifacts | Present and inventoried |
| Validation | Complete for the current Stage 3 data version |
| Cleaning | Complete; 75 clean outputs |
| Modeling manifest | Locked and authoritative |
| Stage 4 processing | In progress; not yet successfully completed |
| Stage 4 final table | Not yet verified; current table is provisional |
| Model training | Not started |

Two Python processes, created at approximately 00:59 on 2026-08-28, are currently invoking `scripts/build_stage4_features.py`. They are network-bound while retrieving auxiliary Landsat reflectance data for albedo. The latest successful completion message has not been recorded.

## 1. Directory and Artifact Inventory

### Root-level reports

- `STATUS_REPORT.md`: consolidated project status.
- `DATA_STATUS_REPORT.md`: this data/log status report.

### `data/aoi/`

- `chennai_boundary.geojson`
- `chennai_boundary.gpkg`
- `chennai_boundary_fetch.json`
- `reference_grid.json`

The locked raster reference grid is 495 columns by 671 rows, 30 m, `EPSG:32644`, origin `E409950/N1452060`, with bounds `409950, 1431930, 424800, 1452060`.

### `data/raw/`

Raw extractor products are present under:

- `data/raw/landsat/`
- `data/raw/dem/`
- `data/raw/osm/`
- `data/raw/population/`
- `data/raw/weather/`
- `data/raw/landcover/`

The Landsat directory contains 96 monthly raster artifacts: 32 months x 3 variables (`LST`, `NDVI`, and `NDBI`). Every configured month from 2024-01 through 2026-08 has the three raw products. Raw artifacts remain subject to validation and must not be used as an unrestricted modeling input.

The weather raw file is `data/raw/weather/chennai_airtemp_era5_daily.json`. It contains Open-Meteo ERA5 temperature records and does not contain wind fields.

### `data/clean/`

The clean directory contains the accepted Stage 3 outputs:

- 22 accepted monthly LST rasters.
- 23 individually accepted NDVI rasters.
- 23 individually accepted NDBI rasters.
- Static DEM and slope rasters.
- Building-density raster.
- Building footprints and road-network GeoPackages.
- Population raster and fallback metadata.
- Clean air-temperature JSON and metadata.
- `CLEAN_DATASET_SUMMARY.json`.

The aggregate cleaner summary reports `n_clean_outputs=75` and `all_30m_layers_pixel_aligned=true`.

### `data/rejected/`

Rejected validation records are stored as JSON. The directory currently contains 67 files, including detailed rejection records and monthly coverage exclusions. The authoritative standalone rejection count is 28 raw Landsat artifacts; the larger directory file count includes related rejection records and non-standalone exclusions.

### `data/features/`

Current files:

- `cell_grid_150m.gpkg`
- `chennai_uhi_feature_table.parquet`
- `FEATURE_TABLE_QUALITY.json`
- `FEATURE_TABLE_REPORT.md`

The grid file was refreshed by the active Stage 4 attempt. The Parquet, JSON quality record, and Markdown report were last written at approximately 00:09 by the earlier provisional run and should not be treated as final until the active Stage 4 process completes successfully.

## 2. Validation Log Status

### Standard validation

Source: `data/logs/validation_report.json`

- Records evaluated: 141.
- Passed: 77.
- Rejected: 32.
- Coverage threshold: 90% valid AOI coverage.
- Target CRS: `EPSG:32644`.

### Standalone validation

Sources:

- `data/logs/standalone_validation_report.json`
- `data/logs/STANDALONE_VALIDATION_REPORT.md`

- Artifacts examined: 103.
- Usable with caveats: 75.
- Rejected: 28.
- All 28 standalone rejections are Landsat coverage failures.
- No remaining NDVI, NDBI, or LST statistical-range violations.

The ten rejected month groups are:

```text
2024-06, 2024-10, 2024-11, 2024-12, 2025-05,
2025-09, 2025-10, 2025-11, 2026-02, 2026-07
```

The 2024-12 NDVI and NDBI files pass individually, but the month is excluded because its LST coverage is only 69.695%.

## 3. Clean Dataset Log Status

Source: `data/clean/CLEAN_DATASET_SUMMARY.json`

Verified properties:

- 75 clean outputs.
- 30 m cell size.
- `EPSG:32644`.
- Dimensions `495 x 671`.
- Origin `E409950/N1452060`.
- Zero detected grid drift.
- Population sidecar identifies `synthetic_fallback=true` and resolution 30 m.
- Rejected monthly products were not copied or silently interpolated into clean output.

The clean summary records exclusions for:

- 28 Landsat monthly artifacts.
- Building compactness at approximately 63.45% coverage.
- Street width at approximately 60.87% coverage.
- Land cover allowlist failure.
- Building height and H-W design exclusions.

## 4. Locked Modeling Manifest Status

Source: `data/logs/MODELING_READY_MANIFEST.json`

The manifest is locked on 2026-08-27 and contains exactly 22 common healthy months:

```text
2024-01, 2024-02, 2024-03, 2024-04, 2024-05, 2024-07,
2024-08, 2024-09, 2025-01, 2025-02, 2025-03, 2025-04,
2025-06, 2025-07, 2025-08, 2025-12, 2026-01, 2026-03,
2026-04, 2026-05, 2026-06, 2026-08
```

Manifest integrity state:

- 66 monthly raster references.
- 6 static layer references.
- 92 total weather records.
- 72 weather scene dates required by the common-month set.
- 0 missing required weather dates.
- 0 raster-grid mismatches.
- All referenced files exist.
- `stage4_started` in the manifest remains `false`; this field has not been rewritten by the current builder.

## 5. Stage 4 Processing Log

### Intended inputs

The builder reads monthly and static clean paths from the locked manifest. It creates a nominal 150 m grid from 5x5 blocks of the 30 m reference grid. The source dimensions produce a nominal 135 x 99 layout, or 13,365 cells, with a partial final source block because 671 rows is not divisible by five.

### Auxiliary inputs

The locked manifest does not contain monthly reflectance-band references or wind variables. The current approved implementation therefore retrieves:

- Landsat blue, red, NIR, and SWIR1 reflectance from Planetary Computer for albedo.
- Open-Meteo ERA5 wind speed and dominant wind direction at the AOI centroid.

These auxiliary retrievals are explicitly outside the manifest's stored references and are documented in `scripts/build_stage4_features.py` and the Stage 4 quality report.

### Processing attempts

1. The original placeholder builder wrote a cell-level compressed CSV, used an incorrect adjacency-based sky-view approximation, left albedo and wind null, and did not build the requested Parquet/grid product.
2. A replacement builder successfully wrote a provisional table with 294,030 rows, 13,365 cells, and 22 months. It contained unexpected null rates of approximately 33% in several required fields and was not accepted as final.
3. A subsequent attempt failed during typology assignment because the road-cell array was not using the same AOI support mask. This was fixed.
4. The next attempt failed during remote reflectance reading because integer masked arrays were filled with `NaN` before float conversion. This was fixed.
5. The next attempt was optimized to read AOI windows from remote COGs and then encountered the same month-level failure because AOI bounds were being applied in the wrong CRS. That was diagnosed and corrected.
6. The current attempt uses the corrected remote-window logic, is still active, and has not yet produced a verified final table/report. The active process has not reported a new exception in the current execution snapshot.

### Current provisional Stage 4 artifact measurements

The provisional report records:

- Rows: 294,030.
- Expected provisional product: 13,365 cells x 22 months = 294,030.
- Cell layout: 135 x 99.
- LST null rate: approximately 33.52%.
- NDVI null rate: approximately 33.51%.
- NDBI null rate: approximately 33.51%.
- Building-density null rate: approximately 33.34%.
- Population/elevation/slope null rates: approximately 33.34%.
- Albedo null rate: approximately 1.05%.

These nulls are why the provisional table is not accepted as the final Stage 4 product. The corrected builder is intended to retain only AOI-supported complete cell-month rows and log exclusions instead of silently filling them.

## 6. Feature and Data Limitations

- Population is a deterministic synthetic fallback, not measured population data.
- Building compactness and street width remain excluded for sub-90% coverage.
- Ten Landsat month groups remain unavailable because of genuine cloud/monsoon scarcity.
- Sky-view factor is a footprint-coverage proxy, not true hemispherical SVF because height data were excluded.
- Wind is city-wide per date because Open-Meteo was queried at one AOI centroid.
- Typology is a deterministic heuristic using density, NDBI, and OSM roads, not a validated land-use classification.
- Prior model results based on the old OSM snapshot are not directly comparable with current rebuilt derivatives.
- Albedo and wind require auxiliary source retrieval because they were not included in the locked manifest.

## 7. Readiness and Stop Conditions

| Deliverable | Current state |
|---|---|
| Raw data | Available and logged |
| Clean data | Complete and verified |
| Locked manifest | Complete and authoritative |
| 150 m cell grid | Present, latest attempt refreshed it |
| Final feature table | Not verified; provisional Parquet exists |
| Feature quality report | Not final; provisional report exists |
| Model training | Not started |
| Prediction/ranking | Not started |

Stage 4 is not yet complete. The project is not ready for model training until the active process completes and the final outputs pass:

- Exact manifest month-set check.
- Expected cell-month row-count check after documented exclusions.
- Deterministic `cell_id` check.
- Grid geometry and CRS check.
- Scene-date and weather join check.
- Required-column and null-rate check.
- 90% coverage-gate audit for albedo and other newly derived features.
- Explicit `synthetic_fallback` population label on every row.

## 8. Relevant Logs and Reports

- [extractor_fetch_log.json](data/logs/extractor_fetch_log.json)
- [extractor_manifest.json](data/logs/extractor_manifest.json)
- [validation_report.json](data/logs/validation_report.json)
- [standalone_validation_report.json](data/logs/standalone_validation_report.json)
- [STANDALONE_VALIDATION_REPORT.md](data/logs/STANDALONE_VALIDATION_REPORT.md)
- [validator_passed_manifest.json](data/logs/validator_passed_manifest.json)
- [cleaner_summary.json](data/logs/cleaner_summary.json)
- [CLEAN_DATASET_SUMMARY.json](data/clean/CLEAN_DATASET_SUMMARY.json)
- [MODELING_READY_MANIFEST.json](data/logs/MODELING_READY_MANIFEST.json)
- [MODELING_READY_REPORT.md](data/logs/MODELING_READY_REPORT.md)
- [STAGE3_CLOSURE_REPORT.md](data/logs/STAGE3_CLOSURE_REPORT.md)
- [REMEDIATION_REPORT.md](data/logs/REMEDIATION_REPORT.md)
- [build_stage4_features.py](scripts/build_stage4_features.py)

**Final statement:** Stage 1-3 data are verified and available. Stage 4 remains in progress with provisional outputs only. No model training has been performed.
