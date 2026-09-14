"""
The four HTTP endpoints the frontend calls. All heavy work (reading the feature stack,
running the model, computing SHAP) happens here; the frontend only ever sees JSON and
GeoJSON.

    POST /region/analyze                   draw a region -> score it, explain it
    GET  /region/{id}/metrics/{name}        fetch one extra metric layer, on demand
    POST /region/{id}/simulate              score interventions for an analyzed region
    GET  /health                            model version, accuracy, cache status
"""

import os
import time
import numpy as np
import pandas as pd
import rasterio.transform
from fastapi import APIRouter, HTTPException

from app.feature_stack import FEATURE_NAMES, FEATURE_STACK_CACHE_PATH
from app.model import MODEL_PATH
from app.region_cache import get_or_compute_region, get_cached_region
from app.simulator import simulate_intervention, STANDARD_CAVEAT, INTERVENTION_ASSUMPTIONS
from app.grid_utils import build_grid_geojson, MAX_UNAGGREGATED_PIXELS
from app.schemas import (
    RegionAnalyzeRequest, RegionAnalyzeResponse, MetricStats, ShapContribution,
    MetricLayerResponse, SimulateRequest, SimulateResponse, InterventionResult, RankedLocation,
    HealthResponse,
)
from app.state import get_app_state

router = APIRouter(prefix="/api/v1")

SHAP_SAMPLE_SIZE = 500
FEATURE_NAME_TO_PLAIN_LANGUAGE = {
    "ndvi_mean": "low vegetation cover",
    "ndwi_mean": "little nearby water/moisture",
    "albedo_mean": "low surface reflectivity (dark, heat-absorbing surfaces)",
    "built_up_pct_mean": "a high proportion of built-up land",
    "vegetation_pct_mean": "limited vegetated land",
    "water_pct_mean": "limited water bodies",
    "built_up_pct_trend": "a rising trend in built-up land over time",
    "building_density_per_km2": "high building density",
    "road_density_km_per_km2": "high road density",
    "ndvi_min": "patches of very low vegetation",
    "ndvi_std": "inconsistent vegetation cover",
    "ndwi_std": "inconsistent moisture presence",
    "albedo_min": "patches of very dark surfaces",
}


def compute_metric_stats(values):
    """Mean/min/max/std for one metric across a region, ignoring nodata pixels."""
    valid = values[~np.isnan(values)]
    if len(valid) == 0:
        return MetricStats(mean=0.0, min=0.0, max=0.0, std=0.0)
    return MetricStats(mean=float(np.mean(valid)), min=float(np.min(valid)), max=float(np.max(valid)), std=float(np.std(valid)))


def build_plain_language_summary(shap_contributions):
    """Turn the top 2 SHAP features into one plain-English sentence for the UI's
    "why this score" panel, e.g. "Low vegetation cover and high road density are the
    main drivers..."."""
    top_two = shap_contributions[:2]
    phrases = [FEATURE_NAME_TO_PLAIN_LANGUAGE.get(c.feature, c.feature) for c in top_two]
    if len(phrases) == 2:
        return f"{phrases[0].capitalize()} and {phrases[1]} are the main drivers of the heat vulnerability score in this area."
    elif len(phrases) == 1:
        return f"{phrases[0].capitalize()} is the main driver of the heat vulnerability score in this area."
    return "No dominant driver identified for this area."


