import { ShapContribution } from "@/lib/api-client";
import { getMetricById } from "@/lib/metrics";

const FEATURE_TO_PLAIN_LANGUAGE: Record<string, string> = {
  ndvi_mean: "low vegetation cover",
  ndwi_mean: "little nearby water/moisture",
  albedo_mean: "low surface reflectivity (dark, heat-absorbing surfaces)",
  built_up_pct_mean: "a high proportion of built-up land",
  vegetation_pct_mean: "limited vegetated land",
  water_pct_mean: "limited water bodies",
  built_up_pct_trend: "a rising trend in built-up land over time",
  building_density_per_km2: "high building density",
  road_density_km_per_km2: "high road density",
  ndvi_min: "patches of very low vegetation",
  ndvi_std: "inconsistent vegetation cover",
  ndwi_std: "inconsistent moisture presence",
  albedo_min: "patches of very dark surfaces",
  albedo_std: "inconsistent surface reflectivity",
};

function buildSummary(shapSummary: ShapContribution[]): string {
  if (shapSummary.length === 0) return "Draw a region on the map to see which factors drive its heat vulnerability score.";
  const topTwo = shapSummary.slice(0, 2).map((c) => FEATURE_TO_PLAIN_LANGUAGE[c.feature] ?? c.feature);
  if (topTwo.length === 2) {
    return `${topTwo[0][0].toUpperCase()}${topTwo[0].slice(1)} and ${topTwo[1]} are the main drivers of the heat vulnerability score in this area.`;
  }
  return `${topTwo[0][0].toUpperCase()}${topTwo[0].slice(1)} is the main driver of the heat vulnerability score in this area.`;
}

interface ExplanationPanelProps {
  shapSummary: ShapContribution[] | null;
  isLoading: boolean;
}

export default function ExplanationPanel({ shapSummary, isLoading }: ExplanationPanelProps) {
  return (
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-wide text-[#a09a89] mb-2">Why this score</div>
      <div className="text-[13px] leading-relaxed text-[#2c2a24] px-3 py-2.5 bg-[#faf5ee] rounded-md">
        {isLoading ? "Analyzing region..." : buildSummary(shapSummary ?? [])}
      </div>
      {shapSummary && shapSummary.length > 0 && (
        <div className="mt-2.5 flex flex-col gap-1">
          {shapSummary.slice(0, 4).map((c) => (
            <div key={c.feature} className="flex items-center justify-between text-[11px] text-[#6b6656]">
              <span>{getMetricLabelSafe(c.feature)}</span>
              <span className="font-mono" style={{ color: c.mean_shap_value >= 0 ? "#b45f42" : "#2f7d5e" }}>
                {c.mean_shap_value >= 0 ? "+" : ""}
                {c.mean_shap_value.toFixed(2)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function getMetricLabelSafe(featureId: string): string {
  try {
    return getMetricById(featureId).label;
  } catch {
    return featureId;
  }
}
