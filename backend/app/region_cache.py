"""
In-memory cache mapping a region_id to the pixel indices inside that user-drawn polygon.

Note (disclosed, not hidden): this is an in-memory prototype cache, not persistent storage.
Region ids are lost on server restart. A production deployment would persist this
(e.g. Redis or a database row) rather than a process-local dict.
"""

import hashlib
import json
import numpy as np
import rasterio.features
import rasterio.transform
from shapely.geometry import shape as shapely_shape

_REGION_CACHE = {}


def make_region_id(region_geojson_geometry):
    canonical = json.dumps(region_geojson_geometry, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def get_or_compute_region(region_geojson_geometry, meta):
    region_id = make_region_id(region_geojson_geometry)
    if region_id in _REGION_CACHE:
        return region_id, _REGION_CACHE[region_id]

    transform = rasterio.transform.Affine(*meta["transform"])
    shape = (meta["height"], meta["width"])
    region_polygon = shapely_shape(region_geojson_geometry)
    region_mask = rasterio.features.geometry_mask([region_polygon], out_shape=shape, transform=transform, invert=True)
    row_indices, col_indices = np.where(region_mask)

    entry = {"geometry": region_geojson_geometry, "row_indices": row_indices, "col_indices": col_indices}
    _REGION_CACHE[region_id] = entry
    return region_id, entry


def get_cached_region(region_id):
    if region_id not in _REGION_CACHE:
        raise KeyError(f"No cached region with id {region_id}. Call /region/analyze first.")
    return _REGION_CACHE[region_id]
