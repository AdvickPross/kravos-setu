"use client";

import { useEffect, useState } from "react";

import { abha, fetchCardUrl } from "@/lib/abha";
import { listPatients, type PatientSummary } from "@/lib/api";
import { getLiveToken, type AbhaSession } from "@/lib/session";

/**
 * What signing in got you, and the step into the clinical workspace.
 *
 * Between the OTP and the records there is a question nobody can answer for
 * us: *which patient record is this?* An ABHA identifies a person nationally;
 * the corpus keys on its own patient ids. `backend/abha_link.py` holds that
 * mapping and refuses to let one ABHA point at two patients — this screen is
 * where the mapping gets made or confirmed.
 *
 * It looks up the tag first. An ABHA already tagged goes straight through,
 * which is the returning-patient case and should be one click. An untagged one
 * has to be matched to a record by a human, and doing that badly is how a
 * person ends up with two half-histories, so the choice is deliberate rather
 * than a silent best guess on name.
 */
export function ProfileGate({
  session,
  onContinue,
  onSignOut,
}: {
  session: AbhaSession;
  onContinue: (patientId: string) => void;
  onSignOut: () => void;
}) {
  const [linkedPatient, setLinkedPatient] = useState<string | null | undefined>(undefined);
  const [patients, setPatients] = useState<PatientSummary[]>([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [cardUrl, setCardUrl] = useState<string | null>(null);
  const [linkError, setLinkError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const account = session.account;
  const abhaNumber = account.abha_number ?? null;

  useEffect(() => {
    if (!abhaNumber) {
      setLinkedPatient(null);
      return;
    }
    abha.linkByAbha(abhaNumber).then((call) => {
      setLinkedPatient(call.ok ? call.body.patient_id : null);
    });
  }, [abhaNumber]);

  useEffect(() => {
    const token = getLiveToken();
    if (!token) return;
    fetchCardUrl(token, session.via.startsWith("ABHA address"))
      .then(setCardUrl)
      .catch(() => setCardUrl(null));
  }, [session.via]);

  useEffect(() => {
    if (linkedPatient) return;
    const handle = setTimeout(() => {
      listPatients(query || undefined)
        .then((list) => setPatients(list.slice(0, 6)))
        .catch(() => setPatients([]));
    }, 200);
    return () => clearTimeout(handle);
  }, [query, linkedPatient]);

  async function linkAndContinue() {
    if (!selected) return;
    if (!abhaNumber) {
      // Nothing to tag against — an address-only login returns no number.
      onContinue(selected);
      return;
    }
    setBusy(true);
    setLinkError(null);
    const call = await abha.link(abhaNumber, selected, account.abha_address ?? null);
    setBusy(false);
    if (call.ok) onContinue(selected);
    else setLinkError(call.message ?? "Could not tag this record.");
  }

  const properties: [string, string | null | undefined][] = [
    ["ABHA number", account.abha_number],
    ["ABHA address", account.abha_address],
    ["Name", account.name],
    ["Date of birth", account.date_of_birth],
    ["Gender", account.gender],
    ["Mobile", account.mobile],
    ["Email", account.email],
    ["State", account.state],
    ["District", account.district],
  ];

  return (
    <div className="mx-auto w-full max-w-3xl p-5 sm:p-8">
      <header className="mb-6 flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold tracking-tight text-ink">
            Signed in{account.name ? ` as ${account.name}` : ""}
          </h1>
          <p className="mt-1 text-sm text-ink-muted">Verified by {session.via}.</p>
        </div>
        <button
          type="button"
          onClick={onSignOut}
          className="rounded border border-border-strong bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-surface-2"
        >
          Sign out
        </button>
      </header>

      <section className="rounded-xl border border-line bg-surface">
        <div className="flex flex-wrap gap-5 p-5">
          <div className="min-w-0 flex-1">
            <h2 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
              ABHA profile
            </h2>
            <dl className="mt-3 grid gap-x-6 gap-y-3 sm:grid-cols-2">
              {properties.map(([label, value]) => (
                <div key={label}>
                  <dt className="text-[11px] text-ink-faint">{label}</dt>
                  <dd
                    className={`text-sm ${
                      value ? "text-ink" : "text-ink-faint"
                    } ${label.startsWith("ABHA") ? "font-mono" : ""}`}
                  >
                    {value || "—"}
                  </dd>
                </div>
              ))}
            </dl>
            {!account.mobile && (
              <p className="mt-4 rounded border border-warn-line bg-warn-bg px-3 py-2 text-xs leading-relaxed text-warn">
                No mobile number is attached to this ABHA. OTP sign-in on it
                will fail until one is linked — this happens when the number
                given at enrolment was not the Aadhaar-linked one, and ABDM
                saves it as null without reporting an error.
              </p>
            )}
          </div>

          {cardUrl && (
            <div className="shrink-0">
              <h2 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
                ABHA card
              </h2>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={cardUrl}
                alt="ABHA card"
                className="mt-2 w-56 rounded-lg border border-line"
              />
              <a
                href={cardUrl}
                download="abha-card.png"
                className="mt-2 block text-center text-xs font-medium text-accent underline-offset-2 hover:underline"
              >
                Download
              </a>
            </div>
          )}
        </div>
      </section>

      <section className="mt-4 rounded-xl border border-line bg-surface p-5">
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
          Clinical record
        </h2>

        {linkedPatient === undefined && (
          <p className="mt-2 text-sm text-ink-faint">Looking up the linked record…</p>
        )}

        {linkedPatient && (
          <>
            <p className="mt-2 text-sm text-ink-muted">
              This ABHA is already tagged to a patient record.
            </p>
            <button
              type="button"
              onClick={() => onContinue(linkedPatient)}
              className="mt-3 w-full rounded-lg bg-accent px-3 py-2.5 text-sm font-semibold text-white transition hover:opacity-90 sm:w-auto sm:px-6"
            >
              Open clinical view →
            </button>
          </>
        )}

        {linkedPatient === null && (
          <>
            <p className="mt-2 text-sm leading-relaxed text-ink-muted">
              No record is tagged to this ABHA yet. Choose the one it belongs
              to — one ABHA can only ever point at one record, so this decision
              is made once.
            </p>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search patients by name…"
              className="mt-3 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none placeholder:text-ink-faint focus:border-accent-line focus:ring-2 focus:ring-accent-bg"
            />
            <div className="mt-2 space-y-1">
              {patients.map((patient) => (
                <button
                  key={patient.patient_id}
                  type="button"
                  onClick={() => setSelected(patient.patient_id)}
                  className={`flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left transition ${
                    selected === patient.patient_id
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
                <p className="px-1 py-2 text-xs text-ink-faint">No matching patients.</p>
              )}
            </div>
            {linkError && (
              <p className="mt-2 rounded border border-danger-line bg-danger-bg px-3 py-2 text-xs text-danger">
                {linkError}
              </p>
            )}
            <button
              type="button"
              onClick={linkAndContinue}
              disabled={!selected || busy}
              className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-3 py-2.5 text-sm font-semibold text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40 sm:w-auto sm:px-6"
            >
              {busy && (
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white border-t-transparent" />
              )}
              Tag and open clinical view →
            </button>
          </>
        )}
      </section>
    </div>
  );
}
