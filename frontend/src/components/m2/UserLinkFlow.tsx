"use client";

import { StepCard } from "@/components/m1/StepCard";
import { Note } from "@/components/m1/controls";
import type { PatientState } from "@/lib/m2";
import { M2_GROUPS } from "@/lib/m2-cases";
import { MobileEditor } from "./MobileEditor";
import { CareContextTable, Check } from "./shared";

const CASES = M2_GROUPS.find((g) => g.key === "user")!.cases;
const pick = (...ids: string[]) => CASES.filter((c) => ids.some((id) => c.id.endsWith(id)));

/**
 * User-initiated linking (USER_INIT_LINK_601-607).
 *
 * The patient does everything in ABDM's PHR app; our server only answers
 * ABDM. So this screen does two jobs: make sure the patient *can* be found
 * here, and show the exchange as it happens (the activity log).
 */
export function UserLinkFlow({ state, reload }: { state: PatientState | null; reload: () => void }) {
  if (!state) return <Note tone="info">Pick the patient you’ll act as in the PHR app.</Note>;

  const tagged = Boolean(state.abha?.abhaAddress);
  const unlinked = state.careContexts.filter((c) => c.status !== "LINKED");

  return (
    <div className="space-y-3">
      <StepCard
        n={1}
        title="Can ABDM find this patient here?"
        cases={pick("603")}
        done={(tagged || Boolean(state.mobile)) && unlinked.length > 0}
        hint="When the PHR app asks us, we look for the patient by ABHA address first, then by verified mobile plus an exact gender and birth year and a close name."
      >
        <ul className="space-y-1.5">
          <Check
            ok={tagged}
            hint={tagged ? "Discovery will match on the ABHA address (strongest)." : "Optional if the mobile path is used."}
          >
            Tagged ABHA address {tagged && <code className="font-mono">{state.abha?.abhaAddress}</code>}
          </Check>
          <Check
            ok={Boolean(state.mobile)}
            hint="Needed for the OTP in step 3 either way, and for the mobile match when the ABHA isn't tagged."
          >
            Mobile on file {state.mobile && <code className="font-mono">{state.mobile}</code>}
          </Check>
          <Check ok={unlinked.length > 0} hint="Only unlinked visits are offered.">
            {unlinked.length} visit{unlinked.length === 1 ? "" : "s"} available to link
          </Check>
        </ul>
        <MobileEditor patientId={state.patientId} current={state.mobile} onSaved={reload} />
      </StepCard>

      <StepCard
        n={2}
        title="In the PHR app: search the facility and fetch records"
        cases={pick("601", "602", "604")}
        hint="Log into the ABDM sandbox PHR app with the patient's ABHA address, search for our facility by name, and request records. The activity log should show DISCOVERY_REQUEST → DISCOVERY_RESPONSE_SENT within a few seconds."
      >
        <Note tone="info">
          Nothing arriving at all means ABDM can’t reach this server — check the tunnel and the bridge URL under
          Sandbox setup.
        </Note>
      </StepCard>

      <StepCard
        n={3}
        title="Link with the OTP"
        cases={pick("605", "606")}
        hint="Pick visits in the PHR app and choose Link. We send a 6-digit OTP (10 minutes, 3 attempts, single use); the log shows LINK_INIT_REQUEST → LINK_INIT_RESPONSE_SENT."
      >
        <Note tone="warn">
          Sandbox mode: the OTP is written to the <strong>API console</strong>, not texted — look for{" "}
          <code className="font-mono">M2 link OTP for ******1234 (linkRef …): 123456</code> and type it into the PHR app.
          Try a wrong one first: it must be refused (OTP_INVALID in the log).
        </Note>
      </StepCard>

      <StepCard
        n={4}
        title="Linked — pull the records"
        cases={pick("607")}
        done={state.careContexts.some((c) => c.status === "LINKED")}
        hint="After the right OTP the log shows LINK_CONFIRMED and the visits below turn linked. Then Pull Records in the PHR app."
      >
        <CareContextTable contexts={state.careContexts} limit={8} />
      </StepCard>
    </div>
  );
}
