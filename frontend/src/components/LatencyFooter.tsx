import type { StageTimings } from "@/lib/api";
import { formatMs } from "@/lib/format";

export function LatencyFooter({ timings }: { timings: StageTimings }) {
  const stages: { label: string; ms: number | null | undefined }[] = [
    { label: "parse", ms: timings.parse_ms },
    { label: "retrieve", ms: timings.retrieve_ms },
    { label: "brief", ms: timings.brief_ms },
  ];

  return (
    <footer className="mt-2 flex flex-wrap gap-x-4 gap-y-1 border-t border-zinc-200 pt-3 text-xs text-zinc-500 dark:border-zinc-800">
      {stages.map((stage) => (
        <span key={stage.label}>
          {stage.label}: {formatMs(stage.ms)}
        </span>
      ))}
      <span className="font-medium text-zinc-700 dark:text-zinc-300">
        total: {formatMs(timings.total_ms)}
      </span>
    </footer>
  );
}
