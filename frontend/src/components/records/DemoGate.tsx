"use client";

import { useEffect, useState } from "react";

import { createPatient, listPatients, type PatientSummary } from "@/lib/api";
import { ArrowRightIcon, PlusIcon, SearchIcon } from "../icons";
import { Avatar, Brand } from "../ui";
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
            className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-on-accent transition hover:brightness-110 disabled:cursor-not-allowed disabled:bg-surface-2 disabled:text-ink-faint"
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
    <div className="mx-auto w-full max-w-3xl px-5 py-10 sm:px-8 sm:py-14">
      <div className="mb-10">
        <Brand subtitle="Clinical context layer · ABDM" />
      </div>

      <header className="mb-7">
        <div className="flex flex-wrap items-center gap-2.5">
          <h1 className="text-3xl font-semibold tracking-tight text-ink">
            Choose a patient
          </h1>
          <span className="rounded-full border border-warn-line bg-warn-bg px-2.5 py-0.5 text-[11px] font-medium text-warn">
            demo — no ABHA verified
          </span>
        </div>
        <p className="mt-2 max-w-xl text-sm leading-relaxed text-ink-muted">
          Work with one of the 952 synthetic patients, or add someone new and
          build their history from a scanned record.
        </p>
      </header>

      <button
        type="button"
        onClick={() => setMode("create")}
        className="group mb-6 flex w-full items-center gap-4 rounded-2xl border border-dashed border-line-strong bg-surface/60 p-4 text-left transition hover:border-accent-line hover:bg-accent-bg/40"
      >
        <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-accent-bg text-accent ring-1 ring-accent-line transition group-hover:scale-105">
          <PlusIcon size={18} />
        </span>
        <span className="flex-1">
          <span className="block text-sm font-semibold text-ink">Add a new person</span>
          <span className="block text-xs text-ink-muted">
            Then upload a scan to give them a record
          </span>
        </span>
        <ArrowRightIcon size={16} className="text-ink-faint transition group-hover:translate-x-0.5 group-hover:text-accent" />
      </button>

      <div className="card overflow-hidden">
        <div className="relative border-b border-line p-3">
          <SearchIcon
            size={16}
            className="pointer-events-none absolute left-6 top-1/2 -translate-y-1/2 text-ink-faint"
          />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search the corpus by name…"
            className={`${inputClass} pl-10`}
          />
        </div>

        <ul className="divide-y divide-line">
          {patients.map((candidate) => (
            <li key={candidate.patient_id}>
              <button
                type="button"
                onClick={() => onReady(candidate)}
                className="group flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-surface-2/70"
              >
                <Avatar name={candidate.name} seed={candidate.patient_id} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-ink">
                    {candidate.name ?? "Unnamed"}
                  </span>
                  <span className="block truncate text-[11px] text-ink-faint">
                    {candidate._n_facts.toLocaleString()} facts
                    {candidate._source_file === "manual" ? " · added here" : ""}
                  </span>
                </span>
                <span className="shrink-0 rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-ink-faint">
                  {candidate.patient_id.slice(0, 8)}
                </span>
                <ArrowRightIcon size={15} className="shrink-0 text-ink-faint opacity-0 transition group-hover:translate-x-0.5 group-hover:text-accent group-hover:opacity-100" />
              </button>
            </li>
          ))}
          {patients.length === 0 && (
            <li className="px-4 py-6 text-center text-xs text-ink-faint">
              No matching patients. Is the API running on port 8000?
            </li>
          )}
        </ul>
      </div>
    </div>
  );
}

const inputClass =
  "w-full rounded-xl border border-line bg-bg px-3.5 py-2.5 text-sm text-ink outline-none placeholder:text-ink-faint focus:border-accent-line focus:ring-4 focus:ring-accent-bg";

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
    <div className="mx-auto w-full max-w-3xl px-5 py-10 sm:px-8 sm:py-14">
      <div className="mb-10">
        <Brand subtitle="Clinical context layer · ABDM" />
      </div>
      <button
        type="button"
        onClick={onBack}
        className="rounded-lg px-2 py-1 text-xs font-medium text-ink-muted transition hover:bg-surface-2 hover:text-ink"
      >
        ← Back
      </button>
      <h1 className="mt-3 mb-4 text-3xl font-semibold tracking-tight text-ink">{title}</h1>
      <div className="card p-6">{children}</div>
    </div>
  );
}
