"use client";

import { useState } from "react";

export function QueryBox({
  disabled,
  onSubmit,
}: {
  disabled: boolean;
  onSubmit: (query: string) => void;
}) {
  const [value, setValue] = useState("");

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        const trimmed = value.trim();
        if (trimmed) onSubmit(trimmed);
      }}
      className="flex gap-2"
    >
      <input
        type="text"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder="Ask a question about this patient…"
        disabled={disabled}
        className="flex-1 rounded border border-zinc-300 bg-white px-3 py-2 text-sm disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900"
      />
      <button
        type="submit"
        disabled={disabled || !value.trim()}
        className="rounded bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900"
      >
        Ask
      </button>
    </form>
  );
}
