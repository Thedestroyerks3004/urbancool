"use client";

/**
 * The MapLibre map. Renders 5 layers on top of an OSM basemap:
 *   1. metric-grid-layer     the currently selected metric (or Heat Vulnerability)
 *   2. simulation-grid-layer before/after intervention overlay (hidden unless active)
 *   3. aoi-boundary-line     dashed outline of the only area with real data
 *   4. draft-region-fill/line   the rectangle being drawn, live, while dragging
 * Only one of (1) and (2) is visible at a time -- see the "Simulation overlay" effect
 * below. The map itself is created once (see the empty-dependency effect) and never
 * recreated; every prop change after that just updates data or paint properties on the
 * existing layers, which is far cheaper than remounting the whole map.
 */

import { useEffect, useRef, useCallback, useState } from "react";
import {
  Map as MapLibreMap,
  GeoJSONSource,
  NavigationControl,
  type StyleSpecification,
  type MapMouseEvent,
  type ExpressionSpecification,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { buildColorExpression, getMetricById, MetricDefinition } from "@/lib/metrics";

const HEAT_VULNERABILITY_METRIC = getMetricById("heat_vulnerability");

// The exact study area the backend has real data for (must match backend/app/
// feature_stack.py's actual data extent). AOI_CENTER/AOI_BOUNDS draw the dashed boundary
// and set the initial camera. Changing these to a box the backend has NO data for means
// every draw inside it returns the real 400 "Drawn region contains no pixels" error --
// this constant does not expand what the backend can serve, it only changes what the map
// visually invites you to draw.
const AOI_CENTER: [number, number] = [80.225, 12.9];
const AOI_BOUNDS: [[number, number], [number, number]] = [
  [80.15, 12.75],
  [80.30, 13.05],
];

// The map is locked to roughly this box (see maxBounds below) so a user can never
// pan/zoom away from the only area the backend actually has data for. Deliberately a bit
// larger than AOI_BOUNDS so the boundary line itself stays visible with some margin
// around it, rather than sitting exactly at the edge of the pannable area.
const AOI_MAX_BOUNDS: [[number, number], [number, number]] = [
  [80.10, 12.70],
  [80.35, 13.10],
];

const AOI_POLYGON: GeoJSON.Feature<GeoJSON.Polygon> = {
  type: "Feature",
  properties: {},
  geometry: {
    type: "Polygon",
    coordinates: [
      [
        [AOI_BOUNDS[0][0], AOI_BOUNDS[0][1]],
        [AOI_BOUNDS[1][0], AOI_BOUNDS[0][1]],
        [AOI_BOUNDS[1][0], AOI_BOUNDS[1][1]],
        [AOI_BOUNDS[0][0], AOI_BOUNDS[1][1]],
        [AOI_BOUNDS[0][0], AOI_BOUNDS[0][1]],
      ],
    ],
  },
};

// MapLibre needs a unique string id per source/layer -- these are just internal names
// used to look the layer up later (map.getLayer(...), map.setPaintProperty(...)). They
// are never seen by the user and can be renamed freely, as long as every reference to
// the same constant below is updated together (a typo'd id just means that layer's
// later updates silently do nothing, since map.getLayer() would return undefined).
const GRID_SOURCE_ID = "metric-grid";       // the active metric layer's data
const GRID_LAYER_ID = "metric-grid-layer";  // the active metric layer's circles
const SIM_SOURCE_ID = "simulation-grid";        // before/after intervention data
const SIM_LAYER_ID = "simulation-grid-layer";   // before/after intervention circles
const AOI_BOUNDARY_SOURCE_ID = "aoi-boundary";       // the dashed study-area outline
const AOI_BOUNDARY_LINE_LAYER_ID = "aoi-boundary-line";
const DRAFT_SOURCE_ID = "draft-region";           // the rectangle being actively dragged
const DRAFT_FILL_LAYER_ID = "draft-region-fill";
const DRAFT_LINE_LAYER_ID = "draft-region-line";

// Real OSM raster tiles -- appropriate for this app's light, interactive usage.
// A production deployment at real traffic volume should move to a dedicated tile
// provider per OSM's tile usage policy; noted here rather than left implicit.
const MAP_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "&copy; OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

export interface GridFeatureCollection {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    geometry: { type: "Point"; coordinates: [number, number] };
    properties: Record<string, number | null>;
  }>;
}

