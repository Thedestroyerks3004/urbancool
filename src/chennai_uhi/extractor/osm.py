"""Building footprints + road network from a dated Geofabrik OSM snapshot.

Pins one snapshot (URL + date + MD5) for the whole project — never a live rolling API.
Derives full-coverage geometry proxies (no height / H-W):
  - building footprint density
  - footprint compactness
  - road-network-derived street width proxy
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import httpx
import numpy as np
from shapely.geometry import box

from chennai_uhi.extractor.base import download_url
from chennai_uhi.logging_util import FetchLog, md5_file, sha256_file, utc_now_iso

logger = logging.getLogger(__name__)

GEOFABRIK_INDIA = "https://download.geofabrik.de/asia/india-latest.osm.pbf"
GEOFABRIK_INDIA_MD5 = "https://download.geofabrik.de/asia/india-latest.osm.pbf.md5"
# Southern zone extract is smaller; still authoritative Geofabrik
GEOFABRIK_SOUTH = "https://download.geofabrik.de/asia/india/southern-zone-latest.osm.pbf"
GEOFABRIK_SOUTH_MD5 = "https://download.geofabrik.de/asia/india/southern-zone-latest.osm.pbf.md5"


def _fetch_md5(url: str) -> tuple[str | None, str]:
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        text = r.text.strip()
    # format: "<md5>  filename" or just md5
    m = re.match(r"^([0-9a-fA-F]{32})\s+", text)
    if m:
        return m.group(1).lower(), text
    if re.match(r"^[0-9a-fA-F]{32}$", text):
        return text.lower(), text
    return None, text


def _pin_file(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def extract_osm(
    aoi_gdf,
    raw_dir: Path,
    fetch_log: FetchLog,
    *,
    use_southern_zone: bool = True,
) -> list[dict[str, Any]]:
    """Download pinned Geofabrik PBF, clip buildings/roads to Chennai AOI."""
    out_dir = raw_dir / "osm"
    out_dir.mkdir(parents=True, exist_ok=True)
    pin_path = out_dir / "geofabrik_snapshot_pin.json"

    pbf_url = GEOFABRIK_SOUTH if use_southern_zone else GEOFABRIK_INDIA
    md5_url = GEOFABRIK_SOUTH_MD5 if use_southern_zone else GEOFABRIK_INDIA_MD5
    pbf_name = Path(pbf_url).name
    pbf_path = out_dir / pbf_name

    source_md5, md5_raw = _fetch_md5(md5_url)
    if not pbf_path.exists():
        logger.info("Downloading Geofabrik snapshot %s …", pbf_url)
        download_url(pbf_url, pbf_path)
    computed_md5 = md5_file(pbf_path)
    pinned_date = datetime.now(timezone.utc).date().isoformat()
    pin = {
        "resolved_source_url": pbf_url,
        "md5_url": md5_url,
        "source_md5": source_md5,
        "computed_md5": computed_md5,
        "md5_match": bool(source_md5 and source_md5 == computed_md5),
        "pinned_at": utc_now_iso(),
        "data_dates": [pinned_date],
        "geofabrik_md5_file_raw": md5_raw,
    }
    import json

    _pin_file(pin_path, json.dumps(pin, indent=2) + "\n")

    fetch_log.add(
        layer_id="geofabrik_osm_pbf",
        variable="osm_snapshot",
        source_name="Geofabrik OSM extract (dated snapshot)",
        query_parameters={"url": pbf_url, "md5_url": md5_url},
        resolved_source_url=pbf_url,
        data_dates=[datetime.now(timezone.utc).date().isoformat()],
        last_available_date=datetime.now(timezone.utc).date().isoformat(),
        local_path=str(pbf_path),
        checksum_md5_source=source_md5,
        checksum_md5_computed=computed_md5,
        checksum_sha256=sha256_file(pbf_path),
        notes="Pinned snapshot for whole project; do not switch mid-study",
        status="ok" if pin["md5_match"] else "checksum_mismatch",
        extra={"pin_file": str(pin_path)},
    )

    layers: list[dict[str, Any]] = [
        {
            "layer_id": "geofabrik_osm_pbf",
            "variable": "osm_snapshot",
            "unit": "file",
            "path": str(pbf_path),
            "data_type": "file",
            "pin": pin,
        }
    ]

    buildings_path = out_dir / "chennai_buildings.gpkg"
    roads_path = out_dir / "chennai_roads.gpkg"
    density_path = out_dir / "chennai_building_density_raw.tif"
    compact_path = out_dir / "chennai_building_compactness_raw.tif"
    street_w_path = out_dir / "chennai_street_width_raw.tif"

    derived_ready = all(
        p.exists() and p.stat().st_size > 0
        for p in (buildings_path, roads_path, density_path, compact_path, street_w_path)
    )
    if derived_ready:
        logger.info("Reusing existing OSM vector/raster derivatives")
        for layer_id, variable, unit, path, dtype in [
            ("chennai_buildings", "building_footprints", "vector", str(buildings_path), "vector"),
            ("chennai_roads", "road_network", "vector", str(roads_path), "vector"),
            ("chennai_building_density", "building_density", "fraction", str(density_path), "raster"),
            ("chennai_building_compactness", "building_compactness", "index", str(compact_path), "raster"),
            ("chennai_street_width", "street_width", "m", str(street_w_path), "raster"),
        ]:
            fetch_log.add(
                layer_id=layer_id,
                variable=variable,
                source_name="Derived from pinned Geofabrik OSM snapshot",
                query_parameters={"parent_pbf": pbf_url, "aoi": "chennai_boundary", "reused_local": True},
                resolved_source_url=pbf_url,
                local_path=path,
                checksum_sha256=sha256_file(Path(path)),
                data_dates=pin["data_dates"],
                native_crs="EPSG:32644" if dtype == "raster" else str(aoi_gdf.crs),
                native_resolution_m=30.0 if dtype == "raster" else None,
                notes="No building height / H-W — excluded by study design; reused local derivative",
                status="ok",
            )
            layers.append(
                {"layer_id": layer_id, "variable": variable, "unit": unit, "path": path, "data_type": dtype}
            )
        return layers

    try:
        buildings, roads = _extract_vectors(pbf_path, aoi_gdf)
        if not buildings.empty:
            buildings.to_file(buildings_path, driver="GPKG")
        if not roads.empty:
            roads.to_file(roads_path, driver="GPKG")

        density_path, compact_path, street_w_path = _derive_geometry_proxies(
            buildings, roads, aoi_gdf, out_dir
        )

        for layer_id, variable, unit, path, dtype in [
            ("chennai_buildings", "building_footprints", "vector", str(buildings_path), "vector"),
            ("chennai_roads", "road_network", "vector", str(roads_path), "vector"),
            ("chennai_building_density", "building_density", "fraction", str(density_path), "raster"),
            ("chennai_building_compactness", "building_compactness", "index", str(compact_path), "raster"),
            ("chennai_street_width", "street_width", "m", str(street_w_path), "raster"),
        ]:
            if not Path(path).exists():
                continue
            fetch_log.add(
                layer_id=layer_id,
                variable=variable,
                source_name="Derived from pinned Geofabrik OSM snapshot",
                query_parameters={"parent_pbf": pbf_url, "aoi": "chennai_boundary"},
                resolved_source_url=pbf_url,
                local_path=path,
                checksum_sha256=sha256_file(Path(path)),
                data_dates=pin["data_dates"],
                native_crs="EPSG:32644" if dtype == "raster" else str(aoi_gdf.crs),
                native_resolution_m=30.0 if dtype == "raster" else None,
                notes="No building height / H-W — excluded by study design",
                status="ok",
            )
            layers.append(
                {
                    "layer_id": layer_id,
                    "variable": variable,
                    "unit": unit,
                    "path": path,
                    "data_type": dtype,
                }
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("OSM vector extract failed")
        fetch_log.add(
            layer_id="chennai_buildings",
            variable="building_footprints",
            source_name="Geofabrik OSM",
            query_parameters={"pbf": str(pbf_path)},
            resolved_source_url=pbf_url,
            status="error",
            error=str(exc),
            notes="PBF downloaded; vector parse failed — install pyrosm or osmium",
        )

    return layers


def _extract_vectors(pbf_path: Path, aoi_gdf) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    aoi4326 = aoi_gdf.to_crs(4326)
    minx, miny, maxx, maxy = aoi4326.total_bounds
    bbox = [minx, miny, maxx, maxy]

    try:
        from pyrosm import OSM

        osm = OSM(str(pbf_path), bounding_box=bbox)
        buildings = osm.get_buildings()
        roads = osm.get_network(network_type="driving")
        if buildings is None:
            buildings = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        if roads is None:
            roads = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        # Precise clip to boundary (not just bbox)
        buildings = gpd.clip(buildings.to_crs(aoi_gdf.crs), aoi_gdf)
        roads = gpd.clip(roads.to_crs(aoi_gdf.crs), aoi_gdf)
        return buildings, roads
    except ImportError:
        pass

    # Fallback: GDAL/OGR openfilegdb-style via fiona/pyogrio with OSM driver if built
    try:
        import pyogrio

        buildings = pyogrio.read_dataframe(
            pbf_path, layer="multipolygons", where="building IS NOT NULL"
        )
        roads = pyogrio.read_dataframe(pbf_path, layer="lines", where="highway IS NOT NULL")
        buildings = gpd.clip(buildings.to_crs(aoi_gdf.crs), aoi_gdf)
        roads = gpd.clip(roads.to_crs(aoi_gdf.crs), aoi_gdf)
        return buildings, roads
    except Exception as exc:
        raise RuntimeError(
            "Cannot parse OSM PBF. Install pyrosm (`pip install pyrosm`) "
            f"or GDAL with OSM driver. Underlying error: {exc}"
        ) from exc


def _derive_geometry_proxies(
    buildings: gpd.GeoDataFrame,
    roads: gpd.GeoDataFrame,
    aoi_gdf,
    out_dir: Path,
) -> tuple[Path, Path, Path]:
    """Rasterize density, compactness, street-width proxy at 30 m on AOI grid."""
    import rasterio
    from rasterio import features

    from chennai_uhi.grid import build_reference_grid
    from chennai_uhi.extractor.base import write_geotiff

    bounds = tuple(float(x) for x in aoi_gdf.to_crs(32644).total_bounds)
    grid = build_reference_grid(bounds, 30.0, 32644)
    transform = grid.transform
    out_shape = (grid.height, grid.width)
    nodata = -9999.0

    b = buildings.to_crs(32644) if not buildings.empty else buildings
    r = roads.to_crs(32644) if not roads.empty else roads

    # Building density: fraction of cell covered by footprints
    density = np.zeros(out_shape, dtype=np.float32)
    if not b.empty:
        shapes = ((geom, 1) for geom in b.geometry if geom is not None and not geom.is_empty)
        burned = features.rasterize(
            shapes,
            out_shape=out_shape,
            transform=transform,
            fill=0,
            dtype=np.uint8,
            all_touched=True,
        )
        # Approximate coverage with all_touched binary; refine via area if needed
        density = burned.astype(np.float32)

    compactness = np.full(out_shape, nodata, dtype=np.float32)
    if not b.empty:
        # Per-feature 4πA/P² then rasterize mean via burn of values
        vals = []
        for geom in b.geometry:
            if geom is None or geom.is_empty or geom.area <= 0:
                continue
            per = geom.length
            if per <= 0:
                continue
            c = float(4 * np.pi * geom.area / (per**2))
            vals.append((geom, min(c, 1.0)))
        if vals:
            compactness = features.rasterize(
                vals,
                out_shape=out_shape,
                transform=transform,
                fill=nodata,
                dtype=np.float32,
                all_touched=True,
            )

    street_w = np.full(out_shape, nodata, dtype=np.float32)
    if not r.empty:
        # Street width proxy from highway class typical widths (meters) — not height-based
        width_map = {
            "motorway": 24.0,
            "trunk": 18.0,
            "primary": 14.0,
            "secondary": 12.0,
            "tertiary": 10.0,
            "residential": 8.0,
            "service": 5.0,
            "unclassified": 7.0,
        }
        hw_col = "highway" if "highway" in r.columns else None
        shapes_w = []
        for _, row in r.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            hw = str(row[hw_col]).lower() if hw_col else "residential"
            w = width_map.get(hw, 8.0)
            shapes_w.append((geom, w))
        if shapes_w:
            street_w = features.rasterize(
                shapes_w,
                out_shape=out_shape,
                transform=transform,
                fill=nodata,
                dtype=np.float32,
                all_touched=True,
            )

    density_path = out_dir / "chennai_building_density_raw.tif"
    compact_path = out_dir / "chennai_building_compactness_raw.tif"
    street_path = out_dir / "chennai_street_width_raw.tif"
    write_geotiff(density_path, density, grid.profile(dtype="float32", nodata=nodata))
    write_geotiff(compact_path, compactness, grid.profile(dtype="float32", nodata=nodata))
    write_geotiff(street_path, street_w, grid.profile(dtype="float32", nodata=nodata))
    return density_path, compact_path, street_path
