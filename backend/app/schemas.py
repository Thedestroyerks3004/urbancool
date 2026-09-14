"""Pydantic request/response models -- self-documenting via FastAPI's auto /docs."""

from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field


class RegionAnalyzeRequest(BaseModel):
    region: Dict[str, Any] = Field(..., description="A GeoJSON Polygon or MultiPolygon geometry drawn by the user on the frontend map.")


class MetricStats(BaseModel):
    mean: float
    min: float
    max: float
    std: float


class ShapContribution(BaseModel):
    feature: str
    mean_shap_value: float


class RegionAnalyzeResponse(BaseModel):
    region_id: str
    pixel_count: int
    aggregated: bool = Field(..., description="True if the returned grid was coarsened from native 10m because the region was large.")
    aggregation_cell_size_meters: Optional[float] = None
    metrics_summary: Dict[str, MetricStats]
    heat_vulnerability_summary: MetricStats
    grid_geojson: Dict[str, Any] = Field(..., description="FeatureCollection of grid cells with per-cell metric values and heat_vulnerability_score.")
    shap_summary: List[ShapContribution]
    plain_language_summary: str
    caveats: str


class MetricLayerResponse(BaseModel):
    region_id: str
    metric_name: str
    grid_geojson: Dict[str, Any]
    caveats: str


class SimulateRequest(BaseModel):
    intervention_types: List[str] = Field(
        ...,
        description=(
            "One or more of: add_tree_cover, cool_roof, add_green_space, green_roof, "
            "cool_pavement, urban_water_feature, reduce_building_density"
        ),
    )
    top_n: int = Field(10, description="How many best-improved locations to return per intervention (the ranked table).")


class RankedLocation(BaseModel):
    lon: float
    lat: float
    before_score: float
    after_score: float
    improvement: float


class InterventionResult(BaseModel):
    intervention_type: str
    assumptions: Dict[str, Any]
    region_pixel_count: int
    mean_improvement: float
    ranked_locations: List[RankedLocation]
    grid_geojson: Dict[str, Any] = Field(..., description="FeatureCollection over the whole region with before_score/after_score per cell.")
    aggregated: bool
    aggregation_cell_size_meters: Optional[float] = None


class SimulateResponse(BaseModel):
    region_id: str
    results: List[InterventionResult]
    caveats: str


class HealthResponse(BaseModel):
    status: str
    model_version: str
    model_r_squared: float
    model_mae: float
    feature_stack_built_at: Optional[str]
    model_trained_at: Optional[str]
    n_training_pixels: int
    caveats: str
