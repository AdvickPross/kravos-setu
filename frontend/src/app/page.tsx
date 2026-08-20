"use client";

import { useState } from "react";

import { BriefView } from "@/components/BriefView";
import { LatencyFooter } from "@/components/LatencyFooter";
import { MissingSection } from "@/components/MissingSection";
import { ParsedQueryView } from "@/components/ParsedQueryView";
import { PatientPicker } from "@/components/PatientPicker";
import { QueryBox } from "@/components/QueryBox";
import { ApiError, postQuery, type Fact, type PatientSummary, type QueryResponse } from "@/lib/api";
import { ageFromBirthDate } from "@/lib/format";

export default function Home() {
  const [patient, setPatient] = useState<PatientSummary | null>(null);
  const [response, setResponse] = useState<QueryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleQuery(query: string) {
    if (!patient) return;
    setLoading(true);
    setError(null);
    try {
      const data = await postQuery(patient.patient_id, query);
      setResponse(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
      setResponse(null);
    } finally {
      setLoading(false);
    }
  }

  const factsById = new Map<string, Fact>();
  if (response) {
    for (const entityFacts of response.result.entities ?? []) {
      for (const fact of entityFacts.facts ?? []) {
        factsById.set(fact.fact_id, fact);
      }
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-1 gap-6 p-6">
      <aside className="w-72 flex-shrink-0">
        <h1 className="mb-4 text-lg font-bold text-zinc-900 dark:text-zinc-100">Kravos Setu</h1>
        <PatientPicker
          selectedId={patient?.patient_id ?? null}
          onSelect={(selected) => {
            setPatient(selected);
            setResponse(null);
            setError(null);
          }}
        />
      </aside>

      <main className="flex flex-1 flex-col gap-6">
        {!patient ? (
          <p className="text-zinc-500">Select a patient to begin.</p>
        ) : (
          <>
            <div className="space-y-2">
              <p className="text-sm text-zinc-500">
                {patient.name ?? patient.patient_id}
                {ageFromBirthDate(patient.birth_date) != null && ` · ${ageFromBirthDate(patient.birth_date)}y`}
                {patient.gender && ` · ${patient.gender}`}
              </p>
              <QueryBox disabled={loading} onSubmit={handleQuery} />
            </div>

            {loading && <p className="text-sm text-zinc-500">Retrieving…</p>}
            {error && <p className="text-sm text-rose-600 dark:text-rose-400">{error}</p>}

            {response && (
              <div className="space-y-6">
                <ParsedQueryView parsed={response.parsed} />
                <MissingSection items={response.brief.missing ?? []} />
                <BriefView brief={response.brief} factsById={factsById} />
                <LatencyFooter timings={response.timings ?? {}} />
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
