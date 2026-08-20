import type { MissingItem } from "@/lib/api";
import { formatDate, relativeAge } from "@/lib/format";
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
      <Panel className="px-4 py-3">
        <Eyebrow>Pre-op checklist</Eyebrow>
        <p className="mt-1 text-sm text-ink-muted">
          Every checklist item has a recent record on file.
        </p>
      </Panel>
    );
  }

  const absent = items.filter((i) => i.reason === "absent");
  const stale = items.filter((i) => i.reason === "stale");

  return (
    <Panel tone="warn" className="px-4 py-3">
      <div className="flex items-baseline justify-between gap-3">
        <Eyebrow tone="warn">Not on file</Eyebrow>
        <span className="tnum text-[11px] text-warn">
          {absent.length > 0 && `${absent.length} never recorded`}
          {absent.length > 0 && stale.length > 0 && " · "}
          {stale.length > 0 && `${stale.length} out of date`}
        </span>
      </div>

      <ul className="mt-2 space-y-1.5">
        {items.map((item) => (
          <li key={item.key} className="flex flex-wrap items-baseline gap-x-2 text-sm">
            <span
              aria-hidden
              className={`mt-1.5 size-1.5 shrink-0 rounded-full ${
                item.reason === "absent" ? "bg-warn" : "bg-warn/50"
              }`}
            />
            <span className="font-medium text-ink">{item.label}</span>
            {item.reason === "absent" ? (
              <span className="text-warn">never recorded</span>
            ) : (
              <span className="tnum text-warn">
                last seen {formatDate(item.last_seen) ?? "an unknown date"}
                {item.last_seen && ` (${relativeAge(item.last_seen)})`}
                {item.window_days != null && ` · expected within ${item.window_days} days`}
              </span>
            )}
          </li>
        ))}
      </ul>

      <p className="mt-2.5 border-t border-warn-line pt-2 text-[11px] leading-relaxed text-warn">
        A set difference over coded facts against the pre-op checklist, not a
        prediction. Absence here means nothing matching is recorded — it does not
        mean the patient is free of the condition.
      </p>
    </Panel>
  );
}
