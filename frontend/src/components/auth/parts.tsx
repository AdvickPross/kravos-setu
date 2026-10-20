"use client";

import { useEffect, useState, type ReactNode } from "react";

import { abha } from "@/lib/abha";

/** Shared pieces for sign-in and sign-up.
 *
 * These live apart from `components/ui.tsx` — that file is the clinical
 * read-only surface and contains no inputs at all. An auth screen has
 * different needs: large touch targets, one visible error at a time, and a
 * sense of progress through a flow that can run to six steps.
 */

export function Labelled({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-faint">
        {label}
      </span>
      <div className="mt-1.5">{children}</div>
      {hint && <span className="mt-1.5 block text-[11px] leading-relaxed text-ink-faint">{hint}</span>}
    </label>
  );
}

export function TextInput({
  value,
  onChange,
  placeholder,
  maxLength,
  autoFocus,
  onEnter,
  numeric = true,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  maxLength?: number;
  autoFocus?: boolean;
  onEnter?: () => void;
  numeric?: boolean;
  disabled?: boolean;
}) {
  return (
    <input
      value={value}
      disabled={disabled}
      autoFocus={autoFocus}
      maxLength={maxLength}
      inputMode={numeric ? "numeric" : "text"}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => e.key === "Enter" && onEnter?.()}
      className="w-full rounded-lg border border-line bg-bg px-3.5 py-2.5 font-mono text-sm tracking-wide text-ink outline-none transition placeholder:font-sans placeholder:tracking-normal placeholder:text-ink-faint/70 focus:border-accent-line focus:ring-4 focus:ring-accent-bg disabled:opacity-50"
    />
  );
}

/** The OTP box. Wide tracking and a big face because it is transcribed from a
 *  phone held in the other hand — the one input where legibility beats
 *  density. */
export function OtpInput({
  value,
  onChange,
  onEnter,
}: {
  value: string;
  onChange: (v: string) => void;
  onEnter?: () => void;
}) {
  return (
    <input
      value={value}
      autoFocus
      maxLength={6}
      inputMode="numeric"
      placeholder="000000"
      onChange={(e) => onChange(e.target.value.replace(/\D/g, ""))}
      onKeyDown={(e) => e.key === "Enter" && onEnter?.()}
      className="w-full rounded-lg border border-line bg-bg py-3 text-center font-mono text-2xl tracking-[0.5em] text-ink outline-none transition placeholder:text-ink-faint/40 focus:border-accent-line focus:ring-4 focus:ring-accent-bg"
    />
  );
}

export function Primary({
  children,
  onClick,
  busy,
  disabled,
}: {
  children: ReactNode;
  onClick: () => void;
  busy?: boolean;
  disabled?: boolean;
}) {
  const off = disabled || busy;
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={off}
      // A disabled primary at low opacity still reads as "filled and ready" on
      // a dark ground. Off state drops to a flat outline instead, so the
      // difference is a change of kind rather than of brightness.
      className={
        off
          ? "w-full cursor-not-allowed rounded-lg border border-line bg-surface-2 px-3 py-2.5 text-sm font-semibold text-ink-faint"
          : "w-full rounded-lg bg-accent px-3 py-2.5 text-sm font-semibold text-on-accent shadow-sm transition hover:brightness-110 active:brightness-95"
      }
    >
      <span className="inline-flex items-center justify-center gap-2">
        {busy && (
          <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
        )}
        {children}
      </span>
    </button>
  );
}

