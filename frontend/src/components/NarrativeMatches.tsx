import type { NarrativeMatch } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { Eyebrow, Panel, Tag } from "./ui";

/**
 * Free-text REPORT hits from the vector fallback, which runs only when
 * nothing in the query resolved to a code. Labelled as unstructured
 * throughout: these are passages from a note, not coded facts, and they carry
 * a similarity score rather than a clinical status.
 */
export function NarrativeMatches({ matches }: { matches: NarrativeMatch[] }) {
  if (matches.length === 0) return null;

  return (
    <Panel className="px-4 py-3">
      <div className="flex items-center gap-2">
        <Eyebrow>From clinical notes</Eyebrow>
        <Tag tone="accent" title="Similarity search over free-text notes, not coded records">
          unstructured
        </Tag>
      </div>
      <p className="mt-1 text-[11px] text-ink-faint">
        Nothing in the question matched a coded concept, so the free-text notes were
        searched instead. These passages are not coded facts and carry no status.
      </p>

      <ul className="mt-2.5 space-y-2.5">
        {matches.map((match) => (
          <li key={match.fact_id} className="border-l-2 border-line pl-3">
            <div className="tnum flex flex-wrap items-baseline gap-x-2 text-[11px] text-ink-faint">
              <span className="font-medium text-ink-muted">{match.section}</span>
              {match.date && <span>{formatDate(match.date)}</span>}
              <span>score {match.score.toFixed(2)}</span>
            </div>
            <p className="mt-0.5 text-sm leading-relaxed text-ink-muted">{match.text}</p>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
