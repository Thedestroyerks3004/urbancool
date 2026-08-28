"""Stage 1 — EXTRACTOR runner.

Fetches all raw layers for full Chennai + 2024-01-01→present, writes
extractor_fetch_log.json and extractor_manifest.json.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from chennai_uhi.aoi import load_or_fetch_aoi
from chennai_uhi.config import load_config, month_starts, write_json
from chennai_uhi.extractor.dem import extract_dem_slope
from chennai_uhi.extractor.landcover import extract_landcover
from chennai_uhi.extractor.landsat import extract_landsat_monthly
from chennai_uhi.extractor.osm import extract_osm
from chennai_uhi.extractor.population import extract_population
from chennai_uhi.extractor.weather import extract_weather_for_dates
from chennai_uhi.grid import build_reference_grid
from chennai_uhi.logging_util import FetchLog

logger = logging.getLogger(__name__)


def run_extractor(work_root: Path | None = None) -> dict[str, Any]:
    cfg = load_config(work_root)
    settings = cfg["_settings"]
    paths = cfg["_paths"]
    t_start = date.fromisoformat(cfg["temporal_start"])
    t_end = date.fromisoformat(cfg["temporal_end"])
    target_epsg = int(settings["crs"]["target_epsg"])
    cell = float(settings["grid"]["cell_size_m"])

    logger.info("EXTRACTOR window: %s → %s (UTC present)", t_start, t_end)

    aoi, aoi_meta = load_or_fetch_aoi(paths["aoi"], target_epsg=target_epsg)
    fetch_log = FetchLog(cfg["temporal_start"], cfg["temporal_end"])
    fetch_log.add(
        layer_id="chennai_boundary",
        variable="aoi",
        source_name=aoi_meta.get("source_name", "AOI"),
        query_parameters=aoi_meta.get("query_parameters", {}),
        resolved_source_url=aoi_meta.get("resolved_source_url"),
        fetch_timestamp=aoi_meta.get("fetch_timestamp"),
        data_dates=aoi_meta.get("data_dates"),
        local_path=str(paths["aoi"] / "chennai_boundary.geojson"),
        native_crs=f"EPSG:{target_epsg}",
        status="ok",
        extra={k: v for k, v in aoi_meta.items() if k not in {"query_parameters"}},
    )

    # Lock shared reference grid once (used by cleaner; also saved now for audit)
    bounds = tuple(float(x) for x in aoi.total_bounds)
    ref_grid = build_reference_grid(bounds, cell_size_m=cell, epsg=target_epsg)
    grid_path = paths["aoi"] / "reference_grid.json"
    ref_grid.save(grid_path)

    layers: list[dict[str, Any]] = []

    # Static DEM once
    layers.extend(extract_dem_slope(aoi, paths["raw"], fetch_log))

    # Land cover
    layers.extend(extract_landcover(aoi, paths["raw"], fetch_log))

    # Population
    layers.extend(extract_population(aoi, paths["raw"], fetch_log))

    # OSM pinned snapshot + geometry proxies (no height/H-W)
    layers.extend(extract_osm(aoi, paths["raw"], fetch_log))

    # Landsat monthly LST / NDVI / NDBI
    months = month_starts(t_start, t_end)
    landsat_layers = extract_landsat_monthly(aoi, paths["raw"], fetch_log, months, t_end)
    layers.extend(landsat_layers)

    # Weather for exact Landsat acquisition dates
    acq_dates: list[str] = []
    for rec in fetch_log.records:
        if rec.get("variable") in {"lst", "ndvi", "ndbi"}:
            acq_dates.extend(rec.get("data_dates") or [])
    layers.extend(extract_weather_for_dates(aoi, paths["raw"], fetch_log, acq_dates))

    # Explicit exclusion record (Constraint 5 / building height)
    fetch_log.add(
        layer_id="chennai_building_height_EXCLUDED",
        variable="building_height",
        source_name="EXCLUDED BY DESIGN",
        query_parameters={},
        resolved_source_url=None,
        status="excluded_by_design",
        notes=(
            "Building height / H-W ratio excluded: prior validation found <5% real "
            "height-tag coverage in OSM and placeholder values in Microsoft building-height "
            "for this region. Geometry effects use footprint density, compactness, "
            "and road-derived street width only."
        ),
    )

    log_path = paths["logs"] / "extractor_fetch_log.json"
    fetch_log.save(log_path)

    manifest = {
        "stage": "extractor",
        "temporal_start": cfg["temporal_start"],
        "temporal_end": cfg["temporal_end"],
        "temporal_end_note": cfg["temporal_end_note"],
        "crs_target": f"EPSG:{target_epsg}",
        "reference_grid": str(grid_path),
        "aoi": str(paths["aoi"] / "chennai_boundary.geojson"),
        "fetch_log": str(log_path),
        "n_layers": len(layers),
        "layers": layers,
        "excluded_by_design": ["building_height", "height_width_ratio"],
    }
    man_path = paths["logs"] / "extractor_manifest.json"
    write_json(man_path, manifest)
    logger.info("EXTRACTOR done — %d layers, log %s", len(layers), log_path)
    return manifest
