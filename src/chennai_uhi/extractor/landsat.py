"""Landsat 8/9 Collection 2 Level-2 via Planetary Computer STAC.

Produces monthly composites for:
  - LST (Kelvin) from ST_B10 + scale/offset
  - NDVI from SR_B5, SR_B4
  - NDBI from SR_B6, SR_B5

Single sensor family (LC08/LC09) for the whole 2024–present window — no MODIS blend.
Cloud/shadow masking uses QA_PIXEL (not raw scenes).
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject
from rasterio.windows import from_bounds

from chennai_uhi.extractor.base import (
    PC_STAC,
    bbox_wgs84,
    month_datetime_range,
    sign_pc_href,
    write_geotiff,
)
from chennai_uhi.logging_util import FetchLog, sha256_file, utc_now_iso

logger = logging.getLogger(__name__)

# Landsat C2 L2 scale factors (USGS)
SR_SCALE, SR_OFFSET = 2.75e-5, -0.2
ST_SCALE, ST_OFFSET = 0.00341802, 149.0  # Kelvin
INDEX_DENOM_EPSILON = 1e-6

# QA_PIXEL bits (Collection 2): cloud, cloud shadow, dilated cloud, cirrus
# Dilated Cloud=1, Cirrus=2, Cloud=3, Cloud Shadow=4 (0-indexed bits)
QA_MASK_BITS = (1, 2, 3, 4)


def _qa_clear(qa: np.ndarray) -> np.ndarray:
    mask = np.ones(qa.shape, dtype=bool)
    for bit in QA_MASK_BITS:
        mask &= ((qa >> bit) & 1) == 0
    # fill / dilated fill often bit 0
    mask &= ((qa >> 0) & 1) == 0
    return mask


def _read_band_window(
    href: str,
    bbox_4326: list[float],
    out_crs,
    out_transform,
    out_shape,
    resampling: Resampling = Resampling.bilinear,
):
    """Read one COG band, reproject to target grid window covering bbox."""
    signed = sign_pc_href(href)
    with rasterio.open(signed) as src:
        dest = np.full(out_shape, np.nan, dtype=np.float32)
        reproject(
            source=rasterio.band(src, 1),
            destination=dest,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=out_transform,
            dst_crs=out_crs,
            resampling=resampling,
            src_nodata=src.nodata,
            dst_nodata=np.nan,
        )
        return dest, str(src.crs), src.res


def _target_grid_from_aoi(aoi_gdf, resolution_m: float = 30.0):
    """Temporary native-ish grid in EPSG:32644 covering AOI (extractor stores raw composites)."""
    from rasterio.transform import from_origin

    from chennai_uhi.grid import build_reference_grid

    b = tuple(float(x) for x in aoi_gdf.to_crs(32644).total_bounds)
    grid = build_reference_grid(b, cell_size_m=resolution_m, epsg=32644)
    return grid


def _collect_scene_dates(items: list[Any]) -> list[str]:
    """Normalise STAC item datetimes to ISO dates for downstream date-bound fetches."""
    dates: list[str] = []
    seen: set[str] = set()
    for item in items:
        props = item.properties if hasattr(item, "properties") else item.get("properties", {})
        dt = props.get("datetime") or props.get("start_datetime") or ""
        iso = str(dt)[:10]
        if iso and iso not in seen:
            seen.add(iso)
            dates.append(iso)
    return sorted(dates)


def _composite_month(
    items: list[Any],
    aoi_gdf,
    variable: str,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Median composite of clear pixels for one month."""
    grid = _target_grid_from_aoi(aoi_gdf, 30.0)
    stack: list[np.ndarray] = []
    scene_dates = _collect_scene_dates(items)
    sensors: set[str] = set()
    native_crs = None
    native_res = None

    for item in items:
        assets = item.assets if hasattr(item, "assets") else item.get("assets", {})
        props = item.properties if hasattr(item, "properties") else item.get("properties", {})
        platform = props.get("platform") or props.get("eo:platform") or ""
        sensors.add(str(platform))
        dt = props.get("datetime") or props.get("start_datetime") or ""
        iso = str(dt)[:10]
        if iso:
            scene_dates.append(iso)

        def asset_href(key: str) -> str | None:
            a = assets.get(key)
            if a is None:
                return None
            return a.href if hasattr(a, "href") else a.get("href")

        qa_href = asset_href("qa_pixel")
        if qa_href is None:
            continue

        try:
            qa, native_crs, res = _read_band_window(
                qa_href,
                bbox_wgs84(aoi_gdf),
                f"EPSG:{grid.epsg}",
                grid.transform,
                (grid.height, grid.width),
                resampling=Resampling.nearest,
            )
            native_res = abs(res[0]) if res else None
            # QA is integer; reproject may float — round
            qa_i = np.nan_to_num(qa, nan=1).astype(np.uint16)
            clear = _qa_clear(qa_i)

            if variable == "lst":
                href = asset_href("lwir11") or asset_href("ST_B10")
                if not href:
                    continue
                raw, _, _ = _read_band_window(
                    href, bbox_wgs84(aoi_gdf), f"EPSG:{grid.epsg}", grid.transform, (grid.height, grid.width)
                )
                val = raw.astype(np.float64) * ST_SCALE + ST_OFFSET
            elif variable == "ndvi":
                nir_h = asset_href("nir08") or asset_href("SR_B5")
                red_h = asset_href("red") or asset_href("SR_B4")
                if not nir_h or not red_h:
                    continue
                nir, _, _ = _read_band_window(
                    nir_h, bbox_wgs84(aoi_gdf), f"EPSG:{grid.epsg}", grid.transform, (grid.height, grid.width)
                )
                red, _, _ = _read_band_window(
                    red_h, bbox_wgs84(aoi_gdf), f"EPSG:{grid.epsg}", grid.transform, (grid.height, grid.width)
                )
                nir_r = nir.astype(np.float64) * SR_SCALE + SR_OFFSET
                red_r = red.astype(np.float64) * SR_SCALE + SR_OFFSET
                denom = nir_r + red_r
                valid_reflectance = np.isfinite(nir_r) & np.isfinite(red_r) & (nir_r >= 0) & (red_r >= 0)
                val = np.where(valid_reflectance & (np.abs(denom) >= INDEX_DENOM_EPSILON), (nir_r - red_r) / denom, np.nan)
            elif variable == "ndbi":
                swir_h = asset_href("swir16") or asset_href("SR_B6")
                nir_h = asset_href("nir08") or asset_href("SR_B5")
                if not swir_h or not nir_h:
                    continue
                swir, _, _ = _read_band_window(
                    swir_h, bbox_wgs84(aoi_gdf), f"EPSG:{grid.epsg}", grid.transform, (grid.height, grid.width)
                )
                nir, _, _ = _read_band_window(
                    nir_h, bbox_wgs84(aoi_gdf), f"EPSG:{grid.epsg}", grid.transform, (grid.height, grid.width)
                )
                swir_r = swir.astype(np.float64) * SR_SCALE + SR_OFFSET
                nir_r = nir.astype(np.float64) * SR_SCALE + SR_OFFSET
                denom = swir_r + nir_r
                valid_reflectance = np.isfinite(swir_r) & np.isfinite(nir_r) & (swir_r >= 0) & (nir_r >= 0)
                val = np.where(valid_reflectance & (np.abs(denom) >= INDEX_DENOM_EPSILON), (swir_r - nir_r) / denom, np.nan)
            else:
                raise ValueError(variable)

            if variable == "lst":
                val = np.where(np.isfinite(val) & (val >= 280.0) & (val <= 340.0), val, np.nan)
            scene = np.where(clear, val.astype(np.float32), np.nan)
            if np.any(np.isfinite(scene)):
                stack.append(scene)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Scene skipped (%s): %s", dt, exc)
            continue

    meta = {
        "n_scenes_used": len(stack),
        "scene_dates": scene_dates,
        "sensors": sorted(sensors),
        "native_crs": native_crs,
        "native_resolution_m": native_res,
        "composite": "median_clear_qa_pixel",
    }
    if not stack:
        arr = np.full((grid.height, grid.width), np.nan, dtype=np.float32)
        meta["empty"] = True
        return arr, meta

    cube = np.stack(stack, axis=0)
    with np.errstate(all="ignore"):
        comp = np.nanmedian(cube, axis=0).astype(np.float32)
    return comp, {**meta, "grid": grid}


