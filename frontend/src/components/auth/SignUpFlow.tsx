"use client";

import { useState } from "react";

import { abha, type AbhaCall, type EnrolmentResult } from "@/lib/abha";
import { explain, type Explained } from "@/lib/abha-errors";
import { abhaAddressProblem } from "@/lib/m1-cases";
import { setLiveToken, type AbhaSession } from "@/lib/session";
import {
  Banner,
  ErrorLine,
  Failure,
  Labelled,
  OtpAge,
  OtpInput,
  Primary,
  Resend,
  Secondary,
  Steps,
  TextInput,
} from "./parts";

/**
 * Create an ABHA, from inside the product.
 *
 * The same five API calls `/m1` drives, presented for the person they happen
 * to rather than for the tester watching them. Two differences that matter:
 *
 * **Consent is a step, not a checkbox beside the Aadhaar field.** NHA's
 * CRT_ABHA_102 requires it to be shown and agreed *before* Aadhaar is
 * collected, and a screen that asks for both at once has not done that.
 *
 * **The null-mobile trap is explained where it is felt.** If the mobile given
 * here is not the one linked to the Aadhaar, ABDM completes the enrolment and
 * silently stores no mobile. That is not an error the API will report, so the
 * flow checks the profile it got back and says so — at the point where the
 * person could still fix it, rather than weeks later when an OTP never
 * arrives.
 */

type Step = "consent" | "aadhaar" | "otp" | "address" | "done";

const STEP_ORDER: Step[] = ["consent", "aadhaar", "otp", "address", "done"];

