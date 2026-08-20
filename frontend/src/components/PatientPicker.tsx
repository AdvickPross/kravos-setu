"use client";

import { useEffect, useState } from "react";

import { listPatients, type PatientSummary } from "@/lib/api";
import { displayAge } from "@/lib/format";
import { Skeleton } from "./ui";

export function PatientPicker({
  selectedId,
  onSelect,
}: {
  selectedId: string | null;
  onSelect: (patient: PatientSummary) => void;
}) {
  const [query, setQuery] = useState("");
  const [patients, setPatients] = useState<PatientSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Debounced so a keystroke does not become a request.
    const handle = setTimeout(() => {
      setLoading(true);
      listPatients(query || undefined)
        .then((data) => {
          if (cancelled) return;
          setPatients(data);
          setError(null);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setError(err instanceof Error ? err.message : "Could not reach the API.");
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [query]);

  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex items-baseline justify-between gap-2">
        <h1 className="text-sm font-semibold tracking-tight text-ink">Kravos Setu</h1>
        {!loading && (
          <span className="tnum text-[11px] text-ink-faint">{patients.length} patients</span>
        )}
      </div>

      <input
        type="search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Search by name…"
        aria-label="Search patients"
        className="rounded-md border border-line bg-surface px-3 py-1.5 text-sm text-ink placeholder:text-ink-faint"
      />

      {error && (
        <p className="rounded border border-danger-line bg-danger-bg px-2 py-1.5 text-[11px] text-danger">
          {error}
        </p>
      )}

      <ul className="min-h-0 flex-1 divide-y divide-line/70 overflow-y-auto rounded-md border border-line bg-surface">
        {loading && patients.length === 0 && (
          <li className="space-y-2 p-3">
            {[0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="space-y-1">
                <Skeleton className="h-3 w-32" />
                <Skeleton className="h-2 w-20" />
              </div>
            ))}
          </li>
        )}

        {!loading && patients.length === 0 && !error && (
          <li className="p-3 text-sm text-ink-muted">No patients match “{query}”.</li>
        )}

        {patients.map((patient) => {
          const selected = patient.patient_id === selectedId;
          const age = displayAge(patient.birth_date, patient.deceased);
          return (
            <li key={patient.patient_id}>
              <button
                type="button"
                onClick={() => onSelect(patient)}
                aria-current={selected}
                className={`block w-full px-3 py-2 text-left transition-colors ${
                  selected ? "bg-accent-bg" : "hover:bg-surface-2"
                }`}
              >
                <div className="flex items-baseline gap-1.5">
                  <span
                    className={`truncate text-sm ${selected ? "font-semibold text-accent" : "font-medium text-ink"}`}
                  >
                    {patient.name ?? patient.patient_id}
                  </span>
                  {patient.deceased && (
                    <span className="shrink-0 text-[10px] font-medium text-danger">†</span>
                  )}
                </div>
                <div className="tnum mt-0.5 text-[11px] text-ink-faint">
                  {[age != null ? `${age}y` : null, patient.gender, `${patient._n_facts} facts`]
                    .filter(Boolean)
                    .join(" · ")}
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
