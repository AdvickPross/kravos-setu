"use client";

import { useEffect, useState, type ReactNode } from "react";

import { abha } from "@/lib/abha";

/** Form primitives for the M1 console.
 *
 * Kept apart from `components/ui.tsx`, which is the clinical read-only surface
 * and has no inputs in it at all. Mixing the two would mean the brief view
 * starts importing button styles it never uses.
 */

export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: ReactNode;
  error?: string | null;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
        {label}
      </span>
      <div className="mt-1">{children}</div>
      {error ? (
        <span className="mt-1 block text-[11px] text-danger">{error}</span>
      ) : hint ? (
        <span className="mt-1 block text-[11px] text-ink-faint">{hint}</span>
      ) : null}
    </label>
  );
}

export function Input({
  value,
  onChange,
  placeholder,
  disabled,
  mono = true,
  maxLength,
  inputMode,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  disabled?: boolean;
  mono?: boolean;
  maxLength?: number;
  inputMode?: "numeric" | "text";
}) {
  return (
    <input
      value={value}
      disabled={disabled}
      maxLength={maxLength}
      inputMode={inputMode}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      className={`w-full rounded border border-line bg-surface px-2.5 py-1.5 text-sm text-ink outline-none placeholder:text-ink-faint focus:border-accent-line focus:ring-2 focus:ring-accent-bg disabled:bg-surface-2 disabled:text-ink-faint ${
        mono ? "font-mono tracking-wide" : ""
      }`}
    />
  );
}

export function Button({
  children,
  onClick,
  disabled,
  busy,
  variant = "primary",
}: {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  busy?: boolean;
  variant?: "primary" | "secondary" | "danger";
}) {
  const styles = {
    primary: "bg-accent text-white border-accent hover:opacity-90",
    secondary: "bg-surface text-ink border-border-strong hover:bg-surface-2",
    danger: "bg-danger-bg text-danger border-danger-line hover:opacity-90",
  } as const;
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busy}
      className={`inline-flex items-center gap-1.5 rounded border px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-45 ${styles[variant]}`}
    >
      {busy && (
        <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
      )}
      {children}
    </button>
  );
}

export function Choice<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string; hint?: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          title={option.hint}
          onClick={() => onChange(option.value)}
          className={`rounded border px-2.5 py-1 text-xs font-medium transition ${
            value === option.value
              ? "border-accent-line bg-accent-bg text-accent"
              : "border-line bg-surface text-ink-muted hover:bg-surface-2"
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function Note({
  tone = "warn",
  children,
}: {
  tone?: "warn" | "danger" | "info";
  children: ReactNode;
}) {
  const tones = {
    warn: "border-warn-line bg-warn-bg text-warn",
    danger: "border-danger-line bg-danger-bg text-danger",
    info: "border-accent-line bg-accent-bg text-accent",
  } as const;
  return (
    <p className={`rounded border px-3 py-2 text-xs leading-relaxed ${tones[tone]}`}>
      {children}
    </p>
  );
}

/**
 * Resend button wired to the server's budget, not to a timer of its own.
 *
 * CRT_ABHA_106 caps resends at two, sixty seconds apart. The obvious
 * implementation is a local countdown, which drifts from the server's view the
 * moment the page is reloaded or opened in a second tab — and then the button
 * says "resend" while the API refuses. `/api/abha/otp/state` is the authority;
 * this polls it.
 */
export function ResendButton({
  txnId,
  onResend,
  busy,
}: {
  txnId: string;
  onResend: () => void;
  busy?: boolean;
}) {
  const [state, setState] = useState<{ left: number; remaining: number } | null>(null);

  useEffect(() => {
    if (!txnId) return;
    let cancelled = false;
    const read = async () => {
      const result = await abha.otpState(txnId);
      if (cancelled || !result.ok) return;
      setState({
        left: Math.ceil(result.body.seconds_until_allowed),
        remaining: result.body.resends_remaining,
      });
    };
    read();
    const handle = setInterval(read, 1000);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
  }, [txnId]);

  const exhausted = state?.remaining === 0;
  const waiting = (state?.left ?? 0) > 0;

  return (
    <div className="flex items-center gap-2">
      <Button variant="secondary" onClick={onResend} disabled={exhausted || waiting} busy={busy}>
        {exhausted
          ? "No resends left"
          : waiting
            ? `Resend in ${state?.left}s`
            : "Resend OTP"}
      </Button>
      {state && (
        <span className="text-[11px] text-ink-faint">
          {state.remaining} of 2 remaining
        </span>
      )}
    </div>
  );
}
