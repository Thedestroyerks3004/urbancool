"""
Turns per-pixel metric arrays into a GeoJSON point grid the map can render.

If a drawn region has more pixels than MAX_UNAGGREGATED_PIXELS, cells are grouped into
NxN blocks and averaged first (an "aggregated" response) so the payload and the number of
map markers stay manageable for a very large region -- the response says whether this
happened and what the resulting cell size is, so the frontend can show it honestly.
"""

import numpy as np
import pandas as pd

MAX_UNAGGREGATED_PIXELS = 800000


def affine_pixel_centers_to_lonlat(transform, rows, cols):
    # Vectorized equivalent of rasterio.transform.xy(transform, rows, cols) at pixel
    # centers -- rasterio's own per-pixel Python-call version was the single biggest
    # cost in building a grid response (18s for 50k pixels); this closed-form affine
    # evaluation over full numpy arrays does the same math in milliseconds.
    rows_c = np.asarray(rows, dtype=np.float64) + 0.5
    cols_c = np.asarray(cols, dtype=np.float64) + 0.5
    lon = transform.a * cols_c + transform.b * rows_c + transform.c
    lat = transform.d * cols_c + transform.e * rows_c + transform.f
    return lon, lat


def build_grid_geojson(row_indices, col_indices, transform, metric_arrays_by_name, max_features=MAX_UNAGGREGATED_PIXELS):
    """Build a GeoJSON FeatureCollection: one Point feature per pixel (or per aggregated
    block, for a large region), carrying whichever metric arrays are passed in as
    properties. Returns (geojson, aggregated, cell_size_meters)."""
    row_indices = np.asarray(row_indices)
    col_indices = np.asarray(col_indices)
    n_pixels = len(row_indices)
    aggregated = n_pixels > max_features
    cell_size_meters = 10.0
    names = list(metric_arrays_by_name.keys())

    if aggregated:
        aggregation_factor = int(np.ceil(np.sqrt(n_pixels / max_features)))
        cell_size_meters = 10.0 * aggregation_factor
        agg_row = row_indices // aggregation_factor
        agg_col = col_indices // aggregation_factor
        df = pd.DataFrame({"key_row": agg_row, "key_col": agg_col})
        for name in names:
            df[name] = metric_arrays_by_name[name]
        grouped = df.groupby(["key_row", "key_col"], as_index=False).mean()
        center_row = grouped["key_row"].to_numpy() * aggregation_factor + aggregation_factor / 2.0
        center_col = grouped["key_col"].to_numpy() * aggregation_factor + aggregation_factor / 2.0
        lon_arr, lat_arr = affine_pixel_centers_to_lonlat(transform, center_row, center_col)
        value_columns = [grouped[name].to_numpy() for name in names]
    else:
        lon_arr, lat_arr = affine_pixel_centers_to_lonlat(transform, row_indices, col_indices)
        # float64 explicitly: rounding a float32 array to 4 decimals still leaves the
        # nearest *float32* bit pattern, which upcasts back to a long, non-round-tripping
        # decimal expansion in JSON (e.g. 0.2713 stored as 0.27129998803138733). Rounding
        # in float64 lands on the clean decimal value instead.
        value_columns = [np.asarray(metric_arrays_by_name[name], dtype=np.float64) for name in names]

    valid_mask = np.ones(len(lon_arr), dtype=bool)
    for col in value_columns:
        valid_mask &= ~pd.isna(col)

    # Rounding before JSON-encoding cuts payload size (and thus both serialization and
    # network-transfer time) roughly in half for a large grid, with no visible precision
    # loss: 6 decimal places on a lon/lat is sub-centimeter, and every metric here is
    # either a bounded index (-1..1, 0..100) or a rate -- 4 decimals is far past what's
    # meaningfully distinguishable when rendered.
    lon_list = np.round(lon_arr[valid_mask], 6).tolist()
    lat_list = np.round(lat_arr[valid_mask], 6).tolist()
    value_lists = [np.round(col[valid_mask], 4).tolist() for col in value_columns]

    features = []
    for i in range(len(lon_list)):
        props = {names[j]: value_lists[j][i] for j in range(len(names))}
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon_list[i], lat_list[i]]},
            "properties": props,
        })

    return {"type": "FeatureCollection", "features": features}, aggregated, cell_size_meters
