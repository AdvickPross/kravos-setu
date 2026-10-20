"use client";

import Link from "next/link";
import { useState } from "react";

import { BriefView } from "./BriefView";
import { ChatPanel } from "./ChatPanel";
import { LatencyFooter } from "./LatencyFooter";
import { MissingSection } from "./MissingSection";
import { NarrativeMatches } from "./NarrativeMatches";
import { ParsedQueryView } from "./ParsedQueryView";
import { PatientHeader } from "./PatientHeader";
import { PatientPicker } from "./PatientPicker";
import { QueryBox } from "./QueryBox";
import {
  AlertIcon,
  ArrowLeftIcon,
  ChatIcon,
  SearchIcon,
  ShieldIcon,
  SparkIcon,
  TerminalIcon,
} from "./icons";
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
  // "Ask" is the original single-shot /api/query path, unchanged; "Conversation"
  // is the new threaded /api/chat path (backend/conversation.py). Kept as
  // separate modes rather than merged -- a doctor who wants one quick coded
  // fact shouldn't have to wait on an LLM call, and a doctor mid-conversation
  // shouldn't lose that thread's context to a stateless query.
  const [mode, setMode] = useState<"ask" | "chat">("ask");

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
    <div className="flex h-dvh w-full flex-col">
      {header}
      <div className="mx-auto flex min-h-0 w-full max-w-[90rem] flex-1 gap-5 px-4 pb-4 pt-4 sm:px-6 sm:pb-6">
      {/* Below md the sidebar collapses; without the drawer below it, a
          narrow window showed "Select a patient to begin." and offered no way
          to select one -- the page read as broken rather than as empty. */}
      <aside className="hidden w-72 shrink-0 md:block">
        <PatientPicker
          selectedId={patient?.patient_id ?? null}
          onSelect={(selected) => {
            setPatient(selected);
            setResponse(null);
            setError(null);
          }}
        />
      </aside>

      {/* Chat mode owns its own internal scroll region (ChatPanel), so the
          page-level scroll that "ask" mode relies on would just add a second,
          fighting scrollbar -- min-h-0 without overflow-y-auto lets the flex
          child size to the available height instead. */}
      <main
        className={`scroll-quiet flex min-h-0 min-w-0 flex-1 flex-col gap-4 ${
          mode === "chat" ? "pr-1" : "overflow-y-auto pr-1"
        }`}
      >

        {!patient ? (
          <EmptyState
            picker={
              <div className="h-96 w-full max-w-sm text-left md:hidden">
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
            <ModeToggle mode={mode} onChange={setMode} />

            {mode === "ask" ? (
              <>
                <QueryBox disabled={loading} onSubmit={handleQuery} />

                {error && (
                  <Panel tone="danger" className="flex gap-3 px-5 py-4">
                    <AlertIcon size={18} className="mt-0.5 shrink-0 text-danger" />
                    <div>
                      <p className="text-sm font-medium text-danger">{error}</p>
                      <p className="mt-1 text-xs text-ink-muted">
                        The API should be running on port 8000 —{" "}
                        <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11px] text-ink">
                          uvicorn backend.main:app --port 8000
                        </code>
                      </p>
                    </div>
                  </Panel>
                )}

                {loading && <LoadingState />}

                {!loading && !response && !error && <IdleState />}

                {!loading && response && (
                  <div className="rise space-y-4 pb-4">
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
            ) : (
              <ChatPanel key={patient.patient_id} patient={patient} />
            )}
          </>
        )}
      </main>
      </div>
    </div>
  );
}

