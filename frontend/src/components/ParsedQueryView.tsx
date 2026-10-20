import { Fragment, type ReactNode } from "react";

import type { MatchQuality, ParsedQuery, ResolvedEntity } from "@/lib/api";
import { categoryNoun } from "@/lib/format";
import { SearchIcon } from "./icons";
import { Eyebrow, Panel, Tag } from "./ui";

/**
 * Shows what the parser actually understood, because an honest "I did not
 * resolve this" is the point of the NER layer and is worth nothing if the UI
 * hides it. Colour is match quality, consistently between the highlighted
 * span and the legend.
 */
const QUALITY_STYLES: Record<MatchQuality, string> = {
  exact: "bg-active-bg text-active ring-active-line",
  synonym: "bg-accent-bg text-accent ring-accent-line",
  fuzzy: "bg-warn-bg text-warn ring-warn-line",
  unresolved: "bg-danger-bg text-danger ring-danger-line",
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
    if (existing) existing.entities.push(entity);
    else byRange.set(key, { start: entity.start, end: entity.end, entities: [entity] });
  }
  return [...byRange.values()].sort((a, b) => a.start - b.start);
}

function worstQuality(entities: ResolvedEntity[]): MatchQuality {
  return entities.reduce<MatchQuality>(
    (worst, e) => (QUALITY_RANK[e.quality] > QUALITY_RANK[worst] ? e.quality : worst),
    entities[0].quality,
  );
}

/** A category cue resolves to a category with no codes -- that empty list is
 *  what makes retrieval drop the code filter and return the whole list. */
function isCategoryEntity(entity: ResolvedEntity): boolean {
  return Boolean(entity.category) && (entity.codes?.length ?? 0) === 0 && entity.quality !== "unresolved";
}

function describe(entity: ResolvedEntity): string {
  const bits = [entity.category ?? "no category", QUALITY_LABELS[entity.quality]];
  if (isCategoryEntity(entity)) bits.push("whole category");
  else if (entity.codes?.length) bits.push(`${entity.codes.length} code(s)`);
  if (entity.negated) bits.push("negated");
  if (entity.display) bits.push(entity.display);
  return bits.join(" · ");
}

export function ParsedQueryView({ parsed }: { parsed: ParsedQuery }) {
  const text = parsed.raw;
  const entities = parsed.entities ?? [];
  const groups = groupByOffsets(entities);

  const nodes: ReactNode[] = [];
  let cursor = 0;
  groups.forEach((group, index) => {
    if (group.start > cursor) {
      nodes.push(<Fragment key={`t-${index}`}>{text.slice(cursor, group.start)}</Fragment>);
    }
    const quality = worstQuality(group.entities);
    const negated = group.entities.some((e) => e.negated);
    nodes.push(
      <mark
        key={`e-${index}`}
        title={group.entities.map(describe).join("\n")}
        className={`rounded-md px-1.5 py-0.5 font-medium ring-1 ${QUALITY_STYLES[quality]} ${
          negated ? "line-through decoration-2" : ""
        }`}
      >
        {text.slice(group.start, group.end)}
      </mark>,
    );
    cursor = group.end;
  });
  if (cursor < text.length) nodes.push(<Fragment key="t-end">{text.slice(cursor)}</Fragment>);

  const unresolved = entities.filter((e) => e.quality === "unresolved");
  const negated = entities.filter((e) => e.negated);
  const categoryEntities = entities.filter(isCategoryEntity);
  const window =
    parsed.date_from || parsed.date_to
      ? `${parsed.date_from ?? "…"} → ${parsed.date_to ?? "…"}`
      : null;

  return (
    <Panel className="px-5 py-4">
      <div className="flex items-center gap-2">
        <span className="inline-flex size-6 items-center justify-center rounded-md bg-accent-bg text-accent">
          <SearchIcon size={13} />
        </span>
        <Eyebrow>Understood as</Eyebrow>
      </div>

      <p className="mt-2.5 text-lg leading-loose text-ink">{nodes}</p>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {(Object.keys(QUALITY_LABELS) as MatchQuality[])
          .filter((q) => entities.some((e) => e.quality === q))
          .map((q) => (
            <span
              key={q}
              className={`rounded-full px-2 py-px text-[10px] font-semibold ring-1 ${QUALITY_STYLES[q]}`}
            >
              {QUALITY_LABELS[q]}
            </span>
          ))}
        {window && <Tag tone="accent" title="Date filter applied to retrieval">{window}</Tag>}
      </div>

      {/* Each note below states a consequence, not a status. The parse view is
          only useful if it explains what the result will look like. */}
      <div className="mt-2.5 space-y-1 text-xs leading-relaxed">
        {categoryEntities.length > 0 && (
          <p className="text-ink-muted">
            Asked for a whole list, so{" "}
            <strong className="font-medium text-ink">
              every {categoryEntities.map((e) => categoryNoun(e.category)).join(" and ")} record
            </strong>{" "}
            on file was retrieved, not just a matching code.
          </p>
        )}

        {negated.length > 0 && (
          <p className="text-ink-muted">
            {negated.map((e) => `"${e.span}"`).join(", ")} read as negated. Matching records
            are still shown —{" "}
            <strong className="font-medium text-ink">
              a record that contradicts the question is the point
            </strong>
            , not something to hide.
          </p>
        )}

        {unresolved.length > 0 && (
          <p className="text-danger">
            Not resolved to any coded concept:{" "}
            {unresolved.map((e) => `"${e.span}"`).join(", ")}. Nothing was retrieved for
            these rather than a plausible guess being made.
          </p>
        )}
      </div>
    </Panel>
  );
}
