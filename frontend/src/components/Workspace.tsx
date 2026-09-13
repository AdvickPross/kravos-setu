"use client";

import Link from "next/link";
import { useState } from "react";

import { BriefView } from "./BriefView";
import { LatencyFooter } from "./LatencyFooter";
import { MissingSection } from "./MissingSection";
import { NarrativeMatches } from "./NarrativeMatches";
import { ParsedQueryView } from "./ParsedQueryView";
import { PatientHeader } from "./PatientHeader";
import { PatientPicker } from "./PatientPicker";
import { QueryBox } from "./QueryBox";
import { Panel, Skeleton } from "./ui";
import {
  ApiError,
  postQuery,
  type Fact,
  type PatientSummary,
  type QueryResponse,
} from "@/lib/api";

/**
 * The clinical workspace: pick a patient, ask, read the brief.
 *
 * Extracted from `app/page.tsx` when ABHA sign-in became the entry point.
 * `page.tsx` is now a gate and holds no clinical logic, so the two concerns
 * can move independently -- the sign-in flow changes with ABDM's API, this
 * changes with what a clinician needs on screen.
 *
 * `initialPatient` is how an identity becomes a record: after sign-in the
 * ABHA is resolved to a patient id through `backend/abha_link.py`, and this
 * opens straight onto it rather than asking a signed-in user to find
 * themselves in a list of 952.
 */
export function Workspace({
  initialPatient = null,
  header,
}: {
  initialPatient?: PatientSummary | null;
  header?: React.ReactNode;
}) {
  const [patient, setPatient] = useState<PatientSummary | null>(initialPatient);
  const [response, setResponse] = useState<QueryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleQuery(query: string) {
    if (!patient) return;
    setLoading(true);
    setError(null);
    try {
      setResponse(await postQuery(patient.patient_id, query));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the API.");
      setResponse(null);
    } finally {
      setLoading(false);
    }
  }

  const factsById = new Map<string, Fact>();
  for (const entityFacts of response?.result.entities ?? []) {
    for (const fact of entityFacts.facts ?? []) factsById.set(fact.fact_id, fact);
  }

  return (
    <div className="mx-auto flex h-dvh w-full max-w-[88rem] flex-col">
      {header}
      <div className="flex min-h-0 flex-1 gap-5 p-4 sm:p-5">
      {/* Below md the sidebar collapses; without the drawer below it, a
          narrow window showed "Select a patient to begin." and offered no way
          to select one -- the page read as broken rather than as empty. */}
      <aside className="hidden w-64 shrink-0 md:block">
        <PatientPicker
          selectedId={patient?.patient_id ?? null}
          onSelect={(selected) => {
            setPatient(selected);
            setResponse(null);
            setError(null);
          }}
        />
      </aside>

      <main className="flex min-w-0 flex-1 flex-col gap-4 overflow-y-auto">
        {!patient ? (
          <EmptyState
            picker={
              <div className="w-full max-w-sm md:hidden">
                <PatientPicker
                  selectedId={null}
                  onSelect={(selected) => {
                    setPatient(selected);
                    setResponse(null);
                    setError(null);
                  }}
                />
              </div>
            }
          />
        ) : (
          <>
            <PatientHeader patient={patient} />
            <QueryBox disabled={loading} onSubmit={handleQuery} />

            {error && (
              <Panel tone="danger" className="px-4 py-3">
                <p className="text-sm text-danger">{error}</p>
                <p className="mt-1 text-[11px] text-danger/80">
                  The API should be running on port 8000 —{" "}
                  <code className="font-mono">uvicorn backend.main:app --port 8000</code>
                </p>
              </Panel>
            )}

            {loading && <LoadingState />}

            {!loading && response && (
              <div className="space-y-3 pb-4">
                <ParsedQueryView parsed={response.parsed} />
                {/* Missingness sits above the retrieved facts on purpose: a
                    reader who stops at the top must still learn what is
                    unknown, which is the product's whole claim. */}
                <MissingSection items={response.brief.missing ?? []} />
                <BriefView brief={response.brief} factsById={factsById} />
                <NarrativeMatches matches={response.result.narrative_matches ?? []} />
                <LatencyFooter timings={response.timings ?? {}} />
              </div>
            )}
          </>
        )}
      </main>
      </div>
    </div>
  );
}

function EmptyState({ picker }: { picker?: React.ReactNode }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center">
      <h2 className="text-lg font-semibold tracking-tight text-ink">
        Pre-operative clinical context
      </h2>
      <p className="max-w-md text-sm leading-relaxed text-ink-muted">
        Ask a question about a patient in plain language. Every line is retrieved from
        that patient&apos;s records and cites the FHIR resource it came from — and the
        brief names what a pre-op assessment expects to see but cannot find.
      </p>
      <p className="mt-1 text-xs text-ink-faint">
        <span className="hidden md:inline">Select a patient from the list on the left.</span>
        <span className="md:hidden">Select a patient below.</span>
      </p>
      {picker}
      <Link
        href="/m1"
        className="mt-4 rounded border border-line-strong bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-2"
      >
        ABDM M1 test console →
      </Link>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="space-y-3" aria-busy>
      <Panel className="space-y-2 px-4 py-3">
        <Skeleton className="h-2.5 w-24" />
        <Skeleton className="h-4 w-3/4" />
      </Panel>
      <Panel className="space-y-2 px-4 py-3">
        <Skeleton className="h-2.5 w-20" />
        <Skeleton className="h-3 w-2/3" />
        <Skeleton className="h-3 w-1/2" />
      </Panel>
      <Panel className="space-y-2 px-4 py-3">
        <Skeleton className="h-2.5 w-16" />
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-3 w-full" />
        ))}
      </Panel>
    </div>
  );
}
