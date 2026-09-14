import { MetricDefinition } from "@/lib/metrics";

interface LegendProps {
  metric: MetricDefinition;
  labelOverride?: string;
}

export default function Legend({ metric, labelOverride }: LegendProps) {
  return (
    <div className="absolute left-4 bottom-4 bg-white/95 rounded-lg shadow-lg p-3.5 min-w-[220px] z-20">
      <div className="text-[11.5px] font-semibold text-[#4a463c] mb-2">{labelOverride ?? metric.label}</div>
      <div className="h-2.5 rounded-full" style={{ background: `linear-gradient(90deg, ${metric.stops.join(",")})` }} />
      <div className="flex justify-between mt-1.5 text-[10.5px] text-[#8a8578] font-mono">
        <span>{metric.minLabel}</span>
        <span>{metric.maxLabel}</span>
      </div>
    </div>
  );
}
