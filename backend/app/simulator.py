"""
Intervention simulator: perturbs the native-10m feature stack within a user-drawn region
and re-scores it with the trained model to estimate a before/after Heat Vulnerability
Index. Every perturbation constant is named and documented here -- these are illustrative
planning assumptions, not measured effect sizes from a field trial in this specific
corridor, and every simulation response must carry that caveat.
"""

import numpy as np
import pandas as pd
import rasterio.features
import rasterio.transform
from shapely.geometry import shape as shapely_shape

from app.feature_stack import FEATURE_NAMES
from app.grid_utils import affine_pixel_centers_to_lonlat

TREE_COVER_NDVI_BOOST = 0.15
COOL_ROOF_ALBEDO_BOOST = 0.20
GREEN_SPACE_NDVI_BOOST = 0.25
GREEN_SPACE_NDWI_BOOST = 0.05
GREEN_SPACE_BUILT_UP_REDUCTION_PCT = 20.0
GREEN_ROOF_NDVI_BOOST = 0.08
GREEN_ROOF_ALBEDO_BOOST = 0.05
COOL_PAVEMENT_ALBEDO_BOOST = 0.15
COOL_PAVEMENT_ROAD_DENSITY_THRESHOLD_KM_PER_KM2 = 5.0
WATER_FEATURE_NDWI_BOOST = 0.20
WATER_FEATURE_NDVI_BOOST = 0.05
WATER_FEATURE_BUILT_UP_REDUCTION_PCT = 12.0
DENSITY_REDUCTION_BUILDING_DENSITY_PCT = 25.0
DENSITY_REDUCTION_BUILT_UP_REDUCTION_PCT = 10.0
DENSITY_REDUCTION_NDVI_BOOST = 0.10

INTERVENTION_ASSUMPTIONS = {
    "add_tree_cover": {
        "description": "Plant trees across the selected area.",
        "assumptions": {"TREE_COVER_NDVI_BOOST": TREE_COVER_NDVI_BOOST},
        "rationale": "Mature tree canopy typically raises local NDVI by roughly this order of magnitude in tropical urban settings; this is an illustrative planning constant, not a measured value for this specific corridor.",
    },
    "cool_roof": {
        "description": "Apply high-reflectivity roofing across built-up area in the selected zone.",
        "assumptions": {"COOL_ROOF_ALBEDO_BOOST": COOL_ROOF_ALBEDO_BOOST},
        "rationale": "Cool-roof coatings commonly raise surface albedo by ~0.15-0.25 versus typical dark roofing; applied only to pixels with meaningful existing built-up presence.",
    },
    "add_green_space": {
        "description": "Convert built-up area to a park/green space.",
        "assumptions": {
            "GREEN_SPACE_NDVI_BOOST": GREEN_SPACE_NDVI_BOOST,
            "GREEN_SPACE_NDWI_BOOST": GREEN_SPACE_NDWI_BOOST,
            "GREEN_SPACE_BUILT_UP_REDUCTION_PCT": GREEN_SPACE_BUILT_UP_REDUCTION_PCT,
        },
        "rationale": "Combines a vegetation increase with a partial reduction in built-up percentage, reflecting that a new green space physically displaces some existing built area rather than adding vegetation on top of it.",
    },
    "green_roof": {
        "description": "Install vegetated (green) roofs across existing built-up area.",
        "assumptions": {"GREEN_ROOF_NDVI_BOOST": GREEN_ROOF_NDVI_BOOST, "GREEN_ROOF_ALBEDO_BOOST": GREEN_ROOF_ALBEDO_BOOST},
        "rationale": "A green roof adds a shallow vegetated layer and a lighter surface, but covers only the roof footprint of existing buildings -- a smaller vegetation and albedo gain than ground-level tree cover or cool-roof coating, applied only where built-up presence is meaningful.",
    },
    "cool_pavement": {
        "description": "Apply reflective (cool) coating to roads and paved surfaces.",
        "assumptions": {
            "COOL_PAVEMENT_ALBEDO_BOOST": COOL_PAVEMENT_ALBEDO_BOOST,
            "COOL_PAVEMENT_ROAD_DENSITY_THRESHOLD_KM_PER_KM2": COOL_PAVEMENT_ROAD_DENSITY_THRESHOLD_KM_PER_KM2,
        },
        "rationale": "Reflective pavement coatings raise albedo on road surfaces specifically; applied only where road density is meaningful, distinct from cool_roof which targets building rooftops.",
    },
    "urban_water_feature": {
        "description": "Add a retention pond or water feature with riparian planting.",
        "assumptions": {
            "WATER_FEATURE_NDWI_BOOST": WATER_FEATURE_NDWI_BOOST,
            "WATER_FEATURE_NDVI_BOOST": WATER_FEATURE_NDVI_BOOST,
            "WATER_FEATURE_BUILT_UP_REDUCTION_PCT": WATER_FEATURE_BUILT_UP_REDUCTION_PCT,
        },
        "rationale": "Open water and adjacent vegetation provide strong local evaporative cooling; modeled as a moisture and modest vegetation increase with a partial built-up reduction reflecting the land the feature physically occupies.",
    },
    "reduce_building_density": {
        "description": "Redevelop toward lower-density built form with more open/green space.",
        "assumptions": {
            "DENSITY_REDUCTION_BUILDING_DENSITY_PCT": DENSITY_REDUCTION_BUILDING_DENSITY_PCT,
            "DENSITY_REDUCTION_BUILT_UP_REDUCTION_PCT": DENSITY_REDUCTION_BUILT_UP_REDUCTION_PCT,
            "DENSITY_REDUCTION_NDVI_BOOST": DENSITY_REDUCTION_NDVI_BOOST,
        },
        "rationale": "A long-horizon planning scenario (rezoning/redevelopment), not a retrofit -- lowers building density and built-up coverage while assuming the freed land gains some vegetation; effects are far more speculative than the other interventions here.",
    },
}

