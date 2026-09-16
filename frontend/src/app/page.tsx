"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AuthScreen } from "@/components/auth/AuthScreen";
import { ProfileGate } from "@/components/auth/ProfileGate";
import { RecordsGate } from "@/components/records/RecordsGate";
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
      header={<SessionBar session={session} onSignOut={signOut} />}
    />
  );
}

function SessionBar({
  session,
  onSignOut,
}: {
  session: AbhaSession;
  onSignOut: () => void;
}) {
  const name = session.account.name;
  const number = session.account.abha_number ?? session.account.abha_address;

  return (
    <header className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2 sm:px-5">
      <span className="text-sm font-semibold tracking-tight text-ink">Kravos Setu</span>

      {session.demo ? (
        <span className="rounded border border-warn-line bg-warn-bg px-1.5 py-px text-[10px] font-medium text-warn">
          demo — no ABHA verified
        </span>
      ) : (
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="rounded border border-active-line bg-active-bg px-1.5 py-px text-[10px] font-medium text-active">
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

      <nav className="ml-auto flex items-center gap-2">
        <Link
          href="/m1"
          className="text-xs font-medium text-ink-muted underline-offset-2 hover:underline"
        >
          M1 console
        </Link>
        <button
          type="button"
          onClick={onSignOut}
          className="rounded border border-line bg-surface px-2 py-1 text-xs font-medium text-ink-muted hover:bg-surface-2"
        >
          {session.demo ? "Exit demo" : "Sign out"}
        </button>
      </nav>
    </header>
  );
}

function Splash() {
  return (
    <div className="flex min-h-dvh items-center justify-center">
      <span className="h-5 w-5 animate-spin rounded-full border-2 border-line-strong border-t-transparent" />
    </div>
  );
}
