import type { MissingItem } from "@/lib/api";
import { formatDate } from "@/lib/format";

// Deliberately the loudest thing on the page -- the missingness section is
// the product's differentiator, not a footnote next to retrieved facts.
export function MissingSection({ items }: { items: MissingItem[] }) {
  if (items.length === 0) return null;

  return (
    <section className="rounded-lg border-2 border-amber-400 bg-amber-50 p-4 dark:border-amber-700 dark:bg-amber-950/40">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-amber-800 dark:text-amber-300">
        Not on file
      </h3>
      <ul className="mt-2 space-y-1 text-sm text-amber-900 dark:text-amber-200">
        {items.map((item) => (
          <li key={item.key}>
            <span className="font-medium">{item.label}</span>
            {item.reason === "absent" && <span> — never recorded</span>}
            {item.reason === "stale" && (
              <span>
                {" "}
                — last seen {formatDate(item.last_seen) ?? "an unknown date"}
                {item.window_days != null && ` (expected within ${item.window_days} days)`}
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
