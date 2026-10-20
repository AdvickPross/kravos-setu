"use client";

import { useEffect, useRef, useState } from "react";

import { SearchIcon } from "./icons";

/** Deliberately spans the behaviours worth demonstrating: a whole-list
 *  request, a specific drug allergy, a multi-code lab, and a question whose
 *  honest answer is "nothing resolved". */
const EXAMPLES = [
  "does she have any allergies",
  "is she allergic to penicillin",
  "most recent blood glucose",
  "what medications is he on",
  "has he ever mentioned trouble sleeping",
];

export function QueryBox({
  disabled,
  onSubmit,
}: {
  disabled: boolean;
  onSubmit: (query: string) => void;
}) {
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // "/" focuses the box, so a repeat query never needs the mouse.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing = target?.tagName === "INPUT" || target?.tagName === "TEXTAREA";
      if (event.key === "/" && !typing) {
        event.preventDefault();
        inputRef.current?.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function submit(query: string) {
    const trimmed = query.trim();
    if (trimmed && !disabled) onSubmit(trimmed);
  }

  return (
    <div className="card space-y-3 p-3 sm:p-4">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit(value);
        }}
        className="flex gap-2"
      >
        <div className="group relative flex-1">
          <SearchIcon
            size={18}
            className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-ink-faint transition-colors group-focus-within:text-accent"
          />
          <input
            ref={inputRef}
            type="text"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="Ask about this patient…"
            disabled={disabled}
            aria-label="Clinical question"
            className="w-full rounded-xl border border-line bg-bg py-3 pl-11 pr-10 text-[15px] text-ink outline-none transition placeholder:text-ink-faint focus:border-accent-line focus:ring-4 focus:ring-accent-bg disabled:opacity-60"
          />
          {!value && (
            <kbd className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 rounded-md border border-line bg-surface px-1.5 py-px font-mono text-[10px] text-ink-faint">
              /
            </kbd>
          )}
        </div>
        <button
          type="submit"
          disabled={disabled || !value.trim()}
          className="btn-primary inline-flex items-center gap-1.5 rounded-xl px-5 text-sm font-semibold"
        >
          Ask
        </button>
      </form>

      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-[10px] font-semibold uppercase tracking-[0.09em] text-ink-faint">Try</span>
        {EXAMPLES.map((example) => (
          <button
            key={example}
            type="button"
            disabled={disabled}
            onClick={() => {
              setValue(example);
              submit(example);
            }}
            className="rounded-full border border-line bg-surface-2/70 px-3 py-1 text-xs text-ink-muted transition hover:border-accent-line hover:bg-accent-bg hover:text-accent disabled:opacity-50"
          >
            {example}
          </button>
        ))}
      </div>
    </div>
  );
}
