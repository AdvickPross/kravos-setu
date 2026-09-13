"use client";

import { useEffect, useState } from "react";

import {
  abha,
  type AbhaAccount,
  type AbhaCall,
  type AddressChannel,
  type LoginMethod,
  type LoginResult,
} from "@/lib/abha";
import { setLiveToken, type AbhaSession } from "@/lib/session";

/**
 * Sign in with ABHA.
 *
 * The front door, not a test harness. `/m1` still exists and still shows raw
 * requests and responses because a certification tester needs that; a patient
 * at a registration desk needs the opposite, so this screen shows a
 * credential, an OTP box, and one clear error line when something is wrong.
 *
 * Two design points worth stating:
 *
 * **ABHA address is a separate credential path, not a fourth tab.** It runs
 * against the PHR host with a scope vocabulary the ABHA host rejects. The tabs
 * here map to that split rather than papering over it, because a UI that
 * offers four interchangeable-looking options will eventually be wired as
 * four interchangeable calls — which is exactly the bug this codebase already
 * had once.
 *
 * **Signing in is optional.** The clinical corpus is 952 synthetic Synthea
 * patients; none of them holds a real ABHA, so gating the demo behind a
 * government OTP would make the product undemonstrable. The skip is explicit
 * and labelled rather than hidden, and the session records `demo: true` so
 * nothing downstream can mistake it for a verified identity.
 */

type Step = "credential" | "otp" | "choose";

const METHODS: { value: LoginMethod; label: string; placeholder: string; hint: string }[] = [
  {
    value: "abha-number",
    label: "ABHA number",
    placeholder: "91-1234-5678-9012",
    hint: "14 digits. The OTP goes to your Aadhaar-linked mobile.",
  },
  {
    value: "mobile",
    label: "Mobile",
    placeholder: "9876543210",
    hint: "The mobile registered against your ABHA.",
  },
  {
    value: "aadhaar",
    label: "Aadhaar",
    placeholder: "9999 9999 9999",
    hint: "12 digits. Encrypted before it leaves this machine.",
  },
];