export function Secondary({
  children,
  onClick,
  disabled,
}: {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm font-medium text-ink-muted transition hover:border-line-strong hover:text-ink disabled:opacity-40"
    >
      {children}
    </button>
  );
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex gap-1 rounded-lg border border-line bg-bg p-1">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className={`flex-1 rounded-md px-2 py-1.5 text-xs font-medium transition ${
            value === option.value
              ? "bg-accent-bg text-accent ring-1 ring-accent-line"
              : "text-ink-faint hover:text-ink-muted"
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function ErrorLine({ children }: { children: ReactNode }) {
  return (
    <p
      role="alert"
      className="flex gap-2 rounded-lg border border-danger-line bg-danger-bg px-3 py-2.5 text-xs leading-relaxed text-danger"
    >
      <span aria-hidden className="mt-px shrink-0 font-bold">!</span>
      <span>{children}</span>
    </p>
  );
}

export function Banner({
  tone,
  children,
}: {
  tone: "warn" | "danger" | "info";
  children: ReactNode;
}) {
  const tones = {
    danger: "border-danger-line bg-danger-bg text-danger",
    warn: "border-warn-line bg-warn-bg text-warn",
    info: "border-accent-line bg-accent-bg text-accent",
  } as const;
  return (
    <p className={`rounded-lg border px-3 py-2.5 text-xs leading-relaxed ${tones[tone]}`}>
      {children}
    </p>
  );
}

/** Progress through a flow. Sign-up runs to five steps and a person part-way
 *  through an Aadhaar verification should be able to see how much is left. */
export function Steps({ total, current }: { total: number; current: number }) {
  return (
    <div className="flex items-center gap-1.5" aria-label={`Step ${current} of ${total}`}>
      {Array.from({ length: total }, (_, i) => (
        <span
          key={i}
          className={`h-1 flex-1 rounded-full transition-colors ${
            i < current ? "bg-accent" : "bg-line"
          }`}
        />
      ))}
    </div>
  );
}

/**
 * Resend, driven by the server's budget.
 *
 * A local countdown drifts from the API the moment the page is reloaded or
 * opened in a second tab, and then the button invites a click the server will
 * refuse. `/api/abha/otp/state` is the authority; this only renders it.
 */
export function Resend({ txnId, onResend }: { txnId: string; onResend: () => void }) {
  const [left, setLeft] = useState(60);
  const [remaining, setRemaining] = useState(2);

  useEffect(() => {
    if (!txnId) return;
    let cancelled = false;
    let handle: ReturnType<typeof setInterval> | undefined;

    const read = async () => {
      const call = await abha.otpState(txnId);
      if (cancelled || !call.ok) return;
      const seconds = Math.ceil(call.body.seconds_until_allowed);
      setLeft(seconds);
      setRemaining(call.body.resends_remaining);
      // Stop once there is nothing left to count down. Polling every second
      // for as long as the screen is open produced 466 requests against one
      // transaction in a single session, all of them answering the same thing.
      if (seconds <= 0 && handle) clearInterval(handle);
    };

    read();
    handle = setInterval(read, 1000);
    return () => {
      cancelled = true;
      if (handle) clearInterval(handle);
    };
  }, [txnId]);

  if (remaining === 0)
    return <span className="text-xs text-ink-faint">No resends left</span>;
  if (left > 0)
    return (
      <span className="text-xs tabular-nums text-ink-faint">Resend in {left}s</span>
    );
  return (
    <button
      type="button"
      onClick={onResend}
      className="text-xs font-medium text-accent underline-offset-2 hover:underline"
    >
      Resend OTP
    </button>
  );
}

/**
 * A failure, with the one action that resolves it.
 *
 * Replaces the single red line. The line was honest and inert: it repeated
 * ABDM's sentence and left the person facing the same screen with the same
 * buttons, which for a UIDAI lockout means retrying into a wall. What is
 * offered here comes from `explain()`, so the recovery and the wording cannot
 * drift apart.
 */
export function Failure({
  explained,
  onRetry,
  onRestart,
  onCreate,
}: {
  explained: import("@/lib/abha-errors").Explained;
  onRetry?: () => void;
  onRestart?: () => void;
  onCreate?: () => void;
}) {
  const { title, detail, recovery } = explained;
  // A lockout is the only one that is not the user's mistake to correct, so it
  // is the only one styled as information rather than as an error.
  const tone =
    recovery === "wait" || recovery === "create"
      ? "border-warn-line bg-warn-bg text-warn"
      : "border-danger-line bg-danger-bg text-danger";

  return (
    <div role="alert" className={`rounded-lg border px-3.5 py-3 ${tone}`}>
      <p className="text-xs font-semibold">{title}</p>
      <p className="mt-1 text-xs leading-relaxed opacity-90">{detail}</p>

      {recovery === "create" && onCreate && (
        <button
          type="button"
          onClick={onCreate}
          className="mt-2.5 rounded-md border border-current px-2.5 py-1 text-xs font-semibold transition hover:bg-current/10"
        >
          Create an ABHA
        </button>
      )}
      {(recovery === "restart" || recovery === "wait") && onRestart && (
        <button
          type="button"
          onClick={onRestart}
          className="mt-2.5 rounded-md border border-current px-2.5 py-1 text-xs font-semibold transition hover:bg-current/10"
        >
          {recovery === "wait" ? "Start over" : "Request a new OTP"}
        </button>
      )}
      {recovery === "retry" && onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-2.5 rounded-md border border-current px-2.5 py-1 text-xs font-semibold transition hover:bg-current/10"
        >
          Try again
        </button>
      )}
    </div>
  );
}

/**
 * How long this OTP has been sitting unused.
 *
 * ABDM closes an enrolment transaction after a few minutes and does not say
 * how many. What it says when you verify against a closed one is *"Maximum
 * number of attempts for OTP match is exceeded **or OTP is not generated**"* —
 * one error code covering two unrelated situations, the second of which reads
 * exactly like a lockout and is not one. Waiting makes it worse.
 *
 * So the screen shows the age instead of pretending to know the deadline, and
 * nudges toward a fresh OTP once it is old enough to be the likely cause.
 */
export function OtpAge({ sentAt, onRefresh }: { sentAt: number | null; onRefresh: () => void }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!sentAt) return;
    const handle = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(handle);
  }, [sentAt]);

  if (!sentAt) return null;
  const seconds = Math.floor((now - sentAt) / 1000);
  if (seconds < 120) return null;

  const minutes = Math.floor(seconds / 60);
  return (
    <div className="rounded-lg border border-warn-line bg-warn-bg px-3.5 py-3">
      <p className="text-xs font-semibold text-warn">
        This OTP is {minutes} minute{minutes === 1 ? "" : "s"} old
      </p>
      <p className="mt-1 text-xs leading-relaxed text-warn/90">
        ABDM closes a verification after a few minutes, and verifying against a
        closed one fails with a message about attempt limits that has nothing
        to do with attempts. Get a fresh code and enter it straight away.
      </p>
      <button
        type="button"
        onClick={onRefresh}
        className="mt-2.5 rounded-md border border-current px-2.5 py-1 text-xs font-semibold text-warn transition hover:bg-current/10"
      >
        Start over with a fresh OTP
      </button>
    </div>
  );
}
