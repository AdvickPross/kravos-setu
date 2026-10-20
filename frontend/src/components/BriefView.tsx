"use client";

import { useState } from "react";

import type { Brief, BriefLine, BriefSection, Fact } from "@/lib/api";
import { criticalityTone, formatDate, relativeAge, statusTone } from "@/lib/format";
import { Sparkline } from "./Sparkline";
import { LinkIcon, SectionIcon } from "./icons";
import { Eyebrow, Panel, StatusPill, Tag } from "./ui";

/** Allergies first, always. The rest follow clinical reading order. The
 *  backend already orders sections this way; the UI does not re-sort, it only
 *  decides which get the danger treatment. */
const DANGER_SECTIONS = new Set(["Allergies"]);

export function BriefView({
  brief,
  factsById,
}: {
  brief: Brief;
  factsById: Map<string, Fact>;
}) {
  const sections = brief.sections ?? [];

  return (
    <div className="space-y-4">
      {brief.prose && <ProsePanel prose={brief.prose} />}

      {sections.length === 0 ? (
        <Panel className="px-5 py-4">
          <Eyebrow>Result</Eyebrow>
          <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">
            Nothing in the question resolved to a coded concept, so no records were
            retrieved. That is different from the patient having no such records —
            nothing was looked up.
          </p>
        </Panel>
      ) : (
        sections.map((section) => (
          <SectionPanel key={section.title} section={section} factsById={factsById} />
        ))
      )}
    </div>
  );
}

/** The optional LLM pass writes `prose` and never touches `sections`. Shown
 *  above the grounded template but labelled, so it is never mistaken for the
 *  cited source of truth. */
function ProsePanel({ prose }: { prose: string }) {
  return (
    <Panel className="px-5 py-4">
      <div className="flex items-center gap-2">
        <Eyebrow>Summary</Eyebrow>
        <Tag tone="accent" title="Rewritten by a language model from the cited facts below">
          LLM rewrite
        </Tag>
      </div>
      <p className="mt-1.5 text-sm leading-relaxed text-ink">{prose}</p>
      <p className="mt-2 text-[11px] text-ink-faint">
        Restates the cited records below and adds nothing. The cited version is
        authoritative.
      </p>
    </Panel>
  );
}

