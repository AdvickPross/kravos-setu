"use client";

import { useState } from "react";

import { abha, fetchCardUrl, type AbhaCall, type EnrolmentResult } from "@/lib/abha";
import { abhaAddressProblem, M1_GROUPS, type M1Case } from "@/lib/m1-cases";
import { Eyebrow, Panel, Tag } from "@/components/ui";
import { Button, Choice, Field, Input, Note, ResendButton } from "./controls";
import { StepCard } from "./StepCard";

const CASES = Object.fromEntries(
  M1_GROUPS.find((g) => g.key === "create")!.cases.map((c) => [c.id, c]),
) as Record<string, M1Case>;

export function CreateFlow() {
  const [consented, setConsented] = useState(false);
  const [aadhaar, setAadhaar] = useState("");
  const [aadhaarError, setAadhaarError] = useState<string | null>(null);
  const [otp, setOtp] = useState("");
  const [mobile, setMobile] = useState("");
  const [txnId, setTxnId] = useState("");
  const [result, setResult] = useState<EnrolmentResult | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [address, setAddress] = useState("");
  const [cardUrl, setCardUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [calls, setCalls] = useState<Record<string, AbhaCall | null>>({});

  const record = (key: string, call: AbhaCall) =>
    setCalls((prev) => ({ ...prev, [key]: call }));

  const digits = aadhaar.replace(/\D/g, "");

  async function requestOtp(resend = false) {
    // CRT_ABHA_104 -- refused here, before a request, because ABDM's rejection
    // of a malformed Aadhaar is indistinguishable from its rejection of a
    // valid one that is not enrolled.
    if (digits.length !== 12) {
      setAadhaarError("Aadhaar Number is not valid — it must be 12 digits.");
      return;
    }
    setAadhaarError(null);
    setBusy("otp");
    const call = await abha.enrolRequestOtp(digits, resend ? txnId : undefined);
    record("otp", call);
    if (call.ok) setTxnId(call.body.txn_id);
    setBusy(null);
  }

  async function verify() {
    setBusy("verify");
    const call = await abha.enrolVerify(txnId, otp, mobile || undefined);
    record("verify", call);
    if (call.ok) {
      setResult(call.body);
      if (call.body.txn_id) {
        const s = await abha.addressSuggestions(call.body.txn_id);
        record("suggest", s);
        if (s.ok) setSuggestions(s.body);
      }
    }
    setBusy(null);
  }

  async function claimAddress() {
    setBusy("address");
    const call = await abha.setAddress(result?.txn_id ?? txnId, address);
    record("address", call);
    setBusy(null);
  }

  async function showCard() {
    if (!result?.token) return;
    setBusy("card");
    try {
      setCardUrl(await fetchCardUrl(result.token));
    } catch (err) {
      record("card", {
        method: "GET",
        path: "/api/abha/card",
        requestBody: null,
        status: 0,
        ok: false,
        body: null,
        message: err instanceof Error ? err.message : "Card fetch failed",
        ms: 0,
      });
    }
    setBusy(null);
  }

  const addressProblem = address ? abhaAddressProblem(address) : null;
  const mobileIsNull = result != null && mobile !== "" && !result.account.mobile;

  return (
    <div className="space-y-3">
      <Note tone="danger">
        <strong>This creates a real ABHA in the sandbox.</strong> The cap is 100
        per client ID and deletion is one-at-a-time through the ABDM portal, so
        only run the final step when you mean to. Everything up to “Verify OTP”
        costs nothing.
      </Note>

      <StepCard
        n={1}
        title="Consent"
        cases={[CASES.CRT_ABHA_102]}
        done={consented}
        hint="The checklist requires consent to be shown and agreed before Aadhaar is collected — not alongside it."
      >
        <Note tone="warn">
          <strong>Placeholder text — not NHA-approved.</strong> CRT_ABHA_102 is
          audited on the exact published wording, which is not in this repo. A
          paraphrase would pass review by looking right, so this deliberately
          does not attempt one. Replace the block below with NHA&apos;s approved
          consent language before certification.
        </Note>
        <blockquote className="rounded border border-line bg-surface-2 px-3 py-2 text-xs leading-relaxed text-ink-muted">
          I consent to the sharing of my Aadhaar details with the National Health
          Authority for the purpose of creating an ABHA, and to the processing of
          my health data in accordance with the ABDM Health Data Management
          Policy.
        </blockquote>
        <label className="flex items-start gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={consented}
            onChange={(e) => setConsented(e.target.checked)}
            className="mt-0.5 h-4 w-4 accent-[var(--accent)]"
          />
          <span>I agree</span>
        </label>
      </StepCard>

      <StepCard
        n={2}
        title="Aadhaar number"
        cases={[CASES.CRT_ABHA_104, CASES.CRT_ABHA_105]}
        done={Boolean(txnId)}
        call={calls.otp}
        hint="Try an 11-digit number first to see the validation, then a real one."
      >
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-56 flex-1">
            <Field label="Aadhaar" error={aadhaarError} hint="12 digits. Never stored, never logged.">
              <Input
                value={aadhaar}
                onChange={setAadhaar}
                disabled={!consented || Boolean(txnId)}
                placeholder="9999 9999 9999"
                inputMode="numeric"
                maxLength={14}
              />
            </Field>
          </div>
          <Button
            onClick={() => requestOtp(false)}
            disabled={!consented || Boolean(txnId)}
            busy={busy === "otp"}
          >
            Send OTP
          </Button>
        </div>
        {calls.otp?.ok && (
          <p className="text-xs text-ink-muted">
            {otpSentMessage(calls.otp.body) ?? "OTP sent to the Aadhaar-linked mobile."}
          </p>
        )}
      </StepCard>

      <StepCard
        n={3}
        title="Verify OTP"
        cases={[CASES.CRT_ABHA_106, CASES.CRT_ABHA_107, CASES.CRT_ABHA_109]}
        done={Boolean(result)}
        call={calls.verify}
        hint="The mobile is optional. Supplying one that is not the Aadhaar-linked number is the documented trap — the enrolment succeeds and the mobile is saved as null."
      >
        <div className="flex flex-wrap items-end gap-2">
          <div className="w-36">
            <Field label="OTP">
              <Input
                value={otp}
                onChange={setOtp}
                disabled={!txnId}
                maxLength={6}
                inputMode="numeric"
                placeholder="000000"
              />
            </Field>
          </div>
          <div className="w-44">
            <Field
              label="Mobile (optional)"
              hint="ABDM rejects repeated-digit numbers like 9999999999 as invalid, so use a real one or leave it blank."
            >
              <Input
                value={mobile}
                onChange={setMobile}
                disabled={!txnId}
                maxLength={10}
                inputMode="numeric"
                placeholder="9876543210"
              />
            </Field>
          </div>
          <Button onClick={verify} disabled={!txnId || otp.length < 4} busy={busy === "verify"}>
            Create ABHA
          </Button>
        </div>
        {txnId && (
          <ResendButton txnId={txnId} onResend={() => requestOtp(true)} busy={busy === "otp"} />
        )}
        {mobileIsNull && (
          <Note tone="danger">
            <strong>Mobile saved as null.</strong> The number you supplied is not
            the one linked to this Aadhaar at UIDAI. The ABHA is real but has no
            contact route, so OTP login against it will fail until the mobile is
            attached separately. ABDM reported this as a success with no warning
            field — this console detected it by reading the profile back.
          </Note>
        )}
      </StepCard>

      {result && (
        <>
          <StepCard
            n={4}
            title="ABHA number"
            cases={[CASES.CRT_ABHA_113]}
            done
            hint="The 14-digit number and the address must both be visible to the user."
          >
            <dl className="grid gap-2 sm:grid-cols-2">
              <Detail label="ABHA number" value={result.account.abha_number} mono />
              <Detail label="ABHA address" value={result.account.abha_address} mono />
              <Detail label="Name" value={result.account.name} />
              <Detail label="Date of birth" value={result.account.date_of_birth} />
              <Detail label="Gender" value={result.account.gender} />
              <Detail label="Mobile" value={result.account.mobile ?? "— not linked —"} />
            </dl>
            <div className="flex gap-1.5">
              <Tag tone={result.is_new ? "accent" : "warn"}>
                {result.is_new ? "newly created" : "already existed"}
              </Tag>
            </div>
          </StepCard>

          <StepCard
            n={5}
            title="ABHA address"
            cases={[CASES.CRT_ABHA_112]}
            call={calls.address ?? calls.suggest}
            done={Boolean(calls.address?.ok)}
            hint="At least three suggestions, or a custom address validated against all five rules."
          >
            {suggestions.length > 0 ? (
              <div className="space-y-1.5">
                <Eyebrow>{suggestions.length} suggestions</Eyebrow>
                <Choice
                  value={address}
                  onChange={setAddress}
                  options={suggestions.map((s) => ({ value: s, label: s }))}
                />
              </div>
            ) : (
              <p className="text-xs text-ink-faint">No suggestions returned.</p>
            )}
            <div className="flex flex-wrap items-end gap-2">
              <div className="min-w-56 flex-1">
                <Field
                  label="Or type one"
                  error={addressProblem}
                  hint="8–18 characters, letters and digits, at most one dot and one underscore, neither first nor last."
                >
                  <Input value={address} onChange={setAddress} placeholder="firstname.lastname" />
                </Field>
              </div>
              <Button
                onClick={claimAddress}
                disabled={!address || Boolean(addressProblem)}
                busy={busy === "address"}
              >
                Claim address
              </Button>
            </div>
          </StepCard>

          <StepCard
            n={6}
            title="ABHA card"
            cases={[CASES.CRT_ABHA_114]}
            call={calls.card}
            done={Boolean(cardUrl)}
            hint="Must render and be downloadable, carrying the ABHA number, QR, date of birth, gender and address."
          >
            <div className="flex flex-wrap items-center gap-2">
              <Button onClick={showCard} disabled={!result.token} busy={busy === "card"}>
                Fetch card
              </Button>
              {cardUrl && (
                <a
                  href={cardUrl}
                  download="abha-card.png"
                  className="rounded border border-border-strong bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-surface-2"
                >
                  Download
                </a>
              )}
            </div>
            {cardUrl && (
              <Panel className="inline-block p-2">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={cardUrl} alt="ABHA card" className="max-w-sm rounded" />
              </Panel>
            )}
          </StepCard>
        </>
      )}
    </div>
  );
}

/** ABDM's "sent to XXXXXX8769" line, when it supplies one. Narrowed rather
 *  than cast: `AbhaCall.body` is deliberately `unknown` so a response shape
 *  that changed upstream cannot be read as text without a check. */
function otpSentMessage(body: unknown): string | null {
  if (body && typeof body === "object") {
    const message = (body as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  return null;
}

function Detail({
  label,
  value,
  mono,
}: {
  label: string;
  value?: string | null;
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
        {label}
      </dt>
      <dd className={`text-sm text-ink ${mono ? "font-mono" : ""}`}>{value || "—"}</dd>
    </div>
  );
}
