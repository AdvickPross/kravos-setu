"use client";

import { useEffect, useState } from "react";

import { abha } from "@/lib/abha";
import type { AbhaSession } from "@/lib/session";
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
      <aside className="relative hidden overflow-hidden border-r border-line bg-bg lg:flex lg:items-center">
        {/* A single soft wash, off-centre. Enough to stop a very large flat
            panel reading as dead space, not enough to compete with the form. */}
        <div
          aria-hidden
          className="pointer-events-none absolute -left-32 top-1/4 h-[32rem] w-[32rem] rounded-full opacity-[0.07] blur-3xl"
          style={{ background: "var(--accent)" }}
        />
        <div className="relative z-10 mx-auto flex max-w-md flex-col gap-8 px-12 py-16">
          <div className="flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-sm font-bold text-[#0b0d11]">
              K
            </span>
            <span>
              <span className="block text-sm font-semibold leading-tight tracking-tight text-ink">
                Kravos Setu
              </span>
              <span className="block text-[11px] leading-tight text-ink-faint">
                Clinical context layer · ABDM
              </span>
            </span>
          </div>

          <div>
            <h1 className="text-[1.75rem] font-semibold leading-[1.2] tracking-tight text-ink">
              The record is only
              <br />
              half the answer.
            </h1>
            <p className="mt-4 text-sm leading-relaxed text-ink-muted">
              Sign in with ABHA to pull a patient&apos;s history across every
              facility they have visited — and to see what a pre-operative
              assessment expects to find and cannot.
            </p>
          </div>

          <ul className="space-y-3.5 border-t border-line pt-7">
            {[
              ["Portable identity", "One ABHA works at any ABDM facility in India."],
              ["Consent-bound", "Records move only with the patient's explicit consent."],
              ["Absence is a finding", "The brief names what is missing, not just what is there."],
            ].map(([title, body]) => (
              <li key={title} className="flex gap-3">
                <span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                <p className="text-sm leading-snug text-ink-muted">
                  <span className="font-medium text-ink">{title}.</span> {body}
                </p>
              </li>
            ))}
          </ul>

          <p className="border-t border-line pt-6 text-[11px] leading-relaxed text-ink-faint">
            Aadhaar numbers, mobile numbers and OTPs are RSA-encrypted before
            they leave this machine, and are never written to disk or logs.
          </p>
        </div>
      </aside>

      <main className="flex items-center justify-center bg-surface-2/40 px-5 py-10 sm:px-8">
        <div className="w-full max-w-[26rem]">
          <div className="mb-6 flex items-center gap-2.5 lg:hidden">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-sm font-bold text-[#0b0d11]">
              K
            </span>
            <span className="text-sm font-semibold tracking-tight text-ink">Kravos Setu</span>
          </div>

          <div className="rounded-2xl border border-line bg-surface p-6 shadow-xl shadow-black/20 sm:p-7">
            <div className="mb-5">
              <div className="flex items-center justify-between gap-2">
                <h2 className="text-lg font-semibold tracking-tight text-ink">
                  {mode === "signin" ? "Sign in with ABHA" : "Create your ABHA"}
                </h2>
                {environment && (
                  <span className="shrink-0 rounded border border-line bg-bg px-1.5 py-px text-[10px] font-medium text-ink-faint">
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

          <div className="mt-4 rounded-2xl border border-line bg-surface/60 p-4">
            <button
              type="button"
              onClick={onSkip}
              className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm font-medium text-ink-muted transition hover:border-line-strong hover:text-ink"
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
