# Chennai UHI Pipeline - Current Status Report

**Status date:** 2026-08-28  
**Study area:** Chennai, Tamil Nadu, India  
**Project root:** `D:\UrbanCool\new\chennai_uhi_pipeline`  
**Authoritative Stage 4 scope:** `data/logs/MODELING_READY_MANIFEST.json`

## Executive Status

Stages 1-3 are complete and verified for the current data version. Stage 4 feature engineering is currently in progress but has not completed successfully. The active process is retrieving Landsat surface-reflectance inputs required for albedo calculation. No model training, SHAP analysis, prediction, ranking, or per-typology model fitting has started.

The existing files under `data/features/` are provisional outputs from an earlier Stage 4 attempt. They must not be treated as the final feature table until the active run exits successfully and the outputs pass the final integrity checks.

## 1. Pipeline Architecture

```text
Extraction -> Unified Store -> Validation -> Feature Engineering -> Prediction / Ranking / Deliverable
```

- **Extraction:** fetches and derives remote-sensing, terrain, weather, population, land-cover, and OSM products with provenance metadata.
- **Unified Store:** keeps raw artifacts, clean artifacts, rejected records, sidecars, manifests, and reports under `data/`.
- **Validation:** checks provenance, allowlists, checksums, dates, AOI coverage, CRS, resolution, sensor consistency, structure, and variable ranges.
- **Feature Engineering:** converts the locked clean inputs into a common analysis-cell-by-month table.
- **Prediction / Ranking / Deliverable:** future per-typology modeling and decision-support outputs. This stage has not started.

The project uses per-typology modeling rather than one global model. It does not assume a single city-wide solution. Source family, temporal scope, sensor family, CRS/grid, and pipeline level must remain consistent.

## 2. Stage 1 - Extraction

### Final sources

| Data | Final source and use |
|---|---|
| AOI | GADM Chennai administrative boundary, with OSM Nominatim documented as fallback |
| LST, NDVI, NDBI | Landsat 8/9 Collection 2 Level-2 through Microsoft Planetary Computer STAC |
| DEM | Copernicus GLO-30 through Planetary Computer |
| OSM | Geofabrik Southern Zone PBF with published MD5 |
| Weather | Open-Meteo historical ERA5 archive at the AOI centroid |
| Population | WorldPop/GHS-POP attempts followed by deterministic synthetic fallback |
| Land cover | ESA WorldCover v2.0 |

### Source decisions

- IMD weather was replaced by Open-Meteo because the operationally reproducible IMD record was not available in the required pipeline form.
- The former OSM local PBF was replaced after its MD5 differed from Geofabrik's current same-URL object. The verified current PBF is 556,919,737 bytes with MD5 `39f1f3360041b1272be90a066386d6dd`.
- The old population metadata labelled the fallback as a 100 m cached product. The fallback generator was corrected to report its actual 30 m, `EPSG:32644` output and `population_source_type=synthetic_fallback`.
- Landsat LST/NDVI/NDBI use only the Landsat 8/9 family. No MODIS blend is used.

The extractor manifest records 107 layer records. Raw artifacts remain subject to validation and are not automatically analysis-ready.

## 3. Stage 2 - Validation

### Final counts

| Validator | Result |
|---|---:|
| Standard validator | 77 passed, 32 rejected |
| Standalone raw validator | 103 entries: 75 usable with caveats, 28 rejected |

The 28 standalone rejected artifacts are all Landsat monthly products failing the 90% valid-AOI coverage rule. There are no remaining NDVI, NDBI, or LST statistical-range violations.

### Rejected month groups and coverage

| Month | LST | NDVI | NDBI |
|---|---:|---:|---:|
| 2024-06 | 72.344% | 72.342% | 72.344% |
| 2024-10 | 72.243% | 71.697% | 72.173% |
| 2024-11 | 2.195% | 2.150% | 2.195% |
| 2024-12 | 69.695% | passes individually | passes individually |
| 2025-05 | 75.609% | 75.609% | 75.609% |
| 2025-09 | 0.020% | 0.020% | 0.020% |
| 2025-10 | 59.247% | 59.205% | 59.247% |
| 2025-11 | 89.604% | 89.604% | 89.604% |
| 2026-02 | 71.436% | 71.433% | 71.436% |
| 2026-07 | 13.656% | 13.656% | 13.656% |

All rejections are coverage decisions, not calculation errors. November 2024 and September 2025 were investigated and confirmed to have genuine cloud/cirrus scarcity in the correctly selected scenes. They are not caused by an inverted QA mask, wrong path/row, or AOI geometry defect.

Additional feature exclusions:

- Building compactness: approximately 63.45% coverage.
- Street width: approximately 60.87% coverage.
- Building height and H-W ratio: excluded by design because real OSM height-tag coverage was below 5% and the alternative Microsoft height layer contained placeholder-like values.
- Land cover: excluded from the current clean set because its source host was not allowlisted.

## 4. Stage 3 - Cleaning

The cleaner was run only from the validator-passed manifest and produced 75 clean outputs.

Verified clean-dataset properties:

- Grid dimensions: `495 x 671`.
- Cell size: 30 m.
- CRS: `EPSG:32644`.
- Origin: `E409950 / N1452060`.
- Bounds: `409950, 1431930, 424800, 1452060`.
- Raster alignment: zero detected drift across checked rasters.
- Small-gap filling: nearest-neighbor only within three pixels; large gaps remain NoData.
- Population clean sidecar: explicitly records `synthetic_fallback=true` and 30 m resolution.

