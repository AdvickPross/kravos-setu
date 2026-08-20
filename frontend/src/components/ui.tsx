import type { ReactNode } from "react";

import type { StatusTone } from "@/lib/format";

/** Small caps section label. Used for every panel heading so the eye can find
 *  section boundaries without relying on size alone. */
export function Eyebrow({ children, tone = "muted" }: { children: ReactNode; tone?: "muted" | "danger" | "warn" }) {
  const colour =
    tone === "danger" ? "text-danger" : tone === "warn" ? "text-warn" : "text-ink-faint";
  return (
    <h3 className={`text-[11px] font-semibold uppercase tracking-[0.08em] ${colour}`}>{children}</h3>
  );
}

export function Panel({
  children,
  tone = "plain",
  className = "",
}: {
  children: ReactNode;
  tone?: "plain" | "danger" | "warn";
  className?: string;
}) {
  const tones = {
    plain: "bg-surface border-line",
    danger: "bg-danger-bg border-danger-line",
    warn: "bg-warn-bg border-warn-line",
  } as const;
  return (
    <section className={`rounded-lg border ${tones[tone]} ${className}`}>{children}</section>
  );
}

const STATUS_STYLES: Record<StatusTone, string> = {
  active: "bg-active-bg text-active border-active-line",
  historical: "bg-surface-2 text-ink-muted border-line",
  neutral: "bg-surface-2 text-ink-muted border-line",
};

export function StatusPill({ status, tone }: { status: string; tone: StatusTone }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded border px-1.5 py-px text-[10px] font-medium uppercase tracking-wide ${STATUS_STYLES[tone]}`}
    >
      {status}
    </span>
  );
}

export function Tag({
  children,
  tone = "neutral",
  title,
}: {
  children: ReactNode;
  tone?: "neutral" | "danger" | "warn" | "accent";
  title?: string;
}) {
  const tones = {
    neutral: "bg-surface-2 text-ink-muted border-line",
    danger: "bg-danger-bg text-danger border-danger-line",
    warn: "bg-warn-bg text-warn border-warn-line",
    accent: "bg-accent-bg text-accent border-accent-line",
  } as const;
  return (
    <span
      title={title}
      className={`inline-flex shrink-0 items-center gap-1 rounded border px-1.5 py-px text-[10px] font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton rounded ${className}`} />;
}