STANDARD_CAVEAT = (
    "This score is derived from land cover, vegetation, and urban density only "
    "(native 10m resolution); it has not been validated against measured land surface temperature. "
    "Intervention effect sizes are documented planning assumptions, not measured outcomes for this corridor."
)


def clip(value, minimum, maximum):
    """Shorthand for np.clip, used everywhere below to keep a perturbed feature inside
    its physically valid range (e.g. NDVI can't go above 1, a percent can't go below 0)."""
    return np.clip(value, minimum, maximum)


def apply_intervention(feature_df, intervention_type):
    """Return a copy of feature_df with one intervention's effect applied. Each branch
    below is the code form of the matching entry in INTERVENTION_ASSUMPTIONS above --
    change a constant there and this function picks it up automatically."""
    perturbed = feature_df.copy()

    if intervention_type == "add_tree_cover":
        perturbed["ndvi_mean"] = clip(perturbed["ndvi_mean"] + TREE_COVER_NDVI_BOOST, -1.0, 1.0)

    elif intervention_type == "cool_roof":
        has_meaningful_built_up = perturbed["built_up_pct_mean"] > 10.0
        perturbed.loc[has_meaningful_built_up, "albedo_mean"] = clip(
            perturbed.loc[has_meaningful_built_up, "albedo_mean"] + COOL_ROOF_ALBEDO_BOOST, 0.0, 1.0
        )

    elif intervention_type == "add_green_space":
        perturbed["ndvi_mean"] = clip(perturbed["ndvi_mean"] + GREEN_SPACE_NDVI_BOOST, -1.0, 1.0)
        perturbed["ndwi_mean"] = clip(perturbed["ndwi_mean"] + GREEN_SPACE_NDWI_BOOST, -1.0, 1.0)
        perturbed["built_up_pct_mean"] = clip(perturbed["built_up_pct_mean"] - GREEN_SPACE_BUILT_UP_REDUCTION_PCT, 0.0, 100.0)

    elif intervention_type == "green_roof":
        has_meaningful_built_up = perturbed["built_up_pct_mean"] > 10.0
        perturbed.loc[has_meaningful_built_up, "ndvi_mean"] = clip(
            perturbed.loc[has_meaningful_built_up, "ndvi_mean"] + GREEN_ROOF_NDVI_BOOST, -1.0, 1.0
        )
        perturbed.loc[has_meaningful_built_up, "albedo_mean"] = clip(
            perturbed.loc[has_meaningful_built_up, "albedo_mean"] + GREEN_ROOF_ALBEDO_BOOST, 0.0, 1.0
        )

    elif intervention_type == "cool_pavement":
        has_meaningful_roads = perturbed["road_density_km_per_km2"] > COOL_PAVEMENT_ROAD_DENSITY_THRESHOLD_KM_PER_KM2
        perturbed.loc[has_meaningful_roads, "albedo_mean"] = clip(
            perturbed.loc[has_meaningful_roads, "albedo_mean"] + COOL_PAVEMENT_ALBEDO_BOOST, 0.0, 1.0
        )

    elif intervention_type == "urban_water_feature":
        perturbed["ndwi_mean"] = clip(perturbed["ndwi_mean"] + WATER_FEATURE_NDWI_BOOST, -1.0, 1.0)
        perturbed["ndvi_mean"] = clip(perturbed["ndvi_mean"] + WATER_FEATURE_NDVI_BOOST, -1.0, 1.0)
        perturbed["built_up_pct_mean"] = clip(perturbed["built_up_pct_mean"] - WATER_FEATURE_BUILT_UP_REDUCTION_PCT, 0.0, 100.0)

    elif intervention_type == "reduce_building_density":
        perturbed["building_density_per_km2"] = clip(
            perturbed["building_density_per_km2"] * (1.0 - DENSITY_REDUCTION_BUILDING_DENSITY_PCT / 100.0), 0.0, None
        )
        perturbed["built_up_pct_mean"] = clip(perturbed["built_up_pct_mean"] - DENSITY_REDUCTION_BUILT_UP_REDUCTION_PCT, 0.0, 100.0)
        perturbed["ndvi_mean"] = clip(perturbed["ndvi_mean"] + DENSITY_REDUCTION_NDVI_BOOST, -1.0, 1.0)

    else:
        raise ValueError(f"Unknown intervention type: {intervention_type}")

    return perturbed


