import { Fragment, type ReactNode } from "react";

import type { MatchQuality, ParsedQuery, ResolvedEntity } from "@/lib/api";

// Match-quality colour coding, applied consistently to the highlighted span
// and the legend below it. Rank drives which colour wins when two entities
// share a span (aspirin -> ALLERGY substance + MEDICATION product).
const QUALITY_STYLES: Record<MatchQuality, string> = {
  exact:
    "bg-emerald-100 text-emerald-900 ring-emerald-300 dark:bg-emerald-950 dark:text-emerald-200 dark:ring-emerald-800",
  synonym:
    "bg-sky-100 text-sky-900 ring-sky-300 dark:bg-sky-950 dark:text-sky-200 dark:ring-sky-800",
  fuzzy:
    "bg-amber-100 text-amber-900 ring-amber-300 dark:bg-amber-950 dark:text-amber-200 dark:ring-amber-800",
  unresolved:
    "bg-rose-100 text-rose-900 ring-rose-300 dark:bg-rose-950 dark:text-rose-200 dark:ring-rose-800",
};

const QUALITY_RANK: Record<MatchQuality, number> = {
  exact: 0,
  synonym: 1,
  fuzzy: 2,
  unresolved: 3,
};

const QUALITY_LABELS: Record<MatchQuality, string> = {
  exact: "Exact",
  synonym: "Synonym",
  fuzzy: "Fuzzy",
  unresolved: "Unresolved",
};

interface Group {
  start: number;
  end: number;
  entities: ResolvedEntity[];
}

function groupByOffsets(entities: ResolvedEntity[]): Group[] {
  const byRange = new Map<string, Group>();
  for (const entity of entities) {
    const key = `${entity.start}-${entity.end}`;
    const existing = byRange.get(key);
    if (existing) {
      existing.entities.push(entity);
    } else {
      byRange.set(key, { start: entity.start, end: entity.end, entities: [entity] });
    }
  }
  return [...byRange.values()].sort((a, b) => a.start - b.start);
}

function worstQuality(entities: ResolvedEntity[]): MatchQuality {
  return entities.reduce<MatchQuality>(
    (worst, entity) => (QUALITY_RANK[entity.quality] > QUALITY_RANK[worst] ? entity.quality : worst),
    entities[0].quality,
  );
}

export function ParsedQueryView({ parsed }: { parsed: ParsedQuery }) {
  const text = parsed.raw;
  const groups = groupByOffsets(parsed.entities ?? []);

  const nodes: ReactNode[] = [];
  let cursor = 0;
  groups.forEach((group, index) => {
    if (group.start > cursor) {
      nodes.push(<Fragment key={`text-${index}`}>{text.slice(cursor, group.start)}</Fragment>);
    }
    const quality = worstQuality(group.entities);
    const negated = group.entities.some((entity) => entity.negated);
    const title = group.entities
      .map((entity) => {
        const bits = [entity.category ?? "unknown category", QUALITY_LABELS[entity.quality]];
        if (entity.negated) bits.push("negated");
        if (entity.display) bits.push(entity.display);
        return bits.join(" · ");
      })
      .join("\n");
    nodes.push(
      <mark
        key={`entity-${index}`}
        title={title}
        className={`rounded px-1 py-0.5 ring-1 ${QUALITY_STYLES[quality]} ${
          negated ? "line-through decoration-2" : ""
        }`}
      >
        {text.slice(group.start, group.end)}
      </mark>,
    );
    cursor = group.end;
  });
  if (cursor < text.length) {
    nodes.push(<Fragment key="text-end">{text.slice(cursor)}</Fragment>);
  }

  return (
    <div className="space-y-2">
      <p className="text-lg leading-relaxed">{nodes}</p>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {(Object.keys(QUALITY_LABELS) as MatchQuality[]).map((quality) => (
          <span key={quality} className={`rounded px-1.5 py-0.5 ring-1 ${QUALITY_STYLES[quality]}`}>
            {QUALITY_LABELS[quality]}
          </span>
        ))}
        <span className="text-zinc-500 line-through decoration-2">Negated</span>
      </div>
    </div>
  );
}