export function LoginScreen({
  onSignedIn,
  onSkip,
}: {
  onSignedIn: (session: AbhaSession, token: string | null) => void;
  onSkip: () => void;
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
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [reachable, setReachable] = useState(true);

  useEffect(() => {
    abha.status().then((call) => {
      setReachable(call.status !== 0);
      setConfigured(call.ok ? call.body.configured : false);
    });
  }, []);

  const active = METHODS.find((m) => m.value === method)!;

  function fail(call: AbhaCall) {
    // One line, in the patient's terms. The field-level detail that /m1 shows
    // is right for a tester and noise for someone holding a phone.
    const fields = call.fields?.map((f) => f.message).filter(Boolean);
    setError(fields?.length ? fields.join(" ") : (call.message ?? "Something went wrong."));
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

  function finish(result: LoginResult, account: AbhaAccount, token: string | null) {
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
    void result;
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
      finish(result, result.accounts[0] ?? {}, result.token);
    } else if (result.t_token) {
      // One credential, several ABHAs. Up to six are allowed per mobile.
      setAccounts(result.accounts);
      setTToken(result.t_token);
      setStep("choose");
    } else {
      setError("Verified, but no account came back. Try again.");
    }
    setBusy(false);
  }

  async function choose(account: AbhaAccount) {
    if (!tToken || !account.index) return;
    setBusy(true);
    const call = await abha.selectAccount(txnId, account.index, tToken);
    if (call.ok) finish(call.body, account, call.body.token ?? null);
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

  return (
    <div className="grid min-h-dvh lg:grid-cols-[1.1fr_1fr]">
      {/* Left: what this is. Hidden on small screens, where the form is the
          only thing worth the space. */}
      <aside className="hidden flex-col justify-between bg-surface-2 p-10 lg:flex">
        <div>
          <p className="text-sm font-semibold tracking-tight text-ink">Kravos Setu</p>
          <p className="text-xs text-ink-faint">Clinical context layer · ABDM</p>
        </div>
        <div className="max-w-md">
          <h1 className="text-2xl font-semibold leading-tight tracking-tight text-ink">
            The record is only half the answer.
          </h1>
          <p className="mt-3 text-sm leading-relaxed text-ink-muted">
            Sign in with ABHA to pull a patient&apos;s history across every facility
            they have visited — and to see what a pre-operative assessment
            expects to find and cannot.
          </p>
          <ul className="mt-6 space-y-2.5">
            {[
              ["Portable identity", "One ABHA works at any ABDM facility in India."],
              ["Consent-bound", "Records move only with the patient's explicit consent."],
              ["Absence is a finding", "The brief names what is missing, not just what is there."],
            ].map(([title, body]) => (
              <li key={title} className="flex gap-2.5">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                <p className="text-sm leading-snug text-ink-muted">
                  <span className="font-medium text-ink">{title}.</span> {body}
                </p>
              </li>
            ))}
          </ul>
        </div>
        <p className="text-[11px] leading-relaxed text-ink-faint">
          Aadhaar, mobile numbers and OTPs are RSA-encrypted before they leave
          this machine and are never written to disk or logs.
        </p>
      </aside>

      {/* Right: the form. */}
      <main className="flex items-center justify-center p-5 sm:p-8">
        <div className="w-full max-w-sm">
          <div className="mb-6 lg:hidden">
            <p className="text-sm font-semibold tracking-tight text-ink">Kravos Setu</p>
            <p className="text-xs text-ink-faint">Clinical context layer · ABDM</p>
          </div>

          <h2 className="text-xl font-semibold tracking-tight text-ink">
            {step === "choose" ? "Choose your ABHA" : "Sign in with ABHA"}
          </h2>
          <p className="mt-1 text-sm text-ink-muted">
            {step === "credential" && "Verify your identity to continue."}
            {step === "otp" &&
              (sentTo
                ? `Enter the 6-digit code sent to ${sentTo}.`
                : "Enter the 6-digit code you received.")}
            {step === "choose" && "This number is linked to more than one ABHA."}
          </p>

          {reachable === false && (
            <Banner tone="danger">
              Cannot reach the API. Start it with{" "}
              <code className="font-mono">uvicorn backend.main:app --port 8000</code>.
            </Banner>
          )}
          {configured === false && reachable && (
            <Banner tone="warn">
              ABDM credentials are not configured, so sign-in is unavailable. Add{" "}
              <code className="font-mono">ABDM_CLIENT_ID</code> and{" "}
              <code className="font-mono">ABDM_CLIENT_SECRET</code> to{" "}
              <code className="font-mono">.env</code> and restart the API.
            </Banner>
          )}

          <div className="mt-6 space-y-4">
            {step === "credential" && (
              <>
                <div className="flex rounded-lg border border-line bg-surface-2 p-0.5">
                  {[
                    { value: false, label: "ABHA number, mobile or Aadhaar" },
                    { value: true, label: "ABHA address" },
                  ].map((tab) => (
                    <button
                      key={String(tab.value)}
                      type="button"
                      onClick={() => {
                        setUseAddress(tab.value);
                        setIdentifier("");
                        setError(null);
                      }}
                      className={`flex-1 rounded-md px-2 py-1.5 text-[11px] font-medium transition ${
                        useAddress === tab.value
                          ? "bg-surface text-ink shadow-sm"
                          : "text-ink-faint hover:text-ink-muted"
                      }`}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>

                {!useAddress && (
                  <div className="flex gap-1.5">
                    {METHODS.map((m) => (
                      <button
                        key={m.value}
                        type="button"
                        onClick={() => {
                          setMethod(m.value);
                          setIdentifier("");
                          setError(null);
                        }}
                        className={`flex-1 rounded border px-2 py-1 text-[11px] font-medium transition ${
                          method === m.value
                            ? "border-accent-line bg-accent-bg text-accent"
                            : "border-line bg-surface text-ink-muted hover:bg-surface-2"
                        }`}
                      >
                        {m.label}
                      </button>
                    ))}
                  </div>
                )}

                {useAddress && (
                  <div className="flex gap-1.5">
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
                        className={`flex-1 rounded border px-2 py-1 text-[11px] font-medium transition ${
                          channel === value
                            ? "border-accent-line bg-accent-bg text-accent"
                            : "border-line bg-surface text-ink-muted hover:bg-surface-2"
                        }`}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                )}

                <LabelledInput
                  label={useAddress ? "ABHA address" : active.label}
                  hint={useAddress ? "For example, yourname@sbx." : active.hint}
                  value={identifier}
                  onChange={setIdentifier}
                  placeholder={useAddress ? "yourname@sbx" : active.placeholder}
                  onEnter={() => identifier && sendOtp()}
                  autoFocus
                />

                {error && <ErrorLine>{error}</ErrorLine>}

                <PrimaryButton
                  onClick={() => sendOtp()}
                  busy={busy}
                  disabled={!identifier.trim() || configured === false}
                >
                  Send OTP
                </PrimaryButton>
              </>
            )}

            {step === "otp" && (
              <>
                <LabelledInput
                  label="One-time password"
                  value={otp}
                  onChange={(v) => setOtp(v.replace(/\D/g, ""))}
                  placeholder="000000"
                  maxLength={6}
                  big
                  autoFocus
                  onEnter={() => otp.length === 6 && verify()}
                />
                {error && <ErrorLine>{error}</ErrorLine>}
                <PrimaryButton onClick={verify} busy={busy} disabled={otp.length < 6}>
                  Verify and continue
                </PrimaryButton>
                <div className="flex items-center justify-between text-xs">
                  <button
                    type="button"
                    onClick={restart}
                    className="text-ink-muted underline-offset-2 hover:underline"
                  >
                    Use a different credential
                  </button>
                  <ResendLink txnId={txnId} onResend={() => sendOtp(true)} />
                </div>
              </>
            )}

            {step === "choose" && (
              <>
                <div className="space-y-1.5">
                  {accounts.map((account) => (
                    <button
                      key={account.index ?? account.abha_number}
                      type="button"
                      disabled={busy}
                      onClick={() => choose(account)}
                      className="flex w-full items-center gap-3 rounded-lg border border-line bg-surface px-3 py-2.5 text-left transition hover:border-accent-line hover:bg-accent-bg/40"
                    >
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-2 text-xs font-semibold text-ink-muted">
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
                  className="text-xs text-ink-muted underline-offset-2 hover:underline"
                >
                  Start over
                </button>
              </>
            )}
          </div>

          <div className="mt-8 border-t border-line pt-4">
            <button
              type="button"
              onClick={onSkip}
              className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm font-medium text-ink-muted transition hover:bg-surface-2"
            >
              Explore the clinical demo without ABHA
            </button>
            <p className="mt-2 text-[11px] leading-relaxed text-ink-faint">
              The demo corpus is 952 synthetic patients; none of them holds a
              real ABHA, so this path is not a verified identity and is marked
              as such throughout.
            </p>
          </div>
        </div>
      </main>
    </div>
  );
}

// -- small pieces -----------------------------------------------------------

function LabelledInput({
  label,
  hint,
  value,
  onChange,
  placeholder,
  maxLength,
  big,
  autoFocus,
  onEnter,
}: {
  label: string;
  hint?: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  maxLength?: number;
  big?: boolean;
  autoFocus?: boolean;
  onEnter?: () => void;
}) {
  return (
    <label className="block">
      <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
        {label}
      </span>
      <input
        value={value}
        autoFocus={autoFocus}
        maxLength={maxLength}
        inputMode="numeric"
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && onEnter?.()}
        className={`mt-1 w-full rounded-lg border border-line bg-surface px-3 text-ink outline-none transition placeholder:text-ink-faint focus:border-accent-line focus:ring-2 focus:ring-accent-bg ${
          big
            ? "py-2.5 text-center font-mono text-2xl tracking-[0.4em]"
            : "py-2 font-mono text-sm tracking-wide"
        }`}
      />
      {hint && <span className="mt-1 block text-[11px] text-ink-faint">{hint}</span>}
    </label>
  );
}

function PrimaryButton({
  children,
  onClick,
  busy,
  disabled,
}: {
  children: React.ReactNode;
  onClick: () => void;
  busy?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busy}
      className="flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-3 py-2.5 text-sm font-semibold text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
    >
      {busy && (
        <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white border-t-transparent" />
      )}
      {children}
    </button>
  );
}

function ErrorLine({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-lg border border-danger-line bg-danger-bg px-3 py-2 text-xs leading-relaxed text-danger">
      {children}
    </p>
  );
}

function Banner({ tone, children }: { tone: "warn" | "danger"; children: React.ReactNode }) {
  return (
    <p
      className={`mt-4 rounded-lg border px-3 py-2 text-xs leading-relaxed ${
        tone === "danger"
          ? "border-danger-line bg-danger-bg text-danger"
          : "border-warn-line bg-warn-bg text-warn"
      }`}
    >
      {children}
    </p>
  );
}

/** Reads the server's resend budget rather than running its own countdown —
 *  a local timer disagrees with the API after a reload or in a second tab. */
function ResendLink({ txnId, onResend }: { txnId: string; onResend: () => void }) {
  const [left, setLeft] = useState(60);
  const [remaining, setRemaining] = useState(2);

  useEffect(() => {
    if (!txnId) return;
    let cancelled = false;
    const read = async () => {
      const call = await abha.otpState(txnId);
      if (cancelled || !call.ok) return;
      setLeft(Math.ceil(call.body.seconds_until_allowed));
      setRemaining(call.body.resends_remaining);
    };
    read();
    const handle = setInterval(read, 1000);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
  }, [txnId]);

  if (remaining === 0) return <span className="text-ink-faint">No resends left</span>;
  if (left > 0) return <span className="text-ink-faint">Resend in {left}s</span>;
  return (
    <button
      type="button"
      onClick={onResend}
      className="font-medium text-accent underline-offset-2 hover:underline"
    >
      Resend OTP
    </button>
  );
}