def get_pixel_indices_within_region(region_geojson_geometry, transform, shape):
    """Which (row, col) pixels of the full-AOI grid fall inside a drawn GeoJSON polygon."""
    region_polygon = shapely_shape(region_geojson_geometry)
    region_mask = rasterio.features.geometry_mask(
        [region_polygon], out_shape=shape, transform=transform, invert=True
    )
    row_indices, col_indices = np.where(region_mask)
    return row_indices, col_indices


def simulate_intervention(feature_arrays, meta, model, region_geojson_geometry, intervention_type, top_n=10):
    """Score a drawn region before and after one intervention.

    Steps: find the region's pixels -> read their real feature values -> score them with
    the trained model ("before") -> apply the intervention's perturbation -> score again
    ("after"). Returns a summary (mean improvement, top_n best-improved locations for the
    table) plus the full before/after arrays so the caller can build a map overlay."""
    transform = rasterio.transform.Affine(*meta["transform"])
    shape = (meta["height"], meta["width"])

    row_indices, col_indices = get_pixel_indices_within_region(region_geojson_geometry, transform, shape)
    if len(row_indices) == 0:
        return {"intervention_type": intervention_type, "ranked_locations": [], "region_pixel_count": 0, "caveat": STANDARD_CAVEAT}

    region_data = {name: feature_arrays[name][row_indices, col_indices] for name in FEATURE_NAMES}
    region_df = pd.DataFrame(region_data)

    valid_mask = region_df[["ndvi_mean", "built_up_pct_mean", "albedo_mean"]].notna().all(axis=1)
    for col in FEATURE_NAMES:
        region_df[col] = region_df[col].fillna(region_df[col].median())

    before_scores = model.predict(region_df[FEATURE_NAMES], thread_count=-1)
    perturbed_df = apply_intervention(region_df, intervention_type)
    after_scores = model.predict(perturbed_df[FEATURE_NAMES], thread_count=-1)

    score_improvement = before_scores - after_scores

    # Vectorized instead of a per-pixel rasterio.transform.xy() Python loop -- the same
    # anti-pattern that made build_grid_geojson take 18s for 50k pixels turned out to be
    # duplicated here too, and scales the same way for a large simulated region.
    valid_mask_arr = valid_mask.to_numpy()
    valid_row_indices = row_indices[valid_mask_arr]
    valid_col_indices = col_indices[valid_mask_arr]
    valid_before_scores = before_scores[valid_mask_arr]
    valid_after_scores = after_scores[valid_mask_arr]
    valid_improvement = score_improvement[valid_mask_arr]
    lon_arr, lat_arr = affine_pixel_centers_to_lonlat(transform, valid_row_indices, valid_col_indices)

    result_rows = [
        {
            "row": int(valid_row_indices[i]), "col": int(valid_col_indices[i]),
            "lon": float(lon_arr[i]), "lat": float(lat_arr[i]),
            "before_score": float(valid_before_scores[i]), "after_score": float(valid_after_scores[i]),
            "improvement": float(valid_improvement[i]),
        }
        for i in range(len(valid_row_indices))
    ]

    result_rows.sort(key=lambda r: r["improvement"], reverse=True)
    top_results = result_rows[:top_n]

    return {
        "intervention_type": intervention_type,
        "assumptions": INTERVENTION_ASSUMPTIONS[intervention_type],
        "region_pixel_count": len(result_rows),
        "mean_improvement": float(np.mean([r["improvement"] for r in result_rows])) if result_rows else 0.0,
        "ranked_locations": top_results,
        "caveat": STANDARD_CAVEAT,
        "row_indices": np.array(valid_row_indices, dtype=np.int64),
        "col_indices": np.array(valid_col_indices, dtype=np.int64),
        "before_scores": np.array(valid_before_scores, dtype=np.float64),
        "after_scores": np.array(valid_after_scores, dtype=np.float64),
    }
