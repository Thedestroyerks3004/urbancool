export type MetricKind = "continuous" | "diverging";

export interface MetricDefinition {
  id: string;
  label: string;
  kind: MetricKind;
  stops: string[];
  minLabel: string;
  maxLabel: string;
}

// These map directly to real backend feature names (app/feature_stack.py FEATURE_NAMES)
// plus the special "heat_vulnerability" model-derived layer. No metric here is invented --
// each one is a real, queryable /api/v1/region/{id}/metrics/{name} endpoint.
export const METRICS: MetricDefinition[] = [
  { id: "heat_vulnerability", label: "Heat Vulnerability", kind: "continuous", stops: ["#fff8dc", "#fdae61", "#d7191c", "#7f0000"], minLabel: "0", maxLabel: "100" },
  { id: "ndvi_mean", label: "NDVI (Vegetation)", kind: "continuous", stops: ["#f7fbef", "#addd8e", "#31a354", "#00441b"], minLabel: "-1", maxLabel: "1" },
  { id: "ndwi_mean", label: "NDWI (Moisture/Water)", kind: "continuous", stops: ["#f3f8fb", "#9ecae1", "#3182bd", "#08306b"], minLabel: "-1", maxLabel: "1" },
  { id: "albedo_mean", label: "Albedo (Reflectivity)", kind: "continuous", stops: ["#3a3630", "#8a8578", "#e8e2d0", "#fdfaf0"], minLabel: "0.0", maxLabel: "1.0" },
  { id: "built_up_pct_mean", label: "Built-up %", kind: "continuous", stops: ["#f5f1ea", "#d9c3a8", "#a9967f", "#5c4a37"], minLabel: "0%", maxLabel: "100%" },
  { id: "building_density_per_km2", label: "Building Density", kind: "continuous", stops: ["#f6f0fa", "#c4a3d9", "#8858a8", "#4a2a66"], minLabel: "0", maxLabel: "high" },
  { id: "road_density_km_per_km2", label: "Road Density", kind: "continuous", stops: ["#eef2f6", "#a9c0d4", "#5b84a8", "#2c4a66"], minLabel: "0", maxLabel: "high" },
  { id: "built_up_pct_trend", label: "Built-up Trend", kind: "diverging", stops: ["#2c6e8e", "#eef2ee", "#b45f42"], minLabel: "declining", maxLabel: "rising" },
];

// Mirrors app/simulator.py's INTERVENTION_ASSUMPTIONS keys exactly -- the backend is the
// source of truth for what each one actually does; these are just display labels.
export const INTERVENTIONS = [
  { id: "add_tree_cover", label: "Add Tree Cover" },
  { id: "cool_roof", label: "Cool Roof" },
  { id: "add_green_space", label: "Add Green Space" },
  { id: "green_roof", label: "Green Roof" },
  { id: "cool_pavement", label: "Cool Pavement" },
  { id: "urban_water_feature", label: "Water Feature" },
  { id: "reduce_building_density", label: "Reduce Density" },
] as const;

export type InterventionId = (typeof INTERVENTIONS)[number]["id"];
export const ALL_INTERVENTION_IDS: InterventionId[] = INTERVENTIONS.map((i) => i.id);

export function getMetricById(id: string): MetricDefinition {
  const found = METRICS.find((m) => m.id === id);
  if (!found) throw new Error(`Unknown metric id: ${id}`);
  return found;
}

// Builds a MapLibre GL "interpolate" color expression from a metric's stops,
// so coloring happens on the GPU via data-driven styling rather than per-feature
// JS color computation -- this is the efficient path for potentially thousands
// of grid cells.
export function buildColorExpression(metric: MetricDefinition, propertyName: string, min: number, max: number): unknown[] {
  const stops = metric.stops;
  const n = stops.length - 1;
  const expression: unknown[] = ["interpolate", ["linear"], ["coalesce", ["get", propertyName], min]];
  stops.forEach((color, i) => {
    const value = min + ((max - min) * i) / n;
    expression.push(value, color);
  });
  return expression;
}
