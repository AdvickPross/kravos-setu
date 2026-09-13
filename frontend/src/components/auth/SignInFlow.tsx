"use client";

import { useState } from "react";

import {
  abha,
  type AbhaAccount,
  type AbhaCall,
  type AddressChannel,
  type LoginMethod,
} from "@/lib/abha";
import { setLiveToken, type AbhaSession } from "@/lib/session";
import {
  ErrorLine,
  Labelled,
  OtpInput,
  Primary,
  Resend,
  Segmented,
  TextInput,
} from "./parts";

/**
 * Sign in with an existing ABHA.
 *
 * The credential tabs mirror a real split rather than a cosmetic one. ABHA
 * number, mobile and Aadhaar are verified on the ABHA host; an ABHA *address*
 * goes to the PHR host, whose scope vocabulary the ABHA host rejects outright.
 * Offering four interchangeable-looking options is how that becomes four
 * interchangeable calls — the exact mistake this codebase has already made
 * once and had to correct.
 */

type Step = "credential" | "otp" | "choose";

const METHODS: Record<
  LoginMethod,
  { label: string; placeholder: string; hint: string; maxLength: number }
> = {
  "abha-number": {
    label: "ABHA number",
    placeholder: "91-1234-5678-9012",
    hint: "14 digits. The OTP goes to your Aadhaar-linked mobile.",
    maxLength: 17,
  },
  mobile: {
    label: "Mobile",
    placeholder: "9876543210",
    hint: "The mobile registered against your ABHA.",
    maxLength: 10,
  },
  aadhaar: {
    label: "Aadhaar",
    placeholder: "9999 9999 9999",
    hint: "12 digits. Encrypted before it leaves this machine.",
    maxLength: 14,
  },
};

