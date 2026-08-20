"use client";

import { useState } from "react";

import type { Brief, BriefLine, BriefSection, Fact } from "@/lib/api";
import { formatDate } from "@/lib/format";

export function BriefView({
  brief,
  factsById,
}: {
  brief: Brief;
  factsById: Map<string, Fact>;
}) {
  return (
    <div className="space-y-5">
      <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-100">{brief.headline}</h2>
      {(brief.sections ?? []).map((section) => (
        <BriefSectionView key={section.title} section={section} factsById={factsById} />
      ))}
    </div>
  );
}

function BriefSectionView({
  section,
  factsById,
}: {
  section: BriefSection;
  factsById: Map<string, Fact>;
}) {
  return (
    <section>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">{section.title}</h3>
      {(section.lines ?? []).length === 0 ? (
        <p className="mt-1 text-sm italic text-zinc-500">{section.empty_note}</p>
      ) : (
        <ul className="mt-1 divide-y divide-zinc-100 dark:divide-zinc-800">
          {(section.lines ?? []).map((line, index) => (
            <BriefLineRow key={index} line={line} factsById={factsById} />
          ))}
        </ul>
      )}
    </section>
  );
}

function BriefLineRow({ line, factsById }: { line: BriefLine; factsById: Map<string, Fact> }) {
  const [open, setOpen] = useState(false);

  return (
    <li className="py-1.5">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        title="Click for provenance"
        className="w-full text-left text-sm text-zinc-800 hover:text-sky-700 dark:text-zinc-200 dark:hover:text-sky-400"
      >
        {line.text}
      </button>
      {open && (
        <div className="mt-1 space-y-2 rounded border border-zinc-200 bg-zinc-50 p-2 text-xs text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400">
          {(line.fact_ids ?? []).length === 0 && <p>No source facts recorded.</p>}
          {(line.fact_ids ?? []).map((factId) => {
            const fact = factsById.get(factId);
            return (
              <dl key={factId} className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
                <dt className="text-zinc-400">fact_id</dt>
                <dd className="font-mono">{factId}</dd>
                <dt className="text-zinc-400">source type</dt>
                <dd>{fact?.source_resource_type ?? "—"}</dd>
                <dt className="text-zinc-400">resource id</dt>
                <dd className="truncate font-mono">{fact?.source_resource_id ?? "—"}</dd>
                <dt className="text-zinc-400">encounter</dt>
                <dd className="truncate font-mono">{fact?.encounter_id ?? "—"}</dd>
                <dt className="text-zinc-400">date</dt>
                <dd>{formatDate(fact?.date) ?? "—"}</dd>
              </dl>
            );
          })}
        </div>
      )}
    </li>
  );
}
