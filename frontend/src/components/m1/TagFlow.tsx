"use client";

import { useEffect, useState } from "react";

import { abha, type AbhaCall } from "@/lib/abha";
import { listPatients, type PatientSummary } from "@/lib/api";
import { M1_GROUPS, type M1Case } from "@/lib/m1-cases";
import { Button, Field, Input, Note } from "./controls";
import { StepCard } from "./StepCard";

const CASE = M1_GROUPS.find((g) => g.key === "tag")!.cases[0] as M1Case;

/**
 * Tagging one ABHA to one patient, and proving the duplicate is refused.
 *
 * The second half is the part worth demonstrating. Anyone can show a link
 * being created; the row is about what happens on the *second* attempt, and
 * the failure it prevents — one person with two partial histories — is
 * invisible until someone queries the wrong half of them. So this screen makes
 * the refusal the headline rather than an error state to be avoided.
 *
 * It needs no ABDM credentials: the guarantee is ours, not ABDM's.
 */
export function TagFlow() {
  const [patients, setPatients] = useState<PatientSummary[]>([]);
  const [query, setQuery] = useState("");
  const [abhaNumber, setAbhaNumber] = useState("91-1234-5678-9012");
  const [patientId, setPatientId] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [calls, setCalls] = useState<Record<string, AbhaCall | null>>({});

  useEffect(() => {
    const handle = setTimeout(() => {
      listPatients(query || undefined)
        .then((list) => setPatients(list.slice(0, 8)))
        .catch(() => setPatients([]));
    }, 200);
    return () => clearTimeout(handle);
  }, [query]);

  const record = (key: string, call: AbhaCall) =>
    setCalls((prev) => ({ ...prev, [key]: call }));

  async function check() {
    setBusy("check");
    record("check", await abha.linkByAbha(abhaNumber));
    setBusy(null);
  }

  async function link() {
    setBusy("link");
    record("link", await abha.link(abhaNumber, patientId));
    setBusy(null);
  }

  async function unlink() {
    setBusy("unlink");
    record("unlink", await abha.unlink(abhaNumber));
    setCalls((prev) => ({ ...prev, link: null, check: null }));
    setBusy(null);
  }

  const conflict = calls.link && calls.link.status === 409;
  const existing = calls.check?.ok
    ? (calls.check.body as { patient_id: string | null }).patient_id
    : undefined;

  return (
    <div className="space-y-3">
      <Note tone="info">
        This section calls no ABDM endpoint and works with no credentials set —
        NHA puts the uniqueness guarantee on the integrator, not on ABDM.
      </Note>

      <StepCard
        n={1}
        title="Check before creating a record"
        cases={[CASE]}
        call={calls.check}
        done={existing !== undefined}
        hint="The checklist's literal ask: validate the ABHA number against the database before a new patient ID is created for it."
      >
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-56 flex-1">
            <Field
              label="ABHA number"
              hint="Hyphens are ignored — 91-1234-5678-9012 and 91123456789012 are the same identifier, which is exactly what stops the duplicate check passing trivially."
            >
              <Input value={abhaNumber} onChange={setAbhaNumber} placeholder="91-1234-5678-9012" />
            </Field>
          </div>
          <Button variant="secondary" onClick={check} busy={busy === "check"}>
            Look up
          </Button>
        </div>
        {existing !== undefined && (
          <Note tone={existing ? "warn" : "info"}>
            {existing
              ? `Already tagged to patient ${existing}. Creating a second record here would split one person's history.`
              : "Not tagged. Safe to create a patient record for this ABHA."}
          </Note>
        )}
      </StepCard>

      <StepCard
        n={2}
        title="Tag to a patient"
        cases={[CASE]}
        call={calls.link}
        done={Boolean(calls.link?.ok)}
        hint="Pick a patient from the corpus, tag it, then try tagging the same ABHA to a different one."
      >
        <Field label="Find a patient">
          <Input value={query} onChange={setQuery} mono={false} placeholder="Search by name…" />
        </Field>
        <div className="max-h-48 space-y-1 overflow-y-auto">
          {patients.map((patient) => (
            <button
              key={patient.patient_id}
              type="button"
              onClick={() => setPatientId(patient.patient_id)}
              className={`flex w-full items-center gap-2 rounded border px-2.5 py-1.5 text-left transition ${
                patientId === patient.patient_id
                  ? "border-accent-line bg-accent-bg"
                  : "border-line bg-surface hover:bg-surface-2"
              }`}
            >
              <span className="min-w-0 flex-1 truncate text-sm text-ink">
                {patient.name ?? "Unnamed"}
              </span>
              <span className="shrink-0 font-mono text-[10px] text-ink-faint">
                {patient.patient_id.slice(0, 8)}
              </span>
            </button>
          ))}
          {patients.length === 0 && (
            <p className="px-1 py-2 text-xs text-ink-faint">
              No patients — is the API running on port 8000?
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={link} disabled={!patientId || !abhaNumber} busy={busy === "link"}>
            Tag this patient
          </Button>
          <Button variant="danger" onClick={unlink} busy={busy === "unlink"}>
            Unlink
          </Button>
        </div>
        {conflict && (
          <Note tone="danger">
            <strong>Refused, as required.</strong> {calls.link?.message}
          </Note>
        )}
        {calls.link?.ok && (
          <Note tone="info">
            Tagged. Now select a <em>different</em> patient above and tag again —
            it must be refused with a 409. That refusal is the row.
          </Note>
        )}
      </StepCard>
    </div>
  );
}
