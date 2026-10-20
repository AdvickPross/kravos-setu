"use client";

import { useEffect, useState } from "react";

import { listPatients, type PatientSummary } from "@/lib/api";
import { displayAge } from "@/lib/format";
import { SearchIcon } from "./icons";
import { Avatar, Skeleton } from "./ui";

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
    <div className="card flex h-full flex-col overflow-hidden">
      <div className="space-y-3 border-b border-line p-3">
        <div className="flex items-center justify-between gap-2 px-1">
          <h1 className="text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-faint">Patients</h1>
          {!loading && (
            <span className="tnum rounded-full bg-surface-2 px-2 py-px text-[11px] font-medium text-ink-muted">
              {patients.length} patients
            </span>
          )}
        </div>

        <div className="relative">
          <SearchIcon
            size={15}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint"
          />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search by name…"
            aria-label="Search patients"
            className="w-full rounded-lg border border-line bg-bg py-2 pl-9 pr-3 text-sm text-ink outline-none transition placeholder:text-ink-faint focus:border-accent-line focus:ring-4 focus:ring-accent-bg"
          />
        </div>
      </div>

      {error && (
        <p className="m-3 rounded-lg border border-danger-line bg-danger-bg px-2.5 py-2 text-[11px] text-danger">
          {error}
        </p>
      )}

      <ul className="scroll-quiet min-h-0 flex-1 space-y-0.5 overflow-y-auto p-1.5">
        {loading && patients.length === 0 && (
          <li className="space-y-3 p-2">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="flex items-center gap-2.5">
                <Skeleton className="size-8 rounded-lg" />
                <div className="flex-1 space-y-1.5">
                  <Skeleton className="h-3 w-32" />
                  <Skeleton className="h-2 w-20" />
                </div>
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
                className={`relative flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left transition-colors ${
                  selected
                    ? "bg-accent-bg ring-1 ring-accent-line"
                    : "hover:bg-surface-2"
                }`}
              >
                <Avatar name={patient.name} seed={patient.patient_id} size="sm" />
                <span className="min-w-0 flex-1">
                  <span className="flex items-baseline gap-1.5">
                    <span
                      className={`truncate text-[13px] ${selected ? "font-semibold text-accent" : "font-medium text-ink"}`}
                    >
                      {patient.name ?? patient.patient_id}
                    </span>
                    {patient.deceased && (
                      <span className="shrink-0 text-[10px] font-medium text-danger" title="Deceased">†</span>
                    )}
                  </span>
                  <span className="tnum mt-0.5 block truncate text-[11px] text-ink-faint">
                    {[age != null ? `${age}y` : null, patient.gender, `${patient._n_facts.toLocaleString()} facts`]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
