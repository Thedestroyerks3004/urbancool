# Stage 4 Feature Table Report

- Rows: `294030`; expected: `294030`; match: `True`
- Cells: `13365` (`135 x 99`); months: `22`
- Aggregation: mean of valid 30m pixels in each nominal 150m 5x5 block.
- Grid: `EPSG:32644`, 30m origin `E409950/N1452060`, nominal 150m cells.
- Only the 22 manifest months were processed; the 10 excluded months were not touched.
- Model training, SHAP analysis, and model fitting: **not performed**

## Null Rates

| Column | Null rate |
|---|---:|
| `cell_id` | 0.000000% |
| `row_150` | 0.000000% |
| `col_150` | 0.000000% |
| `x_center_m` | 0.000000% |
| `y_center_m` | 0.000000% |
| `cell_area_m2` | 0.000000% |
| `month` | 0.000000% |
| `scene_date` | 0.000000% |
| `lst` | 33.517328% |
| `ndvi` | 33.513927% |
| `ndbi` | 33.513927% |
| `building_density` | 33.340816% |
| `sky_view_factor` | 0.000000% |
| `albedo_estimate` | 1.050573% |
| `population_density` | 33.340816% |
| `population_source_type` | 0.000000% |
| `wind_speed` | 0.000000% |
| `wind_direction` | 0.000000% |
| `elevation` | 33.340816% |
| `slope` | 33.340816% |
| `typology` | 0.000000% |

## Coverage Gate

The 90% gate was applied to each monthly aggregate, including newly derived albedo. Below-gate cell-months are listed in `FEATURE_TABLE_QUALITY.json` and are not filled or interpolated.

| Month | Feature | Excluded cell-months |
|---|---|---:|
| `2024-01` | `lst` | 5006 |
| `2024-01` | `ndvi` | 5006 |
| `2024-01` | `ndbi` | 5006 |
| `2024-01` | `albedo` | 500 |
| `2024-02` | `lst` | 5210 |
| `2024-02` | `ndvi` | 5213 |
| `2024-02` | `ndbi` | 5210 |
| `2024-02` | `albedo` | 1268 |
| `2024-03` | `lst` | 4891 |
| `2024-03` | `ndvi` | 4891 |
| `2024-03` | `ndbi` | 4891 |
| `2024-03` | `albedo` | 62 |
| `2024-04` | `lst` | 4890 |
| `2024-04` | `ndvi` | 4890 |
| `2024-04` | `ndbi` | 4890 |
| `2024-04` | `albedo` | 2 |
| `2024-05` | `lst` | 4905 |
| `2024-05` | `ndvi` | 4905 |
| `2024-05` | `ndbi` | 4905 |
| `2024-05` | `albedo` | 151 |
| `2024-07` | `lst` | 4890 |
| `2024-07` | `ndvi` | 4890 |
| `2024-07` | `ndbi` | 4890 |
| `2024-07` | `albedo` | 44 |
| `2024-08` | `lst` | 4890 |
| `2024-08` | `ndvi` | 4890 |
| `2024-08` | `ndbi` | 4890 |
| `2024-08` | `albedo` | 10 |
| `2024-09` | `lst` | 4890 |
| `2024-09` | `ndvi` | 4890 |
| `2024-09` | `ndbi` | 4890 |
| `2024-09` | `albedo` | 12 |
| `2025-01` | `lst` | 4890 |
| `2025-01` | `ndvi` | 4890 |
| `2025-01` | `ndbi` | 4890 |
| `2025-01` | `albedo` | 1228 |
| `2025-02` | `lst` | 4890 |
| `2025-02` | `ndvi` | 4890 |
| `2025-02` | `ndbi` | 4890 |
| `2025-02` | `albedo` | 1031 |
| `2025-03` | `lst` | 4890 |
| `2025-03` | `ndvi` | 4890 |
| `2025-03` | `ndbi` | 4890 |
| `2025-03` | `albedo` | 0 |
| `2025-04` | `lst` | 4941 |
| `2025-04` | `ndvi` | 4941 |
| `2025-04` | `ndbi` | 4941 |
| `2025-04` | `albedo` | 118 |
| `2025-06` | `lst` | 4890 |
| `2025-06` | `ndvi` | 4890 |
| `2025-06` | `ndbi` | 4890 |
| `2025-06` | `albedo` | 42 |
| `2025-07` | `lst` | 4896 |
| `2025-07` | `ndvi` | 4896 |
| `2025-07` | `ndbi` | 4896 |
| `2025-07` | `albedo` | 98 |
| `2025-08` | `lst` | 4890 |
| `2025-08` | `ndvi` | 4890 |
| `2025-08` | `ndbi` | 4890 |
| `2025-08` | `albedo` | 57 |
| `2025-12` | `lst` | 5377 |
| `2025-12` | `ndvi` | 5366 |
| `2025-12` | `ndbi` | 5366 |
| `2025-12` | `albedo` | 893 |
| `2026-01` | `lst` | 5409 |
| `2026-01` | `ndvi` | 5409 |
| `2026-01` | `ndbi` | 5409 |
| `2026-01` | `albedo` | 1893 |
| `2026-03` | `lst` | 4890 |
| `2026-03` | `ndvi` | 4890 |
| `2026-03` | `ndbi` | 4890 |
| `2026-03` | `albedo` | 295 |
| `2026-04` | `lst` | 4890 |
| `2026-04` | `ndvi` | 4890 |
| `2026-04` | `ndbi` | 4890 |
| `2026-04` | `albedo` | 0 |
| `2026-05` | `lst` | 4890 |
| `2026-05` | `ndvi` | 4890 |
| `2026-05` | `ndbi` | 4890 |
| `2026-05` | `albedo` | 22 |
| `2026-06` | `lst` | 4890 |
| `2026-06` | `ndvi` | 4890 |
| `2026-06` | `ndbi` | 4890 |
| `2026-06` | `albedo` | 25 |
| `2026-08` | `lst` | 4913 |
| `2026-08` | `ndvi` | 4913 |
| `2026-08` | `ndbi` | 4913 |
| `2026-08` | `albedo` | 131 |

## Simplifications

- Sky-view factor is `1 - building_footprint_area / cell_area`, estimated from 30m all-touched footprint rasterization; it is a coverage proxy, not true hemispherical SVF.
- Wind is an Open-Meteo AOI-centroid value broadcast to every cell for the representative scene date.
- Population is synthetic fallback and is explicitly labelled on every row.
- Typology is a deterministic heuristic from density, NDBI, and OSM road presence, broadcast by month.
- Building compactness and street width are excluded because they are below the 90% source-coverage gate.

## Auxiliary Provenance

- Albedo: `Liang-style: 0.356 blue + 0.130 red + 0.373 NIR + 0.085 SWIR1 - 0.072`; source `Planetary Computer landsat-c2-l2`.
- Wind: `wind_speed_10m_mean, wind_direction_10m_dominant`; `Open-Meteo ERA5 archive`; centroid-only.
- The locked modeling manifest remains the scope authority for monthly clean layers and static layers.
