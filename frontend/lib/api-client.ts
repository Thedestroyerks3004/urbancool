import type { components } from "./api-types";

export type RegionAnalyzeResponse = components["schemas"]["RegionAnalyzeResponse"];
export type MetricLayerResponse = components["schemas"]["MetricLayerResponse"];
export type SimulateResponse = components["schemas"]["SimulateResponse"];
export type HealthResponse = components["schemas"]["HealthResponse"];
export type ShapContribution = components["schemas"]["ShapContribution"];
export type InterventionResult = components["schemas"]["InterventionResult"];
export type RankedLocation = components["schemas"]["RankedLocation"];
export type MetricStats = components["schemas"]["MetricStats"];

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const text = await response.text().catch(() => response.statusText);
    throw new ApiError(response.status, text);
  }
  return response.json() as Promise<T>;
}

export type GeoJSONPolygon = {
  type: "Polygon";
  coordinates: number[][][];
};

export function analyzeRegion(region: GeoJSONPolygon): Promise<RegionAnalyzeResponse> {
  return request<RegionAnalyzeResponse>("/api/v1/region/analyze", {
    method: "POST",
    body: JSON.stringify({ region }),
  });
}

export function getMetricLayer(regionId: string, metricName: string): Promise<MetricLayerResponse> {
  return request<MetricLayerResponse>(`/api/v1/region/${regionId}/metrics/${metricName}`);
}

export function simulateInterventions(
  regionId: string,
  interventionTypes: string[],
  topN = 10,
): Promise<SimulateResponse> {
  return request<SimulateResponse>(`/api/v1/region/${regionId}/simulate`, {
    method: "POST",
    body: JSON.stringify({ intervention_types: interventionTypes, top_n: topN }),
  });
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/api/v1/health");
}
