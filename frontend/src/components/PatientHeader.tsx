import type { PatientSummary } from "@/lib/api";
import { displayAge, formatDate } from "@/lib/format";
import { Tag } from "./ui";

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
    <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5 border-b border-line pb-3">
      <h2 className="text-xl font-semibold tracking-tight text-ink">
        {patient.name ?? patient.patient_id}
      </h2>

      {patient.deceased && (
        <Tag tone="danger" title={`Deceased ${deceasedOn}`}>
          Deceased {deceasedOn}
        </Tag>
      )}

      <p className="tnum text-sm text-ink-muted">{facts.join(" · ")}</p>

      <p className="tnum ml-auto text-xs text-ink-faint">
        {patient._n_facts.toLocaleString()} facts on file
      </p>
    </header>
  );
}