The clean outputs include the accepted monthly LST/NDVI/NDBI layers, DEM, slope, population fallback, building density, OSM vectors, and weather JSON. Rejected Landsat months were not copied or silently interpolated into clean output.

## 5. Locked Modeling Manifest

The manifest is locked on 2026-08-27 and contains exactly 22 common healthy months:

```text
2024-01, 2024-02, 2024-03, 2024-04, 2024-05, 2024-07,
2024-08, 2024-09, 2025-01, 2025-02, 2025-03, 2025-04,
2025-06, 2025-07, 2025-08, 2025-12, 2026-01, 2026-03,
2026-04, 2026-05, 2026-06, 2026-08
```

The manifest contains:

- 66 monthly raster references: 22 months x LST/NDVI/NDBI.
- 6 static layer references.
- 92 total weather records.
- 72 weather scene dates required by the common-month set.
- 0 missing required weather dates.
- 0 raster-grid mismatches.
- Explicit synthetic population status.

December 2024 is excluded from the common set because NDVI and NDBI pass individually but LST has only 69.695% coverage. A modeling month requires all three monthly variables.

## 6. Stage 4 Feature Engineering

### Intended output

The requested table is one row per analysis cell and month, with:

```text
cell_id, month, scene_date, typology, lst, ndvi, ndbi,
building_density, sky_view_factor, albedo_estimate,
population_density, population_source_type, wind_speed,
wind_direction, elevation, slope
```

The canonical analysis grid is a 5x5 aggregation of the 30 m source grid into nominal 150 m cells. The source dimensions produce a nominal `135 x 99` layout and 13,365 cells. The final bottom edge contains a partial source block because 671 is not divisible by five; this is recorded by the Stage 4 implementation.

### Current implementation

The Stage 4 builder is [scripts/build_stage4_features.py](scripts/build_stage4_features.py). It is intended to:

- Read monthly and static paths from the locked manifest.
- Build the canonical 150 m GeoPackage grid.
- Aggregate valid 30 m pixels by mean.
- Use the logged median Landsat acquisition date as `scene_date`.
- Compute the building-footprint coverage proxy for sky-view factor.
- Retrieve Landsat reflectance bands for Liang-style albedo.
- Retrieve Open-Meteo centroid wind values.
- Assign typology using a deterministic building-density/NDBI/road heuristic.
- Broadcast static features by month.
- Exclude incomplete cell-month rows rather than filling them silently.
- Write a Parquet table and quality report.

### Current artifact state

Files currently present under `data/features/`:

- `cell_grid_150m.gpkg`
- `chennai_uhi_feature_table.parquet`
- `FEATURE_TABLE_QUALITY.json`
- `FEATURE_TABLE_REPORT.md`

The existing Parquet/report were generated by a provisional run and contain 294,030 rows across 13,365 cells and 22 months, but they contain substantial null rates in required variables, including approximately 33.5% for LST/NDVI/NDBI and approximately 33.3% for static raster fields. They are therefore not accepted as the final Stage 4 result.

The latest rebuild started at approximately 00:59 on 2026-08-28, recreated the cell grid, and is still running remote reflectance retrieval. It has not yet written a verified final table/report. Earlier attempts encountered:

1. A road-cell alignment error during typology assignment, which was corrected.
2. A long/interrupted remote Planetary Computer reflectance read while computing albedo.

The active run has not emitted a new exception in its current execution snapshot. Earlier runs emitted `All-NaN slice encountered` warnings for some remote scene pixels. These warnings require final output inspection; they do not by themselves establish that the final table is valid.

## 7. Known Limitations

- Population is a synthetic fallback, not measured population data. Every downstream output using it must retain that warning.
- Compactness and street width remain excluded because their coverage is below 90%.
- Ten Landsat month groups remain unavailable at the required coverage because of genuine cloud/monsoon scarcity.
- Previous analyses based on the old mismatched OSM snapshot are a different data version and are not directly comparable with current OSM-derived products.
- Sky-view factor is a ground-coverage proxy, not a true hemispherical SVF because building height was excluded.
- Wind is available only at the AOI centroid and must be treated as city-wide when broadcast to cells.
- The current typology is heuristic, not a validated land-use classification.
- The repository does not preserve the numerical R-squared/confidence interval from the earlier height test; only the documented `<5%` real height-tag coverage and placeholder-value evidence remain.

## 8. Readiness

| Area | Current status |
|---|---|
| Extraction | Complete |
| Validation | Complete |
| Cleaning | Complete and grid-verified |
| Locked manifest | Complete and authoritative |
| Feature engineering | In progress; not yet accepted as complete |
| Model training | Not started |
| Prediction/ranking | Not started |
| Final deliverable | Not started |

**Overall readiness:** The project is ready for controlled continuation of Stage 4, but not yet ready for model training. Stage 4 must first finish successfully and pass final checks for exact row scope, cell IDs, grid alignment, date joins, null rates, coverage exclusions, and synthetic-population labelling.

No model training, SHAP analysis, per-typology model fitting, prediction, ranking, or deliverable generation has been performed.

## Authoritative Files

- [MODELING_READY_MANIFEST.json](data/logs/MODELING_READY_MANIFEST.json)
- [STAGE3_CLOSURE_REPORT.md](data/logs/STAGE3_CLOSURE_REPORT.md)
- [CLEAN_DATASET_SUMMARY.json](data/clean/CLEAN_DATASET_SUMMARY.json)
- [build_stage4_features.py](scripts/build_stage4_features.py)
- [FEATURE_TABLE_REPORT.md](data/features/FEATURE_TABLE_REPORT.md) - provisional until Stage 4 completes