@router.post("/region/analyze", response_model=RegionAnalyzeResponse)
def analyze_region(request: RegionAnalyzeRequest):
    """Score a user-drawn polygon: find its pixels, run the model, explain the result
    with SHAP, and return a heat-vulnerability map grid plus summary stats for every
    metric. This is the first call the frontend makes after a region is drawn."""
    state = get_app_state()
    meta = state.meta
    feature_arrays = state.feature_arrays
    model = state.model

    region_id, region_entry = get_or_compute_region(request.region, meta)
    row_indices, col_indices = region_entry["row_indices"], region_entry["col_indices"]

    if len(row_indices) == 0:
        raise HTTPException(status_code=400, detail="Drawn region contains no pixels inside the study area AOI.")

    per_pixel_values = {name: feature_arrays[name][row_indices, col_indices] for name in FEATURE_NAMES}
    df = pd.DataFrame(per_pixel_values)
    valid_mask = df[["ndvi_mean", "built_up_pct_mean", "albedo_mean"]].notna().all(axis=1)
    df_valid = df.loc[valid_mask].reset_index(drop=True)
    row_valid = row_indices[valid_mask.values]
    col_valid = col_indices[valid_mask.values]

    if len(df_valid) == 0:
        raise HTTPException(status_code=400, detail="Drawn region has no valid (non-nodata) pixels.")

    df_filled = df_valid.copy()
    for col in FEATURE_NAMES:
        df_filled[col] = df_filled[col].fillna(df_filled[col].median())

    scores = model.predict(df_filled[FEATURE_NAMES], thread_count=-1)

    # SHAP cost scales with row count and only feeds an aggregate mean-per-feature summary
    # ("why this score"), not a per-pixel value -- a bounded random sample gives the same
    # aggregate to a negligible error while cutting a ~50k-pixel region from ~16s to ~1s.
    explainer = state.shap_explainer
    shap_sample = df_filled[FEATURE_NAMES] if len(df_filled) <= SHAP_SAMPLE_SIZE else df_filled[FEATURE_NAMES].sample(n=SHAP_SAMPLE_SIZE, random_state=0)
    shap_values = explainer.shap_values(shap_sample)
    mean_abs_shap = np.abs(shap_values).mean(axis=0)
    shap_order = np.argsort(-mean_abs_shap)
    shap_summary = [
        ShapContribution(feature=FEATURE_NAMES[i], mean_shap_value=float(shap_values[:, i].mean()))
        for i in shap_order
    ]

    transform = rasterio.transform.Affine(*meta["transform"])
    # Only the default (heat_vulnerability) layer ships with the analyze response --
    # bundling all 14 raw features per point too (as before) made a large drawn region's
    # payload balloon into the hundreds of MB, dominating request time. Every other metric
    # is fetched on demand, once, via /metrics/{name} (itself now fast after the grid
    # builder was vectorized) and cached client-side, so switching metrics stays cheap
    # without paying for data nobody looks at up front.
    grid_geojson, aggregated, cell_size = build_grid_geojson(row_valid, col_valid, transform, {"heat_vulnerability_score": scores})

    metrics_summary = {name: compute_metric_stats(df_valid[name].values) for name in FEATURE_NAMES}
    heat_vulnerability_summary = compute_metric_stats(scores)

    plain_language_summary = build_plain_language_summary(shap_summary)

    return RegionAnalyzeResponse(
        region_id=region_id,
        pixel_count=len(df_valid),
        aggregated=aggregated,
        aggregation_cell_size_meters=cell_size if aggregated else None,
        metrics_summary=metrics_summary,
        heat_vulnerability_summary=heat_vulnerability_summary,
        grid_geojson=grid_geojson,
        shap_summary=shap_summary,
        plain_language_summary=plain_language_summary,
        caveats=STANDARD_CAVEAT,
    )


