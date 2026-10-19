"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";

import { Button, Input } from "@/components/m1/controls";
import { Eyebrow, Panel, Tag } from "@/components/ui";
import { listPatients, type PatientSummary } from "@/lib/api";
import { m2, STATUS_HINTS, type CareContext, type PatientState, type Transaction } from "@/lib/m2";

/** Shared pieces of the M2 console. */

// -- patient selection --------------------------------------------------------------

export function PatientSelect({
  value,
  onChange,
}: {
  value: string | null;
  onChange: (patientId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [patients, setPatients] = useState<PatientSummary[]>([]);
  const [open, setOpen] = useState(!value);

  useEffect(() => {
    const handle = setTimeout(() => {
      listPatients(query || undefined)
        .then((list) => setPatients(list.slice(0, 8)))
        .catch(() => setPatients([]));
    }, 200);
    return () => clearTimeout(handle);
  }, [query]);

  if (!open && value) {
    return (
      <Button variant="secondary" onClick={() => setOpen(true)}>
        Change patient
      </Button>
    );
  }

  return (
    <div className="w-full space-y-2">
      <Input value={query} onChange={setQuery} mono={false} placeholder="Search patients by name…" />
      <div className="max-h-56 space-y-1 overflow-y-auto">
        {patients.map((patient) => (
          <button
            key={patient.patient_id}
            type="button"
            onClick={() => {
              onChange(patient.patient_id);
              setOpen(false);
            }}
            className={`flex w-full items-center gap-2 rounded border px-2.5 py-1.5 text-left transition ${
              value === patient.patient_id
                ? "border-accent-line bg-accent-bg"
                : "border-line bg-surface hover:bg-surface-2"
            }`}
          >
            <span className="min-w-0 flex-1 truncate text-sm text-ink">{patient.name ?? "Unnamed"}</span>
            <span className="shrink-0 text-[11px] text-ink-faint">
              {patient.gender ?? "—"} · {patient.birth_date?.slice(0, 4) ?? "—"}
            </span>
            <span className="shrink-0 font-mono text-[10px] text-ink-faint">{patient.patient_id.slice(0, 8)}</span>
          </button>
        ))}
        {patients.length === 0 && (
          <p className="px-1 py-2 text-xs text-ink-faint">No patients — is the API running on port 8000?</p>
        )}
      </div>
    </div>
  );
}

/** Load (and reload) one patient's M2 state. State for a previously
 *  selected patient is never shown: it is derived away, not reset. */
export function usePatientState(patientId: string | null) {
  const [loaded, setLoaded] = useState<{ id: string; state: PatientState | null; error: string | null } | null>(null);

  const fetchState = useCallback(
    (id: string) =>
      m2.patientState(id).then((result) =>
        setLoaded({
          id,
          state: result.ok ? result.body : null,
          error: result.ok ? null : (result.message ?? `HTTP ${result.status}`),
        }),
      ),
    [],
  );

  useEffect(() => {
    if (patientId) fetchState(patientId);
  }, [patientId, fetchState]);

  const reload = useCallback(() => {
    if (patientId) fetchState(patientId);
  }, [patientId, fetchState]);

  const current = loaded && loaded.id === patientId ? loaded : null;
  return { state: current?.state ?? null, error: current?.error ?? null, reload };
}

// -- status display -------------------------------------------------------------------

const CONTEXT_TONES: Record<string, "accent" | "warn" | "danger" | "neutral"> = {
  LINKED: "accent",
  LINK_REQUESTED: "warn",
  LINK_FAILED: "danger",
  UNLINKED: "neutral",
};

export function ContextStatus({ status }: { status: string }) {
  return <Tag tone={CONTEXT_TONES[status] ?? "neutral"}>{status.replace("_", " ").toLowerCase()}</Tag>;
}

/** Done / missing line in a readiness checklist. */
export function Check({ ok, children, hint }: { ok: boolean; children: ReactNode; hint?: ReactNode }) {
  return (
    <li className="flex gap-2 text-xs leading-relaxed">
      <span
        className={`mt-px flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ${
          ok ? "bg-active text-white" : "border border-warn-line bg-warn-bg text-warn"
        }`}
      >
        {ok ? "✓" : "!"}
      </span>
      <span className="min-w-0">
        <span className={ok ? "text-ink" : "text-warn"}>{children}</span>
        {hint && <span className="block text-[11px] text-ink-faint">{hint}</span>}
      </span>
    </li>
  );
}

export function CareContextTable({
  contexts,
  selected,
  onSelect,
  limit = 12,
}: {
  contexts: CareContext[];
  selected?: string | null;
  onSelect?: (reference: string) => void;
  limit?: number;
}) {
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? contexts : contexts.slice(0, limit);
  const counts = contexts.reduce<Record<string, number>>((acc, c) => {
    acc[c.status] = (acc[c.status] ?? 0) + 1;
    return acc;
  }, {});

  if (contexts.length === 0) {
    return <p className="text-xs text-ink-faint">This patient has no visits, so nothing can be linked.</p>;
  }

  return (
    <div className="space-y-1.5">
      <p className="text-[11px] text-ink-faint">
        {contexts.length} visits ·{" "}
        {Object.entries(counts)
          .map(([status, n]) => `${n} ${status.replace("_", " ").toLowerCase()}`)
          .join(" · ")}
      </p>
      <div className="max-h-72 divide-y divide-line overflow-y-auto rounded border border-line">
        {shown.map((context) => (
          <button
            key={context.referenceNumber}
            type="button"
            disabled={!onSelect}
            onClick={() => onSelect?.(context.referenceNumber)}
            className={`flex w-full items-center gap-2 px-2.5 py-1.5 text-left ${
              onSelect ? "hover:bg-surface-2" : "cursor-default"
            } ${selected === context.referenceNumber ? "bg-accent-bg" : ""}`}
          >
            <span className="min-w-0 flex-1 truncate text-xs text-ink">{context.display}</span>
            <ContextStatus status={context.status} />
          </button>
        ))}
      </div>
      {contexts.length > limit && (
        <button type="button" onClick={() => setShowAll((v) => !v)} className="text-[11px] text-accent hover:underline">
          {showAll ? "Show fewer" : `Show all ${contexts.length}`}
        </button>
      )}
    </div>
  );
}

// -- the activity log --------------------------------------------------------------------

/** Wall-clock time, with the date only when it isn't today. A live log is
 *  read in seconds, not days. */
export function clock(value: string | null | undefined): string {
  if (!value) return "—";
  const when = new Date(value);
  if (Number.isNaN(when.getTime())) return value;
  const time = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return when.toDateString() === new Date().toDateString() ? time : `${when.toLocaleDateString()} ${time}`;
}

const BAD = /FAIL|ERROR|REFUSED|REJECTED|NO_MATCH/;
const GOOD = /SENT|GENERATED|LINKED|CONFIRMED|STORED|TRANSFERRED|ACCEPTED|NOTIFIED|DELETED/;

function rowTone(t: Transaction): "danger" | "warn" | "accent" | "neutral" {
  if (BAD.test(t.status) || (t.error_code && !GOOD.test(t.status))) return "danger";
  if (t.error_code) return "warn"; // an error *reply* we sent on purpose, e.g. no match
  if (GOOD.test(t.status)) return "accent";
  return "neutral";
}

/**
 * Every exchange with ABDM, newest first, polled while live.
 *
 * NHA's guidance: keep every exchange, and treat the *first* refusal as the
 * authoritative one — a retry is answered "duplicate", which hides the cause.
 * So rows are never collapsed, and an error code always carries its hint.
 */
export function ActivityLog({ patientId, title = "ABDM activity" }: { patientId?: string | null; title?: string }) {
  const [rows, setRows] = useState<Transaction[]>([]);
  const [live, setLive] = useState(true);
  const [mine, setMine] = useState(true);
  const [reachable, setReachable] = useState(true);

  const filter = mine && patientId ? patientId : undefined;

  const load = useCallback(
    () =>
      m2.transactions(filter).then((result) => {
        setReachable(result.status !== 0);
        if (result.ok) setRows(result.body);
      }),
    [filter],
  );

  useEffect(() => {
    load();
    if (!live) return;
    const handle = setInterval(load, 4000);
    return () => clearInterval(handle);
  }, [load, live]);

  return (
    <Panel className="overflow-hidden">
      <header className="flex flex-wrap items-center gap-2 border-b border-line bg-surface-2 px-4 py-2">
        <Eyebrow>{title}</Eyebrow>
        {live && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-active" title="refreshing every 4s" />}
        <div className="ml-auto flex items-center gap-3 text-[11px] text-ink-muted">
          {patientId && (
            <label className="flex items-center gap-1">
              <input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} />
              this patient only
            </label>
          )}
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} />
            live
          </label>
          <button type="button" onClick={load} className="text-accent hover:underline">
            refresh
          </button>
        </div>
      </header>
      {!reachable ? (
        <p className="px-4 py-3 text-xs text-danger">Cannot reach the API.</p>
      ) : rows.length === 0 ? (
        <p className="px-4 py-3 text-xs text-ink-faint">
          Nothing yet. Exchanges with ABDM appear here as they happen — including ones ABDM starts from the PHR app.
        </p>
      ) : (
        <ul className="max-h-96 divide-y divide-line overflow-y-auto">
          {rows.map((t) => {
            const tone = rowTone(t);
            const hint = (t.error_code && STATUS_HINTS[t.error_code]) || STATUS_HINTS[t.status];
            return (
              <li key={t.id} className="px-4 py-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-[11px] text-ink">{t.operation}</span>
                  <span className="text-[10px] text-ink-faint">→</span>
                  <Tag tone={tone}>{t.status}</Tag>
                  {t.error_code && <span className="font-mono text-[10px] text-danger">{t.error_code}</span>}
                  <span className="ml-auto shrink-0 text-[10px] tabular-nums text-ink-faint" title={t.created_at}>
                    {clock(t.updated_at)}
                  </span>
                </div>
                {t.error_message && <p className="mt-0.5 text-[11px] text-ink-muted">{t.error_message}</p>}
                {hint && <p className="mt-0.5 text-[11px] text-warn">{hint}</p>}
                <p className="mt-0.5 font-mono text-[10px] text-ink-faint">
                  req {t.request_id.slice(0, 8)}
                  {t.transaction_id ? ` · txn ${t.transaction_id.slice(0, 12)}` : ""}
                  {t.patient_id && !filter ? ` · patient ${t.patient_id.slice(0, 8)}` : ""}
                </p>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
