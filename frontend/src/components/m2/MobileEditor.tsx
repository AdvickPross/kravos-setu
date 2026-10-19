"use client";

import { useState } from "react";

import { Button, Field, Input } from "@/components/m1/controls";
import { CallEvidence } from "@/components/m1/StepCard";
import type { AbhaCall } from "@/lib/abha";
import { m2 } from "@/lib/m2";

/** Record the mobile captured at registration (patient_mobiles). The corpus
 *  has none, and both the mobile match and the linking OTP depend on it. */
export function MobileEditor({
  patientId,
  current,
  onSaved,
}: {
  patientId: string;
  current: string | null;
  onSaved: () => void;
}) {
  const [mobile, setMobile] = useState("");
  const [call, setCall] = useState<AbhaCall | null>(null);
  const [busy, setBusy] = useState(false);
  const digits = mobile.replace(/\D/g, "");

  async function save() {
    setBusy(true);
    const result = await m2.setMobile(patientId, mobile);
    setCall(result);
    setBusy(false);
    if (result.ok) {
      setMobile("");
      onSaved();
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-56 flex-1">
          <Field
            label={current ? `Mobile on file: ${current}` : "Mobile number"}
            hint="Use the mobile linked to the test ABHA, so ABDM's identifiers and ours agree."
            error={mobile && digits.length < 10 ? "Needs at least 10 digits" : null}
          >
            <Input value={mobile} onChange={setMobile} inputMode="numeric" placeholder="98765 43210" maxLength={16} />
          </Field>
        </div>
        <Button onClick={save} busy={busy} disabled={digits.length < 10}>
          {current ? "Replace" : "Save"} mobile
        </Button>
      </div>
      {call && !call.ok && <CallEvidence call={call} />}
    </div>
  );
}
