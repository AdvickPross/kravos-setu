"use client";

import Link from "next/link";
import { useState } from "react";

import { StepCard } from "@/components/m1/StepCard";
import { Button, Note } from "@/components/m1/controls";
import { Eyebrow } from "@/components/ui";
import type { AbhaCall } from "@/lib/abha";
import { LINK_OUTCOMES, m2, type LinkOutcome, type PatientState } from "@/lib/m2";
import { M2_GROUPS } from "@/lib/m2-cases";
import { CareContextTable, Check } from "./shared";

const CASES = M2_GROUPS.find((g) => g.key === "hip")!.cases;
const pick = (...ids: string[]) => CASES.filter((c) => ids.some((id) => c.id.endsWith(id)));

/**
 * HIP-initiated linking by demographic auth (HIP_INTI_LINK_501-506).
 *
 * The step that fails in practice is 503: ABDM compares our demographics
 * against the ABHA's and refuses on a mismatch. So step 2 shows *exactly*
 * what will be sent, before anything is.
 */
export function HipLinkFlow({
  state,
  reload,
  hipConfigured,
}: {
  state: PatientState | null;
  reload: () => void;
  hipConfigured: boolean;
}) {
  const [linkCall, setLinkCall] = useState<AbhaCall<LinkOutcome> | null>(null);
  const [batchCall, setBatchCall] = useState<AbhaCall | null>(null);
  const [notifyCall, setNotifyCall] = useState<AbhaCall | null>(null);
  const [notifyRef, setNotifyRef] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  if (!state) return <Note tone="info">Pick a patient above to link their records.</Note>;

  const demo = state.tokenDemographics;
  const tagged = Boolean(state.abha?.abhaAddress);
  const linked = state.careContexts.filter((c) => c.status === "LINKED");
  const outcome = linkCall?.ok ? LINK_OUTCOMES[linkCall.body.outcome] : null;

  async function link() {
    setBusy("link");
    setLinkCall(await m2.linkPatient(state!.patientId));
    setBusy(null);
    reload();
  }

  async function batch() {
    setBusy("batch");
    setBatchCall(await m2.runBatch());
    setBusy(null);
    setTimeout(reload, 1500);
  }

  async function notify() {
    if (!notifyRef) return;
    setBusy("notify");
    setNotifyCall(await m2.notifyUpdate(state!.patientId, notifyRef));
    setBusy(null);
  }

  return (
    <div className="space-y-3">
      {!hipConfigured && (
        <Note tone="warn">
          No HIP ID yet — the buttons below will answer 503 until <code className="font-mono">ABDM_HIP_ID</code> is
          set. Everything up to step 3 is still worth checking now.
        </Note>
      )}

      <StepCard n={1} title="Patient is tagged to an ABHA" cases={pick("501")} done={tagged}>
        <ul className="space-y-1.5">
          <Check
            ok={tagged}
            hint={
              tagged ? (
                `ABHA number ${state.abha?.abhaNumber ?? "—"} · tagged ${state.abha?.taggedAt.slice(0, 10)}`
              ) : (
                <>
                  Tag this patient to their ABHA in the{" "}
                  <Link href="/m1" className="text-accent hover:underline">
                    M1 console → Tag
                  </Link>
                  , with the ABHA address.
                </>
              )
            }
          >
            ABHA address {tagged ? <code className="font-mono">{state.abha?.abhaAddress}</code> : "not tagged"}
          </Check>
        </ul>
      </StepCard>

      <StepCard
        n={2}
        title="Demographics ABDM will check"
        cases={pick("502", "503")}
        done={Boolean(demo.name && demo.yearOfBirth)}
        hint="Sent to generate-token exactly as below. ABDM compares them with the ABHA's own record and refuses on a mismatch."
      >
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
          <dt className="text-ink-faint">name</dt>
          <dd className="font-mono text-ink">{demo.name || "—"}</dd>
          <dt className="text-ink-faint">gender</dt>
          <dd className="font-mono text-ink">{demo.gender}</dd>
          <dt className="text-ink-faint">yearOfBirth</dt>
          <dd className="font-mono text-ink">{demo.yearOfBirth ?? "—"}</dd>
          <dt className="text-ink-faint">mobile on file</dt>
          <dd className="font-mono text-ink">{state.mobile ?? "—"}</dd>
        </dl>
        {state.name && state.name !== demo.name && (
          <p className="text-[11px] text-ink-faint">
            Stored as “{state.name}” — digits are stripped because ABDM accepts letters only.
          </p>
        )}
        <Note tone="warn">
          Synthea patients are synthetic, so their demographics will never match a real sandbox ABHA. For a live test,
          use a patient record carrying the test ABHA’s actual name, gender and year of birth.
        </Note>
      </StepCard>

      <StepCard
        n={3}
        title="Link this patient's records"
        cases={pick("503", "504", "505")}
        call={linkCall}
        done={linked.length > 0 && linked.length === state.careContexts.length}
        hint={
          <>
            One click starts it; the rest needs none. No token yet → request one; when ABDM’s token callback arrives,
            every unlinked visit is linked automatically. Link token:{" "}
            <strong>
              {state.linkTokenExpiresAt ? `stored, valid until ${state.linkTokenExpiresAt.slice(0, 10)}` : "none"}
            </strong>
            .
          </>
        }
      >
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={link} busy={busy === "link"} disabled={!tagged}>
            Link records to ABHA
          </Button>
          <Button variant="secondary" onClick={reload}>
            Refresh status
          </Button>
        </div>
        {outcome && <Note tone={outcome.tone}>{outcome.text}</Note>}
        <CareContextTable contexts={state.careContexts} />
      </StepCard>

      <StepCard
        n={4}
        title="Backend batch (every tagged patient)"
        cases={pick("505")}
        call={batchCall}
        done={Boolean(batchCall?.ok)}
        hint={
          <>
            What HIP_INTI_LINK_505 asks for: linking pushed from the backend, no UI involved. Schedule{" "}
            <code className="font-mono">python scripts/m2_link_batch.py</code> to run it unattended; this button runs it
            once. It also sends the deep-link SMS (§7).
          </>
        }
      >
        <Button variant="secondary" onClick={batch} busy={busy === "batch"}>
          Run linking batch now
        </Button>
      </StepCard>

      <StepCard
        n={5}
        title="Tell ABDM a linked visit changed"
        call={notifyCall}
        done={Boolean(notifyCall?.ok)}
        hint="link/context/notify — only for a visit already linked, when new records are added to it."
      >
        {linked.length === 0 ? (
          <p className="text-xs text-ink-faint">No linked visits yet.</p>
        ) : (
          <>
            <Eyebrow>Linked visits</Eyebrow>
            <CareContextTable contexts={linked} selected={notifyRef} onSelect={setNotifyRef} limit={6} />
            <Button variant="secondary" onClick={notify} busy={busy === "notify"} disabled={!notifyRef}>
              Notify update
            </Button>
          </>
        )}
      </StepCard>

      <StepCard
        n={6}
        title="Pull the records in the PHR app"
        cases={pick("506")}
        hint="In the ABDM sandbox PHR app, open the linked facility and press “Pull Records”. Check the HI type, visit date, and the patient's demographics on each record. The transfer itself shows under Consent & data transfer."
      />
    </div>
  );
}
