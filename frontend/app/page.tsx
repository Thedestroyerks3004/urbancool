"use client";

/**
 * Top-level page: owns all app state and wires the map + side panels together.
 *
 * Data flow, in order:
 *   1. User draws a region on the map -> handleRegionDrawn -> POST /region/analyze.
 *      The response carries the Heat Vulnerability grid, per-metric summary stats, and
 *      a SHAP explanation -- and immediately triggers step 2.
 *   2. All 7 interventions are simulated for that region in one POST /region/simulate
 *      call, so they can be ranked by real effectiveness (rankedInterventions below).
 *   3. The user can then switch between two independent things:
 *      - which METRIC layer is shown (Metric Layer panel -> handleSelectMetric)
 *      - whether the map is showing a metric or a simulation before/after overlay
 *        (mapMode, switched by clicking a metric vs. clicking an intervention row)
 */

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { analyzeRegion, getMetricLayer, simulateInterventions, GeoJSONPolygon, RegionAnalyzeResponse, SimulateResponse } from "@/lib/api-client";
import { getMetricById, InterventionId, INTERVENTIONS, ALL_INTERVENTION_IDS } from "@/lib/metrics";
import MetricTogglePanel from "@/components/MetricTogglePanel";
import Legend from "@/components/Legend";
import ExplanationPanel from "@/components/ExplanationPanel";
import InterventionPanel from "@/components/InterventionPanel";
import CaveatBanner from "@/components/CaveatBanner";
import type { GridFeatureCollection } from "@/components/MapView";

// MapLibre touches window/DOM directly -- must never run during SSR. Dynamic import
// with ssr:false keeps it out of the server bundle entirely (smaller server bundle,
// no hydration mismatch risk).
const MapView = dynamic(() => import("@/components/MapView"), { ssr: false });

const DEFAULT_CAVEAT =
  "This score is derived from land cover, vegetation, and urban density only (native 10m resolution); it has not been validated against measured land surface temperature.";

function metricPropertyName(metricId: string): string {
  return metricId === "heat_vulnerability" ? "heat_vulnerability_score" : metricId;
}