export function SignUpFlow({
  onCreated,
  onSwitchToSignIn,
}: {
  onCreated: (session: AbhaSession, token: string | null) => void;
  onSwitchToSignIn: () => void;
}) {
  const [step, setStep] = useState<Step>("consent");
  const [aadhaar, setAadhaar] = useState("");
  const [mobile, setMobile] = useState("");
  const [otp, setOtp] = useState("");
  const [txnId, setTxnId] = useState("");
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [result, setResult] = useState<EnrolmentResult | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [address, setAddress] = useState("");
  const [otpSentAt, setOtpSentAt] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Explained | null>(null);
  // Local validation failures are separate: they are ours, not ABDM's, and
  // routing them through `explain` would dress a typo as a protocol failure.
  const [localError, setLocalError] = useState<string | null>(null);

  const digits = aadhaar.replace(/\D/g, "");
  const addressProblem = address ? abhaAddressProblem(address) : null;

  function fail(call: AbhaCall) {
    setError(explain(call));
  }

  function restart() {
    setStep("aadhaar");
    setOtp("");
    setTxnId("");
    setError(null);
    setLocalError(null);
  }

  async function sendOtp(resend = false) {
    if (digits.length !== 12) {
      setLocalError("Aadhaar number is not valid — it must be 12 digits.");
      return;
    }
    setBusy(true);
    setError(null);
    setLocalError(null);
    const call = await abha.enrolRequestOtp(digits, resend ? txnId : undefined);
    if (call.ok) {
      // Only when ABDM actually sent one. A resend response that omits txnId
      // parses to "" upstream, and storing that would silently destroy the
      // transaction in progress -- every later verify would carry an empty id.
      if (call.body.txn_id) setTxnId(call.body.txn_id);
      setOtpSentAt(Date.now());
      setSentTo(call.body.masked_identifier ?? null);
      setStep("otp");
    } else {
      fail(call);
    }
    setBusy(false);
  }

  async function create() {
    setBusy(true);
    setError(null);
    const call = await abha.enrolVerify(txnId, otp, mobile || undefined);
    if (!call.ok) {
      fail(call);
      setBusy(false);
      return;
    }
    setResult(call.body);
    setLiveToken(call.body.token ?? null);
    if (call.body.txn_id) {
      const suggested = await abha.addressSuggestions(call.body.txn_id);
      if (suggested.ok) setSuggestions(suggested.body);
    }
    setStep("address");
    setBusy(false);
  }

  async function claim() {
    if (!result) return;
    setBusy(true);
    setError(null);
    const call = await abha.setAddress(result.txn_id ?? txnId, address);
    setBusy(false);
    if (!call.ok) {
      fail(call);
      return;
    }
    setStep("done");
  }

  function finish() {
    if (!result) return;
    onCreated(
      {
        account: { ...result.account, abha_address: address || result.account.abha_address },
        via: "ABHA created · Aadhaar OTP",
        signedInAt: new Date().toISOString(),
        patientId: null,
      },
      result.token ?? null,
    );
  }

  const stepIndex = STEP_ORDER.indexOf(step) + 1;
  const mobileIsNull = Boolean(result && mobile && !result.account.mobile);

  return (
    <div className="space-y-5">
      <Steps total={STEP_ORDER.length} current={stepIndex} />

      {step === "consent" && (
        <div className="space-y-4">
          <div>
            <h3 className="text-sm font-semibold text-ink">Before we begin</h3>
            <p className="mt-1 text-xs leading-relaxed text-ink-muted">
              Creating an ABHA uses your Aadhaar. Please read and agree before
              entering it.
            </p>
          </div>
          <blockquote className="rounded-lg border border-line bg-bg px-3.5 py-3 text-xs leading-relaxed text-ink-muted">
            I consent to the sharing of my Aadhaar details with the National
            Health Authority for the purpose of creating an ABHA, and to the
            processing of my health data in accordance with the ABDM Health Data
            Management Policy.
          </blockquote>
          <Banner tone="warn">
            Placeholder wording — not NHA&apos;s approved consent text, which is
            what certification audits. Replace it before going live.
          </Banner>
          <Primary onClick={() => setStep("aadhaar")}>I agree, continue</Primary>
          <button
            type="button"
            onClick={onSwitchToSignIn}
            className="w-full text-center text-xs text-ink-muted underline-offset-2 hover:underline"
          >
            I already have an ABHA
          </button>
        </div>
      )}

      {step === "aadhaar" && (
        <div className="space-y-4">
          <Labelled
            label="Aadhaar number"
            hint="Encrypted before it leaves this machine. Never written to disk or logs."
          >
            <TextInput
              value={aadhaar}
              onChange={setAadhaar}
              placeholder="9999 9999 9999"
              maxLength={14}
              autoFocus
              onEnter={() => sendOtp()}
            />
          </Labelled>
          <Labelled
            label="Mobile number (optional)"
            hint="Use the number linked to this Aadhaar. A different one is accepted by ABDM but then saved as blank, leaving the ABHA with no way to receive an OTP."
          >
            <TextInput
              value={mobile}
              onChange={(v) => setMobile(v.replace(/\D/g, ""))}
              placeholder="9876543210"
              maxLength={10}
            />
          </Labelled>
          {localError && <ErrorLine>{localError}</ErrorLine>}
          {error && <Failure explained={error} onRetry={() => setError(null)} onRestart={restart} />}
          <Primary onClick={() => sendOtp()} busy={busy} disabled={digits.length !== 12}>
            Send OTP
          </Primary>
          <Secondary onClick={() => setStep("consent")}>Back</Secondary>
        </div>
      )}

      {step === "otp" && (
        <div className="space-y-4">
          <Labelled
            label="One-time password"
            hint={
              sentTo
                ? `Sent to ${sentTo}, the mobile linked to your Aadhaar.`
                : "Sent to the mobile linked to your Aadhaar."
            }
          >
            <OtpInput value={otp} onChange={setOtp} onEnter={() => otp.length === 6 && create()} />
          </Labelled>
          <OtpAge sentAt={otpSentAt} onRefresh={restart} />
          {error && (
            <Failure
              explained={error}
              onRetry={() => {
                setOtp("");
                setError(null);
              }}
              onRestart={restart}
            />
          )}
          <Primary onClick={create} busy={busy} disabled={otp.length < 6}>
            Create my ABHA
          </Primary>
          <div className="flex items-center justify-between">
            <button
              type="button"
              onClick={() => {
                setStep("aadhaar");
                setOtp("");
                setError(null);
              }}
              className="text-xs text-ink-muted underline-offset-2 hover:underline"
            >
              Change details
            </button>
            <Resend txnId={txnId} onResend={() => sendOtp(true)} />
          </div>
        </div>
      )}

      {step === "address" && result && (
        <div className="space-y-4">
          <div className="rounded-lg border border-active-line bg-active-bg px-3.5 py-3">
            <p className="text-xs font-medium text-active">
              {result.is_new ? "ABHA created" : "You already had an ABHA"}
            </p>
            <p className="mt-1 font-mono text-sm text-ink">{result.account.abha_number}</p>
          </div>

          {mobileIsNull && (
            <Banner tone="warn">
              The mobile you entered is not the one linked to your Aadhaar, so
              ABDM saved it as blank. Your ABHA works, but OTPs cannot reach it
              until a mobile is attached.
            </Banner>
          )}

          <div>
            <h3 className="text-sm font-semibold text-ink">Choose your ABHA address</h3>
            <p className="mt-1 text-xs leading-relaxed text-ink-muted">
              A memorable name you can share instead of the 14-digit number.
            </p>
          </div>

          {suggestions.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {suggestions.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => setAddress(suggestion)}
                  className={`rounded-full border px-3 py-1 font-mono text-xs transition ${
                    address === suggestion
                      ? "border-accent-line bg-accent-bg text-accent"
                      : "border-line bg-bg text-ink-muted hover:border-line-strong"
                  }`}
                >
                  {suggestion}
                </button>
              ))}
            </div>
          )}

          <Labelled
            label="Or type your own"
            hint="8–18 characters. Letters and digits, with at most one dot and one underscore, neither first nor last."
          >
            <TextInput
              value={address}
              onChange={setAddress}
              placeholder="firstname.lastname"
              numeric={false}
            />
          </Labelled>

          {addressProblem && <ErrorLine>{addressProblem}</ErrorLine>}
          {error && <Failure explained={error} onRetry={() => setError(null)} />}

          <Primary onClick={claim} busy={busy} disabled={!address || Boolean(addressProblem)}>
            Claim address
          </Primary>
          <button
            type="button"
            onClick={() => setStep("done")}
            className="w-full text-center text-xs text-ink-muted underline-offset-2 hover:underline"
          >
            Skip for now
          </button>
        </div>
      )}

      {step === "done" && result && (
        <div className="space-y-4">
          <div className="rounded-lg border border-active-line bg-active-bg px-4 py-4 text-center">
            <p className="text-sm font-semibold text-active">You&apos;re all set</p>
            <p className="mt-2 font-mono text-lg text-ink">{result.account.abha_number}</p>
            {(address || result.account.abha_address) && (
              <p className="mt-0.5 font-mono text-xs text-ink-muted">
                {address || result.account.abha_address}
              </p>
            )}
          </div>
          <p className="text-xs leading-relaxed text-ink-muted">
            Keep your ABHA number safe. You can sign in with it, your mobile, or
            your ABHA address at any ABDM facility in India.
          </p>
          <Primary onClick={finish}>Continue</Primary>
        </div>
      )}
    </div>
  );
}
