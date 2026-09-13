"use client";

import { useState, type ReactNode } from "react";

import type { AbhaCall } from "@/lib/abha";
import type { M1Case } from "@/lib/m1-cases";
import { Eyebrow, Panel, Tag } from "@/components/ui";

/**
 * One step of a flow, plus the evidence it produced.
 *
 * Functional testing against NHA's sheet is someone sitting with the
 * spreadsheet open, doing a thing, and deciding whether the result matches the
 * "Expected Result" column. So a step shows three things and not more: which
 * rows it is evidence for, the control to do the thing, and the raw
 * request/response so the decision is made on what actually crossed the wire
 * rather than on a green tick this UI drew for itself.
 *
 * The response panel is collapsed by default. Expanded, it is the only
 * artefact worth screenshotting for a certification pack.
 */

export function CaseTag({ kase }: { kase: M1Case }) {
  return (
    <Tag tone={kase.mandate === "mandatory" ? "accent" : "neutral"} title={kase.expect}>
      <span className="font-mono">{kase.id}</span>
    </Tag>
  );
}

export function StepCard({
  n,
  title,
  hint,
  cases = [],
  call,
  done,
  children,
}: {
  n: number;
  title: string;
  hint?: ReactNode;
  cases?: M1Case[];
  call?: AbhaCall | null;
  done?: boolean;
  children?: ReactNode;
}) {
  return (
    <Panel className="overflow-hidden">
      <header className="flex flex-wrap items-center gap-2 border-b border-line bg-surface-2 px-4 py-2.5">
        <span
          className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ${
            done
              ? "bg-active text-white"
              : "border border-line-strong text-ink-muted"
          }`}
        >
          {done ? "✓" : n}
        </span>
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        <div className="ml-auto flex flex-wrap gap-1">
          {cases.map((kase) => (
            <CaseTag key={kase.id} kase={kase} />
          ))}
        </div>
      </header>

      <div className="space-y-3 px-4 py-3">
        {hint && <p className="text-xs leading-relaxed text-ink-muted">{hint}</p>}
        {children}
        {call && <CallEvidence call={call} />}
      </div>
    </Panel>
  );
}

export function CallEvidence({ call }: { call: AbhaCall }) {
  const [open, setOpen] = useState(false);
  const tone = call.ok ? "active" : call.status === 0 ? "danger" : "warn";

  return (
    <div className="rounded border border-line bg-surface-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <span
          className={`inline-flex shrink-0 items-center rounded border px-1.5 py-px font-mono text-[10px] font-semibold ${
            tone === "active"
              ? "border-active-line bg-active-bg text-active"
              : tone === "danger"
                ? "border-danger-line bg-danger-bg text-danger"
                : "border-warn-line bg-warn-bg text-warn"
          }`}
        >
          {call.status || "ERR"}
        </span>
        <span className="truncate font-mono text-[11px] text-ink-muted">
          {call.method} {call.path}
        </span>
        {call.abdmCode && (
          <span className="shrink-0 font-mono text-[10px] text-ink-faint">
            {call.abdmCode}
          </span>
        )}
        <span className="ml-auto shrink-0 text-[10px] tabular-nums text-ink-faint">
          {call.ms.toFixed(0)} ms
        </span>
        <span className="shrink-0 text-[10px] text-ink-faint">{open ? "hide" : "show"}</span>
      </button>

      {/* Field errors are shown whether or not the raw panel is expanded.
          They are the actionable part of an ABDM 400 -- "txnId: Invalid
          Transaction Id" tells the tester their transaction expired, where
          the status code alone sends them guessing. */}
      {call.fields && call.fields.length > 0 ? (
        <ul className="space-y-0.5 px-3 pb-2">
          {call.fields.map((field, i) => (
            <li key={i} className="flex gap-1.5 text-xs">
              {field.attribute && (
                <code className="shrink-0 font-mono text-danger/70">{field.attribute}</code>
              )}
              <span className="text-danger">{field.message}</span>
            </li>
          ))}
        </ul>
      ) : (
        call.message &&
        !open && (
          <p className={`px-3 pb-2 text-xs ${call.ok ? "text-ink-muted" : "text-danger"}`}>
            {call.message}
          </p>
        )
      )}

      {open && (
        <div className="space-y-2 border-t border-line px-3 py-2">
          {call.requestBody != null && (
            <div>
              <Eyebrow>Request</Eyebrow>
              <Json value={redact(call.requestBody)} />
              <p className="mt-1 text-[10px] text-ink-faint">
                Aadhaar, OTP and mobile values are masked here. They are
                RSA-encrypted before leaving the API — this panel shows the
                shape, not the secret.
              </p>
            </div>
          )}
          <div>
            <Eyebrow>Response</Eyebrow>
            <Json value={call.body} />
          </div>
        </div>
      )}
    </div>
  );
}

function Json({ value }: { value: unknown }) {
  return (
    <pre className="mt-1 max-h-56 overflow-auto rounded border border-line bg-surface px-2 py-1.5 font-mono text-[11px] leading-relaxed text-ink-muted">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

/** A test console that echoes an Aadhaar number back onto the screen is a
 *  console nobody can screenshot for a certification pack. */
const SENSITIVE = new Set(["aadhaar", "otp", "mobile", "identifier", "loginId"]);

function redact(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([k, v]) => [
        k,
        SENSITIVE.has(k) && typeof v === "string"
          ? `${"•".repeat(Math.max(0, v.length - 4))}${v.slice(-4)}`
          : redact(v),
      ]),
    );
  }
  return value;
}
