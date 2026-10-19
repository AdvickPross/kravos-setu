"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { HipLinkFlow } from "@/components/m2/HipLinkFlow";
import { RecordsFlow } from "@/components/m2/RecordsFlow";
import { SetupFlow } from "@/components/m2/SetupFlow";
import { ShareFlow } from "@/components/m2/ShareFlow";
import { ActivityLog, PatientSelect, usePatientState } from "@/components/m2/shared";
import { SmsFlow } from "@/components/m2/SmsFlow";
import { UserLinkFlow } from "@/components/m2/UserLinkFlow";
import { Eyebrow, Panel, Tag } from "@/components/ui";
import { m2, type M2Status } from "@/lib/m2";
import { ALL_M2_CASES, M2_GROUPS, type M2Group } from "@/lib/m2-cases";
import { useHash, useLocalStorage } from "@/lib/use-local-storage";

type Verdict = "pass" | "fail";

const STORAGE_KEY = "kravos.m2.verdicts";
const PATIENT_KEY = "kravos.m2.patient";
const NEEDS_PATIENT: M2Group["key"][] = ["records", "hip", "user", "sms"];
// Exchanges we start carry the patient id. Ones ABDM starts from the PHR app
// (discovery, OTP, confirm, consent, data) arrive before we know who the
// patient is, so filtering by patient would hide exactly those rows.
const LOG_BY_PATIENT: M2Group["key"][] = ["hip", "sms"];

/**
 * The M2 ("Building HIP") functional-testing console.
 *
 * Same contract as the M1 console: each screen names the NHA rows it is
 * evidence for, and the tester records a verdict next to the row. The
 * difference is that M2 mostly happens *to* us — ABDM calls our server when
 * the patient acts in the PHR app — so the live activity log under every
 * screen is the main evidence, not a button's response.
 *
 * Verdicts and the selected patient live in localStorage: one tester's notes
 * during a session, not a shared record.
 */
