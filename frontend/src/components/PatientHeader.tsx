import type { PatientSummary } from "@/lib/api";
import { displayAge, formatDate } from "@/lib/format";
import { Avatar, Tag } from "./ui";

/**
 * Identity bar. Deliberately states death before anything else it might
 * qualify: `age_years` reports age *at death* for a deceased patient, so a
 * patient born in 1950 who died in 1982 shows as "32y". Without the badge
 * that reads as a living 32-year-old, and every date below it looks stale for
 * no visible reason.
 */
export function PatientHeader({ patient }: { patient: PatientSummary }) {
  const age = displayAge(patient.birth_date, patient.deceased);
  const deceasedOn = formatDate(patient.deceased);

  const facts: string[] = [];
  if (age != null) facts.push(`${age}y${patient.deceased ? " at death" : ""}`);
  if (patient.gender) facts.push(patient.gender);
  if (patient.birth_date) facts.push(`b. ${formatDate(patient.birth_date)}`);

  return (
    <header className="card flex flex-wrap items-center gap-x-5 gap-y-3 px-5 py-4">
      <Avatar name={patient.name} seed={patient.patient_id} size="lg" />

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <h2 className="min-w-0 break-words text-xl font-semibold tracking-tight text-ink">
            {patient.name ?? patient.patient_id}
          </h2>
          {patient.deceased && (
            <Tag tone="danger" title={`Deceased ${deceasedOn}`}>
              Deceased {deceasedOn}
            </Tag>
          )}
          {patient._source_file === "manual" && <Tag tone="accent">added here</Tag>}
        </div>
        <p className="tnum mt-0.5 flex flex-wrap items-center gap-x-2 text-sm text-ink-muted">
          <span>{facts.join(" · ")}</span>
          <span className="font-mono text-[11px] text-ink-faint">{patient.patient_id.slice(0, 8)}</span>
        </p>
      </div>

      <dl className="w-full rounded-xl border border-line bg-surface-2/60 sm:w-auto">
        <div className="px-4 py-2">
          <dt className="text-[10px] font-semibold uppercase tracking-[0.08em] text-ink-faint">Records</dt>
          <dd className="tnum mt-0.5 text-sm font-semibold text-ink">
            {patient._n_facts.toLocaleString()} facts on file
          </dd>
        </div>
      </dl>
    </header>
  );
}
