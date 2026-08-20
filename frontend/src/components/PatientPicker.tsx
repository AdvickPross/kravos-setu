"use client";

import { useEffect, useState } from "react";

import { listPatients, type PatientSummary } from "@/lib/api";

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
    // Debounce so every keystroke doesn't fire a request.
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
          setError(err instanceof Error ? err.message : "Failed to load patients.");
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
    <div className="flex flex-col gap-2">
      <input
        type="search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Search patients…"
        className="rounded border border-zinc-300 bg-white px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900"
      />
      {error && <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>}
      <ul className="max-h-[65vh] overflow-y-auto rounded border border-zinc-200 dark:border-zinc-800">
        {loading && patients.length === 0 && (
          <li className="p-3 text-sm text-zinc-500">Loading…</li>
        )}
        {!loading && patients.length === 0 && (
          <li className="p-3 text-sm text-zinc-500">No patients found.</li>
        )}
        {patients.map((patient) => (
          <li key={patient.patient_id}>
            <button
              type="button"
              onClick={() => onSelect(patient)}
              className={`block w-full border-b border-zinc-100 px-3 py-2 text-left text-sm last:border-b-0 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-900 ${
                patient.patient_id === selectedId ? "bg-sky-50 dark:bg-sky-950/60" : ""
              }`}
            >
              <div className="font-medium text-zinc-900 dark:text-zinc-100">
                {patient.name ?? patient.patient_id}
              </div>
              <div className="text-xs text-zinc-500">
                {patient.gender ?? "—"} · {patient._n_facts} facts
              </div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
