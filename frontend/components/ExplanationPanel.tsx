import { ShapContribution } from "@/lib/api-client";
import { getMetricById } from "@/lib/metrics";

// The plain-language sentence ("Low vegetation cover and high road density are the main
// drivers...") comes from the backend's plain_language_summary field, not recomputed
// here -- the feature-name-to-plain-English mapping only needs to exist once
// (backend/app/api/routes.py FEATURE_NAME_TO_PLAIN_LANGUAGE), not duplicated client-side.
interface ExplanationPanelProps {
  shapSummary: ShapContribution[] | null;
  plainLanguageSummary: string | null;
  isLoading: boolean;
}

export default function ExplanationPanel({ shapSummary, plainLanguageSummary, isLoading }: ExplanationPanelProps) {
  return (
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-wide text-[#a09a89] mb-2">Why this score</div>
      <div className="text-[13px] leading-relaxed text-[#2c2a24] px-3 py-2.5 bg-[#faf5ee] rounded-md">
        {isLoading
          ? "Analyzing region..."
          : plainLanguageSummary ?? "Draw a region on the map to see which factors drive its heat vulnerability score."}
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
