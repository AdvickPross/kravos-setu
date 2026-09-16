"use client";

import { useEffect, useState } from "react";

import { createPatient, listPatients, type PatientSummary } from "@/lib/api";
import { UploadFlow } from "./UploadFlow";

/**
 * The demo path's equivalent of the records step.
 *
 * Signing in with ABHA answers "who is this?" from a national registry. With
 * no ABHA there is no registry, so the question has to be answered locally:
 * pick someone from the 952 synthetic patients, or create a new one and give
 * them a history by uploading it.
 *
 * Creating a patient here is not a lesser version of the ABHA path. It is the
 * walk-in case -- somebody at a desk whose history is a paper folder and who
 * has no digital identity yet -- and it is the only route by which a record
 * with *no* prior facts gets one. That makes it the honest way to show the
 * OCR pipeline: an empty patient, a scan, and a chart that exists only
 * because the scan was read.
 */
export function DemoGate({
  onReady,
}: {
  onReady: (patient: PatientSummary) => void;
}) {
  const [mode, setMode] = useState<"choose" | "create" | "upload">("choose");
  const [patient, setPatient] = useState<PatientSummary | null>(null);
  const [patients, setPatients] = useState<PatientSummary[]>([]);
  const [query, setQuery] = useState("");

  const [name, setName] = useState("");
  const [birthDate, setBirthDate] = useState("");
  const [gender, setGender] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (mode !== "choose") return;
    const handle = setTimeout(() => {
      listPatients(query || undefined)
        .then((list) => setPatients(list.slice(0, 8)))
        .catch(() => setPatients([]));
    }, 200);
    return () => clearTimeout(handle);
  }, [query, mode]);

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const created = await createPatient({
        name: name.trim(),
        birthDate: birthDate || undefined,
        gender: gender || undefined,
      });
      setPatient(created);
      // Straight into upload: a patient with no facts is not useful, and the
      // reason to create one here is to give it a history.
      setMode("upload");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create that patient.");
    } finally {
      setBusy(false);
    }
  }

  if (mode === "upload" && patient) {
    return (
      <UploadFlow
        patientId={patient.patient_id}
        onBack={() => setMode("choose")}
        onFinished={() => onReady(patient)}
      />
    );
  }

  if (mode === "create") {
    return (
      <Shell
        title="Add a person"
        onBack={() => {
          setMode("choose");
          setError(null);
        }}
      >
        <p className="mb-5 max-w-lg text-sm leading-relaxed text-ink-muted">
          Creates an empty record. You will be taken straight to uploading a
          scan, because a patient with no history cannot be asked anything
          useful.
        </p>
        <div className="max-w-sm space-y-4">
          <Field label="Name">
            <input
              value={name}
              autoFocus
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && name.trim().length > 1 && create()}
              placeholder="Ramesh Kumar"
              className={inputClass}
            />
          </Field>
          <Field label="Date of birth (optional)">
            <input
              type="date"
              value={birthDate}
              max={new Date().toISOString().slice(0, 10)}
              onChange={(e) => setBirthDate(e.target.value)}
              className={inputClass}
            />
          </Field>
          <Field label="Gender (optional)">
            <div className="flex flex-wrap gap-1.5">
              {["male", "female", "other", "unknown"].map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setGender(gender === value ? "" : value)}
                  className={`rounded-lg border px-3 py-1.5 text-xs font-medium capitalize transition ${
                    gender === value
                      ? "border-accent-line bg-accent-bg text-accent"
                      : "border-line bg-bg text-ink-faint hover:border-line-strong"
                  }`}
                >
                  {value}
                </button>
              ))}
            </div>
          </Field>

          {error && (
            <p role="alert" className="rounded-lg border border-danger-line bg-danger-bg px-3 py-2.5 text-xs text-danger">
              {error}
            </p>
          )}

          <button
            type="button"
            onClick={create}
            disabled={name.trim().length < 2 || busy}
            className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-[#0b0d11] transition hover:brightness-110 disabled:cursor-not-allowed disabled:bg-surface-2 disabled:text-ink-faint"
          >
            {busy && (
              <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
            )}
            Create and upload records
          </button>
        </div>
      </Shell>
    );
  }

  return (
    <div className="mx-auto w-full max-w-3xl px-5 py-10 sm:px-8">
      <header className="mb-7">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-xl font-semibold tracking-tight text-ink">
            Choose a patient
          </h1>
          <span className="rounded border border-warn-line bg-warn-bg px-1.5 py-px text-[10px] font-medium text-warn">
            demo — no ABHA verified
          </span>
        </div>
        <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">
          Work with one of the 952 synthetic patients, or add someone new and
          build their history from a scanned record.
        </p>
      </header>

      <button
        type="button"
        onClick={() => setMode("create")}
        className="mb-5 flex w-full items-center gap-3 rounded-xl border border-dashed border-line bg-surface p-4 text-left transition hover:border-accent-line hover:bg-accent-bg/30"
      >
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-accent-line bg-accent-bg text-lg leading-none text-accent">
          +
        </span>
        <span>
          <span className="block text-sm font-semibold text-ink">Add a new person</span>
          <span className="block text-xs text-ink-muted">
            Then upload a scan to give them a record
          </span>
        </span>
      </button>

      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search the corpus by name…"
        className={inputClass}
      />

      <ul className="mt-2 space-y-1">
        {patients.map((candidate) => (
          <li key={candidate.patient_id}>
            <button
              type="button"
              onClick={() => onReady(candidate)}
              className="flex w-full items-center gap-3 rounded-lg border border-line bg-surface px-3 py-2.5 text-left transition hover:border-accent-line hover:bg-accent-bg/30"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-ink">
                  {candidate.name ?? "Unnamed"}
                </span>
                <span className="block truncate text-[11px] text-ink-faint">
                  {candidate._n_facts.toLocaleString()} facts
                  {candidate._source_file === "manual" ? " · added here" : ""}
                </span>
              </span>
              <span className="shrink-0 font-mono text-[10px] text-ink-faint">
                {candidate.patient_id.slice(0, 8)}
              </span>
            </button>
          </li>
        ))}
        {patients.length === 0 && (
          <li className="px-1 py-3 text-xs text-ink-faint">
            No matching patients. Is the API running on port 8000?
          </li>
        )}
      </ul>
    </div>
  );
}

const inputClass =
  "w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink outline-none placeholder:text-ink-faint focus:border-accent-line focus:ring-4 focus:ring-accent-bg";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-faint">
        {label}
      </span>
      <div className="mt-1.5">{children}</div>
    </label>
  );
}

function Shell({
  title,
  onBack,
  children,
}: {
  title: string;
  onBack: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="mx-auto w-full max-w-3xl px-5 py-10 sm:px-8">
      <button
        type="button"
        onClick={onBack}
        className="text-xs text-ink-muted underline-offset-2 hover:underline"
      >
        ← Back
      </button>
      <h1 className="mt-2 mb-4 text-xl font-semibold tracking-tight text-ink">{title}</h1>
      {children}
    </div>
  );
}
