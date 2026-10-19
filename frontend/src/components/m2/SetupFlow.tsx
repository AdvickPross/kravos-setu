"use client";

import { useState } from "react";

import { StepCard } from "@/components/m1/StepCard";
import { Button, Field, Input, Note } from "@/components/m1/controls";
import { m2, type BridgeInfo, type M2Status } from "@/lib/m2";
import type { AbhaCall } from "@/lib/abha";
import { M2_GROUPS } from "@/lib/m2-cases";
import { Check } from "./shared";

const SETUP_CASES = M2_GROUPS.find((g) => g.key === "setup")!.cases;

/**
 * Everything that has to be true before any M2 row can be tested live.
 *
 * The three blockers are external (credentials, a HIP ID from NHA, a public
 * bridge URL), so this screen states each plainly instead of letting the
 * tester discover them as an opaque ABDM error three screens later.
 */
export function SetupFlow({ status }: { status: M2Status | null }) {
  const [bridgeCall, setBridgeCall] = useState<AbhaCall<BridgeInfo> | null>(null);
  const [updateCall, setUpdateCall] = useState<AbhaCall | null>(null);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  async function checkBridge() {
    setBusy("bridge");
    setBridgeCall(await m2.bridge());
    setBusy(null);
  }

  async function updateUrl() {
    setBusy("update");
    setUpdateCall(await m2.updateBridgeUrl(url));
    setBusy(null);
    await checkBridge();
  }

  const bridge = bridgeCall?.ok ? bridgeCall.body : null;
  const services = bridge?.services ?? [];
  const bridgeUrl = bridge?.bridge?.url ?? "";
  const ourService = status?.hipId ? services.find((s) => s.id === status.hipId) : undefined;
  const placeholderUrl = /webhook\.site|requestbin|localhost|127\.0\.0\.1/.test(bridgeUrl);

  return (
    <div className="space-y-3">
      <StepCard
        n={1}
        title="Server configuration"
        done={Boolean(status?.abdmConfigured && status?.hipConfigured)}
        hint="Read from the API's .env. Nothing here calls ABDM."
      >
        {status ? (
          <ul className="space-y-1.5">
            <Check ok={status.abdmConfigured} hint="ABDM_CLIENT_ID / ABDM_CLIENT_SECRET">
              ABDM credentials {status.abdmConfigured ? `(${status.environment})` : "missing"}
            </Check>
            <Check
              ok={status.hipConfigured}
              hint={
                status.hipConfigured
                  ? `Patients will see “${status.hipName}”.`
                  : "ABDM_HIP_ID — the facility ID NHA issues and links to our bridge. Every M2 call needs it (ABDM-1035 without it)."
              }
            >
              HIP ID {status.hipConfigured ? <code className="font-mono">{status.hipId}</code> : "not set — waiting on NHA"}
            </Check>
            <Check
              ok={status.verifyGatewayTokens}
              hint="Callbacks must carry a gateway-signed JWT. Only switch this off to curl callbacks locally."
            >
              Gateway signature check {status.verifyGatewayTokens ? "on" : "OFF"}
            </Check>
            <Check
              ok={status.otpDelivery !== "log"}
              hint={
                status.otpDelivery === "log"
                  ? "Linking OTPs are written to the API console — fine for sandbox demos, not for certification (needs DLT + an SMS provider)."
                  : undefined
              }
            >
              OTP delivery: {status.otpDelivery === "log" ? "server log only" : status.otpDelivery}
            </Check>
          </ul>
        ) : (
          <p className="text-xs text-ink-faint">Loading…</p>
        )}
      </StepCard>

      <StepCard
        n={2}
        title="Bridge as ABDM sees it"
        cases={SETUP_CASES}
        call={bridgeCall}
        done={Boolean(bridge && ourService && !placeholderUrl)}
        hint="Live read of our bridge registration. Works without a HIP ID: it shows the callback URL and which facilities are linked to it."
      >
        <Button variant="secondary" onClick={checkBridge} busy={busy === "bridge"} disabled={!status?.abdmConfigured}>
          Check bridge
        </Button>
        {bridge && (
          <ul className="space-y-1.5">
            <Check
              ok={Boolean(bridgeUrl) && !placeholderUrl}
              hint={
                placeholderUrl
                  ? "ABDM's callbacks go to this address, not to us. Point it at this server's public URL below."
                  : "ABDM calls this URL + /api/v3/… for every discovery, OTP, consent and data request."
              }
            >
              Callback URL: <code className="break-all font-mono">{bridgeUrl || "none"}</code>
            </Check>
            <Check
              ok={Boolean(ourService)}
              hint={
                services.length === 0
                  ? "No facility is linked to the bridge, so the PHR app can't find us. This is the HIP ID NHA has to issue."
                  : ourService
                    ? undefined
                    : `Linked: ${services.map((s) => s.id).join(", ")} — none matches ABDM_HIP_ID.`
              }
            >
              Facility linked to the bridge {ourService ? <code className="font-mono">{ourService.id}</code> : ""}
            </Check>
          </ul>
        )}
      </StepCard>

      <StepCard
        n={3}
        title="Point the bridge at this server"
        call={updateCall}
        done={Boolean(updateCall?.ok)}
        hint={
          <>
            Run <code className="font-mono">ngrok http --url=&lt;your-domain&gt; 8000</code> so ABDM can reach this
            API, then set the bridge to that domain. Base URL only — ABDM appends <code>/api/v3/…</code> itself.
          </>
        }
      >
        <Note tone="warn">
          This changes our registration on ABDM: from now on every callback for our bridge goes to this URL. Keep the
          tunnel running whenever you test.
        </Note>
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-64 flex-1">
            <Field label="Public base URL">
              <Input value={url} onChange={setUrl} placeholder="https://kravos-setu.ngrok-free.app" />
            </Field>
          </div>
          <Button
            onClick={updateUrl}
            busy={busy === "update"}
            disabled={!url.startsWith("https://") || !status?.abdmConfigured}
          >
            Update bridge URL
          </Button>
        </div>
      </StepCard>
    </div>
  );
}
