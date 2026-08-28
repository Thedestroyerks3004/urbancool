"""Warp any raster onto the shared reference grid; clip to AOI polygon."""

from __future__ import annotations

from typing import Any

import numpy as np
import rasterio
from rasterio import features
from rasterio.enums import Resampling
from rasterio.warp import reproject

from chennai_uhi.grid import ReferenceGrid


RESAMPLE_MAP = {
    "bilinear": Resampling.bilinear,
    "nearest": Resampling.nearest,
    "cubic": Resampling.cubic,
}


def aoi_mask_for_grid(aoi_gdf, grid: ReferenceGrid) -> np.ndarray:
    geoms = [g for g in aoi_gdf.to_crs(grid.epsg).geometry if g is not None and not g.is_empty]
    mask = features.geometry_mask(
        geoms,
        out_shape=(grid.height, grid.width),
        transform=grid.transform,
        invert=True,
    )
    return mask


def warp_to_grid(
    src_path: str,
    grid: ReferenceGrid,
    *,
    resampling: str = "bilinear",
    categorical: bool = False,
    nodata: float = -9999.0,
) -> tuple[np.ndarray, dict[str, Any]]:
    rs = RESAMPLE_MAP["nearest" if categorical else resampling]
    with rasterio.open(src_path) as src:
        src_nodata = src.nodata
        dtype = "uint8" if categorical else "float32"
        if categorical:
            dest = np.zeros((grid.height, grid.width), dtype=np.uint8)
        else:
            dest = np.full((grid.height, grid.width), np.nan, dtype=np.float32)
        reproject(
            source=rasterio.band(src, 1),
            destination=dest,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=grid.transform,
            dst_crs=f"EPSG:{grid.epsg}",
            resampling=rs,
            src_nodata=src_nodata,
            dst_nodata=0 if categorical else np.nan,
        )
        meta = {
            "src_crs": str(src.crs),
            "src_epsg": src.crs.to_epsg() if src.crs else None,
            "src_res": list(src.res),
            "src_nodata": src_nodata,
            "resampling": "nearest" if categorical else resampling,
        }
    if categorical:
        return dest, meta
    out = np.where(np.isfinite(dest), dest, nodata).astype(np.float32)
    return out, meta


def apply_aoi_mask(array: np.ndarray, mask: np.ndarray, nodata: float, categorical: bool = False) -> np.ndarray:
    out = array.copy()
    if categorical:
        out = np.where(mask, out, 0)
    else:
        out = np.where(mask, out, nodata)
    return out
