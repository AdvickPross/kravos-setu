"use client";

import { useState } from "react";

import { StepCard } from "@/components/m1/StepCard";
import { Button, Note } from "@/components/m1/controls";
import { Eyebrow, Tag } from "@/components/ui";
import type { AbhaCall } from "@/lib/abha";
import { m2, type PatientState } from "@/lib/m2";
import { M2_GROUPS } from "@/lib/m2-cases";
import { CareContextTable } from "./shared";

const CASES = M2_GROUPS.find((g) => g.key === "records")!.cases;

type Bundle = { entry?: { resource?: { resourceType?: string; section?: { title?: string; entry?: unknown[] }[] } }[] };

/**
 * Health_RECORD_CREATION_101: show the FHIR a visit is shared as.
 *
 * Works today with no ABDM at all. It's the same builder the data transfer
 * uses, minus the consent's date clipping and the encryption.
 */
export function RecordsFlow({ state }: { state: PatientState | null }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [call, setCall] = useState<AbhaCall<Record<string, unknown>> | null>(null);
  const [busy, setBusy] = useState(false);

  async function preview(reference: string) {
    if (!state) return;
    setSelected(reference);
    setBusy(true);
    setCall(await m2.fhirPreview(state.patientId, reference));
    setBusy(false);
  }

  function download() {
    if (!call?.ok || !selected) return;
    const blob = new Blob([JSON.stringify(call.body, null, 2)], { type: "application/fhir+json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `opconsult-${selected}.json`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  if (!state) return <Note tone="info">Pick a patient above to see their visits as FHIR.</Note>;

  const bundle = call?.ok ? (call.body as Bundle) : null;
  const resources = (bundle?.entry ?? []).map((e) => e.resource?.resourceType ?? "?");
  const counts = resources.reduce<Record<string, number>>((acc, r) => ({ ...acc, [r]: (acc[r] ?? 0) + 1 }), {});
  const sections = bundle?.entry?.[0]?.resource?.section ?? [];

  return (
    <div className="space-y-3">
      <StepCard
        n={1}
        title="Pick a visit"
        cases={CASES}
        done={Boolean(selected)}
        hint="Each visit (encounter) is one care context — the unit ABDM links and shares."
      >
        <CareContextTable contexts={state.careContexts} selected={selected} onSelect={preview} />
      </StepCard>

      <StepCard
        n={2}
        title="FHIR document bundle"
        cases={CASES}
        call={call}
        done={Boolean(bundle)}
        hint="OPConsultRecord, following ABDM's NRCeS profiles. The corpus' SNOMED / LOINC / RxNorm / CVX codes are carried as-is."
      >
        {busy && <p className="text-xs text-ink-faint">Building…</p>}
        {bundle && (
          <>
            <div className="flex flex-wrap gap-1">
              {Object.entries(counts).map(([type, n]) => (
                <Tag key={type} tone={type === "Composition" ? "accent" : "neutral"}>
                  {type} × {n}
                </Tag>
              ))}
            </div>
            <div>
              <Eyebrow>Composition sections</Eyebrow>
              <ul className="mt-1 space-y-0.5">
                {sections.map((s) => (
                  <li key={s.title} className="text-xs text-ink">
                    {s.title} <span className="text-ink-faint">· {s.entry?.length ?? 0} entries</span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={download}>
                Download bundle (.json)
              </Button>
            </div>
            <Note tone="info">
              Not yet run through the NRCeS validator — do that before certification. Open the response below to
              inspect the full bundle.
            </Note>
          </>
        )}
      </StepCard>
    </div>
  );
}