function ModeToggle({
  mode,
  onChange,
}: {
  mode: "ask" | "chat";
  onChange: (mode: "ask" | "chat") => void;
}) {
  const tabs = [
    { id: "ask" as const, label: "Ask", icon: SparkIcon, hint: "One question, a cited brief" },
    { id: "chat" as const, label: "Conversation", icon: ChatIcon, hint: "Threaded follow-ups" },
  ];
  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="inline-flex w-fit rounded-xl border border-line bg-surface-2 p-1">
        {tabs.map((tab) => {
          const on = mode === tab.id;
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => onChange(tab.id)}
              aria-pressed={on}
              title={tab.hint}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3.5 py-1.5 text-xs font-semibold transition-all ${
                on
                  ? "bg-surface text-ink shadow-card ring-1 ring-line"
                  : "text-ink-faint hover:text-ink"
              }`}
            >
              <Icon size={13} className={on ? "text-accent" : ""} />
              {tab.label}
            </button>
          );
        })}
      </div>
      <span className="hidden text-[11px] text-ink-faint sm:inline">
        {mode === "ask"
          ? "Single question → a brief where every line cites its source record."
          : "Follow-ups keep context; each answer is grounded in retrieved facts."}
      </span>
    </div>
  );
}

function EmptyState({ picker }: { picker?: React.ReactNode }) {
  return (
    <div className="card dot-grid relative flex flex-1 flex-col items-center justify-center gap-3 overflow-hidden px-6 py-16 text-center">
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-1/2 size-[28rem] -translate-x-1/2 -translate-y-1/2 rounded-full opacity-60 blur-3xl"
        style={{ background: "radial-gradient(circle, var(--glow), transparent 70%)" }}
      />
      <span className="brand-mark relative flex size-12 items-center justify-center rounded-2xl text-lg font-bold">
        K
      </span>
      <h2 className="relative mt-2 text-2xl font-semibold tracking-tight text-ink">
        Pre-operative clinical context
      </h2>
      <p className="relative max-w-md text-sm leading-relaxed text-ink-muted">
        Ask a question about a patient in plain language. Every line is retrieved from
        that patient&apos;s records and cites the FHIR resource it came from — and the
        brief names what a pre-op assessment expects to see but cannot find.
      </p>
      <p className="relative mt-1 inline-flex items-center gap-1.5 text-xs font-medium text-accent">
        <ArrowLeftIcon size={13} className="hidden md:inline" />
        <span className="hidden md:inline">Select a patient from the list on the left.</span>
        <span className="md:hidden">Select a patient below.</span>
      </p>
      {picker}
      <div className="relative mt-4 flex flex-wrap justify-center gap-2">
        {[
          ["/m1", "ABDM M1 test console →"],
          ["/m2", "ABDM M2 test console →"],
        ].map(([href, label]) => (
          <Link
            key={href}
            href={href}
            className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-medium text-ink-muted transition hover:border-line-strong hover:text-ink"
          >
            <TerminalIcon size={13} />
            {label}
          </Link>
        ))}
      </div>
    </div>
  );
}

/** Before the first question: what each part of the answer will be, so the
 *  page explains itself instead of sitting empty under the composer. */
function IdleState() {
  const tiles = [
    {
      icon: SearchIcon,
      tone: "text-accent bg-accent-bg",
      title: "Understood as",
      body: "See exactly which words resolved to coded concepts — and which honestly did not.",
    },
    {
      icon: AlertIcon,
      tone: "text-warn bg-warn-bg",
      title: "Not on file",
      body: "The pre-op checklist items that were never recorded, or are out of date.",
    },
    {
      icon: ShieldIcon,
      tone: "text-danger bg-danger-bg",
      title: "Cited facts",
      body: "Allergies first, then medications, conditions and labs — each line opens its source.",
    },
  ];
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {tiles.map((tile) => {
        const Icon = tile.icon;
        return (
          <div key={tile.title} className="card px-4 py-4">
            <span className={`inline-flex size-8 items-center justify-center rounded-lg ${tile.tone}`}>
              <Icon size={16} />
            </span>
            <p className="mt-3 text-sm font-semibold text-ink">{tile.title}</p>
            <p className="mt-1 text-xs leading-relaxed text-ink-muted">{tile.body}</p>
          </div>
        );
      })}
    </div>
  );
}

function LoadingState() {
  return (
    <div className="space-y-4" aria-busy>
      <Panel className="space-y-2.5 px-5 py-4">
        <Skeleton className="h-2.5 w-24" />
        <Skeleton className="h-5 w-3/4" />
      </Panel>
      <Panel className="space-y-2.5 px-5 py-4">
        <Skeleton className="h-2.5 w-20" />
        <Skeleton className="h-3.5 w-2/3" />
        <Skeleton className="h-3.5 w-1/2" />
      </Panel>
      <Panel className="space-y-2.5 px-5 py-4">
        <Skeleton className="h-2.5 w-16" />
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-3.5 w-full" />
        ))}
      </Panel>
    </div>
  );
}
