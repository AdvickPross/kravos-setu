"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AuthScreen } from "@/components/auth/AuthScreen";
import { ProfileGate } from "@/components/auth/ProfileGate";
import { DemoGate } from "@/components/records/DemoGate";
import { RecordsGate } from "@/components/records/RecordsGate";
import { Brand } from "@/components/ui";
import { Workspace } from "@/components/Workspace";
import { getPatient, type PatientSummary } from "@/lib/api";
import { setLiveToken, useAbhaSession, type AbhaSession } from "@/lib/session";

/**
 * The gate.
 *
 * Three states, in the order a person meets them: sign in with ABHA, see what
 * that identity is and which record it belongs to, then work. No clinical
 * logic lives here — that is `components/Workspace.tsx` — so the entry flow
 * and the clinical surface change independently.
 *
 * Signing in is skippable and the skip is visible. The corpus is 952 synthetic
 * patients and none of them holds a real ABHA, so a hard gate would make the
 * product impossible to show. The escape hatch sets `demo: true` and the
 * header says so on every screen after it, because the difference between a
 * verified national identity and a demo selection is exactly the kind of thing
 * that stops being obvious ten minutes into a walkthrough.
 */
export default function Home() {
  const { session, ready, signIn, update, signOut } = useAbhaSession();
  const [patient, setPatient] = useState<PatientSummary | null>(null);
  const [loadingPatient, setLoadingPatient] = useState(false);

  // Resolve the tagged patient id into the summary the workspace opens on.
  useEffect(() => {
    const id = session?.patientId;
    if (!id) {
      setPatient(null);
      return;
    }
    setLoadingPatient(true);
    getPatient(id)
      .then(setPatient)
      .catch(() => setPatient(null))
      .finally(() => setLoadingPatient(false));
  }, [session?.patientId]);

  if (!ready) return <Splash />;

  if (!session) {
    return (
      <AuthScreen
        onAuthenticated={(next, token) => {
          setLiveToken(token);
          signIn(next);
        }}
        onSkip={() =>
          signIn({
            account: {},
            via: "demo — no ABHA verified",
            signedInAt: new Date().toISOString(),
            patientId: null,
            demo: true,
          })
        }
      />
    );
  }

  // A verified ABHA that has not been matched to a record yet.
  if (!session.demo && !session.patientId) {
    return (
      <ProfileGate
        session={session}
        onSignOut={signOut}
        onContinue={(patientId) => update({ ...session, patientId })}
      />
    );
  }

  if (loadingPatient) return <Splash />;

  // The demo path has no ABHA to identify anyone, so the same question --
  // whose records are these? -- is answered locally: pick a synthetic
  // patient, or create one and give it a history from a scan.
  if (session.demo && !session.patientId) {
    return (
      <DemoGate
        onReady={(chosen) => {
          setPatient(chosen);
          update({ ...session, patientId: chosen.patient_id, recordsDone: true });
        }}
      />
    );
  }

  // Between an identity and a question sits the records the question will be
  // answered from. Offered once per session, then remembered.
  if (session.patientId && !session.recordsDone && !session.demo) {
    return (
      <RecordsGate
        session={session}
        patientId={session.patientId}
        onDone={() => update({ ...session, recordsDone: true })}
        onSkip={() => update({ ...session, recordsDone: true })}
      />
    );
  }

  return (
    <Workspace
      initialPatient={patient}
      header={
        <SessionBar
          session={session}
          onSignOut={signOut}
          onBackToPatients={
            session.demo
              ? () => update({ ...session, patientId: null, recordsDone: false })
              : undefined
          }
        />
      }
    />
  );
}

function SessionBar({
  session,
  onSignOut,
  onBackToPatients,
}: {
  session: AbhaSession;
  onSignOut: () => void;
  onBackToPatients?: () => void;
}) {
  const name = session.account.name;
  const number = session.account.abha_number ?? session.account.abha_address;

  return (
    <header className="sticky top-0 z-20 border-b border-line bg-surface/75 backdrop-blur-xl">
      <div className="mx-auto flex w-full max-w-[90rem] flex-wrap items-center gap-3 px-4 py-2.5 sm:px-6">
        <Brand />

        <span aria-hidden className="hidden h-5 w-px bg-line sm:block" />

        {session.demo ? (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-warn-line bg-warn-bg px-2.5 py-0.5 text-[11px] font-medium text-warn">
            <span aria-hidden className="size-1.5 rounded-full bg-warn" />
            <span>demo — no ABHA verified</span>
          </span>
        ) : (
          <span className="flex min-w-0 items-center gap-1.5">
            <span className="rounded-full border border-active-line bg-active-bg px-2.5 py-0.5 text-[11px] font-medium text-active">
              ABHA verified
            </span>
            {name && <span className="truncate text-xs text-ink-muted">{name}</span>}
            {number && (
              <span className="hidden truncate font-mono text-[11px] text-ink-faint sm:inline">
                {number}
              </span>
            )}
          </span>
        )}

        <nav className="ml-auto flex items-center gap-1">
          {onBackToPatients && (
            <button
              type="button"
              onClick={onBackToPatients}
              className="rounded-lg px-2.5 py-1.5 text-xs font-medium text-ink-muted transition hover:bg-surface-2 hover:text-ink"
            >
              ← Choose patient
            </button>
          )}
          <Link
            href="/m1"
            className="rounded-lg px-2.5 py-1.5 text-xs font-medium text-ink-muted transition hover:bg-surface-2 hover:text-ink"
          >
            M1 console
          </Link>
          <Link
            href="/m2"
            className="rounded-lg px-2.5 py-1.5 text-xs font-medium text-ink-muted transition hover:bg-surface-2 hover:text-ink"
          >
            M2 console
          </Link>
          <button
            type="button"
            onClick={onSignOut}
            className="ml-1 rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-medium text-ink transition hover:border-line-strong hover:bg-surface-2"
          >
            {session.demo ? "Exit demo" : "Sign out"}
          </button>
        </nav>
      </div>
    </header>
  );
}

function Splash() {
  return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-4">
      <span className="brand-mark flex size-11 animate-pulse items-center justify-center rounded-2xl text-base font-bold">
        K
      </span>
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-line-strong border-t-transparent" />
    </div>
  );
}