@router.get("/region/{region_id}/metrics/{metric_name}", response_model=MetricLayerResponse)
def get_metric_layer(region_id: str, metric_name: str):
    """Fetch one metric's grid for a region that /analyze has already cached. The
    frontend calls this only when the user switches to a non-default metric layer, so a
    region's full feature set is never sent unless it's actually looked at."""
    state = get_app_state()
    meta = state.meta
    feature_arrays = state.feature_arrays
    model = state.model

    valid_metric_names = FEATURE_NAMES + ["heat_vulnerability"]
    if metric_name not in valid_metric_names:
        raise HTTPException(status_code=404, detail=f"Unknown metric '{metric_name}'. Valid options: {valid_metric_names}")

    try:
        region_entry = get_cached_region(region_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error))

    row_indices, col_indices = region_entry["row_indices"], region_entry["col_indices"]
    transform = rasterio.transform.Affine(*meta["transform"])

    if metric_name == "heat_vulnerability":
        per_pixel_values = {name: feature_arrays[name][row_indices, col_indices] for name in FEATURE_NAMES}
        df = pd.DataFrame(per_pixel_values)
        valid_mask = df[["ndvi_mean", "built_up_pct_mean", "albedo_mean"]].notna().all(axis=1)
        df_valid = df.loc[valid_mask].reset_index(drop=True)
        for col in FEATURE_NAMES:
            df_valid[col] = df_valid[col].fillna(df_valid[col].median())
        values = model.predict(df_valid[FEATURE_NAMES], thread_count=-1)
        row_valid = row_indices[valid_mask.values]
        col_valid = col_indices[valid_mask.values]
    else:
        values = feature_arrays[metric_name][row_indices, col_indices]
        not_nan = ~np.isnan(values)
        values = values[not_nan]
        row_valid = row_indices[not_nan]
        col_valid = col_indices[not_nan]

    grid_geojson, _, _ = build_grid_geojson(row_valid, col_valid, transform, {metric_name: values})

    return MetricLayerResponse(region_id=region_id, metric_name=metric_name, grid_geojson=grid_geojson, caveats=STANDARD_CAVEAT)


@router.post("/region/{region_id}/simulate", response_model=SimulateResponse)
def simulate(region_id: str, request: SimulateRequest):
    """Score one or more interventions against an already-analyzed region. Returns, per
    intervention: a ranked top-locations table and a full before/after map grid, so the
    frontend can both list results and paint them on the map."""
    state = get_app_state()
    meta = state.meta
    feature_arrays = state.feature_arrays
    model = state.model

    try:
        region_entry = get_cached_region(region_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error))

    valid_types = set(INTERVENTION_ASSUMPTIONS.keys())
    unknown = set(request.intervention_types) - valid_types
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown intervention type(s): {unknown}. Valid: {sorted(valid_types)}")

    transform = rasterio.transform.Affine(*meta["transform"])

    results = []
    for intervention_type in request.intervention_types:
        raw_result = simulate_intervention(feature_arrays, meta, model, region_entry["geometry"], intervention_type, top_n=request.top_n)

        if len(raw_result.get("row_indices", [])) > 0:
            grid_geojson, aggregated, cell_size = build_grid_geojson(
                raw_result["row_indices"], raw_result["col_indices"], transform,
                {"before_score": raw_result["before_scores"], "after_score": raw_result["after_scores"]},
            )
        else:
            grid_geojson, aggregated, cell_size = {"type": "FeatureCollection", "features": []}, False, None

        results.append(InterventionResult(
            intervention_type=raw_result["intervention_type"],
            assumptions=raw_result.get("assumptions", {}),
            region_pixel_count=raw_result["region_pixel_count"],
            mean_improvement=raw_result["mean_improvement"],
            ranked_locations=[RankedLocation(**loc) for loc in raw_result["ranked_locations"]],
            grid_geojson=grid_geojson,
            aggregated=aggregated,
            aggregation_cell_size_meters=cell_size if aggregated else None,
        ))

    return SimulateResponse(region_id=region_id, results=results, caveats=STANDARD_CAVEAT)


@router.get("/health", response_model=HealthResponse)
def health():
    """Model version, accuracy, and cache freshness -- used by run.sh/run.ps1 to detect
    the backend is actually ready, and handy for a quick sanity check in the browser."""
    state = get_app_state()
    metadata = state.model_metadata

    feature_stack_built_at = None
    if os.path.exists(FEATURE_STACK_CACHE_PATH):
        feature_stack_built_at = time.ctime(os.path.getmtime(FEATURE_STACK_CACHE_PATH))

    model_trained_at = None
    if os.path.exists(MODEL_PATH):
        model_trained_at = time.ctime(os.path.getmtime(MODEL_PATH))

    return HealthResponse(
        status="ok",
        model_version="native10m-v1",
        model_r_squared=float(metadata["r_squared"]),
        model_mae=float(metadata["mae"]),
        feature_stack_built_at=feature_stack_built_at,
        model_trained_at=model_trained_at,
        n_training_pixels=int(metadata["n_train"]) + int(metadata["n_test"]),
        caveats=STANDARD_CAVEAT,
    )
