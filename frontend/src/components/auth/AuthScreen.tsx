"use client";

import { useEffect, useState } from "react";

import { abha } from "@/lib/abha";
import type { AbhaSession } from "@/lib/session";
import { AlertIcon, IdCardIcon, LockIcon } from "../icons";
import { Brand } from "../ui";
import { Banner } from "./parts";
import { SignInFlow } from "./SignInFlow";
import { SignUpFlow } from "./SignUpFlow";

/**
 * The shell both auth flows live in.
 *
 * Layout notes, because the first version of this got them wrong:
 *
 * **The two halves need different grounds.** `--bg` and `--surface-2` sit four
 * points apart in the dark palette, which on a large flat area reads as one
 * colour with a seam rather than as two panels. The left is now the deeper
 * `--bg` with a soft accent wash; the form sits in a raised `--surface` card
 * with a border. Depth does the separating, not hue.
 *
 * **Vertical centring beats space-between.** The old left panel pinned the
 * brand to the top and the security note to the bottom, which on a tall
 * monitor left a void in the middle and pushed the footnote under the dev
 * overlay. Everything is one centred column now, and the footnote is part of
 * it rather than anchored to the viewport.
 */
export function AuthScreen({
  onAuthenticated,
  onSkip,
}: {
  onAuthenticated: (session: AbhaSession, token: string | null) => void;
  onSkip: () => void;
}) {
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [reachable, setReachable] = useState(true);
  const [environment, setEnvironment] = useState<string | null>(null);

  useEffect(() => {
    abha.status().then((call) => {
      setReachable(call.status !== 0);
      if (call.ok) {
        setConfigured(call.body.configured);
        setEnvironment(call.body.environment);
      } else {
        setConfigured(false);
      }
    });
  }, []);

  const blocked = configured === false || !reachable;

  return (
    <div className="grid min-h-dvh lg:grid-cols-2">
      <aside className="dot-grid relative hidden overflow-hidden border-r border-line lg:flex lg:items-center">
        {/* Two soft washes, off-centre. Enough to stop a very large flat
            panel reading as dead space, not enough to compete with the form. */}
        <div
          aria-hidden
          className="pointer-events-none absolute -left-40 top-[10%] h-[36rem] w-[36rem] rounded-full opacity-[0.16] blur-3xl"
          style={{ background: "var(--accent)" }}
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -bottom-40 right-[-10rem] h-[30rem] w-[30rem] rounded-full opacity-[0.08] blur-3xl"
          style={{ background: "#0ea5e9" }}
        />
        <div className="relative z-10 mx-auto flex max-w-lg flex-col gap-10 px-12 py-16">
          <Brand subtitle="Clinical context layer · ABDM" />

          <div>
            <h1 className="text-[2.6rem] font-semibold leading-[1.08] tracking-[-0.02em] text-ink">
              The record is only
              <br />
              <span className="bg-linear-to-r from-accent to-sky-500 bg-clip-text text-transparent">
                half the answer.
              </span>
            </h1>
            <p className="mt-5 max-w-md text-[15px] leading-relaxed text-ink-muted">
              Sign in with ABHA to pull a patient&apos;s history across every
              facility they have visited — and to see what a pre-operative
              assessment expects to find and cannot.
            </p>
          </div>

          <ul className="grid gap-3">
            {(
              [
                [IdCardIcon, "Portable identity", "One ABHA works at any ABDM facility in India."],
                [LockIcon, "Consent-bound", "Records move only with the patient's explicit consent."],
                [AlertIcon, "Absence is a finding", "The brief names what is missing, not just what is there."],
              ] as const
            ).map(([Icon, title, body]) => (
              <li key={title} className="card flex items-start gap-3.5 bg-surface/70 px-4 py-3.5 backdrop-blur">
                <span className="mt-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-lg bg-accent-bg text-accent">
                  <Icon size={16} />
                </span>
                <p className="text-sm leading-snug text-ink-muted">
                  <span className="block font-semibold text-ink">{title}.</span> {body}
                </p>
              </li>
            ))}
          </ul>

          <p className="flex items-start gap-2 text-[11px] leading-relaxed text-ink-faint">
            <LockIcon size={13} className="mt-px shrink-0" />
            Aadhaar numbers, mobile numbers and OTPs are RSA-encrypted before
            they leave this machine, and are never written to disk or logs.
          </p>
        </div>
      </aside>

      <main className="flex items-center justify-center px-5 py-10 sm:px-8">
        <div className="w-full max-w-[26rem]">
          <div className="mb-6 lg:hidden">
            <Brand subtitle="Clinical context layer · ABDM" />
          </div>

          <div className="rounded-2xl border border-line bg-surface p-6 shadow-raised sm:p-7">
            <div className="mb-5">
              <div className="flex items-center justify-between gap-2">
                <h2 className="text-xl font-semibold tracking-tight text-ink">
                  {mode === "signin" ? "Sign in with ABHA" : "Create your ABHA"}
                </h2>
                {environment && (
                  <span className="shrink-0 rounded-full border border-line bg-surface-2 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-ink-faint">
                    {environment}
                  </span>
                )}
              </div>
              <p className="mt-1 text-sm text-ink-muted">
                {mode === "signin"
                  ? "Verify your identity to continue."
                  : "A free health ID, created with your Aadhaar."}
              </p>
            </div>

            {!reachable && (
              <div className="mb-4">
                <Banner tone="danger">
                  Cannot reach the API. Start it with{" "}
                  <code className="font-mono">uvicorn backend.main:app --port 8000</code>.
                </Banner>
              </div>
            )}
            {configured === false && reachable && (
              <div className="mb-4">
                <Banner tone="warn">
                  ABDM credentials are not configured, so ABHA is unavailable.
                  Add <code className="font-mono">ABDM_CLIENT_ID</code> and{" "}
                  <code className="font-mono">ABDM_CLIENT_SECRET</code> to{" "}
                  <code className="font-mono">.env</code>, then restart the API.
                </Banner>
              </div>
            )}

            {mode === "signin" ? (
              <SignInFlow
                disabled={blocked}
                onSignedIn={onAuthenticated}
                onSwitchToSignUp={() => setMode("signup")}
              />
            ) : (
              <SignUpFlow
                onCreated={onAuthenticated}
                onSwitchToSignIn={() => setMode("signin")}
              />
            )}
          </div>

          <div className="mt-4 rounded-2xl border border-dashed border-line-strong bg-surface/50 p-4">
            <button
              type="button"
              onClick={onSkip}
              className="w-full rounded-xl border border-line bg-surface px-3 py-2.5 text-sm font-semibold text-ink shadow-card transition hover:border-accent-line hover:text-accent"
            >
              Explore the clinical demo without ABHA
            </button>
            <p className="mt-2.5 text-[11px] leading-relaxed text-ink-faint">
              The demo corpus is 952 synthetic patients and none of them holds a
              real ABHA, so this path is not a verified identity — every screen
              after it says so.
            </p>
          </div>
        </div>
      </main>
    </div>
  );
}
