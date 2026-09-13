"use client";

import { useState } from "react";

import {
  abha,
  fetchCardUrl,
  type AbhaAccount,
  type AbhaCall,
  type AddressChannel,
  type LoginMethod,
  type LoginResult,
} from "@/lib/abha";
import { M1_GROUPS, type M1Case } from "@/lib/m1-cases";
import { Panel } from "@/components/ui";
import { Button, Choice, Field, Input, Note, ResendButton } from "./controls";
import { StepCard } from "./StepCard";

const HOST_CASES = Object.fromEntries(
  M1_GROUPS.find((g) => g.key === "verify")!.cases.map((c) => [c.id, c]),
) as Record<string, M1Case>;
const ADDR_CASES = Object.fromEntries(
  M1_GROUPS.find((g) => g.key === "address")!.cases.map((c) => [c.id, c]),
) as Record<string, M1Case>;

/**
 * Verification, both families.
 *
 * `family` is the control that matters. ABHA number / Aadhaar / mobile go to
 * the ABHA host; an ABHA *address* goes to the PHR host, with different
 * endpoints and a scope vocabulary the other host rejects. Presenting them as
 * one four-way credential picker would be the same mistake the backend made
 * and had to be corrected for — so the split is visible in the UI too.
 */
export function VerifyFlow() {
  const [family, setFamily] = useState<"host" | "address">("host");
  const [method, setMethod] = useState<LoginMethod>("abha-number");
  const [channel, setChannel] = useState<AddressChannel>("aadhaar");
  const [identifier, setIdentifier] = useState("");
  const [otp, setOtp] = useState("");
  const [txnId, setTxnId] = useState("");
  const [login, setLogin] = useState<LoginResult | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [cardUrl, setCardUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [calls, setCalls] = useState<Record<string, AbhaCall | null>>({});

  const record = (key: string, call: AbhaCall) =>
    setCalls((prev) => ({ ...prev, [key]: call }));

  function reset() {
    setTxnId("");
    setOtp("");
    setLogin(null);
    setToken(null);
    setCardUrl(null);
    setCalls({});
  }

  async function search() {
    setBusy("search");
    const call = await abha.addressSearch(identifier);
    record("search", call);
    setBusy(null);
  }

  async function requestOtp(resend = false) {
    setBusy("otp");
    const call =
      family === "address"
        ? await abha.addressOtp(identifier, channel)
        : await abha.loginOtp(identifier, method, resend ? txnId : undefined);
    record("otp", call);
    if (call.ok) setTxnId(call.body.txn_id);
    setBusy(null);
  }

  async function verify() {
    setBusy("verify");
    const call =
      family === "address"
        ? await abha.addressVerify(txnId, otp, channel)
        : await abha.loginVerify(txnId, otp, method);
    record("verify", call);
    if (call.ok) {
      setLogin(call.body);
      if (call.body.token) setToken(call.body.token);
    }
    setBusy(null);
  }

  async function choose(account: AbhaAccount) {
    if (!login?.t_token || !account.index) return;
    setBusy("select");
    const call = await abha.selectAccount(txnId, account.index, login.t_token);
    record("select", call);
    if (call.ok && call.body.token) setToken(call.body.token);
    setBusy(null);
  }

  async function loadProfile() {
    if (!token) return;
    setBusy("profile");
    const call = await abha.profile(token, family === "address");
    record("profile", call);
    try {
      setCardUrl(await fetchCardUrl(token, family === "address"));
    } catch {
      /* the card is a separate row; a failure here must not hide the profile */
    }
    setBusy(null);
  }

  const searchedNotFound = calls.search?.ok && calls.search.body === null;
  const needsChoice = Boolean(login && !login.token && login.t_token);

  return (
    <div className="space-y-3">
      <StepCard
        n={1}
        title="Credential"
        cases={
          family === "address"
            ? [ADDR_CASES.VRFY_ABHA_102, ADDR_CASES.VRFY_ABHA_202]
            : [HOST_CASES.VRFY_ABHA_101, HOST_CASES.VRFY_ABHA_201, HOST_CASES.VRFY_ABHA_401]
        }
        done={Boolean(txnId)}
        call={calls.search}
        hint="ABHA number, Aadhaar and mobile are verified on the ABHA host. An ABHA address is a separate family on the PHR host — not a fourth option on the same one."
      >
        <Choice
          value={family}
          onChange={(v) => {
            setFamily(v);
            setIdentifier("");
            reset();
          }}
          options={[
            { value: "host", label: "ABHA number / Aadhaar / mobile", hint: "ABHA host" },
            { value: "address", label: "ABHA address", hint: "PHR host" },
          ]}
        />

        {family === "host" ? (
          <Choice
            value={method}
            onChange={(v) => {
              setMethod(v);
              reset();
            }}
            options={[
              { value: "abha-number", label: "ABHA number", hint: "OTP from UIDAI" },
              { value: "aadhaar", label: "Aadhaar", hint: "OTP from UIDAI" },
              { value: "mobile", label: "Mobile", hint: "OTP from ABDM" },
            ]}
          />
        ) : (
          <Choice
            value={channel}
            onChange={(v) => {
              setChannel(v);
              reset();
            }}
            options={[
              { value: "aadhaar", label: "Aadhaar OTP", hint: "VRFY_ABHA_102" },
              { value: "mobile", label: "Mobile OTP", hint: "VRFY_ABHA_202" },
            ]}
          />
        )}

        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-56 flex-1">
            <Field
              label={
                family === "address"
                  ? "ABHA address"
                  : method === "abha-number"
                    ? "ABHA number"
                    : method === "aadhaar"
                      ? "Aadhaar"
                      : "Mobile"
              }
              hint={
                family === "host" && method === "abha-number"
                  ? "The OTP still comes from UIDAI, not ABDM — the non-obvious pairing."
                  : undefined
              }
            >
              <Input
                value={identifier}
                onChange={setIdentifier}
                disabled={Boolean(txnId)}
                mono
                placeholder={
                  family === "address"
                    ? "patient@sbx"
                    : method === "abha-number"
                      ? "91-1234-5678-9012"
                      : method === "aadhaar"
                        ? "9999 9999 9999"
                        : "9999999999"
                }
              />
            </Field>
          </div>
          {family === "address" && (
            <Button variant="secondary" onClick={search} disabled={!identifier} busy={busy === "search"}>
              Check exists
            </Button>
          )}
          <Button onClick={() => requestOtp(false)} disabled={!identifier || Boolean(txnId)} busy={busy === "otp"}>
            Send OTP
          </Button>
          {txnId && (
            <Button variant="secondary" onClick={reset}>
              Start over
            </Button>
          )}
        </div>

        {searchedNotFound && (
          <Note tone="info">
            No ABHA exists for that address. VRFY_ABHA_302 requires this to be a
            branch, not a dead end — the create flow stays available, which is
            why this is a message and not an error.
          </Note>
        )}
      </StepCard>

      <StepCard
        n={2}
        title="Verify OTP"
        cases={[HOST_CASES.VRFY_ABHA_304, HOST_CASES.VRFY_ABHA_301]}
        done={Boolean(login)}
        call={calls.otp ?? undefined}
        hint="Enter a wrong OTP first to see the failure path, then the real one."
      >
        <div className="flex flex-wrap items-end gap-2">
          <div className="w-36">
            <Field label="OTP">
              <Input value={otp} onChange={setOtp} disabled={!txnId} maxLength={6} inputMode="numeric" placeholder="000000" />
            </Field>
          </div>
          <Button onClick={verify} disabled={!txnId || otp.length < 4} busy={busy === "verify"}>
            Verify
          </Button>
        </div>
        {txnId && family === "host" && (
          <ResendButton txnId={txnId} onResend={() => requestOtp(true)} busy={busy === "otp"} />
        )}
        {calls.verify && !calls.verify.ok && (
          <Note tone="danger">{calls.verify.message ?? "Verification failed."}</Note>
        )}
      </StepCard>

      {needsChoice && (
        <StepCard
          n={3}
          title="Choose an account"
          cases={[HOST_CASES.VRFY_ABHA_303]}
          call={calls.select}
          done={Boolean(token)}
          hint="One mobile can carry up to six ABHA numbers. All of them must be listed with name, number and address before one is picked."
        >
          <div className="space-y-1.5">
            {login!.accounts.map((account) => (
              <button
                key={account.index ?? account.abha_number}
                type="button"
                onClick={() => choose(account)}
                disabled={busy === "select"}
                className="flex w-full items-center gap-3 rounded border border-line bg-surface px-3 py-2 text-left transition hover:bg-surface-2"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-ink">{account.name ?? "—"}</p>
                  <p className="truncate font-mono text-[11px] text-ink-muted">
                    {account.abha_number} · {account.abha_address ?? "no address"}
                  </p>
                </div>
                <span className="shrink-0 text-xs text-accent">Select</span>
              </button>
            ))}
          </div>
        </StepCard>
      )}

      {token && (
        <StepCard
          n={needsChoice ? 4 : 3}
          title="Profile and card"
          cases={[HOST_CASES.VRFY_ABHA_404]}
          call={calls.profile}
          done={Boolean(calls.profile?.ok)}
          hint={
            family === "address"
              ? "Read from the PHR host — the token from an address login returns 401 against the ABHA host."
              : "Read from the ABHA host."
          }
        >
          <Button onClick={loadProfile} busy={busy === "profile"}>
            Fetch profile
          </Button>
          {cardUrl && (
            <Panel className="inline-block p-2">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={cardUrl} alt="ABHA card" className="max-w-sm rounded" />
            </Panel>
          )}
        </StepCard>
      )}
    </div>
  );
}