def extract_landsat_monthly(
    aoi_gdf,
    raw_dir: Path,
    fetch_log: FetchLog,
    months: list[date],
    temporal_end: date,
) -> list[dict[str, Any]]:
    """Fetch & composite LST/NDVI/NDBI for each month; log every fetch."""
    import os

    import planetary_computer as pc
    import pystac_client

    # Avoid indefinite hangs on signed COG reads
    os.environ.setdefault("GDAL_HTTP_TIMEOUT", "90")
    os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "3")
    os.environ.setdefault("CPL_VSIL_CURL_CACHE_SIZE", "20000000")

    out_layers: list[dict[str, Any]] = []
    bbox = bbox_wgs84(aoi_gdf)
    catalog = pystac_client.Client.open(PC_STAC, modifier=pc.sign_inplace)
    variables = ("lst", "ndvi", "ndbi")
    landsat_dir = raw_dir / "landsat"
    landsat_dir.mkdir(parents=True, exist_ok=True)

    for month in months:
        dt_range = month_datetime_range(month)
        yyyymm = month.strftime("%Y-%m")
        query = {
            "platform": {"in": ["landsat-8", "landsat-9"]},
            "eo:cloud_cover": {"lt": 80},
        }

        existing = {
            v: landsat_dir / f"chennai_{v}_{yyyymm}_raw.tif" for v in variables
        }
        rebuild = os.environ.get("CHENNAI_UHI_REBUILD_LANDSAT") == "1"
        rebuild_from_raw = os.environ.get("CHENNAI_UHI_REBUILD_LANDSAT_FROM")
        rebuild_from = None
        if rebuild_from_raw:
            try:
                rebuild_from = date.fromisoformat(rebuild_from_raw[:7] + "-01")
            except ValueError:
                logger.warning("Ignoring invalid CHENNAI_UHI_REBUILD_LANDSAT_FROM=%s", rebuild_from_raw)
        rebuild = rebuild or (rebuild_from is not None and month >= rebuild_from)
        all_exist = all(p.exists() and p.stat().st_size > 0 for p in existing.values())
        if all_exist and not rebuild:
            logger.info("Reusing existing Landsat composites for %s", yyyymm)
            try:
                reused_items = list(
                    catalog.search(
                        collections=["landsat-c2-l2"],
                        bbox=bbox,
                        datetime=dt_range,
                        query=query,
                        max_items=100,
                    ).items()
                )
                data_dates = _collect_scene_dates(reused_items)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not refresh scene dates for reused month %s: %s", yyyymm, exc)
                data_dates = []
            fetch_log.add(
                layer_id=f"landsat_stac_{yyyymm}",
                variable="landsat_stac_search",
                source_name="Microsoft Planetary Computer STAC — landsat-c2-l2",
                query_parameters={
                    "collections": ["landsat-c2-l2"],
                    "bbox": bbox,
                    "datetime": dt_range,
                    "query": query,
                    "access": "planetary_computer",
                    "reused_local": True,
                },
                resolved_source_url=PC_STAC,
                data_dates=data_dates,
                sensors=["LC08", "LC09"],
                notes="Skipped STAC re-fetch; local monthly composites already present",
                status="ok",
            )
            for variable in variables:
                out_path = existing[variable]
                unit = "k" if variable == "lst" else "index"
                layer_id = f"chennai_{variable}_{yyyymm}"
                rec = fetch_log.add(
                    layer_id=layer_id,
                    variable=variable,
                    source_name="Landsat 8/9 Collection 2 Level-2 (Planetary Computer)",
                    query_parameters={
                        "month": yyyymm,
                        "bbox": bbox,
                        "datetime": dt_range,
                        "composite": "monthly_median_qa_masked",
                        "variable": variable,
                        "unit": unit,
                        "reused_local": True,
                    },
                    resolved_source_url=PC_STAC,
                    data_dates=data_dates or [yyyymm],
                    last_available_date=None,
                    local_path=str(out_path),
                    checksum_sha256=sha256_file(out_path),
                    native_crs="EPSG:32644",
                    native_resolution_m=30.0,
                    sensors=["LC08", "LC09"],
                    notes="Reused local composite from prior extractor run",
                    status="ok",
                )
                out_layers.append(
                    {
                        "layer_id": layer_id,
                        "variable": variable,
                        "unit": unit,
                        "path": str(out_path),
                        "month": yyyymm,
                        "fetch_record": rec,
                        "data_type": "raster",
                    }
                )
            continue

        search = catalog.search(
            collections=["landsat-c2-l2"],
            bbox=bbox,
            datetime=dt_range,
            query=query,
            max_items=100,
        )
        items = list(search.items())
        scene_dates = _collect_scene_dates(items)
        fetch_log.add(
            layer_id=f"landsat_stac_{yyyymm}",
            variable="landsat_stac_search",
            source_name="Microsoft Planetary Computer STAC — landsat-c2-l2",
            query_parameters={
                "collections": ["landsat-c2-l2"],
                "bbox": bbox,
                "datetime": dt_range,
                "query": query,
                "access": "planetary_computer",
            },
            resolved_source_url=PC_STAC,
            data_dates=scene_dates,
            last_available_date=max(scene_dates) if scene_dates else None,
            sensors=["LC08", "LC09"],
            notes=f"n_items={len(items)}",
            status="ok" if items else "empty",
        )

        for variable in variables:
            layer_id = f"chennai_{variable}_{yyyymm}"
            out_path = existing[variable]
            if out_path.exists() and out_path.stat().st_size > 0 and not rebuild:
                unit = "k" if variable == "lst" else "index"
                rec = fetch_log.add(
                    layer_id=layer_id,
                    variable=variable,
                    source_name="Landsat 8/9 Collection 2 Level-2 (Planetary Computer)",
                    query_parameters={"month": yyyymm, "variable": variable, "reused_local": True},
                    resolved_source_url=PC_STAC,
                    local_path=str(out_path),
                    checksum_sha256=sha256_file(out_path),
                    native_resolution_m=30.0,
                    sensors=["LC08", "LC09"],
                    status="ok",
                    notes="Reused local composite",
                )
                out_layers.append(
                    {
                        "layer_id": layer_id,
                        "variable": variable,
                        "unit": unit,
                        "path": str(out_path),
                        "month": yyyymm,
                        "fetch_record": rec,
                        "data_type": "raster",
                    }
                )
                continue
            try:
                arr, meta = _composite_month(items, aoi_gdf, variable)
                grid = meta.get("grid") or _target_grid_from_aoi(aoi_gdf)
                nodata = -9999.0
                out_arr = np.where(np.isfinite(arr), arr, nodata).astype(np.float32)
                write_geotiff(out_path, out_arr, grid.profile(dtype="float32", nodata=nodata))
                scene_dates = meta.get("scene_dates") or []
                last_avail = max(scene_dates) if scene_dates else None
                if last_avail and date.fromisoformat(last_avail) < temporal_end and month == months[-1]:
                    lag_note = (
                        f"Source last scene date {last_avail} is older than "
                        f"pipeline present date {temporal_end.isoformat()}"
                    )
                else:
                    lag_note = None
                unit = "k" if variable == "lst" else "index"
                rec = fetch_log.add(
                    layer_id=layer_id,
                    variable=variable,
                    source_name="Landsat 8/9 Collection 2 Level-2 (Planetary Computer)",
                    query_parameters={
                        "month": yyyymm,
                        "bbox": bbox,
                        "datetime": dt_range,
                        "composite": "monthly_median_qa_masked",
                        "variable": variable,
                        "unit": unit,
                    },
                    resolved_source_url=PC_STAC,
                    data_dates=scene_dates,
                    last_available_date=last_avail,
                    local_path=str(out_path),
                    checksum_sha256=sha256_file(out_path) if out_path.exists() else None,
                    native_crs=str(meta.get("native_crs")),
                    native_resolution_m=30.0,
                    sensors=meta.get("sensors") or ["LC08", "LC09"],
                    notes=lag_note or meta.get("composite"),
                    status="ok" if meta.get("n_scenes_used", 0) > 0 else "empty_month",
                    extra={
                        "n_scenes_used": meta.get("n_scenes_used"),
                        "gap_month": meta.get("n_scenes_used", 0) == 0,
                    },
                )
                out_layers.append(
                    {
                        "layer_id": layer_id,
                        "variable": variable,
                        "unit": unit,
                        "path": str(out_path),
                        "month": yyyymm,
                        "fetch_record": rec,
                        "data_type": "raster",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                fetch_log.add(
                    layer_id=layer_id,
                    variable=variable,
                    source_name="Landsat 8/9 Collection 2 Level-2 (Planetary Computer)",
                    query_parameters={"month": yyyymm, "variable": variable},
                    resolved_source_url=PC_STAC,
                    status="error",
                    error=str(exc),
                )
                logger.exception("Landsat %s %s failed", variable, yyyymm)

    return out_layers