export default function M2Console() {
  const [hash, setGroup] = useHash();
  const group: M2Group["key"] = M2_GROUPS.some((g) => g.key === hash) ? (hash as M2Group["key"]) : "setup";
  const [status, setStatus] = useState<M2Status | null>(null);
  const [reachable, setReachable] = useState<boolean | null>(null);
  const [savedVerdicts, saveVerdicts] = useLocalStorage(STORAGE_KEY);
  const [patientId, setPatientId] = useLocalStorage(PATIENT_KEY);
  const verdicts = useMemo<Record<string, Verdict>>(() => {
    try {
      return savedVerdicts ? JSON.parse(savedVerdicts) : {};
    } catch {
      return {};
    }
  }, [savedVerdicts]);
  const { state, error, reload } = usePatientState(patientId);

  useEffect(() => {
    m2.status().then((call) => {
      setReachable(call.status !== 0);
      if (call.ok) setStatus(call.body);
    });
  }, []);

  function choosePatient(id: string) {
    setPatientId(id);
  }

  function setVerdict(id: string, verdict: Verdict | null) {
    const next = { ...verdicts };
    if (verdict === null) delete next[id];
    else next[id] = verdict;
    saveVerdicts(JSON.stringify(next));
  }

  const active = M2_GROUPS.find((g) => g.key === group)!;
  const passed = ALL_M2_CASES.filter((c) => verdicts[c.id] === "pass").length;
  const failed = ALL_M2_CASES.filter((c) => verdicts[c.id] === "fail").length;
  const needsPatient = NEEDS_PATIENT.includes(group);

  return (
    <div className="mx-auto w-full max-w-[92rem] p-4 sm:p-5">
      <header className="mb-4 flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-lg font-semibold tracking-tight text-ink">M2 — Building HIP</h1>
            {status && (
              <Tag tone={status.environment === "production" ? "danger" : "accent"}>
                {status.environment ?? "unconfigured"}
              </Tag>
            )}
            {status && (
              <Tag tone={status.hipConfigured ? "accent" : "warn"}>
                {status.hipConfigured ? `HIP ${status.hipId}` : "no HIP ID"}
              </Tag>
            )}
          </div>
          <p className="mt-1 max-w-3xl text-sm leading-relaxed text-ink-muted">
            Link records to ABHA, answer the PHR app, and share records under consent. Most of M2 is ABDM calling this
            server, so every screen ends with the live exchange log — that is the evidence for each row.
          </p>
        </div>
        <div className="flex gap-2">
          <Link
            href="/m1"
            className="rounded border border-line-strong bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-surface-2"
          >
            M1 console
          </Link>
          <Link
            href="/"
            className="rounded border border-line-strong bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-surface-2"
          >
            ← Clinical view
          </Link>
        </div>
      </header>

      {reachable === false && (
        <Panel tone="danger" className="mb-4 px-4 py-3">
          <p className="text-sm font-medium text-danger">Cannot reach the API.</p>
          <p className="mt-1 text-xs text-danger/80">
            Start it with <code className="font-mono">uvicorn backend.main:app --port 8000</code>.
          </p>
        </Panel>
      )}

      {status && !status.hipConfigured && (
        <Panel tone="warn" className="mb-4 px-4 py-3">
          <p className="text-sm font-medium text-warn">No HIP ID — nothing here can run against ABDM yet.</p>
          <p className="mt-1 text-xs text-warn/90">
            ABDM rejects every M2 call without the facility ID NHA issues (requested — see{" "}
            <code className="font-mono">M2_SANDBOX_ONBOARDING.local.md</code>). Until then, Health record creation
            works fully, and every other screen shows what is ready.
          </p>
        </Panel>
      )}

      <div className="flex flex-col gap-5 lg:flex-row">
        <aside className="w-full shrink-0 lg:w-72">
          <Panel className="sticky top-4 overflow-hidden">
            <div className="border-b border-line bg-surface-2 px-3 py-2">
              <Eyebrow>Test cases</Eyebrow>
              <p className="mt-0.5 text-[11px] text-ink-faint">
                {passed} passed · {failed} failed · {ALL_M2_CASES.length - passed - failed} untested
              </p>
            </div>
            <nav className="divide-y divide-line">
              {M2_GROUPS.map((g) => {
                const groupPassed = g.cases.filter((c) => verdicts[c.id] === "pass").length;
                return (
                  <div key={g.key}>
                    <button
                      type="button"
                      onClick={() => setGroup(g.key)}
                      className={`flex w-full items-center gap-2 px-3 py-2 text-left transition ${
                        group === g.key ? "bg-accent-bg" : "hover:bg-surface-2"
                      }`}
                    >
                      <span className="shrink-0 font-mono text-[10px] text-ink-faint">{g.section}</span>
                      <span
                        className={`min-w-0 flex-1 truncate text-sm font-medium ${
                          group === g.key ? "text-accent" : "text-ink"
                        }`}
                      >
                        {g.title}
                      </span>
                      <span className="shrink-0 text-[10px] tabular-nums text-ink-faint">
                        {groupPassed}/{g.cases.length}
                      </span>
                    </button>

                    {group === g.key && (
                      <ul className="space-y-1 bg-surface px-2 pb-2 pt-1">
                        {g.cases.map((kase) => (
                          <li key={kase.id} className="rounded border border-line px-2 py-1.5">
                            <p className="font-mono text-[10px] text-ink-faint">
                              {kase.id}
                              {kase.mandate === "mandatory" && <span className="ml-1 text-accent">· mandatory</span>}
                            </p>
                            <p className="text-[11px] leading-snug text-ink">{kase.title}</p>
                            <p className="mt-0.5 text-[10px] leading-snug text-ink-faint">{kase.expect}</p>
                            <div className="mt-1.5 flex gap-1">
                              {(["pass", "fail"] as const).map((v) => (
                                <button
                                  key={v}
                                  type="button"
                                  onClick={() => setVerdict(kase.id, verdicts[kase.id] === v ? null : v)}
                                  className={`rounded border px-1.5 py-px text-[10px] font-medium capitalize transition ${
                                    verdicts[kase.id] === v
                                      ? v === "pass"
                                        ? "border-active-line bg-active-bg text-active"
                                        : "border-danger-line bg-danger-bg text-danger"
                                      : "border-line bg-surface text-ink-faint hover:bg-surface-2"
                                  }`}
                                >
                                  {v}
                                </button>
                              ))}
                            </div>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                );
              })}
            </nav>
            <p className="border-t border-line px-3 py-2 text-[10px] leading-snug text-ink-faint">
              Optional HIP-initiated methods (Mobile OTP, Aadhaar OTP, Direct Auth) are not implemented — the sheet needs
              one method, and Demographic Auth is the mandatory one.
            </p>
          </Panel>
        </aside>

        <main className="min-w-0 flex-1 space-y-3">
          <Panel className="px-4 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-[11px] text-ink-faint">{active.section}</span>
              <h2 className="text-sm font-semibold text-ink">{active.title}</h2>
            </div>
            <p className="mt-1 text-xs leading-relaxed text-ink-muted">{active.blurb}</p>
          </Panel>

          {needsPatient && (
            <Panel className="px-4 py-3">
              <div className="flex flex-wrap items-center gap-3">
                <Eyebrow>Patient</Eyebrow>
                {state ? (
                  <>
                    <span className="text-sm font-medium text-ink">{state.name ?? "Unnamed"}</span>
                    <span className="text-xs text-ink-faint">
                      {state.gender ?? "—"} · born {state.birthDate ?? "—"}
                    </span>
                    {state.abha?.abhaAddress ? (
                      <Tag tone="accent">{state.abha.abhaAddress}</Tag>
                    ) : (
                      <Tag>no ABHA</Tag>
                    )}
                    {state.mobile ? <Tag>{state.mobile}</Tag> : <Tag tone="warn">no mobile</Tag>}
                    <Tag>
                      {state.careContexts.filter((c) => c.status === "LINKED").length}/{state.careContexts.length} visits
                      linked
                    </Tag>
                  </>
                ) : (
                  <span className="text-xs text-ink-faint">{error ?? "None selected"}</span>
                )}
              </div>
              <div className="mt-2">
                <PatientSelect value={patientId} onChange={choosePatient} />
              </div>
            </Panel>
          )}

          {group === "setup" && <SetupFlow status={status} />}
          {group === "records" && <RecordsFlow state={state} />}
          {group === "hip" && (
            <HipLinkFlow state={state} reload={reload} hipConfigured={Boolean(status?.hipConfigured)} />
          )}
          {group === "user" && <UserLinkFlow state={state} reload={reload} />}
          {group === "sms" && <SmsFlow state={state} reload={reload} />}
          {group === "share" && <ShareFlow />}

          {group !== "records" && (
            <ActivityLog patientId={LOG_BY_PATIENT.includes(group) ? patientId : null} />
          )}
        </main>
      </div>
    </div>
  );
}
