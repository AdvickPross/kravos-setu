import type { StageTimings } from "@/lib/api";
import { formatMs } from "@/lib/format";

/**
 * Per-stage latency, shown rather than logged. A stage that did not run reads
 * "not run" instead of 0 ms -- the vector fallback and the LLM pass are both
 * off by default, and reporting them as instant would be a lie about which
 * code paths the answer went through.
 */
export function LatencyFooter({ timings }: { timings: StageTimings }) {
  const stages: { label: string; ms: number | null | undefined; note?: string }[] = [
    { label: "parse", ms: timings.parse_ms },
    { label: "retrieve", ms: timings.retrieve_ms },
    { label: "brief", ms: timings.brief_ms },
    { label: "vector", ms: timings.vector_ms, note: "narrative fallback" },
    { label: "llm", ms: timings.llm_ms, note: "prose rewrite" },
  ];

  return (
    <footer className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line pt-2.5 text-[11px] text-ink-faint">
      {stages.map((stage) => (
        <span key={stage.label} className="tnum" title={stage.note}>
          {stage.label}{" "}
          <span className={stage.ms == null ? "italic" : "text-ink-muted"}>
            {stage.ms == null ? "not run" : formatMs(stage.ms)}
          </span>
        </span>
      ))}
      <span className="tnum ml-auto font-medium text-ink-muted">
        total {formatMs(timings.total_ms)}
      </span>
    </footer>
  );
}
