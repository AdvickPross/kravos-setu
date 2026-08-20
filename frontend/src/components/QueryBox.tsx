"use client";

import { useEffect, useRef, useState } from "react";

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
    <div className="space-y-2">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit(value);
        }}
        className="flex gap-2"
      >
        <div className="relative flex-1">
          <input
            ref={inputRef}
            type="text"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="Ask about this patient…"
            disabled={disabled}
            aria-label="Clinical question"
            className="w-full rounded-md border border-line bg-surface px-3 py-2 pr-8 text-sm text-ink placeholder:text-ink-faint disabled:opacity-60"
          />
          {!value && (
            <kbd className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 rounded border border-line px-1 text-[10px] text-ink-faint">
              /
            </kbd>
          )}
        </div>
        <button
          type="submit"
          disabled={disabled || !value.trim()}
          className="rounded-md bg-ink px-4 py-2 text-sm font-medium text-bg transition-opacity disabled:opacity-40"
        >
          Ask
        </button>
      </form>

      <div className="flex flex-wrap gap-1.5">
        {EXAMPLES.map((example) => (
          <button
            key={example}
            type="button"
            disabled={disabled}
            onClick={() => {
              setValue(example);
              submit(example);
            }}
            className="rounded-full border border-line bg-surface px-2.5 py-1 text-[11px] text-ink-muted transition-colors hover:border-line-strong hover:text-ink disabled:opacity-50"
          >
            {example}
          </button>
        ))}
      </div>
    </div>
  );
}