function SectionPanel({
  section,
  factsById,
}: {
  section: BriefSection;
  factsById: Map<string, Fact>;
}) {
  const danger = DANGER_SECTIONS.has(section.title);
  const lines = section.lines ?? [];

  return (
    <Panel tone={danger && lines.length > 0 ? "danger" : "plain"}>
      <div
        className={`flex items-center justify-between gap-3 px-5 py-3 ${
          lines.length > 0 ? "border-b border-line" : ""
        } ${danger && lines.length > 0 ? "bg-danger-bg/60" : ""}`}
      >
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex size-6 items-center justify-center rounded-md ${
              danger ? "bg-danger-bg text-danger" : "bg-surface-2 text-ink-muted"
            }`}
          >
            <SectionIcon title={section.title} size={13} />
          </span>
          <Eyebrow tone={danger ? "danger" : "muted"}>{section.title}</Eyebrow>
        </div>
        {lines.length > 0 && (
          <span
            className={`tnum rounded-full border px-2 py-px text-[11px] font-semibold ${
              danger
                ? "border-danger-line bg-danger-bg text-danger"
                : "border-line bg-surface-2 text-ink-muted"
            }`}
          >
            {lines.length} {lines.length === 1 ? "record" : "records"}
          </span>
        )}
      </div>

      {lines.length === 0 ? (
        <p className="px-5 pb-4 text-sm text-ink-muted">{section.empty_note}</p>
      ) : (
        <ul className="divide-y divide-line">
          {groupLines(lines, factsById).map((group, index) =>
            group.lines.length === 1 ? (
              <LineRow key={`${group.label}-${index}`} line={group.lines[0]} factsById={factsById} />
            ) : (
              <RepeatedGroup key={`${group.label}-${index}`} group={group} factsById={factsById} />
            ),
          )}
        </ul>
      )}

      {section.coverage_note && (
        <p className="border-t border-line px-5 py-2.5 text-[11px] text-ink-faint">
          {section.coverage_note}
        </p>
      )}
    </Panel>
  );
}

/** Consecutive lines sharing a label are one measurement repeated over time,
 *  not twelve unrelated findings. Twelve rows reading "Glucose [Mass/volume]
 *  in Blood" is a wall; one label with twelve readings under it is a trend.
 *
 *  Grouping is on *consecutive* lines only, so it never reorders anything --
 *  the backend already sorted most-recent-first and that ordering is the
 *  clinically meaningful one. */
interface LineGroup {
  label: string;
  lines: BriefLine[];
}

function groupLines(lines: BriefLine[], factsById: Map<string, Fact>): LineGroup[] {
  const groups: LineGroup[] = [];
  for (const line of lines) {
    const label = lineLabel(line, factsById);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.lines.push(line);
    else groups.push({ label, lines: [line] });
  }
  return groups;
}

function lineLabel(line: BriefLine, factsById: Map<string, Fact>): string {
  const fact = line.fact_ids?.[0] ? factsById.get(line.fact_ids[0]) : undefined;
  return fact?.display ?? line.text.split(" — ")[0];
}

function numericValue(line: BriefLine, factsById: Map<string, Fact>): number | null {
  const fact = line.fact_ids?.[0] ? factsById.get(line.fact_ids[0]) : undefined;
  if (!fact?.value) return null;
  const parsed = Number.parseFloat(fact.value);
  return Number.isFinite(parsed) ? parsed : null;
}

function RepeatedGroup({
  group,
  factsById,
}: {
  group: LineGroup;
  factsById: Map<string, Fact>;
}) {
  const firstFact = group.lines[0].fact_ids?.[0]
    ? factsById.get(group.lines[0].fact_ids[0])
    : undefined;
  const unit = firstFact?.unit ?? null;

  // Lines arrive most-recent-first; a trend reads oldest-first.
  const values = group.lines.map((l) => numericValue(l, factsById));
  const points = values.every((v) => v != null) ? (values as number[]).slice().reverse() : [];

  const statuses = [...new Set(group.lines.map((l) => l.status).filter(Boolean))] as string[];

  return (
    <li>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 bg-surface-2/60 px-5 py-2">
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink">{group.label}</span>
        {points.length > 1 && <Sparkline points={points} label={group.label} />}
        {statuses.length > 1 && (
          <Tag tone="neutral" title="Status changed across these records">
            {statuses.join(" → ")}
          </Tag>
        )}
        <span className="tnum text-[11px] text-ink-faint">
          {group.lines.length} records{unit ? ` · ${unit}` : ""}
        </span>
      </div>
      <ul className="divide-y divide-line/60">
        {group.lines.map((line, index) => (
          <LineRow
            key={`${line.text}-${index}`}
            line={line}
            factsById={factsById}
            hideLabel
          />
        ))}
      </ul>
    </li>
  );
}


/** The backend renders each line as "label — value — status date". Splitting
 *  on that separator lets the value and status be styled without the backend
 *  having to serialise presentation concerns it deliberately does not own. */
function LineRow({
  line,
  factsById,
  hideLabel = false,
}: {
  line: BriefLine;
  factsById: Map<string, Fact>;
  hideLabel?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const fact = line.fact_ids?.[0] ? factsById.get(line.fact_ids[0]) : undefined;

  const label = line.text.split(" — ")[0];
  // For ALLERGY rows `value` is AllergyIntolerance.criticality, not a
  // measurement -- rendering it as a bare "low" beside an allergen invites it
  // to be read as one.
  const isCriticality = fact?.category === "ALLERGY";
  const value = fact?.value ? [fact.value, fact.unit].filter(Boolean).join(" ") : null;
  const tone = statusTone(line.status);
  const date = formatDate(line.date);
  const ago = relativeAge(line.date);

  return (
    <li>
      <div
        className={`group flex flex-wrap items-center gap-x-2.5 gap-y-1 py-2.5 transition-colors hover:bg-surface-2/50 ${
          hideLabel ? "pl-10 pr-5" : "px-5"
        }`}
      >
        {!hideLabel && <span className="min-w-0 flex-1 text-sm text-ink">{label}</span>}

        {value &&
          (isCriticality ? (
            <Tag tone={criticalityTone(fact?.value)} title="Recorded criticality of this allergy">
              criticality {value}
            </Tag>
          ) : (
            // Fixed width inside a group so the decimal points line up down
            // the column; a ragged column of readings is much harder to scan
            // for an outlier than an aligned one.
            <span
              className={`tnum text-sm font-medium text-ink ${hideLabel ? "w-28 text-right" : ""}`}
            >
              {value}
            </span>
          ))}
        {line.status && <StatusPill status={line.status} tone={tone} />}

        {date && (
          <span className="tnum text-xs text-ink-faint" title={ago ?? undefined}>
            {date}
          </span>
        )}

        {hideLabel && <span className="flex-1" aria-hidden />}

        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium transition ${
            open
              ? "border-accent-line bg-accent-bg text-accent"
              : "border-transparent text-ink-faint hover:border-line hover:text-accent"
          }`}
        >
          <LinkIcon size={11} />
          {open ? "hide source" : "source"}
        </button>
      </div>

      {open && <Provenance factIds={line.fact_ids ?? []} factsById={factsById} />}
    </li>
  );
}

/** Provenance is not optional in this product: every line cites the fact it
 *  came from, and every fact carries the FHIR resource it was extracted from. */
function Provenance({
  factIds,
  factsById,
}: {
  factIds: string[];
  factsById: Map<string, Fact>;
}) {
  if (factIds.length === 0) {
    return (
      <div className="border-t border-line bg-surface-2 px-5 py-2.5 text-xs text-ink-muted">
        No source facts recorded for this line.
      </div>
    );
  }

  return (
    <div className="space-y-2 border-t border-line bg-surface-2/70 px-5 py-3">
      {factIds.map((factId) => {
        const fact = factsById.get(factId);
        const rows: [string, string | null | undefined][] = [
          ["fact id", factId],
          ["source", fact ? `${fact.source_resource_type}/${fact.source_resource_id}` : null],
          ["encounter", fact?.encounter_id],
          ["code", fact ? [fact.code_system?.split("/").pop(), fact.code].filter(Boolean).join(" ") : null],
          ["recorded", formatDate(fact?.date)],
        ];
        return (
          <dl
            key={factId}
            className="grid grid-cols-[5.5rem_1fr] gap-x-3 gap-y-1 rounded-lg border border-line bg-surface px-3 py-2.5 text-xs"
          >
            {rows.map(([term, detail]) => (
              <div key={term} className="contents">
                <dt className="text-ink-faint">{term}</dt>
                <dd className="truncate font-mono text-[11px] text-ink-muted">{detail || "—"}</dd>
              </div>
            ))}
          </dl>
        );
      })}
    </div>
  );
}
