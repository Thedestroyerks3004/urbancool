import { METRICS } from "@/lib/metrics";

interface MetricTogglePanelProps {
  activeMetricId: string;
  onSelect: (id: string) => void;
}

export default function MetricTogglePanel({ activeMetricId, onSelect }: MetricTogglePanelProps) {
  return (
    <div className="w-full bg-white rounded-lg shadow-lg p-3.5 shrink-0">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-[#a09a89] mb-2.5">Metric layer</div>
      <div className="flex flex-col gap-0.5">
        {METRICS.map((m) => {
          const active = m.id === activeMetricId;
          const swatch = m.kind === "diverging" ? `linear-gradient(90deg,${m.stops[0]},${m.stops[m.stops.length - 1]})` : m.stops[m.stops.length - 1];
          return (
            <button
              key={m.id}
              onClick={() => onSelect(m.id)}
              className="flex items-center gap-2.5 px-2 py-2 rounded-md text-left w-full transition-colors"
              style={{ background: active ? "#eef2ee" : "transparent" }}
            >
              <span className="w-2.5 h-2.5 rounded-[3px] shrink-0" style={{ background: swatch }} />
              <span className="text-[12.8px]" style={{ color: active ? "#1e3a5f" : "#4a463c", fontWeight: active ? 600 : 400 }}>
                {m.label}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
