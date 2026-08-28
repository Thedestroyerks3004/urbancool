"""Shared extractor helpers (STAC / Planetary Computer, raster IO)."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx
import numpy as np

logger = logging.getLogger(__name__)

PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"


def bbox_wgs84(gdf_any_crs) -> list[float]:
    """Return [west, south, east, north] in EPSG:4326."""
    if gdf_any_crs.crs is None:
        raise ValueError("AOI GeoDataFrame has no CRS")
    g = gdf_any_crs if gdf_any_crs.crs.to_epsg() == 4326 else gdf_any_crs.to_crs(4326)
    minx, miny, maxx, maxy = g.total_bounds
    return [float(minx), float(miny), float(maxx), float(maxy)]


def sign_pc_href(href: str) -> str:
    """Sign a Planetary Computer asset HREF if planetary-computer is installed."""
    try:
        import planetary_computer as pc

        return pc.sign(href)
    except Exception:  # noqa: BLE001
        return href


def download_url(url: str, dest: Path, *, headers: dict[str, str] | None = None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=600.0, follow_redirects=True, headers=headers or {}) as client:
        with client.stream("GET", url) as r:
            r.raise_for_status()
            with dest.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
    return dest


def host_allowed(url: str, allowed_hosts: Iterable[str]) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == h or host.endswith("." + h) or h in host for h in allowed_hosts)


def month_datetime_range(month_start: date) -> str:
    """STAC datetime string for a calendar month (inclusive)."""
    if month_start.month == 12:
        end = date(month_start.year + 1, 1, 1)
    else:
        end = date(month_start.year, month_start.month + 1, 1)
    last = end - timedelta(days=1)
    return f"{month_start.isoformat()}/{last.isoformat()}"


def write_geotiff(path: Path, array: np.ndarray, profile: dict[str, Any]) -> Path:
    import rasterio

    path.parent.mkdir(parents=True, exist_ok=True)
    prof = dict(profile)
    if array.ndim == 2:
        prof.update(height=array.shape[0], width=array.shape[1], count=1)
    else:
        prof.update(height=array.shape[1], width=array.shape[2], count=array.shape[0])
    with rasterio.open(path, "w", **prof) as dst:
        if array.ndim == 2:
            dst.write(array, 1)
        else:
            dst.write(array)
    return path
