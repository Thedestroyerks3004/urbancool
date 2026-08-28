"""Digital elevation + slope — Copernicus DEM GLO-30 via Planetary Computer.

Static layer: fetched once, reused for every time step (never re-fetched per month).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.merge import merge
from rasterio.warp import Resampling, calculate_default_transform, reproject

from chennai_uhi.extractor.base import PC_STAC, bbox_wgs84, sign_pc_href, write_geotiff
from chennai_uhi.grid import build_reference_grid
from chennai_uhi.logging_util import FetchLog, sha256_file, utc_now_iso

logger = logging.getLogger(__name__)


def _slope_degrees(dem: np.ndarray, cell_size_m: float) -> np.ndarray:
    """Horn-style slope in degrees from DEM (meters)."""
    dem_f = dem.astype(np.float64)
    dy, dx = np.gradient(dem_f, cell_size_m, cell_size_m)
    slope = np.degrees(np.arctan(np.hypot(dx, dy)))
    return slope.astype(np.float32)


def extract_dem_slope(
    aoi_gdf,
    raw_dir: Path,
    fetch_log: FetchLog,
) -> list[dict[str, Any]]:
    import planetary_computer as pc
    import pystac_client

    out_dir = raw_dir / "dem"
    out_dir.mkdir(parents=True, exist_ok=True)
    dem_path = out_dir / "chennai_dem_m_raw.tif"
    slope_path = out_dir / "chennai_slope_deg_raw.tif"

    # Reuse if already present (static)
    if dem_path.exists() and slope_path.exists():
        fetch_log.add(
            layer_id="chennai_dem_m",
            variable="dem",
            source_name="Copernicus DEM GLO-30 (cached)",
            query_parameters={"reuse": True},
            resolved_source_url=PC_STAC,
            local_path=str(dem_path),
            checksum_sha256=sha256_file(dem_path),
            native_crs="EPSG:4326",
            native_resolution_m=30.0,
            data_dates=["static"],
            notes="Static DEM reused; not re-fetched per month",
            status="ok",
        )
        fetch_log.add(
            layer_id="chennai_slope_deg",
            variable="slope",
            source_name="Derived from Copernicus DEM GLO-30 (cached)",
            query_parameters={"reuse": True, "method": "numpy_gradient_horn"},
            resolved_source_url=PC_STAC,
            local_path=str(slope_path),
            checksum_sha256=sha256_file(slope_path),
            native_crs="EPSG:32644",
            native_resolution_m=30.0,
            data_dates=["static"],
            status="ok",
        )
        return [
            {"layer_id": "chennai_dem_m", "variable": "dem", "unit": "m", "path": str(dem_path), "data_type": "raster"},
            {
                "layer_id": "chennai_slope_deg",
                "variable": "slope",
                "unit": "deg",
                "path": str(slope_path),
                "data_type": "raster",
            },
        ]

    bbox = bbox_wgs84(aoi_gdf)
    catalog = pystac_client.Client.open(PC_STAC, modifier=pc.sign_inplace)
    search = catalog.search(collections=["cop-dem-glo-30"], bbox=bbox, max_items=20)
    items = list(search.items())
    if not items:
        fetch_log.add(
            layer_id="chennai_dem_m",
            variable="dem",
            source_name="Copernicus DEM GLO-30",
            query_parameters={"collections": ["cop-dem-glo-30"], "bbox": bbox},
            resolved_source_url=PC_STAC,
            status="error",
            error="No COP-DEM items for AOI",
        )
        return []

    src_files = []
    hrefs = []
    for item in items:
        asset = item.assets.get("data")
        if asset is None:
            continue
        href = sign_pc_href(asset.href)
        hrefs.append(href)
        src_files.append(rasterio.open(href))

    if not src_files:
        raise RuntimeError("COP-DEM items found but no readable data assets")

    mosaic, mosaic_transform = merge(src_files)
    for s in src_files:
        s.close()
    mosaic = mosaic[0]
    src_crs = "EPSG:4326"

    aoi32644 = aoi_gdf.to_crs(32644)
    bounds = tuple(float(x) for x in aoi32644.total_bounds)
    grid = build_reference_grid(bounds, cell_size_m=30.0, epsg=32644)
    dest = np.full((grid.height, grid.width), np.nan, dtype=np.float32)
    reproject(
        source=mosaic.astype(np.float32),
        destination=dest,
        src_transform=mosaic_transform,
        src_crs=src_crs,
        dst_transform=grid.transform,
        dst_crs=f"EPSG:{grid.epsg}",
        resampling=Resampling.bilinear,
        dst_nodata=np.nan,
    )
    nodata = -9999.0
    dem_out = np.where(np.isfinite(dest), dest, nodata).astype(np.float32)
    write_geotiff(dem_path, dem_out, grid.profile(dtype="float32", nodata=nodata))

    slope = _slope_degrees(np.where(dem_out == nodata, np.nan, dem_out), grid.cell_size_m)
    slope_out = np.where(np.isfinite(slope), slope, nodata).astype(np.float32)
    write_geotiff(slope_path, slope_out, grid.profile(dtype="float32", nodata=nodata))

    fetch_log.add(
        layer_id="chennai_dem_m",
        variable="dem",
        source_name="Copernicus DEM GLO-30 (Planetary Computer)",
        query_parameters={"collections": ["cop-dem-glo-30"], "bbox": bbox, "n_tiles": len(hrefs)},
        resolved_source_url=hrefs[0] if hrefs else PC_STAC,
        fetch_timestamp=utc_now_iso(),
        data_dates=["static_copernicus_glo30"],
        local_path=str(dem_path),
        checksum_sha256=sha256_file(dem_path),
        native_crs=src_crs,
        native_resolution_m=30.0,
        notes="Static DEM fetched once for entire project",
        status="ok",
    )
    fetch_log.add(
        layer_id="chennai_slope_deg",
        variable="slope",
        source_name="Derived from Copernicus DEM GLO-30",
        query_parameters={"method": "numpy_gradient_horn", "cell_size_m": grid.cell_size_m},
        resolved_source_url=hrefs[0] if hrefs else PC_STAC,
        data_dates=["static_derived"],
        local_path=str(slope_path),
        checksum_sha256=sha256_file(slope_path),
        native_crs=f"EPSG:{grid.epsg}",
        native_resolution_m=grid.cell_size_m,
        status="ok",
    )
    return [
        {"layer_id": "chennai_dem_m", "variable": "dem", "unit": "m", "path": str(dem_path), "data_type": "raster"},
        {
            "layer_id": "chennai_slope_deg",
            "variable": "slope",
            "unit": "deg",
            "path": str(slope_path),
            "data_type": "raster",
        },
    ]