export default function HomePage() {
  const [activeMetricId, setActiveMetricId] = useState("heat_vulnerability");
  const [isDrawMode, setIsDrawMode] = useState(false);
  const [selectedIntervention, setSelectedIntervention] = useState<InterventionId | null>(null);
  const [beforeAfter, setBeforeAfter] = useState<"before" | "after">("before");
  // Which layer the map actually renders. Ranking/auto-selecting a top intervention for the
  // side panel must not, by itself, force the map into simulation view -- otherwise clicking
  // a metric in the Metric Layer panel appears to do nothing (the simulation overlay is what
  // was actually on top). Metric-panel clicks switch this back to "metric" explicitly.
  const [mapMode, setMapMode] = useState<"metric" | "simulation">("metric");

  const analyzeMutation = useMutation<RegionAnalyzeResponse, Error, GeoJSONPolygon>({
    mutationFn: analyzeRegion,
    onSuccess: (result) => {
      setSelectedIntervention(null);
      setBeforeAfter("before");
      setMapMode("metric");
      // Simulate every intervention type in one request so they can be ranked by real
      // effectiveness for this specific region, rather than only computing whichever one
      // the user happens to click first.
      simulateMutation.mutate({ regionId: result.region_id });
    },
  });

  const simulateMutation = useMutation<SimulateResponse, Error, { regionId: string }>({
    mutationFn: ({ regionId }) => simulateInterventions(regionId, ALL_INTERVENTION_IDS, 8),
  });

  const handleRegionDrawn = useCallback(
    (polygon: { type: "Polygon"; coordinates: number[][][] }) => {
      analyzeMutation.mutate(polygon);
    },
    [analyzeMutation],
  );

  const handleSelectIntervention = useCallback((id: InterventionId) => {
    setSelectedIntervention(id);
    setBeforeAfter("before");
    setMapMode("simulation");
  }, []);

  const handleSelectMetric = useCallback((id: string) => {
    setActiveMetricId(id);
    setMapMode("metric");
  }, []);

  const rankedInterventions = useMemo(() => {
    if (!simulateMutation.data) return [];
    return [...simulateMutation.data.results].sort((a, b) => b.mean_improvement - a.mean_improvement);
  }, [simulateMutation.data]);

  const analyzeResult = analyzeMutation.data;
  const activeMetric = getMetricById(activeMetricId);
  const activePropertyName = metricPropertyName(activeMetricId);

  // The analyze response only ships the default (heat_vulnerability) layer -- bundling
  // every feature's full-resolution value per cell up front made large regions' payloads
  // balloon into the hundreds of MB. Any other metric is fetched once, on demand, and
  // react-query caches it by [region_id, metric] so flipping back to an already-viewed
  // metric is instant with no repeat request.
  const otherMetricQuery = useQuery({
    queryKey: ["metric-layer", analyzeResult?.region_id, activeMetricId],
    queryFn: () => getMetricLayer(analyzeResult!.region_id, activeMetricId),
    enabled: !!analyzeResult && activeMetricId !== "heat_vulnerability",
    staleTime: Infinity,
  });

  const gridData: GridFeatureCollection | null = useMemo(() => {
    if (!analyzeResult) return null;
    if (activeMetricId === "heat_vulnerability") {
      return analyzeResult.grid_geojson as unknown as GridFeatureCollection;
    }
    return (otherMetricQuery.data?.grid_geojson as unknown as GridFeatureCollection) ?? null;
  }, [analyzeResult, activeMetricId, otherMetricQuery.data]);

  const { min: metricMin, max: metricMax } = useMemo(() => {
    if (!analyzeResult) return { min: 0, max: 100 };
    if (activeMetricId === "heat_vulnerability") {
      return { min: analyzeResult.heat_vulnerability_summary.min, max: analyzeResult.heat_vulnerability_summary.max };
    }
    const stats = analyzeResult.metrics_summary[activeMetricId];
    return stats ? { min: stats.min, max: stats.max } : { min: 0, max: 100 };
  }, [analyzeResult, activeMetricId]);

  const interventionResult = simulateMutation.data?.results.find((r) => r.intervention_type === selectedIntervention) ?? null;

  // Auto-select the top-ranked intervention the first time results land for this region,
  // so the map/table show the best option immediately rather than sitting empty until a
  // click; the user can still pick any other ranked row to compare.
  useEffect(() => {
    if (selectedIntervention === null && rankedInterventions.length > 0) {
      setSelectedIntervention(rankedInterventions[0].intervention_type as InterventionId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rankedInterventions]);

  const simulationGridData: GridFeatureCollection | null = useMemo(() => {
    if (!interventionResult) return null;
    return interventionResult.grid_geojson as unknown as GridFeatureCollection;
  }, [interventionResult]);

  const simulationField: "before_score" | "after_score" | null =
    mapMode === "simulation" && interventionResult ? (beforeAfter === "before" ? "before_score" : "after_score") : null;
  const effectiveSimulationGridData = mapMode === "simulation" ? simulationGridData : null;

  return (
    <div className="relative w-full h-screen flex flex-col bg-[#f4f2ee] overflow-hidden">
      <header className="flex items-center justify-between px-6 py-3 bg-white border-b border-[#e4e0d8] z-20">
        <div className="flex items-center gap-3">
          <div>
            <div className="text-[17px] font-semibold text-[#1c1a16] tracking-tight">Urban Cool</div>
            <div className="text-[11.5px] text-[#8a8578] -mt-0.5">Heat Vulnerability — Anna University · OMR–ECR Corridor</div>
          </div>
        </div>
        <button
          onClick={() => setIsDrawMode((d) => !d)}
          className="px-4 py-2 rounded-md text-sm font-medium text-white transition-colors"
          style={{ background: isDrawMode ? "#8a4a2f" : "#1e3a5f" }}
        >
          {isDrawMode ? "Drawing... click-drag on map" : "Draw region"}
        </button>
      </header>

      <div className="relative flex-1">
        <MapView
          gridData={gridData}
          activeMetric={activeMetric}
          activeMetricPropertyName={activePropertyName}
          metricMin={metricMin}
          metricMax={metricMax}
          isDrawMode={isDrawMode}
          onRegionDrawn={handleRegionDrawn}
          onDrawModeChange={setIsDrawMode}
          simulationGridData={effectiveSimulationGridData}
          simulationField={simulationField}
        />

        <Legend
          metric={mapMode === "simulation" ? getMetricById("heat_vulnerability") : activeMetric}
          labelOverride={
            mapMode === "simulation" && interventionResult
              ? `Heat Vulnerability — ${beforeAfter === "before" ? "Before" : "After"} (${INTERVENTIONS.find((i) => i.id === selectedIntervention)?.label ?? ""})`
              : undefined
          }
        />

        {!analyzeResult && !analyzeMutation.isPending && (
          <div className="absolute top-4 left-4 bg-white/95 rounded-lg shadow-lg px-4 py-3 text-sm text-[#4a463c] z-20 max-w-xs">
            No region analyzed yet. Click <strong>Draw region</strong>, then click-drag a box
            <strong> inside the dashed orange boundary</strong> — that outline marks the only
            area with data (Anna University · OMR–ECR corridor). Drawing outside it will fail.
          </div>
        )}

        {analyzeMutation.isPending && (
          <div className="absolute top-4 left-4 bg-white/95 rounded-lg shadow-lg px-4 py-3 text-sm text-[#4a463c] z-20">
            Analyzing drawn region...
          </div>
        )}
        {otherMetricQuery.isFetching && (
          <div className="absolute top-4 left-4 bg-white/95 rounded-lg shadow-lg px-4 py-3 text-sm text-[#4a463c] z-20">
            Loading {activeMetric.label}...
          </div>
        )}
        {analyzeMutation.isError && (
          <div className="absolute top-4 left-4 bg-white/95 rounded-lg shadow-lg px-4 py-3 text-sm text-[#b45f42] z-20 max-w-xs">
            {analyzeMutation.error.message}
          </div>
        )}

        {/* Single right-side flex column: panels stack in normal document flow,
            so real content height (e.g. 8 metric rows) can never overlap the
            panel below it, unlike two independently-hardcoded absolute offsets. */}
        <div className="absolute top-4 right-4 bottom-4 w-[300px] flex flex-col gap-3 z-20 overflow-y-auto">
          <MetricTogglePanel activeMetricId={activeMetricId} onSelect={handleSelectMetric} />
          <div className="w-full bg-white rounded-lg shadow-lg p-4">
            <ExplanationPanel
              shapSummary={analyzeResult?.shap_summary ?? null}
              plainLanguageSummary={analyzeResult?.plain_language_summary ?? null}
              isLoading={analyzeMutation.isPending}
            />
            <InterventionPanel
              rankedInterventions={rankedInterventions}
              selectedIntervention={selectedIntervention}
              onSelectIntervention={handleSelectIntervention}
              result={interventionResult}
              isLoading={simulateMutation.isPending}
              beforeAfter={beforeAfter}
              onToggleBeforeAfter={() => setBeforeAfter((b) => (b === "before" ? "after" : "before"))}
            />
          </div>
        </div>
      </div>

      <CaveatBanner caveat={analyzeResult?.caveats ?? DEFAULT_CAVEAT} />
    </div>
  );
}
