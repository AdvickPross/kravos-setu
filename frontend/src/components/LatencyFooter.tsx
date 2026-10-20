import type { StageTimings } from "@/lib/api";
import { formatMs } from "@/lib/format";
import { ClockIcon } from "./icons";

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
    <footer className="flex flex-wrap items-center gap-1.5 pt-1 text-[11px] text-ink-faint">
      <ClockIcon size={13} className="mr-0.5" />
      {stages.map((stage) => (
        <span
          key={stage.label}
          className="tnum rounded-full border border-line bg-surface px-2 py-0.5"
          title={stage.note}
        >
          {stage.label}{" "}
          <span className={stage.ms == null ? "italic" : "font-medium text-ink-muted"}>
            {stage.ms == null ? "not run" : formatMs(stage.ms)}
          </span>
        </span>
      ))}
      <span className="tnum ml-auto rounded-full border border-accent-line bg-accent-bg px-2.5 py-0.5 font-semibold text-accent">
        total {formatMs(timings.total_ms)}
      </span>
    </footer>
  );
}
