"""ESA WorldCover via Planetary Computer — citywide full-coverage land cover."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.merge import merge
from rasterio.warp import Resampling, reproject

from chennai_uhi.extractor.base import PC_STAC, bbox_wgs84, sign_pc_href, write_geotiff
from chennai_uhi.grid import build_reference_grid
from chennai_uhi.logging_util import FetchLog, sha256_file, utc_now_iso

logger = logging.getLogger(__name__)


def extract_landcover(aoi_gdf, raw_dir: Path, fetch_log: FetchLog) -> list[dict[str, Any]]:
    import planetary_computer as pc
    import pystac_client

    out_path = raw_dir / "landcover" / "chennai_landcover_worldcover_raw.tif"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bbox = bbox_wgs84(aoi_gdf)

    catalog = pystac_client.Client.open(PC_STAC, modifier=pc.sign_inplace)
    # Prefer v200 (2021) — most recent ESA WorldCover release; document actual year
    search = catalog.search(collections=["esa-worldcover"], bbox=bbox, max_items=20)
    items = list(search.items())
    if not items:
        fetch_log.add(
            layer_id="chennai_landcover",
            variable="landcover",
            source_name="ESA WorldCover",
            query_parameters={"collections": ["esa-worldcover"], "bbox": bbox},
            resolved_source_url=PC_STAC,
            status="error",
            error="No WorldCover items",
        )
        return []

    # Prefer newest year
    def item_year(it) -> int:
        props = it.properties
        return int(props.get("start_datetime", props.get("datetime", "2021"))[:4])

    items = sorted(items, key=item_year, reverse=True)
    year = item_year(items[0])
    year_items = [i for i in items if item_year(i) == year]

    srcs = []
    hrefs = []
    for item in year_items:
        asset = item.assets.get("map")
        if asset is None:
            continue
        href = sign_pc_href(asset.href)
        hrefs.append(href)
        srcs.append(rasterio.open(href))
    if not srcs:
        raise RuntimeError("WorldCover assets missing")

    mosaic, transform = merge(srcs)
    for s in srcs:
        s.close()
    data = mosaic[0]
    src_crs = "EPSG:4326"

    bounds = tuple(float(x) for x in aoi_gdf.to_crs(32644).total_bounds)
    # Native WorldCover is 10 m — keep finer than 30 m
    grid = build_reference_grid(bounds, cell_size_m=10.0, epsg=32644)
    dest = np.zeros((grid.height, grid.width), dtype=np.uint8)
    reproject(
        source=data,
        destination=dest,
        src_transform=transform,
        src_crs=src_crs,
        dst_transform=grid.transform,
        dst_crs=f"EPSG:{grid.epsg}",
        resampling=Resampling.nearest,
    )
    write_geotiff(
        out_path,
        dest.astype(np.uint8),
        {**grid.profile(dtype="uint8", nodata=0), "nodata": 0},
    )

    last_avail = f"{year}-12-31"
    note = None
    from datetime import date

    # WorldCover epoch may lag "today" — record explicitly
    from chennai_uhi.config import present_date_utc

    today = present_date_utc()
    if date(year, 12, 31) < today:
        note = (
            f"ESA WorldCover last available epoch year={year} "
            f"(last_available_date={last_avail}); pipeline present date={today.isoformat()}. "
            "Static product — not monthly."
        )

    fetch_log.add(
        layer_id="chennai_landcover",
        variable="landcover",
        source_name="ESA WorldCover (Planetary Computer)",
        query_parameters={"collections": ["esa-worldcover"], "bbox": bbox, "year": year},
        resolved_source_url=hrefs[0] if hrefs else PC_STAC,
        fetch_timestamp=utc_now_iso(),
        data_dates=[str(year)],
        last_available_date=last_avail,
        local_path=str(out_path),
        checksum_sha256=sha256_file(out_path),
        native_crs=src_crs,
        native_resolution_m=10.0,
        notes=note,
        status="ok",
    )
    return [
        {
            "layer_id": "chennai_landcover",
            "variable": "landcover",
            "unit": "class",
            "path": str(out_path),
            "data_type": "raster",
            "categorical": True,
            "native_resolution_m": 10.0,
        }
    ]
