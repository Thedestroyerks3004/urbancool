"""Build the Stage 4 per-cell, per-month Chennai UHI feature table."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import httpx
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import from_bounds
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
CLEAN = ROOT / "data" / "clean"
LOGS = ROOT / "data" / "logs"
FEATURES = ROOT / "data" / "features"
MANIFEST_PATH = LOGS / "MODELING_READY_MANIFEST.json"
TABLE_PATH = FEATURES / "chennai_uhi_feature_table.parquet"
GRID_PATH = FEATURES / "cell_grid_150m.gpkg"
QUALITY_PATH = FEATURES / "FEATURE_TABLE_QUALITY.json"
REPORT_PATH = FEATURES / "FEATURE_TABLE_REPORT.md"
PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
TARGET_CRS = "EPSG:32644"
SOURCE_SHAPE = (671, 495)
BLOCK = 5
GATE = 0.90
EXPECTED_COLUMNS = [
    "cell_id", "month", "scene_date", "typology", "lst", "ndvi", "ndbi",
    "building_density", "sky_view_factor", "albedo_estimate",
    "population_density", "population_source_type", "wind_speed",
    "wind_direction", "elevation", "slope",
]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def clean_path(name: str) -> Path:
    path = CLEAN / name
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def build_cells() -> tuple[gpd.GeoDataFrame, Affine]:
    """Create the locked 135 x 99 grid, retaining the partial bottom blocks."""
    transform = Affine(30.0, 0, 409950.0, 0, -30.0, 1452060.0)
    nrows, ncols = (671 + BLOCK - 1) // BLOCK, (495 + BLOCK - 1) // BLOCK
    records: list[dict[str, Any]] = []
    for row in range(nrows):
        for col in range(ncols):
            pixel_rows = min(BLOCK, 671 - row * BLOCK)
            pixel_cols = min(BLOCK, 495 - col * BLOCK)
            xmin = transform.c + col * BLOCK * transform.a
            ymax = transform.f + row * BLOCK * transform.e
            xmax = xmin + pixel_cols * transform.a
            ymin = ymax + pixel_rows * transform.e
            geom = box(xmin, ymin, xmax, ymax)
            records.append({
                "cell_id": f"{row:03d}_{col:03d}", "row_150": row, "col_150": col,
                "cell_area_m2": float(geom.area), "x_center_m": float(geom.centroid.x),
                "y_center_m": float(geom.centroid.y), "source_pixel_count": pixel_rows * pixel_cols,
                "geometry": geom,
            })
    return gpd.GeoDataFrame(records, geometry="geometry", crs=TARGET_CRS), transform


def read_clean_raster(path: Path) -> np.ndarray:
    with rasterio.open(path) as src:
        if (src.height, src.width) != (671, 495) or str(src.crs) != TARGET_CRS:
            raise ValueError(f"Unexpected grid for {path}: {src.width}x{src.height}, {src.crs}")
        masked = src.read(1, masked=True).astype(np.float32)
        return masked.filled(np.nan)


def aggregate_5x5(array: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return valid-pixel means and fractions, including the partial bottom block."""
    if array.shape != (671, 495):
        raise ValueError(f"Expected (671, 495), got {array.shape}")
    nrows, ncols = 135, 99
    values = np.full((nrows, ncols), np.nan, dtype=np.float32)
    coverage = np.zeros((nrows, ncols), dtype=np.float32)
    for row in range(nrows):
        for col in range(ncols):
            tile = array[row * BLOCK:min((row + 1) * BLOCK, 671), col * BLOCK:min((col + 1) * BLOCK, 495)]
            valid = np.isfinite(tile)
            coverage[row, col] = float(valid.mean())
            if valid.any():
                values[row, col] = float(tile[valid].mean())
    return values, coverage


def static_rasters() -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], str]:
    names = {
        "building_density": "chennai_building_density_fraction_static_30m.tif",
        "population_density": "chennai_population_density_people_static_30m.tif",
        "elevation": "chennai_dem_m_static_30m.tif",
        "slope": "chennai_slope_deg_static_30m.tif",
    }
    values, coverage = {}, {}
    for key, filename in names.items():
        values[key], coverage[key] = aggregate_5x5(read_clean_raster(clean_path(filename)))
    source_type = load_json(clean_path("chennai_population_density_people_static_30m_meta.json")).get("population_source_type")
    if source_type != "synthetic_fallback":
        raise ValueError(f"Unexpected population_source_type: {source_type!r}")
    return values, coverage, source_type


