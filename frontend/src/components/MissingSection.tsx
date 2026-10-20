import type { MissingItem } from "@/lib/api";
import { formatDate, relativeAge } from "@/lib/format";
import { AlertIcon, CheckCircleIcon, ClockIcon } from "./icons";
import { Eyebrow, Panel } from "./ui";

/**
 * The product's actual differentiator: naming what *should* be on file and
 * isn't. It sits above the retrieved facts on purpose -- a clinician who
 * reads only the top of this page must still leave knowing what is unknown.
 *
 * `absent` and `stale` are shown as distinct states rather than merged into
 * "missing", because they lead to different actions: order the test, versus
 * find out whether a result exists that predates the window.
 */
export function MissingSection({ items }: { items: MissingItem[] }) {
  if (items.length === 0) {
    return (
      <Panel className="flex items-center gap-3 px-5 py-4">
        <span className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg bg-active-bg text-active">
          <CheckCircleIcon size={16} />
        </span>
        <div>
          <Eyebrow>Pre-op checklist</Eyebrow>
          <p className="mt-0.5 text-sm text-ink-muted">
            Every checklist item has a recent record on file.
          </p>
        </div>
      </Panel>
    );
  }

  const absent = items.filter((i) => i.reason === "absent");
  const stale = items.filter((i) => i.reason === "stale");

  return (
    <Panel tone="warn">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3">
        <div className="flex items-center gap-2">
          <span className="inline-flex size-6 items-center justify-center rounded-md bg-warn-bg text-warn">
            <AlertIcon size={13} />
          </span>
          <Eyebrow tone="warn">Not on file</Eyebrow>
        </div>
        <span className="tnum flex items-center gap-1.5 text-[11px]">
          {absent.length > 0 && (
            <span className="rounded-full border border-warn-line bg-warn-bg px-2 py-px font-semibold text-warn">
              {absent.length} never recorded
            </span>
          )}
          {absent.length > 0 && stale.length > 0 && <span className="sr-only"> · </span>}
          {stale.length > 0 && (
            <span className="rounded-full border border-line bg-surface-2 px-2 py-px font-semibold text-ink-muted">
              {stale.length} out of date
            </span>
          )}
        </span>
      </div>

      <ul className="divide-y divide-line">
        {items.map((item) => (
          <li key={item.key} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-5 py-2.5 text-sm">
            <span
              aria-hidden
              className={`size-2 shrink-0 rounded-full ${
                item.reason === "absent" ? "bg-warn" : "bg-warn/40 ring-2 ring-warn/20"
              }`}
            />
            <span className="min-w-0 flex-1 font-medium text-ink">{item.label}</span>
            {item.reason === "absent" ? (
              <span className="rounded-full bg-warn-bg px-2 py-0.5 text-xs font-medium text-warn">
                never recorded
              </span>
            ) : (
              <span className="tnum inline-flex items-center gap-1 text-xs text-ink-muted">
                <ClockIcon size={12} className="text-warn" />
                last seen {formatDate(item.last_seen) ?? "an unknown date"}
                {item.last_seen && ` (${relativeAge(item.last_seen)})`}
                {item.window_days != null && ` · expected within ${item.window_days} days`}
              </span>
            )}
          </li>
        ))}
      </ul>

      <p className="border-t border-line bg-surface-2/50 px-5 py-2.5 text-[11px] leading-relaxed text-ink-faint">
        A set difference over coded facts against the pre-op checklist, not a
        prediction. Absence here means nothing matching is recorded — it does not
        mean the patient is free of the condition.
      </p>
    </Panel>
  );
}
