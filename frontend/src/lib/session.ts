"use client";

import { useCallback, useEffect, useState } from "react";

import type { AbhaAccount } from "./abha";

/**
 * Who is signed in, for as long as this tab is open.
 *
 * **sessionStorage, not localStorage.** The difference matters here in a way
 * it usually does not: this runs on a shared terminal at a registration desk,
 * and localStorage would keep a patient's ABHA identity alive across browser
 * restarts, for the next person who sits down. sessionStorage dies with the
 * tab, which is the correct lifetime for a walk-up kiosk.
 *
 * The X-token is held in memory only and deliberately never written to
 * storage. It is a live credential against a government API; a token in
 * sessionStorage survives a refresh, which sounds convenient and means a
 * credential sitting in a place any script on the page can read. The cost is
 * that a refresh drops the ability to re-fetch the profile — so the profile
 * itself is persisted and the token is not, and the session stays usable for
 * display while losing the power to act.
 */

export type AbhaSession = {
  account: AbhaAccount;
  /** How this person proved who they were — shown on the profile. */
  via: string;
  signedInAt: string;
  /** The Kravos patient this ABHA is tagged to, once resolved. */
  patientId: string | null;
  /** True when the user chose to explore the corpus without signing in. */
  demo?: boolean;
  /** Set once the records step has been answered -- uploaded, or skipped.
   *  Without it the gate would reappear on every render after tagging. */
  recordsDone?: boolean;
};

const KEY = "kravos.abha.session";

let liveToken: string | null = null;

export function setLiveToken(token: string | null) {
  liveToken = token;
}

export function getLiveToken(): string | null {
  return liveToken;
}

export function useAbhaSession() {
  const [session, setSession] = useState<AbhaSession | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    try {
      const saved = sessionStorage.getItem(KEY);
      if (saved) setSession(JSON.parse(saved));
    } catch {
      /* private windows and blocked storage must not break sign-in */
    }
    setReady(true);
  }, []);

  const save = useCallback((next: AbhaSession | null) => {
    setSession(next);
    try {
      if (next) sessionStorage.setItem(KEY, JSON.stringify(next));
      else sessionStorage.removeItem(KEY);
    } catch {
      /* ignore */
    }
  }, []);

  const signOut = useCallback(() => {
    setLiveToken(null);
    save(null);
  }, [save]);

  return { session, ready, signIn: save, update: save, signOut };
}