def sidecar_scene_date(month: str) -> str:
    """Use the median original acquisition date across the three monthly sidecars."""
    product_medians: list[date] = []
    for prefix in ("lst_k", "ndvi_index", "ndbi_index"):
        meta = load_json(clean_path(f"chennai_{prefix}_{month}_30m_meta.json"))
        dates = sorted(date.fromisoformat(str(value)[:10]) for value in meta.get("original_acquisition_dates", []))
        if dates:
            product_medians.append(dates[(len(dates) - 1) // 2])
    if not product_medians:
        raise ValueError(f"No acquisition dates in sidecars for {month}")
    product_medians.sort()
    return product_medians[(len(product_medians) - 1) // 2].isoformat()


def monthly_landsat(months: list[str]) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, dict[str, np.ndarray]], dict[str, str]]:
    values: dict[str, dict[str, np.ndarray]] = {}
    coverage: dict[str, dict[str, np.ndarray]] = {}
    scene_dates: dict[str, str] = {}
    for month in months:
        values[month], coverage[month] = {}, {}
        for variable, prefix in (("lst", "lst_k"), ("ndvi", "ndvi_index"), ("ndbi", "ndbi_index")):
            values[month][variable], coverage[month][variable] = aggregate_5x5(read_clean_raster(clean_path(f"chennai_{prefix}_{month}_30m.tif")))
        scene_dates[month] = sidecar_scene_date(month)
    return values, coverage, scene_dates


def vector_metric(path: Path, cells: gpd.GeoDataFrame, metric: str) -> tuple[np.ndarray, np.ndarray]:
    """Sum exact cell intersections for area or length on the shared grid."""
    layer = gpd.read_file(path)
    if layer.crs is None:
        raise ValueError(f"Vector has no CRS: {path}")
    layer = layer.to_crs(TARGET_CRS)
    index = layer.sindex
    totals = np.zeros(len(cells), dtype=np.float64)
    for cell_position, cell_geometry in enumerate(cells.geometry):
        for feature_position in index.query(cell_geometry, predicate="intersects"):
            geometry = layer.geometry.iloc[feature_position]
            if geometry is not None and not geometry.is_empty:
                intersection = geometry.intersection(cell_geometry)
                totals[cell_position] += float(intersection.area if metric == "area" else intersection.length)
    return totals, np.ones(len(cells), dtype=np.float32)


def derive_vector_features(cells: gpd.GeoDataFrame) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    footprint_area, footprint_coverage = vector_metric(clean_path("chennai_building_footprints_vector_static.gpkg"), cells, "area")
    road_length, road_coverage = vector_metric(clean_path("chennai_road_network_vector_static.gpkg"), cells, "length")
    area = cells["cell_area_m2"].to_numpy(dtype=np.float64)
    sky_view = np.clip(1.0 - footprint_area / area, 0.0, 1.0).astype(np.float32)
    road_density = (road_length / area).astype(np.float32)
    return sky_view, road_density, {"footprints": footprint_coverage, "roads": road_coverage}


def remote_band_window(href: str, bbox_4326: list[float], transform: Affine, shape: tuple[int, int], resampling: Resampling) -> np.ndarray:
    """Read a COG after transforming the AOI bounds into its native CRS."""
    try:
        import planetary_computer as pc
        signed_href = pc.sign(href)
    except Exception:
        signed_href = href
    with rasterio.open(signed_href) as source:
        source_bounds = transform_bounds("EPSG:4326", source.crs, *bbox_4326)
        window = from_bounds(*source_bounds, transform=source.transform).round_offsets().round_lengths()
        source_array = source.read(1, window=window, masked=True).astype(np.float32).filled(np.nan)
        destination = np.full(shape, np.nan, dtype=np.float32)
        reproject(source_array, destination, src_transform=source.window_transform(window), src_crs=source.crs, dst_transform=transform, dst_crs=TARGET_CRS, resampling=resampling, src_nodata=np.nan, dst_nodata=np.nan)
        return destination


def fetch_albedo(months: list[str], transform: Affine) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, Any]]:
    import planetary_computer as pc
    import pystac_client
    from chennai_uhi.extractor.landsat import SR_OFFSET, SR_SCALE, _qa_clear

    aoi = gpd.read_file(ROOT / "data" / "aoi" / "chennai_boundary.geojson").to_crs(4326)
    bbox = [float(value) for value in aoi.total_bounds]
    catalog = pystac_client.Client.open(PC_STAC, modifier=pc.sign_inplace)
    result, result_coverage, scene_counts = {}, {}, {}
    bands = ("blue", "red", "nir", "swir1", "swir2")
    for month in months:
        year, number = map(int, month.split("-"))
        next_month = date(year + (number == 12), 1 if number == 12 else number + 1, 1)
        items = list(catalog.search(collections=["landsat-c2-l2"], bbox=bbox, datetime=f"{month}-01/{next_month.isoformat()}", query={"platform": {"in": ["landsat-8", "landsat-9"]}, "eo:cloud_cover": {"lt": 80}}, max_items=100).items())
        band_values: dict[str, list[np.ndarray]] = {band: [] for band in bands}
        band_coverages: dict[str, list[np.ndarray]] = {band: [] for band in bands}
        used = 0
        for item in items:
            assets = item.assets
            def href(*keys: str) -> str | None:
                return next((assets[key].href for key in keys if key in assets), None)
            qa_href = href("qa_pixel")
            hrefs = {"blue": href("blue", "SR_B2"), "red": href("red", "SR_B4"), "nir": href("nir08", "SR_B5"), "swir1": href("swir16", "SR_B6"), "swir2": href("swir22", "SR_B7")}
            if not qa_href or not all(hrefs.values()):
                continue
            try:
                qa = remote_band_window(qa_href, bbox, transform, (671, 495), Resampling.nearest)
                clear = _qa_clear(np.nan_to_num(qa, nan=1).astype(np.uint16))
                for band in bands:
                    raw = remote_band_window(hrefs[band], bbox, transform, (671, 495), Resampling.bilinear)
                    reflectance = raw.astype(np.float32) * SR_SCALE + SR_OFFSET
                    valid = clear & np.isfinite(reflectance) & (reflectance >= 0)
                    aggregated, fraction = aggregate_5x5(np.where(valid, reflectance, np.nan))
                    band_values[band].append(aggregated)
                    band_coverages[band].append(fraction)
                used += 1
            except Exception:
                continue
        if not used:
            raise RuntimeError(f"No usable reflectance scene for {month}")
        medians = {}
        for band in bands:
            stack = np.stack(band_values[band])
            finite = np.isfinite(stack)
            medians[band] = np.divide(
                np.nansum(stack, axis=0), finite.sum(axis=0),
                out=np.full(stack.shape[1:], np.nan, dtype=np.float32),
                where=finite.sum(axis=0) > 0,
            )
        valid = np.ones_like(medians["blue"], dtype=bool)
        for band in bands:
            valid &= np.isfinite(medians[band])
        result[month] = np.where(valid, 0.356 * medians["blue"] + 0.130 * medians["red"] + 0.373 * medians["nir"] + 0.085 * medians["swir1"] + 0.072 * medians["swir2"] - 0.0018, np.nan).astype(np.float32)
        band_max_coverages = []
        for band in bands:
            stack = np.stack(band_coverages[band])
            band_max_coverages.append(np.max(np.where(np.isfinite(stack), stack, 0.0), axis=0))
        result_coverage[month] = np.minimum.reduce(band_max_coverages).astype(np.float32)
        scene_counts[month] = used
    return result, result_coverage, {"source": "Planetary Computer landsat-c2-l2", "outside_locked_manifest": True, "scene_counts": scene_counts, "formula": "0.356*blue + 0.130*red + 0.373*nir + 0.085*swir1 + 0.072*swir2 - 0.0018", "aggregation_order": "150m reflectance bands first, then Liang (2001) formula"}