interface MapViewProps {
  gridData: GridFeatureCollection | null;
  activeMetric: MetricDefinition;
  activeMetricPropertyName: string;
  metricMin: number;
  metricMax: number;
  isDrawMode: boolean;
  onRegionDrawn: (polygon: { type: "Polygon"; coordinates: number[][][] }) => void;
  onDrawModeChange: (isDrawing: boolean) => void;
  // When set, a simulation overlay (before_score/after_score per cell) replaces the
  // normal metric layer entirely -- this is what makes the before/after toggle actually
  // change what's on the map instead of only relabeling the side panel.
  simulationGridData: GridFeatureCollection | null;
  simulationField: "before_score" | "after_score" | null;
}

export default function MapView({
  gridData,
  activeMetric,
  activeMetricPropertyName,
  metricMin,
  metricMax,
  isDrawMode,
  onRegionDrawn,
  onDrawModeChange,
  simulationGridData,
  simulationField,
}: MapViewProps) {
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const drawStateRef = useRef<{ start: [number, number] | null }>({ start: null });
  const [isMapReady, setIsMapReady] = useState(false);

  // Map is created exactly once. Data and styling updates below never recreate it --
  // that is the efficient path (avoids a full remount/re-tile-load on every state change).
  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return;

    const map = new MapLibreMap({
      container: mapContainerRef.current,
      style: MAP_STYLE,
      center: AOI_CENTER,
      zoom: 12,
      minZoom: 10,
      maxBounds: AOI_MAX_BOUNDS,
      attributionControl: { compact: true },
    });
    map.addControl(new NavigationControl({ showCompass: false }), "top-left");
    map.fitBounds(AOI_BOUNDS, { padding: 20, duration: 0 });

    map.on("error", (e) => {
      console.error("[MapView] MapLibre error:", e.error?.message ?? e);
    });

    map.on("load", () => {
      map.addSource(GRID_SOURCE_ID, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({
        id: GRID_LAYER_ID,
        type: "circle",
        source: GRID_SOURCE_ID,
        paint: {
          // Denser, native-resolution point cloud (backend serves up to 800k cells
          // unaggregated) needs smaller radii and slight blur so adjacent 10m cells
          // blend into a smooth surface instead of visible gaps or dot texture. Each
          // [zoom, radius] pair below is a fixed point MapLibre interpolates between --
          // raise the radius values for bigger, more visible dots (at the cost of more
          // overlap/blur on dense regions); the "#cccccc" color here is only the
          // fallback before real data loads -- the actual per-metric colors are set at
          // runtime by buildColorExpression (lib/metrics.ts), not here.
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 1.4, 14, 4, 18, 9],
          "circle-blur": 0.35,
          "circle-color": "#cccccc",
          "circle-opacity": 0.9,
          "circle-stroke-width": 0,
        },
      });

      map.addSource(SIM_SOURCE_ID, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({
        id: SIM_LAYER_ID,
        type: "circle",
        source: SIM_SOURCE_ID,
        layout: { visibility: "none" },
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 1.4, 14, 4, 18, 9],
          "circle-blur": 0.35,
          "circle-color": "#cccccc",
          "circle-opacity": 0.9,
          "circle-stroke-width": 0,
        },
      });

      map.addSource(AOI_BOUNDARY_SOURCE_ID, { type: "geojson", data: AOI_POLYGON });
      map.addLayer({
        id: AOI_BOUNDARY_LINE_LAYER_ID,
        type: "line",
        source: AOI_BOUNDARY_SOURCE_ID,
        paint: { "line-color": "#b45f42", "line-width": 2.5, "line-dasharray": [3, 2] },
      });

      map.addSource(DRAFT_SOURCE_ID, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({
        id: DRAFT_FILL_LAYER_ID,
        type: "fill",
        source: DRAFT_SOURCE_ID,
        paint: { "fill-color": "#1e3a5f", "fill-opacity": 0.12 },
      });
      map.addLayer({
        id: DRAFT_LINE_LAYER_ID,
        type: "line",
        source: DRAFT_SOURCE_ID,
        paint: { "line-color": "#1e3a5f", "line-width": 2, "line-dasharray": [2, 1] },
      });

      setIsMapReady(true);
    });

    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Update grid data + coloring without touching the map/style otherwise.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isMapReady) return;
    const source = map.getSource(GRID_SOURCE_ID) as GeoJSONSource | undefined;
    if (source) {
      source.setData((gridData ?? { type: "FeatureCollection", features: [] }) as GeoJSON.FeatureCollection);
    }
    if (map.getLayer(GRID_LAYER_ID)) {
      const colorExpression = buildColorExpression(activeMetric, activeMetricPropertyName, metricMin, metricMax);
      map.setPaintProperty(GRID_LAYER_ID, "circle-color", colorExpression as unknown as ExpressionSpecification);
    }
  }, [gridData, activeMetric, activeMetricPropertyName, metricMin, metricMax, isMapReady]);

  // Simulation overlay: entirely replaces the metric layer while an intervention result is
  // active, colored by heat_vulnerability's own scale (both before_score/after_score are
  // heat vulnerability scores 0-100) using whichever field the before/after toggle selects.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isMapReady) return;

    const simSource = map.getSource(SIM_SOURCE_ID) as GeoJSONSource | undefined;
    const isSimulating = !!simulationGridData && !!simulationField;

    if (simSource) {
      simSource.setData((simulationGridData ?? { type: "FeatureCollection", features: [] }) as GeoJSON.FeatureCollection);
    }
    if (map.getLayer(SIM_LAYER_ID) && simulationField) {
      const colorExpression = buildColorExpression(HEAT_VULNERABILITY_METRIC, simulationField, 0, 100);
      map.setPaintProperty(SIM_LAYER_ID, "circle-color", colorExpression as unknown as ExpressionSpecification);
      map.setLayoutProperty(SIM_LAYER_ID, "visibility", "visible");
    } else if (map.getLayer(SIM_LAYER_ID)) {
      map.setLayoutProperty(SIM_LAYER_ID, "visibility", "none");
    }
    if (map.getLayer(GRID_LAYER_ID)) {
      map.setLayoutProperty(GRID_LAYER_ID, "visibility", isSimulating ? "none" : "visible");
    }
  }, [simulationGridData, simulationField, isMapReady]);

  // Redraws the dashed rectangle preview while the user is dragging (before mouseup
  // actually submits a region to the backend).
  const updateDraftRectangle = useCallback((start: [number, number], end: [number, number]) => {
    const map = mapRef.current;
    if (!map) return;
    const source = map.getSource(DRAFT_SOURCE_ID) as GeoJSONSource | undefined;
    if (!source) return;
    const coordinates = [
      [start[0], start[1]],
      [end[0], start[1]],
      [end[0], end[1]],
      [start[0], end[1]],
      [start[0], start[1]],
    ];
    source.setData({
      type: "FeatureCollection",
      features: [{ type: "Feature", geometry: { type: "Polygon", coordinates: [coordinates] }, properties: {} }],
    });
  }, []);

  // Click-and-drag-to-draw-a-rectangle, implemented with raw mouse events (no external
  // drawing library): mousedown records the start corner, mousemove live-updates the
  // preview rectangle, mouseup computes the final west/south/east/north box and hands it
  // to the parent via onRegionDrawn. Panning is disabled while in draw mode so a drag
  // draws a rectangle instead of moving the map.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isMapReady) return;

    if (!isDrawMode) {
      map.dragPan.enable();
      drawStateRef.current.start = null;
      return;
    }

    map.dragPan.disable();

    const handleMouseDown = (e: MapMouseEvent) => {
      drawStateRef.current.start = [e.lngLat.lng, e.lngLat.lat];
    };
    const handleMouseMove = (e: MapMouseEvent) => {
      const start = drawStateRef.current.start;
      if (!start) return;
      updateDraftRectangle(start, [e.lngLat.lng, e.lngLat.lat]);
    };
    const handleMouseUp = (e: MapMouseEvent) => {
      const start = drawStateRef.current.start;
      if (!start) return;
      const end: [number, number] = [e.lngLat.lng, e.lngLat.lat];
      drawStateRef.current.start = null;

      const west = Math.min(start[0], end[0]);
      const east = Math.max(start[0], end[0]);
      const south = Math.min(start[1], end[1]);
      const north = Math.max(start[1], end[1]);

      onRegionDrawn({
        type: "Polygon",
        coordinates: [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
      });
      onDrawModeChange(false);
    };

    map.on("mousedown", handleMouseDown);
    map.on("mousemove", handleMouseMove);
    map.on("mouseup", handleMouseUp);

    return () => {
      map.off("mousedown", handleMouseDown);
      map.off("mousemove", handleMouseMove);
      map.off("mouseup", handleMouseUp);
      map.dragPan.enable();
    };
  }, [isDrawMode, isMapReady, onRegionDrawn, onDrawModeChange, updateDraftRectangle]);

  return (
    // w-full/h-full, not absolute+inset-0: MapLibre's Map constructor forces
    // position:relative on the container via an inline style, which overrides an
    // "absolute" class (inline styles win) and makes inset-0 sizing a no-op --
    // the container then collapses to height 0 and MapLibre falls back to a
    // hardcoded 300px canvas. w-full/h-full sizes correctly under either position,
    // as long as the parent (a flex item here) has a definite height, which it does.
    <div
      ref={mapContainerRef}
      data-testid="map-container"
      className="w-full h-full"
      style={{ cursor: isDrawMode ? "crosshair" : "grab" }}
    />
  );
}
