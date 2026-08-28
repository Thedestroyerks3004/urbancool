"""Authoritative Chennai AOI (full city/metropolitan administrative boundary).

Sources (tried in order):
  1. GADM administrative boundary for Chennai (India ADM2 / city)
  2. Geofabrik/OSM relation for Chennai Corporation / Chennai district

Never hand-draw or approximate the AOI. Boundary is written once and reused.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import geopandas as gpd
import httpx
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

logger = logging.getLogger(__name__)

# GADM 4.1 India ADM2 GeoJSON subset endpoints / known mirrors.
# Primary: GADM download page requires interactive download; we use the
# documented geopackage URL pattern and filter to Chennai.
GADM_IND_URL = (
    "https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_IND_2.json.zip"
)
# Nominatim for OSM relation lookup (read-only; result pinned to file).
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

CHENNAI_NAME_FILTERS = (
    "chennai",
    "madras",
)


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


def _fetch_gadm_chennai(out_dir: Path) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    """Download GADM IND ADM2 and filter to Chennai district/city."""
    zip_path = out_dir / "gadm41_IND_2.json.zip"
    meta: dict[str, Any] = {
        "source_name": "GADM 4.1 India ADM2",
        "query_parameters": {"country": "IND", "level": 2, "filter": "Chennai"},
        "resolved_source_url": GADM_IND_URL,
        "fetch_timestamp": datetime.now(timezone.utc).isoformat(),
        "host": _host(GADM_IND_URL),
    }
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        r = client.get(GADM_IND_URL)
        r.raise_for_status()
        zip_path.write_bytes(r.content)

    gdf = gpd.read_file(f"zip://{zip_path}")
    name_cols = [c for c in gdf.columns if "NAME" in c.upper()]
    mask = False
    for col in name_cols:
        for needle in CHENNAI_NAME_FILTERS:
            mask = mask | gdf[col].astype(str).str.lower().str.contains(needle, na=False)
    subset = gdf.loc[mask].copy()
    if subset.empty:
        raise RuntimeError("GADM download succeeded but no Chennai feature matched")
    # Prefer exact district match if present
    for col in name_cols:
        exact = subset[subset[col].astype(str).str.lower().isin(CHENNAI_NAME_FILTERS)]
        if not exact.empty:
            subset = exact
            break
    meta["data_dates"] = ["GADM 4.1 (static administrative)"]
    meta["feature_count"] = int(len(subset))
    meta["name_columns_used"] = name_cols
    return subset, meta


def _fetch_osm_nominatim_chennai(out_dir: Path) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    """Fallback: OSM administrative boundary via Nominatim (pinned snapshot)."""
    params = {
        "q": "Chennai Corporation, Tamil Nadu, India",
        "format": "geojson",
        "polygon_geojson": 1,
        "limit": 5,
    }
    meta: dict[str, Any] = {
        "source_name": "OpenStreetMap Nominatim (Chennai admin boundary)",
        "query_parameters": params,
        "resolved_source_url": NOMINATIM_URL,
        "fetch_timestamp": datetime.now(timezone.utc).isoformat(),
        "host": _host(NOMINATIM_URL),
    }
    headers = {"User-Agent": "chennai-uhi-pipeline/1.0 (research; urban heat island)"}
    with httpx.Client(timeout=60.0, headers=headers, follow_redirects=True) as client:
        r = client.get(NOMINATIM_URL, params=params)
        r.raise_for_status()
        payload = r.json()
    raw_path = out_dir / "chennai_nominatim_raw.geojson"
    raw_path.write_text(json.dumps(payload), encoding="utf-8")
    gdf = gpd.GeoDataFrame.from_features(payload.get("features", []), crs="EPSG:4326")
    if gdf.empty:
        # Try district query
        params["q"] = "Chennai, Tamil Nadu, India"
        meta["query_parameters"] = params
        with httpx.Client(timeout=60.0, headers=headers, follow_redirects=True) as client:
            r = client.get(NOMINATIM_URL, params=params)
            r.raise_for_status()
            payload = r.json()
        raw_path.write_text(json.dumps(payload), encoding="utf-8")
        gdf = gpd.GeoDataFrame.from_features(payload.get("features", []), crs="EPSG:4326")
    if gdf.empty:
        raise RuntimeError("Nominatim returned no geometry for Chennai")
    # Keep largest polygon (city/district extent)
    gdf["area_tmp"] = gdf.to_crs(32644).geometry.area
    gdf = gdf.sort_values("area_tmp", ascending=False).head(1).drop(columns=["area_tmp"])
    meta["data_dates"] = [datetime.now(timezone.utc).date().isoformat()]
    meta["note"] = (
        "OSM Nominatim snapshot pinned at fetch; for production prefer GADM "
        "or a dated Geofabrik extract."
    )
    return gdf, meta


def load_or_fetch_aoi(
    aoi_dir: Path,
    target_epsg: int = 32644,
    force: bool = False,
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    """Return full Chennai boundary in target CRS + fetch provenance meta."""
    aoi_dir.mkdir(parents=True, exist_ok=True)
    geojson_path = aoi_dir / "chennai_boundary.geojson"
    meta_path = aoi_dir / "chennai_boundary_fetch.json"
    gpkg_path = aoi_dir / "chennai_boundary.gpkg"

    if geojson_path.exists() and meta_path.exists() and not force:
        gdf = gpd.read_file(geojson_path)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if gdf.crs is None or gdf.crs.to_epsg() != target_epsg:
            gdf = gdf.to_crs(epsg=target_epsg)
        return gdf, meta

    errors: list[str] = []
    gdf = None
    meta: dict[str, Any] = {}
    try:
        gdf, meta = _fetch_gadm_chennai(aoi_dir)
    except Exception as exc:  # noqa: BLE001 — try fallback
        errors.append(f"GADM: {exc}")
        logger.warning("GADM AOI fetch failed: %s", exc)
        try:
            gdf, meta = _fetch_osm_nominatim_chennai(aoi_dir)
        except Exception as exc2:  # noqa: BLE001
            errors.append(f"Nominatim: {exc2}")
            raise RuntimeError(
                "Failed to obtain authoritative Chennai boundary. "
                f"Attempts: {errors}"
            ) from exc2

    assert gdf is not None
    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326)
    # Dissolve to single multipolygon covering full city extent
    geom = unary_union(gdf.geometry.values)
    out = gpd.GeoDataFrame(
        {"name": ["Chennai"], "source": [meta.get("source_name")]},
        geometry=[geom],
        crs=gdf.crs,
    )
    out = out.to_crs(epsg=target_epsg)
    out.to_file(geojson_path, driver="GeoJSON")
    out.to_file(gpkg_path, driver="GPKG")
    meta["output_crs_epsg"] = target_epsg
    meta["output_files"] = [str(geojson_path), str(gpkg_path)]
    meta["bbox_32644"] = list(out.total_bounds)
    meta_path.write_text(json.dumps(meta, indent=2, default=str) + "\n", encoding="utf-8")
    return out, meta


def aoi_polygon(gdf: gpd.GeoDataFrame):
    """Single shapely geometry for precise clip (not bbox)."""
    return unary_union(gdf.geometry.values)


def aoi_geojson_dict(gdf: gpd.GeoDataFrame) -> dict:
    geom = aoi_polygon(gdf)
    return mapping(geom)