def fetch_wind(scene_dates: dict[str, str]) -> tuple[dict[str, tuple[float, float] | None], dict[str, float], dict[str, Any]]:
    unique_dates = sorted(set(scene_dates.values()))
    params = {"latitude": 13.047973965685829, "longitude": 80.23500538766442, "start_date": unique_dates[0], "end_date": unique_dates[-1], "daily": "wind_speed_10m_mean,wind_direction_10m_dominant", "timezone": "Asia/Kolkata", "models": "era5"}
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        response = client.get(OPEN_METEO_ARCHIVE, params=params)
        response.raise_for_status()
        daily = response.json().get("daily", {})
    by_date = {day: (speed, direction) for day, speed, direction in zip(daily.get("time", []), daily.get("wind_speed_10m_mean", []), daily.get("wind_direction_10m_dominant", []))}
    values, coverage = {}, {}
    for month, scene_date in scene_dates.items():
        value = by_date.get(scene_date)
        valid = value is not None and value[0] is not None and value[1] is not None
        values[month] = (float(value[0]), float(value[1])) if valid else None
        coverage[month] = 1.0 if valid else 0.0
    return values, coverage, {"source": "Open-Meteo ERA5 archive", "outside_locked_manifest": True, "centroid_only": True, "coordinates": [params["longitude"], params["latitude"]], "query": params}


