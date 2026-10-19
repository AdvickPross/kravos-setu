"use client";

import { useCallback, useSyncExternalStore } from "react";

/**
 * A localStorage value as React state.
 *
 * `useSyncExternalStore` rather than reading in an effect: the server render
 * and the first client render both see `null` (no hydration mismatch), and
 * the stored value arrives without a setState-in-effect cascade. Blocked
 * storage (private window, disabled site data) falls back to memory, so the
 * page still works and only loses persistence.
 */

const memory = new Map<string, string | null>();
const EVENT = "kravos-local-storage";

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return memory.get(key) ?? null;
  }
}

function subscribe(callback: () => void): () => void {
  window.addEventListener("storage", callback);
  window.addEventListener(EVENT, callback);
  return () => {
    window.removeEventListener("storage", callback);
    window.removeEventListener(EVENT, callback);
  };
}

export function useLocalStorage(key: string): [string | null, (value: string | null) => void] {
  const value = useSyncExternalStore(
    subscribe,
    () => read(key),
    () => null,
  );

  const set = useCallback(
    (next: string | null) => {
      try {
        if (next === null) window.localStorage.removeItem(key);
        else window.localStorage.setItem(key, next);
      } catch {
        memory.set(key, next);
      }
      window.dispatchEvent(new Event(EVENT));
    },
    [key],
  );

  return [value, set];
}

/** The URL hash (without '#') as state, so console sections are linkable and
 *  the back button moves between them. */
export function useHash(): [string, (value: string) => void] {
  const value = useSyncExternalStore(
    (callback) => {
      window.addEventListener("hashchange", callback);
      return () => window.removeEventListener("hashchange", callback);
    },
    () => window.location.hash.replace(/^#/, ""),
    () => "",
  );
  const set = useCallback((next: string) => {
    window.location.hash = next;
  }, []);
  return [value, set];
}
