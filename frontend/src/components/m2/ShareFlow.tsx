"use client";

import { useCallback, useEffect, useState } from "react";

import { StepCard } from "@/components/m1/StepCard";
import { Note } from "@/components/m1/controls";
import { Tag } from "@/components/ui";
import { m2, type Consent, type Transfer } from "@/lib/m2";
import { M2_GROUPS } from "@/lib/m2-cases";
import { clock } from "./shared";

const CASES = M2_GROUPS.find((g) => g.key === "share")!.cases;
const pick = (...ids: string[]) => CASES.filter((c) => ids.some((id) => c.id.includes(id)));

const HI_TONE: Record<string, "accent" | "danger" | "neutral"> = { DELIVERED: "accent", ERRORED: "danger" };

/**
 * Consent artefacts and data transfers (HIP_INIT_SHARE_CARECONTEXT and the
 * consent grant/revoke/expire rows).
 *
 * The test is "the consent shows up in the HIP's system" and "it disappears
 * on revoke or expiry", so the consent list *is* the evidence. Polled, since
 * every change arrives from ABDM.
 */
export function ShareFlow() {
  const [consents, setConsents] = useState<Consent[]>([]);
  const [transfers, setTransfers] = useState<Transfer[]>([]);

  const load = useCallback(
    () =>
      Promise.all([m2.consents(), m2.transfers()]).then(([c, t]) => {
        if (c.ok) setConsents(c.body);
        if (t.ok) setTransfers(t.body);
      }),
    [],
  );

  useEffect(() => {
    load();
    const handle = setInterval(load, 4000);
    return () => clearInterval(handle);
  }, [load]);

  return (
    <div className="space-y-3">
      <StepCard
        n={1}
        title="Grant a consent"
        cases={pick("GRANT")}
        done={consents.length > 0}
        hint="From the sandbox HIU (or the PHR app), request a consent for this facility's records, then approve it in the PHR app. It must appear below within seconds."
      >
        {consents.length === 0 ? (
          <p className="text-xs text-ink-faint">No active consents. Waiting for ABDM…</p>
        ) : (
          <ul className="divide-y divide-line rounded border border-line">
            {consents.map((c) => (
              <li key={c.consent_id} className="space-y-0.5 px-3 py-2 text-xs">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="font-mono text-ink">{c.consent_id.slice(0, 13)}…</code>
                  <Tag tone="accent">granted</Tag>
                  <span className="text-ink-muted">{c.abha_address}</span>
                  <span className="ml-auto text-[10px] text-ink-faint">{clock(c.updated_at)}</span>
                </div>
                <p className="text-ink-muted">
                  {c.hi_types.join(", ")} · {c.care_contexts.length} visit(s) · {c.date_from.slice(0, 10)} →{" "}
                  {c.date_to.slice(0, 10)}
                  {c.data_erase_at ? ` · erase ${c.data_erase_at.slice(0, 10)}` : ""}
                </p>
                <p className="text-[10px] text-ink-faint">HIU {c.hiu_id ?? "—"}</p>
              </li>
            ))}
          </ul>
        )}
      </StepCard>

      <StepCard
        n={2}
        title="Revoke, or let one expire"
        cases={pick("REVOKE", "EXPIRE")}
        hint="Revoke a granted consent in the PHR app (or grant one with a very short expiry). It must vanish from the list above, and any later data request under it is refused."
      >
        <Note tone="info">
          The activity log keeps the record — CONSENT_REVOKED → CONSENT_REVOKED_DELETED — even though the artefact
          itself is gone.
        </Note>
      </StepCard>

      <StepCard
        n={3}
        title="Data transfers"
        cases={pick("SHARE")}
        done={transfers.some((t) => t.status === "TRANSFERRED")}
        hint="“Get data” in the PHR app (or the HIU) asks us for the consented records. We check the consent, encrypt each visit for the requester, push it, and report per visit. ABDM allows 2 hours; we start immediately."
      >
        {transfers.length === 0 ? (
          <p className="text-xs text-ink-faint">No data requests yet.</p>
        ) : (
          <ul className="divide-y divide-line rounded border border-line">
            {transfers.map((t) => (
              <li key={t.transaction_id} className="space-y-1 px-3 py-2 text-xs">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="font-mono text-ink">txn {t.transaction_id.slice(0, 13)}…</code>
                  <Tag tone={t.status === "TRANSFERRED" ? "accent" : t.status === "RECEIVED" ? "warn" : "danger"}>
                    {t.status}
                  </Tag>
                  <span className="ml-auto text-[10px] text-ink-faint">{clock(t.completed_at ?? t.created_at)}</span>
                </div>
                {t.error_message && <p className="text-danger">{t.error_message}</p>}
                {t.statuses && t.statuses.length > 0 && (
                  <ul className="space-y-0.5">
                    {t.statuses.map((s) => (
                      <li key={s.careContextReference} className="flex flex-wrap items-center gap-2">
                        <Tag tone={HI_TONE[s.hiStatus] ?? "neutral"}>{s.hiStatus}</Tag>
                        <code className="font-mono text-[10px] text-ink-faint">{s.careContextReference.slice(0, 13)}</code>
                        <span className="text-ink-muted">{s.description}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        )}
      </StepCard>
    </div>
  );
}