def classify_typology(building_density: np.ndarray, ndbi: np.ndarray, road_density: np.ndarray) -> np.ndarray:
    valid = np.isfinite(building_density) & np.isfinite(ndbi) & np.isfinite(road_density)
    result = np.full(len(building_density), "informal", dtype=object)
    result[(road_density > 0.0005) & (building_density < 0.35) & valid] = "transport"
    result[(building_density >= 0.80) & (ndbi >= 0.10) & valid] = "dense core"
    result[(ndbi >= 0.25) & valid] = "industrial"
    result[(building_density >= 0.40) & (ndbi >= 0.05) & valid] = "commercial"
    result[(building_density < 0.15) & (ndbi < 0) & valid] = "residential"
    result[~valid] = None
    return result


def assemble_rows(months: list[str], cells: gpd.GeoDataFrame, landsat: dict[str, dict[str, np.ndarray]], landsat_coverage: dict[str, dict[str, np.ndarray]], scene_dates: dict[str, str], static: dict[str, np.ndarray], static_coverage: dict[str, np.ndarray], sky_view: np.ndarray, road_density: np.ndarray, vector_coverage: dict[str, np.ndarray], albedo: dict[str, np.ndarray], albedo_coverage: dict[str, np.ndarray], wind: dict[str, tuple[float, float] | None], wind_coverage: dict[str, float], population_source_type: str) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    rows, exclusions = [], []
    ncols = 99
    typologies = {month: classify_typology(static["building_density"].ravel(), landsat[month]["ndbi"].ravel(), road_density) for month in months}
    for month in months:
        for position, cell_id in enumerate(cells["cell_id"].to_numpy()):
            row, col = position // ncols, position % ncols
            reasons: list[dict[str, Any]] = []
            coverage_fields = {**landsat_coverage[month], **{name: values.ravel()[position] for name, values in static_coverage.items()}, "sky_view_factor": vector_coverage["footprints"][position], "road_density": vector_coverage["roads"][position], "albedo_estimate": albedo_coverage[month].ravel()[position], "wind_speed": wind_coverage[month], "wind_direction": wind_coverage[month]}
            for field, fraction in coverage_fields.items():
                if fraction < GATE:
                    reasons.append({"field": field, "coverage_fraction": round(float(fraction), 6), "coverage_percent": round(float(fraction) * 100, 4)})
            wind_value = wind[month]
            values = {"lst": landsat[month]["lst"].ravel()[position], "ndvi": landsat[month]["ndvi"].ravel()[position], "ndbi": landsat[month]["ndbi"].ravel()[position], "building_density": static["building_density"].ravel()[position], "sky_view_factor": sky_view[position], "albedo_estimate": albedo[month].ravel()[position], "population_density": static["population_density"].ravel()[position], "elevation": static["elevation"].ravel()[position], "slope": static["slope"].ravel()[position]}
            if wind_value is None:
                values["wind_speed"], values["wind_direction"] = np.nan, np.nan
            else:
                values["wind_speed"], values["wind_direction"] = wind_value
            for field, value in values.items():
                if not np.isfinite(value):
                    reasons.append({"field": field, "reason": "missing_or_non_finite"})
            if reasons:
                exclusions.append({"cell_id": cell_id, "month": month, "reasons": reasons})
                continue
            rows.append({"cell_id": cell_id, "month": month, "scene_date": scene_dates[month], "typology": typologies[month][position], **values, "population_source_type": population_source_type})
    return pd.DataFrame(rows, columns=EXPECTED_COLUMNS), exclusions


