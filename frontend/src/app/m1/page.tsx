"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { CreateFlow } from "@/components/m1/CreateFlow";
import { TagFlow } from "@/components/m1/TagFlow";
import { VerifyFlow } from "@/components/m1/VerifyFlow";
import { Eyebrow, Panel, Tag } from "@/components/ui";
import { abha, type AbhaStatus } from "@/lib/abha";
import { ALL_CASES, M1_GROUPS, type M1Group } from "@/lib/m1-cases";

type Verdict = "pass" | "fail";

/**
 * The M1 functional-testing console.
 *
 * NHA's sheet is executed by a human: do the thing, compare against the
 * "Expected Result" column, write pass or fail. This page is built for exactly
 * that and nothing else — every screen names the test-case IDs it is evidence
 * for, shows the raw request and response, and lets the tester record a
 * verdict next to the row.
 *
 * Verdicts live in localStorage. They are one person's notes during a session,
 * not a shared record: putting them on the server would make them look like an
 * authority they are not, and the artefact that actually matters is the
 * request/response pair each step exposes.
 */

const STORAGE_KEY = "kravos.m1.verdicts";

export default function M1Console() {
  const [group, setGroup] = useState<M1Group["key"]>("create");
  const [status, setStatus] = useState<AbhaStatus | null>(null);
  const [reachable, setReachable] = useState<boolean | null>(null);
  const [verdicts, setVerdicts] = useState<Record<string, Verdict>>({});

  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) setVerdicts(JSON.parse(saved));
    } catch {
      /* a private window or blocked storage must not break the console */
    }
  }, []);

  useEffect(() => {
    abha.status().then((call) => {
      setReachable(call.status !== 0);
      if (call.ok) setStatus(call.body);
    });
  }, []);

  function setVerdict(id: string, verdict: Verdict | null) {
    setVerdicts((prev) => {
      const next = { ...prev };
      if (verdict === null) delete next[id];
      else next[id] = verdict;
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  }

  const active = M1_GROUPS.find((g) => g.key === group)!;
  const passed = ALL_CASES.filter((c) => verdicts[c.id] === "pass").length;
  const failed = ALL_CASES.filter((c) => verdicts[c.id] === "fail").length;

  return (
    <div className="mx-auto w-full max-w-[92rem] p-4 sm:p-5">
      <header className="mb-4 flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-lg font-semibold tracking-tight text-ink">
              M1 — ABHA creation and verification
            </h1>
            {status && (
              <Tag tone={status.environment === "production" ? "danger" : "accent"}>
                {status.environment ?? "unconfigured"}
              </Tag>
            )}
          </div>
          <p className="mt-1 max-w-3xl text-sm leading-relaxed text-ink-muted">
            Walk each NHA test case by hand against the live sandbox. Every step
            names the rows it is evidence for and shows the request and response
            that produced it.
          </p>
        </div>
        <Link
          href="/"
          className="rounded border border-line-strong bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-surface-2"
        >
          ← Clinical view
        </Link>
      </header>

      {reachable === false && (
        <Panel tone="danger" className="mb-4 px-4 py-3">
          <p className="text-sm font-medium text-danger">Cannot reach the API.</p>
          <p className="mt-1 text-xs text-danger/80">
            Start it with{" "}
            <code className="font-mono">uvicorn backend.main:app --port 8000</code>. If it
            is running, the browser may be resolving <code>localhost</code> to IPv6
            while uvicorn listens on IPv4 — set{" "}
            <code className="font-mono">NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000</code>{" "}
            in <code className="font-mono">frontend/.env.local</code>.
          </p>
        </Panel>
      )}

      {status && !status.configured && (
        <Panel tone="warn" className="mb-4 px-4 py-3">
          <p className="text-sm font-medium text-warn">
            No ABDM credentials configured.
          </p>
          <p className="mt-1 text-xs text-warn/90">
            Put <code className="font-mono">ABDM_CLIENT_ID</code> and{" "}
            <code className="font-mono">ABDM_CLIENT_SECRET</code> in{" "}
            <code className="font-mono">.env</code> and restart the API. The tagging
            section below still works — it calls no ABDM endpoint.
          </p>
        </Panel>
      )}

      <div className="flex flex-col gap-5 lg:flex-row">
        <aside className="w-full shrink-0 lg:w-72">
          <Panel className="sticky top-4 overflow-hidden">
            <div className="border-b border-line bg-surface-2 px-3 py-2">
              <Eyebrow>Test cases</Eyebrow>
              <p className="mt-0.5 text-[11px] text-ink-faint">
                {passed} passed · {failed} failed · {ALL_CASES.length - passed - failed}{" "}
                untested
              </p>
            </div>
            <nav className="divide-y divide-line">
              {M1_GROUPS.map((g) => {
                const groupPassed = g.cases.filter((c) => verdicts[c.id] === "pass").length;
                return (
                  <div key={g.key}>
                    <button
                      type="button"
                      onClick={() => setGroup(g.key)}
                      className={`flex w-full items-center gap-2 px-3 py-2 text-left transition ${
                        group === g.key ? "bg-accent-bg" : "hover:bg-surface-2"
                      }`}
                    >
                      <span className="shrink-0 font-mono text-[10px] text-ink-faint">
                        {g.section}
                      </span>
                      <span
                        className={`min-w-0 flex-1 truncate text-sm font-medium ${
                          group === g.key ? "text-accent" : "text-ink"
                        }`}
                      >
                        {g.title}
                      </span>
                      <span className="shrink-0 text-[10px] tabular-nums text-ink-faint">
                        {groupPassed}/{g.cases.length}
                      </span>
                    </button>

                    {group === g.key && (
                      <ul className="space-y-1 bg-surface px-2 pb-2 pt-1">
                        {g.cases.map((kase) => (
                          <li
                            key={kase.id}
                            className="rounded border border-line px-2 py-1.5"
                          >
                            <p className="font-mono text-[10px] text-ink-faint">{kase.id}</p>
                            <p className="text-[11px] leading-snug text-ink">{kase.title}</p>
                            <p className="mt-0.5 text-[10px] leading-snug text-ink-faint">
                              {kase.expect}
                            </p>
                            <div className="mt-1.5 flex gap-1">
                              {(["pass", "fail"] as const).map((v) => (
                                <button
                                  key={v}
                                  type="button"
                                  onClick={() =>
                                    setVerdict(kase.id, verdicts[kase.id] === v ? null : v)
                                  }
                                  className={`rounded border px-1.5 py-px text-[10px] font-medium capitalize transition ${
                                    verdicts[kase.id] === v
                                      ? v === "pass"
                                        ? "border-active-line bg-active-bg text-active"
                                        : "border-danger-line bg-danger-bg text-danger"
                                      : "border-line bg-surface text-ink-faint hover:bg-surface-2"
                                  }`}
                                >
                                  {v}
                                </button>
                              ))}
                            </div>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                );
              })}
            </nav>
          </Panel>
        </aside>

        <main className="min-w-0 flex-1 space-y-3">
          <Panel className="px-4 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-[11px] text-ink-faint">{active.section}</span>
              <h2 className="text-sm font-semibold text-ink">{active.title}</h2>
            </div>
            <p className="mt-1 text-xs leading-relaxed text-ink-muted">{active.blurb}</p>
          </Panel>

          {group === "create" && <CreateFlow />}
          {(group === "verify" || group === "address") && <VerifyFlow />}
          {group === "tag" && <TagFlow />}
        </main>
      </div>
    </div>
  );
}
