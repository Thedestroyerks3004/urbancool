from __future__ import annotations

from typing import Any

import numpy as np
from scipy import ndimage


def fill_small_gaps(
    array: np.ndarray,
    nodata: float,
    max_radius_px: int = 3,
) -> tuple[np.ndarray, dict[str, Any]]:
    data = array.astype(np.float64).copy()
    valid = np.isfinite(data) & (data != nodata)
    n_nodata_before = int((~valid).sum())
    if n_nodata_before == 0 or not valid.any():
        return array, {
            "method": "none",
            "max_radius_px": max_radius_px,
            "nodata_before": n_nodata_before,
            "nodata_after": n_nodata_before,
            "filled_pixels": 0,
            "remaining_nodata": n_nodata_before,
        }

    dist, indices = ndimage.distance_transform_edt(~valid, return_distances=True, return_indices=True)
    fillable = (~valid) & (dist <= max_radius_px) & (dist > 0)
    out = data.copy()
    out[fillable] = data[indices[0][fillable], indices[1][fillable]]
    valid_after = np.isfinite(out) & (out != nodata)
    out = np.where(np.isfinite(out) & ((array == nodata) | fillable | valid), out, nodata)
    out = np.where(valid | fillable, out, nodata)
    n_after = int((~(np.isfinite(out) & (out != nodata))).sum())
    filled = int(fillable.sum())
    return out.astype(array.dtype), {
        "method": "nearest_within_radius",
        "max_radius_px": max_radius_px,
        "nodata_before": n_nodata_before,
        "nodata_after": n_after,
        "filled_pixels": filled,
        "remaining_nodata": n_after,
        "note": "Gaps farther than max_radius_px left as NoData — not interpolated as real data",
    }