def main() -> None:
    manifest = load_json(MANIFEST_PATH)
    months = list(manifest["common_healthy_months"])
    if len(months) != 22 or set(months) != set(manifest["monthly_layers"]):
        raise ValueError("Manifest must contain exactly the 22 locked months")
    cells, transform = build_cells()
    if len(cells) != 13365:
        raise ValueError(f"Unexpected cell count: {len(cells)}")
    FEATURES.mkdir(parents=True, exist_ok=True)
    cells.to_file(GRID_PATH, layer="cell_grid_150m", driver="GPKG")
    static, static_coverage, population_source_type = static_rasters()
    landsat, landsat_coverage, scene_dates = monthly_landsat(months)
    sky_view, road_density, vector_coverage = derive_vector_features(cells)
    albedo, albedo_coverage, albedo_provenance = fetch_albedo(months, transform)
    wind, wind_coverage, wind_provenance = fetch_wind(scene_dates)
    table, exclusions = assemble_rows(months, cells, landsat, landsat_coverage, scene_dates, static, static_coverage, sky_view, road_density, vector_coverage, albedo, albedo_coverage, wind, wind_coverage, population_source_type)
    table.to_parquet(TABLE_PATH, index=False)
    expected_max = len(cells) * len(months)
    quality = {"rows": int(len(table)), "expected_max_rows": expected_max, "cells": len(cells), "months": months, "excluded_cell_months": len(exclusions), "exclusions": exclusions, "null_rates": {column: float(table[column].isna().mean()) if len(table) else 0.0 for column in EXPECTED_COLUMNS}, "columns": list(table.columns), "population_source_type": population_source_type, "grid": {"source_shape": [671, 495], "target_shape": [135, 99], "source_resolution_m": 30, "target_nominal_resolution_m": 150, "crs": TARGET_CRS, "origin": [409950, 1452060], "bounds": [409950, 1431930, 424800, 1452060], "partial_bottom_block": True, "bottom_block_source_pixel_count": 1}, "coverage_gate": {"threshold": GATE, "rule": "exclude the complete cell-month if any required field is below threshold or missing", "albedo_and_wind_applied": True}, "auxiliary_live_fetches": {"albedo_estimate": albedo_provenance, "wind_speed": wind_provenance, "wind_direction": wind_provenance}, "sky_view_factor_note": "Footprint coverage proxy, not true hemispherical SVF.", "typology_note": "Fixed deterministic heuristic, not a validated land-use classification.", "training_started": False, "generated_at": datetime.now().astimezone().isoformat()}
    QUALITY_PATH.write_text(json.dumps(quality, indent=2) + "\n", encoding="utf-8")
    report = ["# Stage 4 Feature Table Report", "", f"- Rows: `{len(table)}`; expected maximum: `{expected_max}`; excluded cell-months: `{len(exclusions)}`", f"- Columns: `{', '.join(table.columns)}`", "- Grid: 30 m `EPSG:32644` source; 5x5 aggregation; 13,365 nominal 150 m cells.", "- The partial bottom source block is retained and flagged in `FEATURE_TABLE_QUALITY.json`.", "- Every required field, including auxiliary albedo and wind, is subject to the 90% coverage gate.", "- Sky-view factor is a footprint coverage proxy; wind is centroid-only and broadcast by month.", "- Population source type is copied verbatim as `synthetic_fallback`.", "- Model training and SHAP analysis were not performed.", "", "## Null Rates", "", "| Column | Null rate |", "|---|---:|"]
    report.extend(f"| `{column}` | {quality['null_rates'][column]:.6%} |" for column in EXPECTED_COLUMNS)
    REPORT_PATH.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"wrote {TABLE_PATH}; rows={len(table)} excluded={len(exclusions)} cells={len(cells)} months={len(months)}")


if __name__ == "__main__":
    main()
