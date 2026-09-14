"use client";

/**
 * Right-side panel: the ranked intervention list (a button per intervention, sorted
 * best-first, with a proportional effect bar) plus, once one is selected, a before/after
 * map toggle and a sortable table of its best-improved locations.
 */

import { useMemo, useState } from "react";
import { INTERVENTIONS, InterventionId } from "@/lib/metrics";
import { InterventionResult } from "@/lib/api-client";

type SortColumn = "location" | "before" | "improvement";
type SortDirection = "asc" | "desc";

function labelFor(interventionType: string): string {
  return INTERVENTIONS.find((i) => i.id === interventionType)?.label ?? interventionType;
}

interface InterventionPanelProps {
  // Every intervention type simulated for this region, already sorted best-first by
  // mean_improvement (the caller ranks them) -- empty while nothing has been simulated yet.
  rankedInterventions: InterventionResult[];
  selectedIntervention: InterventionId | null;
  onSelectIntervention: (id: InterventionId) => void;
  result: InterventionResult | null;
  isLoading: boolean;
  beforeAfter: "before" | "after";
  onToggleBeforeAfter: () => void;
}

export default function InterventionPanel({
  rankedInterventions,
  selectedIntervention,
  onSelectIntervention,
  result,
  isLoading,
  beforeAfter,
  onToggleBeforeAfter,
}: InterventionPanelProps) {
  const [sortColumn, setSortColumn] = useState<SortColumn>("improvement");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");

  function handleSort(column: SortColumn) {
    if (sortColumn === column) {
      setSortDirection((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortColumn(column);
      setSortDirection("desc");
    }
  }

  const sortedLocations = useMemo(() => {
    if (!result) return [];
    const rows = [...result.ranked_locations];
    rows.sort((a, b) => {
      let av: number, bv: number;
      if (sortColumn === "before") { av = a.before_score; bv = b.before_score; }
      else if (sortColumn === "improvement") { av = a.improvement; bv = b.improvement; }
      else { av = a.lat; bv = b.lat; }
      return sortDirection === "asc" ? av - bv : bv - av;
    });
    return rows;
  }, [result, sortColumn, sortDirection]);

  const arrow = (col: SortColumn) => (sortColumn === col ? (sortDirection === "asc" ? "↑" : "↓") : "");

  const bestImprovement = rankedInterventions[0]?.mean_improvement ?? 0;

  return (
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-wide text-[#a09a89] mb-2 mt-4">
        Interventions ranked by effect
      </div>

      {isLoading && rankedInterventions.length === 0 && (
        <div className="text-[12px] text-[#a09a89] mb-3">Simulating all interventions for this region...</div>
      )}

      {rankedInterventions.length === 0 && !isLoading ? (
        <div className="flex flex-col gap-1.5 mb-3">
          {INTERVENTIONS.map((iv) => (
            <div key={iv.id} className="px-2.5 py-2 rounded-md border border-[#e4e0d8] text-[12.6px] text-[#a09a89]">
              {iv.label}
            </div>
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-1.5 mb-3">
          {rankedInterventions.map((iv, rank) => {
            const active = selectedIntervention === iv.intervention_type;
            // Bar width as a % of the best intervention's improvement (so #1 always
            // fills 100%, others scale relative to it). The `Math.max(6, ...)` floor
            // keeps a very low-effect intervention's bar from disappearing to 0px --
            // it's a minimum visible width, not a real data value.
            const barWidth = bestImprovement > 0 ? Math.max(6, (iv.mean_improvement / bestImprovement) * 100) : 0;
            return (
              <button
                key={iv.intervention_type}
                onClick={() => onSelectIntervention(iv.intervention_type as InterventionId)}
                className="relative px-2.5 py-2 rounded-md border text-left text-[12.6px] transition-colors overflow-hidden"
                style={{
                  borderColor: active ? "#1e3a5f" : "#e4e0d8",
                  background: active ? "#eef2ee" : "#ffffff",
                  color: active ? "#1e3a5f" : "#4a463c",
                  fontWeight: active ? 600 : 500,
                }}
              >
                <span
                  className="absolute left-0 top-0 bottom-0"
                  style={{ width: `${barWidth}%`, background: active ? "#d8e2da" : "#f4f2ee", zIndex: 0 }}
                />
                <span className="relative flex items-center justify-between gap-2">
                  <span>
                    <span className="text-[#a09a89] font-mono mr-1.5">#{rank + 1}</span>
                    {labelFor(iv.intervention_type)}
                  </span>
                  <span className="font-mono text-[11px] text-[#2f7d5e] shrink-0">-{iv.mean_improvement.toFixed(1)} avg</span>
                </span>
              </button>
            );
          })}
        </div>
      )}

      {result && (
        <div>
          <div className="flex items-center justify-between mb-1 px-2.5 py-2 bg-[#f4f2ee] rounded-md">
            <span className="text-xs font-medium" style={{ color: beforeAfter === "before" ? "#1e3a5f" : "#a09a89" }}>Before</span>
            <button onClick={onToggleBeforeAfter} className="relative w-9 h-5 rounded-full bg-[#1e3a5f]">
              <span
                className="absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all"
                style={{ left: beforeAfter === "before" ? 2 : 20 }}
              />
            </button>
            <span className="text-xs font-medium" style={{ color: beforeAfter === "after" ? "#1e3a5f" : "#a09a89" }}>After</span>
          </div>
          <div className="text-[10.5px] text-[#a09a89] mb-3 px-0.5">Controls the map layer below. The table always shows both scores.</div>

          <div className="text-[11px] font-semibold uppercase tracking-wide text-[#a09a89] mb-1.5">
            Top locations ({result.region_pixel_count.toLocaleString()} px analyzed)
          </div>
          <div className="border border-[#ece8de] rounded-md overflow-hidden">
            <div className="grid grid-cols-4 bg-[#faf5ee] px-2.5 py-1.5">
              <button onClick={() => handleSort("location")} className="text-left text-[10.5px] font-semibold text-[#6b6656]">Lat {arrow("location")}</button>
              <button onClick={() => handleSort("before")} className="text-left text-[10.5px] font-semibold text-[#6b6656]">Before {arrow("before")}</button>
              <span className="text-left text-[10.5px] font-semibold text-[#6b6656]">After</span>
              <button onClick={() => handleSort("improvement")} className="text-left text-[10.5px] font-semibold text-[#6b6656]">&Delta; {arrow("improvement")}</button>
            </div>
            {sortedLocations.map((row, i) => (
              <div key={i} className="grid grid-cols-4 px-2.5 py-1.5 border-t border-[#f0ede4]">
                <span className="text-[11.5px] font-mono text-[#2c2a24]">{row.lat.toFixed(4)}</span>
                <span className="text-[11.5px] font-mono text-[#8a8578]">{row.before_score.toFixed(0)}</span>
                <span className="text-[11.5px] font-mono text-[#8a8578]">{row.after_score.toFixed(0)}</span>
                <span className="text-[11.5px] font-mono font-semibold text-[#2f7d5e]">-{row.improvement.toFixed(0)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