export function SignInFlow({
  disabled,
  onSignedIn,
  onSwitchToSignUp,
}: {
  disabled?: boolean;
  onSignedIn: (session: AbhaSession, token: string | null) => void;
  onSwitchToSignUp: () => void;
}) {
  const [useAddress, setUseAddress] = useState(false);
  const [method, setMethod] = useState<LoginMethod>("abha-number");
  const [channel, setChannel] = useState<AddressChannel>("aadhaar");
  const [identifier, setIdentifier] = useState("");
  const [otp, setOtp] = useState("");
  const [txnId, setTxnId] = useState("");
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [accounts, setAccounts] = useState<AbhaAccount[]>([]);
  const [tToken, setTToken] = useState<string | null>(null);
  const [step, setStep] = useState<Step>("credential");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const active = METHODS[method];

  function fail(call: AbhaCall) {
    // One line, in the patient's terms. The field-by-field breakdown that /m1
    // renders is right for a tester and noise for someone holding a phone.
    const fields = call.fields?.map((f) => f.message).filter(Boolean);
    setError(fields?.length ? fields.join(" ") : (call.message ?? "Something went wrong."));
  }

  function finish(account: AbhaAccount, token: string | null) {
    setLiveToken(token);
    onSignedIn(
      {
        account,
        via: useAddress
          ? `ABHA address · ${channel === "aadhaar" ? "Aadhaar OTP" : "mobile OTP"}`
          : `${active.label} · OTP`,
        signedInAt: new Date().toISOString(),
        patientId: null,
      },
      token,
    );
  }

  async function sendOtp(resend = false) {
    setBusy(true);
    setError(null);
    const call = useAddress
      ? await abha.addressOtp(identifier.trim(), channel)
      : await abha.loginOtp(identifier.trim(), method, resend ? txnId : undefined);
    if (call.ok) {
      setTxnId(call.body.txn_id);
      setSentTo(call.body.masked_identifier ?? null);
      setStep("otp");
    } else {
      fail(call);
    }
    setBusy(false);
  }

  async function verify() {
    setBusy(true);
    setError(null);
    const call = useAddress
      ? await abha.addressVerify(txnId, otp, channel)
      : await abha.loginVerify(txnId, otp, method);
    if (!call.ok) {
      fail(call);
      setBusy(false);
      return;
    }
    const result = call.body;
    if (result.token) {
      finish(result.accounts[0] ?? {}, result.token);
    } else if (result.t_token) {
      // One credential, several ABHAs — six are allowed per mobile.
      setAccounts(result.accounts);
      setTToken(result.t_token);
      setStep("choose");
    } else {
      setError("Verified, but no account came back. Please try again.");
    }
    setBusy(false);
  }

  async function choose(account: AbhaAccount) {
    if (!tToken || !account.index) return;
    setBusy(true);
    const call = await abha.selectAccount(txnId, account.index, tToken);
    if (call.ok) finish(account, call.body.token ?? null);
    else fail(call);
    setBusy(false);
  }

  function restart() {
    setStep("credential");
    setOtp("");
    setTxnId("");
    setAccounts([]);
    setTToken(null);
    setError(null);
  }

  if (step === "choose") {
    return (
      <div className="space-y-4">
        <p className="text-xs leading-relaxed text-ink-muted">
          This number is linked to more than one ABHA. Choose the one to use.
        </p>
        <div className="space-y-1.5">
          {accounts.map((account) => (
            <button
              key={account.index ?? account.abha_number}
              type="button"
              disabled={busy}
              onClick={() => choose(account)}
              className="flex w-full items-center gap-3 rounded-lg border border-line bg-bg px-3 py-2.5 text-left transition hover:border-accent-line hover:bg-accent-bg/50"
            >
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-surface-2 text-xs font-semibold text-ink-muted">
                {(account.name ?? "?").slice(0, 1).toUpperCase()}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-ink">
                  {account.name ?? "Unnamed"}
                </span>
                <span className="block truncate font-mono text-[11px] text-ink-faint">
                  {account.abha_number ?? account.abha_address}
                </span>
              </span>
            </button>
          ))}
        </div>
        {error && <ErrorLine>{error}</ErrorLine>}
        <button
          type="button"
          onClick={restart}
          className="w-full text-center text-xs text-ink-muted underline-offset-2 hover:underline"
        >
          Start over
        </button>
      </div>
    );
  }

  if (step === "otp") {
    return (
      <div className="space-y-4">
        <Labelled
          label="One-time password"
          hint={sentTo ? `Sent to ${sentTo}.` : "Check the mobile linked to your ABHA."}
        >
          <OtpInput value={otp} onChange={setOtp} onEnter={() => otp.length === 6 && verify()} />
        </Labelled>
        {error && <ErrorLine>{error}</ErrorLine>}
        <Primary onClick={verify} busy={busy} disabled={otp.length < 6}>
          Verify and continue
        </Primary>
        <div className="flex items-center justify-between">
          <button
            type="button"
            onClick={restart}
            className="text-xs text-ink-muted underline-offset-2 hover:underline"
          >
            Use something else
          </button>
          {!useAddress && <Resend txnId={txnId} onResend={() => sendOtp(true)} />}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Segmented
        value={useAddress ? "address" : "id"}
        onChange={(v) => {
          setUseAddress(v === "address");
          setIdentifier("");
          setError(null);
        }}
        options={[
          { value: "id", label: "Number, mobile or Aadhaar" },
          { value: "address", label: "ABHA address" },
        ]}
      />

      {!useAddress ? (
        <div className="grid grid-cols-3 gap-1.5">
          {(Object.keys(METHODS) as LoginMethod[]).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => {
                setMethod(key);
                setIdentifier("");
                setError(null);
              }}
              className={`rounded-lg border px-2 py-1.5 text-xs font-medium transition ${
                method === key
                  ? "border-accent-line bg-accent-bg text-accent"
                  : "border-line bg-bg text-ink-faint hover:border-line-strong hover:text-ink-muted"
              }`}
            >
              {METHODS[key].label}
            </button>
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-1.5">
          {(
            [
              ["aadhaar", "Aadhaar OTP"],
              ["mobile", "Mobile OTP"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setChannel(value)}
              className={`rounded-lg border px-2 py-1.5 text-xs font-medium transition ${
                channel === value
                  ? "border-accent-line bg-accent-bg text-accent"
                  : "border-line bg-bg text-ink-faint hover:border-line-strong hover:text-ink-muted"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      <Labelled
        label={useAddress ? "ABHA address" : active.label}
        hint={useAddress ? "For example, yourname@sbx." : active.hint}
      >
        <TextInput
          value={identifier}
          onChange={setIdentifier}
          placeholder={useAddress ? "yourname@sbx" : active.placeholder}
          maxLength={useAddress ? 40 : active.maxLength}
          numeric={!useAddress && method !== "abha-number"}
          autoFocus
          onEnter={() => identifier.trim() && sendOtp()}
        />
      </Labelled>

      {error && <ErrorLine>{error}</ErrorLine>}

      <Primary onClick={() => sendOtp()} busy={busy} disabled={!identifier.trim() || disabled}>
        Send OTP
      </Primary>

      <p className="text-center text-xs text-ink-muted">
        Don&apos;t have an ABHA?{" "}
        <button
          type="button"
          onClick={onSwitchToSignUp}
          className="font-medium text-accent underline-offset-2 hover:underline"
        >
          Create one
        </button>
      </p>
    </div>
  );
}
