"use client";

import { useState } from "react";

import type { AbhaSession } from "@/lib/session";
import { UploadFlow } from "./UploadFlow";

/**
 * Where a signed-in patient's records come from.
 *
 * Two routes, and only one of them is built. The honest thing is to show
 * both and say so: fetching records from other facilities is Milestone 2 and
 * 3 of the ABDM integration -- the consent request, the HIP/HIU exchange, the
 * ECDH-encrypted bundle -- and none of it exists yet. A greyed-out card that
 * explains what it will do is more useful than hiding the capability, because
 * "can it pull my records from another hospital?" is the first question
 * anyone asks about an ABHA login, and the answer today is "not yet".
 *
 * Faking it would be worse than either. A screen that pretends to fetch and
 * returns the demo corpus would demonstrate a capability the certification
 * checklist has not been anywhere near.
 */
export function RecordsGate({
  session,
  patientId,
  onDone,
  onSkip,
}: {
  session: AbhaSession;
  patientId: string;
  onDone: () => void;
  onSkip: () => void;
}) {
  const [mode, setMode] = useState<"choose" | "upload">("choose");

  if (mode === "upload") {
    return (
      <UploadFlow
        patientId={patientId}
        onBack={() => setMode("choose")}
        onFinished={onDone}
      />
    );
  }

  return (
    <div className="mx-auto w-full max-w-3xl px-5 py-10 sm:px-8">
      <header className="mb-8">
        <h1 className="text-xl font-semibold tracking-tight text-ink">
          Bring in {session.account.name ? `${session.account.name}'s` : "the"} records
        </h1>
        <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">
          Add history before you start asking questions — or skip and work with
          what is already on file.
        </p>
      </header>

      <div className="grid gap-4 sm:grid-cols-2">
        <button
          type="button"
          onClick={() => setMode("upload")}
          className="group rounded-xl border border-line bg-surface p-5 text-left transition hover:border-accent-line hover:bg-accent-bg/30"
        >
          <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-accent-line bg-accent-bg text-accent">
            <UploadIcon />
          </span>
          <h2 className="mt-3 text-sm font-semibold text-ink">Upload a scanned record</h2>
          <p className="mt-1 text-xs leading-relaxed text-ink-muted">
            A discharge summary, prescription or lab report — photo or PDF. It
            is read on this machine, and you approve every fact before any of it
            is kept.
          </p>
          <span className="mt-3 inline-block text-xs font-medium text-accent">
            Upload a file →
          </span>
        </button>

        <div
          aria-disabled
          className="rounded-xl border border-dashed border-line bg-surface/40 p-5"
        >
          <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-line bg-surface-2 text-ink-faint">
            <CloudIcon />
          </span>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold text-ink-muted">Fetch from ABDM</h2>
            <span className="rounded border border-warn-line bg-warn-bg px-1.5 py-px text-[10px] font-medium text-warn">
              not built
            </span>
          </div>
          <p className="mt-1 text-xs leading-relaxed text-ink-faint">
            Pull history from every facility this ABHA has visited, with the
            patient&apos;s consent. That is Milestone 2 and 3 of the ABDM
            integration — the consent request, the HIP/HIU exchange and the
            encrypted bundle transfer. M1, the identity layer it all sits on, is
            done; none of this is.
          </p>
        </div>
      </div>

      <div className="mt-6 flex flex-wrap items-center gap-3 border-t border-line pt-5">
        <button
          type="button"
          onClick={onSkip}
          className="rounded-lg border border-line bg-surface px-3.5 py-2 text-sm font-medium text-ink-muted transition hover:border-line-strong hover:text-ink"
        >
          Skip, go to the clinical view
        </button>
        <p className="text-xs text-ink-faint">
          You can upload a record at any time from the workspace.
        </p>
      </div>
    </div>
  );
}

function UploadIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path
        d="M8 11V3m0 0L5 6m3-3 3 3M3 11v1.5A1.5 1.5 0 0 0 4.5 14h7a1.5 1.5 0 0 0 1.5-1.5V11"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function CloudIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path
        d="M4.5 12a2.5 2.5 0 0 1 0-5 3.5 3.5 0 0 1 6.8-1.1A2.75 2.75 0 0 1 12 12H4.5Z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
    </svg>
  );
}
