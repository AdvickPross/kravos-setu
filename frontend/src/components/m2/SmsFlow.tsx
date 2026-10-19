"use client";

import { useState } from "react";

import { StepCard } from "@/components/m1/StepCard";
import { Button, Note } from "@/components/m1/controls";
import type { AbhaCall } from "@/lib/abha";
import { m2, type PatientState } from "@/lib/m2";
import { M2_GROUPS } from "@/lib/m2-cases";
import { MobileEditor } from "./MobileEditor";
import { Check } from "./shared";

const CASES = M2_GROUPS.find((g) => g.key === "sms")!.cases;

/**
 * HIP_INIT_NOTIFY_HIECM: a patient with a mobile but no ABHA address gets an
 * SMS from ABDM with a link to a PHR app. Sent by the batch, once per new set
 * of records.
 */
export function SmsFlow({ state, reload }: { state: PatientState | null; reload: () => void }) {
  const [call, setCall] = useState<AbhaCall | null>(null);
  const [busy, setBusy] = useState(false);

  if (!state) return <Note tone="info">Pick a patient who has a mobile but no ABHA address.</Note>;

  const eligible = !state.abha && Boolean(state.mobile) && state.careContexts.length > 0;

  async function run() {
    setBusy(true);
    setCall(await m2.runBatch());
    setBusy(false);
    setTimeout(reload, 1500);
  }

  return (
    <div className="space-y-3">
      <StepCard n={1} title="Patient qualifies" cases={CASES} done={eligible}>
        <ul className="space-y-1.5">
          <Check
            ok={!state.abha}
            hint={state.abha ? "Tagged patients are linked directly instead (§8a) — pick an untagged one." : undefined}
          >
            No ABHA address on file
          </Check>
          <Check ok={Boolean(state.mobile)} hint="Use a phone you can see — ABDM texts this number.">
            Mobile on file {state.mobile && <code className="font-mono">{state.mobile}</code>}
          </Check>
          <Check ok={state.careContexts.length > 0}>Has records to tell them about</Check>
        </ul>
        {!state.abha && <MobileEditor patientId={state.patientId} current={state.mobile} onSaved={reload} />}
      </StepCard>

      <StepCard
        n={2}
        title="Send the SMS (via the batch)"
        cases={CASES}
        call={call}
        done={state.sms?.status === "ACCEPTED"}
        hint="The batch asks ABDM to text every qualifying patient once per new set of records; repeats are suppressed."
      >
        <Button onClick={run} busy={busy} disabled={!eligible}>
          Run batch now
        </Button>
        {state.sms ? (
          <Note tone={state.sms.status === "ERRORED" ? "danger" : "info"}>
            Last SMS request: <strong>{state.sms.status}</strong> at {state.sms.notifiedAt.slice(0, 16).replace("T", " ")}{" "}
            for {state.sms.recordsNotified} record(s). ACCEPTED means ABDM accepted it for sending — not that it was
            delivered.
          </Note>
        ) : (
          <p className="text-xs text-ink-faint">No SMS sent for this patient yet.</p>
        )}
      </StepCard>

      <StepCard
        n={3}
        title="On the phone"
        cases={CASES}
        hint="The SMS links to a PHR app. Install it, create or log into an ABHA, then search this facility — discovery finds the patient by mobile (User-initiated linking, step 2 onwards)."
      />
    </div>
  );
}
