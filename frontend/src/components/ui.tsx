import type { ReactNode } from "react";

import type { StatusTone } from "@/lib/format";

/** Small caps section label. Used for every panel heading so the eye can find
 *  section boundaries without relying on size alone. */
export function Eyebrow({ children, tone = "muted" }: { children: ReactNode; tone?: "muted" | "danger" | "warn" }) {
  const colour =
    tone === "danger" ? "text-danger" : tone === "warn" ? "text-warn" : "text-ink-faint";
  return (
    <h3 className={`text-[11px] font-semibold uppercase tracking-[0.09em] ${colour}`}>{children}</h3>
  );
}

/** The one raised surface. `tone` adds a coloured edge on the left rather than
 *  flooding the whole card: the colour still says "allergy" or "missing", but
 *  the content inside stays as readable as everything else on the page. */
export function Panel({
  children,
  tone = "plain",
  className = "",
}: {
  children: ReactNode;
  tone?: "plain" | "danger" | "warn";
  className?: string;
}) {
  const edge = {
    plain: "",
    danger: "before:bg-danger",
    warn: "before:bg-warn",
  } as const;
  return (
    <section
      className={`card relative overflow-hidden ${
        tone === "plain" ? "" : `before:absolute before:inset-y-0 before:left-0 before:w-[3px] ${edge[tone]}`
      } ${className}`}
    >
      {children}
    </section>
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
      className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-px text-[10px] font-semibold uppercase tracking-wide ${STATUS_STYLES[tone]}`}
    >
      {tone === "active" && <span aria-hidden className="size-1.5 rounded-full bg-current" />}
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
  tone?: "neutral" | "danger" | "warn" | "accent" | "active";
  title?: string;
}) {
  const tones = {
    neutral: "bg-surface-2 text-ink-muted border-line",
    danger: "bg-danger-bg text-danger border-danger-line",
    warn: "bg-warn-bg text-warn border-warn-line",
    accent: "bg-accent-bg text-accent border-accent-line",
    active: "bg-active-bg text-active border-active-line",
  } as const;
  return (
    <span
      title={title}
      className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-px text-[10px] font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton rounded-md ${className}`} />;
}

/** Initials on a hue derived from the id, so the same patient always gets the
 *  same chip and neighbouring rows in a list are easy to tell apart. */
export function Avatar({
  name,
  seed,
  size = "md",
}: {
  name: string | null | undefined;
  seed: string;
  size?: "sm" | "md" | "lg";
}) {
  const initials =
    (name ?? "?")
      .replace(/[0-9]/g, "")
      .split(/\s+/)
      .filter(Boolean)
      .map((part) => part[0])
      .filter((_, i, all) => i === 0 || i === all.length - 1)
      .join("")
      .toUpperCase() || "?";
  let hash = 0;
  for (const ch of seed) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  const hue = hash % 360;
  const dims = {
    sm: "size-8 text-[11px] rounded-lg",
    md: "size-9 text-xs rounded-xl",
    lg: "size-14 text-lg rounded-2xl",
  } as const;
  return (
    <span
      aria-hidden
      className={`inline-flex shrink-0 items-center justify-center font-semibold tracking-wide ${dims[size]}`}
      style={{
        background: `color-mix(in srgb, hsl(${hue} 70% 55%) 16%, var(--surface))`,
        color: `color-mix(in srgb, hsl(${hue} 65% 45%) 75%, var(--text))`,
        boxShadow: `inset 0 0 0 1px color-mix(in srgb, hsl(${hue} 70% 55%) 28%, transparent)`,
      }}
    >
      {initials}
    </span>
  );
}

/** Brand mark + wordmark, shared by every screen header. */
export function Brand({ subtitle }: { subtitle?: string }) {
  return (
    <span className="flex items-center gap-2.5">
      <span className="brand-mark flex size-8 items-center justify-center rounded-[10px] text-[13px] font-bold">
        K
      </span>
      <span className="leading-tight">
        <span className="block text-sm font-semibold tracking-tight text-ink">Kravos Setu</span>
        {subtitle && <span className="block text-[11px] text-ink-faint">{subtitle}</span>}
      </span>
    </span>
  );
}
