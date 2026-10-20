"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  commitScan,
  extractScan,
  OcrError,
  type CommitResult,
  type ExtractResult,
} from "@/lib/ocr";

/**
 * Upload a scan, see what was read, keep what is right.
 *
 * The review step is not a formality and the screen is built to make that
 * obvious. OCR output is a recognition model's guess about a clinical
 * document; the failure mode is not a crash but a plausible wrong drug name
 * entering a record and being believed by every query afterwards. So:
 *
 * * every proposed fact shows the **line it was read from** and the OCR
 *   confidence, because "Furosemide 40 MG" is only trustworthy if the line
 *   really did say Furosemide;
 * * anything below 90% is **unticked by default** — approving it has to be a
 *   decision, not an oversight;
 * * the lines that produced *nothing* are shown too. A screen listing twelve
 *   facts and silently dropping four drug names looks complete and is not.
 */

type Stage = "choose" | "reading" | "review" | "saving" | "done";

const HIGH_CONFIDENCE = 0.9;

const ACCEPTED = ".png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,.pdf";

export function UploadFlow({
  patientId,
  onBack,
  onFinished,
}: {
  patientId: string;
  onBack: () => void;
  onFinished: () => void;
}) {
  const [stage, setStage] = useState<Stage>("choose");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [result, setResult] = useState<ExtractResult | null>(null);
  const [approved, setApproved] = useState<Set<string>>(new Set());
  const [committed, setCommitted] = useState<CommitResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [showText, setShowText] = useState(false);
  const [documentDate, setDocumentDate] = useState("");

  // Object URLs are a leak if they are not revoked; this one lives as long as
  // the chosen file and no longer.
  useEffect(() => {
    if (!file || file.type === "application/pdf") {
      setPreview(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const start = useCallback(
    async (chosen: File) => {
      setFile(chosen);
      setError(null);
      setStage("reading");
      try {
        const extracted = await extractScan(chosen, patientId, documentDate || undefined);
        setResult(extracted);
        // Pre-tick only what the model was confident about. The rest is a
        // deliberate choice the reviewer has to make.
        setApproved(
          new Set(
            extracted.facts
              .filter((fact) => fact.confidence >= HIGH_CONFIDENCE)
              .map((fact) => fact.fact_id),
          ),
        );
        setStage("review");
      } catch (err) {
        setError(err instanceof OcrError ? err.message : "Could not read that file.");
        setStage("choose");
      }
    },
    [patientId, documentDate],
  );

  async function save() {
    if (!file || !result) return;
    setStage("saving");
    setError(null);
    try {
      setCommitted(
        await commitScan(file, patientId, [...approved], documentDate || undefined),
      );
      setStage("done");
    } catch (err) {
      setError(err instanceof OcrError ? err.message : "Could not save those facts.");
      setStage("review");
    }
  }

  const grouped = useMemo(() => {
    const map = new Map<string, ExtractResult["facts"]>();
    for (const fact of result?.facts ?? []) {
      map.set(fact.category, [...(map.get(fact.category) ?? []), fact]);
    }
    return [...map.entries()];
  }, [result]);

  // -- choose ---------------------------------------------------------------

  if (stage === "choose" || stage === "reading") {
    const busy = stage === "reading";
    return (
      <Shell onBack={onBack} title="Upload a scanned record">
        <label
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            const dropped = e.dataTransfer.files?.[0];
            if (dropped && !busy) void start(dropped);
          }}
          className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-14 text-center transition ${
            dragging
              ? "border-accent-line bg-accent-bg/40"
              : "border-line bg-surface hover:border-line-strong"
          } ${busy ? "pointer-events-none opacity-60" : ""}`}
        >
          <input
            type="file"
            accept={ACCEPTED}
            className="sr-only"
            disabled={busy}
            onChange={(e) => {
              const chosen = e.target.files?.[0];
              if (chosen) void start(chosen);
            }}
          />
          {busy ? (
            <>
              <span className="h-6 w-6 animate-spin rounded-full border-2 border-accent border-t-transparent" />
              <p className="mt-3 text-sm font-medium text-ink">Reading {file?.name}…</p>
              <p className="mt-1 text-xs text-ink-faint">
                Recognising text on this machine. Nothing is sent anywhere.
              </p>
            </>
          ) : (
            <>
              <p className="text-sm font-medium text-ink">
                Drop a scan here, or click to choose
              </p>
              <p className="mt-1.5 max-w-sm text-xs leading-relaxed text-ink-faint">
                Discharge summary, prescription or lab report. PNG, JPG, TIFF or
                PDF, up to 25 MB.
              </p>
            </>
          )}
        </label>

        <div className="mt-4">
          <label className="block">
            <span className="text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-faint">
              Date on the document (optional)
            </span>
            <input
              type="date"
              value={documentDate}
              disabled={busy}
              onChange={(e) => setDocumentDate(e.target.value)}
              className="mt-1.5 w-full max-w-xs rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-accent-line focus:ring-4 focus:ring-accent-bg sm:w-auto"
            />
            <span className="mt-1.5 block text-[11px] leading-relaxed text-ink-faint">
              A discharge summary dated last March describes last March. Left
              blank it is filed as today, which moves it in and out of every
              recency window the brief computes.
            </span>
          </label>
        </div>

        {error && <Problem>{error}</Problem>}
      </Shell>
    );
  }

  // -- done -----------------------------------------------------------------

  if (stage === "done" && committed) {
    return (
      <Shell onBack={onBack} title="Record added">
        <div className="rounded-xl border border-active-line bg-active-bg px-5 py-5">
          <p className="text-sm font-semibold text-active">
            {committed.inserted} fact{committed.inserted === 1 ? "" : "s"} added to
            this patient&apos;s record
          </p>
          {committed.already_present > 0 && (
            <p className="mt-1 text-xs text-active/90">
              {committed.already_present} were already on file — re-reading the
              same document does not duplicate anything.
            </p>
          )}
        </div>
        <p className="mt-4 text-sm leading-relaxed text-ink-muted">
          They carry the same shape as everything else on file, so the clinical
          view answers questions about them exactly as it does about the rest —
          and cites the scan they came from.
        </p>
        <div className="mt-5 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={onFinished}
            className="rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-on-accent transition hover:brightness-110"
          >
            Open the clinical view →
          </button>
          <button
            type="button"
            onClick={() => {
              setStage("choose");
              setFile(null);
              setResult(null);
              setCommitted(null);
            }}
            className="rounded-lg border border-line bg-surface px-4 py-2.5 text-sm font-medium text-ink-muted transition hover:text-ink"
          >
            Upload another
          </button>
        </div>
      </Shell>
    );
  }

  // -- review ---------------------------------------------------------------

  if (!result) return null;
  const saving = stage === "saving";
  const missed =
    result.unmatched.length + result.ambiguous.length + result.low_confidence.length;

  return (
    <Shell onBack={onBack} title={`Review what was read from ${result.source_file}`}>
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="min-w-0 space-y-4">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-faint">
            <span>{result.lines.length} lines read</span>
            <span>mean confidence {(result.mean_confidence * 100).toFixed(0)}%</span>
            <span>{result.facts.length} facts proposed</span>
            <button
              type="button"
              onClick={() => setShowText((v) => !v)}
              className="text-accent underline-offset-2 hover:underline"
            >
              {showText ? "hide" : "show"} the raw text
            </button>
          </div>

          {showText && (
            <pre className="max-h-56 overflow-auto rounded-lg border border-line bg-bg px-3 py-2 font-mono text-[11px] leading-relaxed text-ink-muted">
              {result.lines
                .map((l) => `${(l.confidence * 100).toFixed(0)}%  ${l.text}`)
                .join("\n")}
            </pre>
          )}

          {grouped.map(([category, facts]) => (
            <section key={category}>
              <h3 className="text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-faint">
                {category}
              </h3>
              <ul className="mt-1.5 space-y-1.5">
                {facts.map((fact) => {
                  const on = approved.has(fact.fact_id);
                  const low = fact.confidence < HIGH_CONFIDENCE;
                  return (
                    <li key={fact.fact_id}>
                      <label
                        className={`flex cursor-pointer gap-3 rounded-lg border px-3 py-2.5 transition ${
                          on
                            ? "border-accent-line bg-accent-bg/40"
                            : "border-line bg-surface hover:border-line-strong"
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={on}
                          disabled={saving}
                          onChange={() =>
                            setApproved((prev) => {
                              const next = new Set(prev);
                              if (next.has(fact.fact_id)) next.delete(fact.fact_id);
                              else next.add(fact.fact_id);
                              return next;
                            })
                          }
                          className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--accent)]"
                        />
                        <span className="min-w-0 flex-1">
                          <span className="flex flex-wrap items-baseline gap-x-2">
                            <span className="text-sm font-medium text-ink">
                              {fact.display ?? "(uncoded)"}
                            </span>
                            {fact.value && (
                              <span className="font-mono text-xs text-ink-muted">
                                {fact.value} {fact.unit}
                              </span>
                            )}
                            <span
                              className={`rounded border px-1 py-px text-[10px] font-medium ${
                                low
                                  ? "border-warn-line bg-warn-bg text-warn"
                                  : "border-line bg-surface-2 text-ink-faint"
                              }`}
                            >
                              {(fact.confidence * 100).toFixed(0)}%
                            </span>
                          </span>
                          <span className="mt-1 block truncate font-mono text-[11px] text-ink-faint">
                            read from “{fact.line_text}” · p{fact.page} line {fact.line_no}
                          </span>
                          {fact.also_in.length > 0 && (
                            <span className="mt-0.5 block text-[11px] text-ink-faint">
                              {fact.also_in.length} other code
                              {fact.also_in.length === 1 ? "" : "s"} matched this
                              phrase; the most general was chosen
                            </span>
                          )}
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}

          {result.facts.length === 0 && (
            <p className="rounded-lg border border-warn-line bg-warn-bg px-3.5 py-3 text-xs leading-relaxed text-warn">
              Nothing on this page matched a known clinical concept. It may not
              be a clinical document, or the scan may be too poor to read.
            </p>
          )}
        </div>

        {/* What was NOT extracted. Deliberately beside the facts and not
            behind a link: a list of twelve facts that quietly dropped four
            drug names looks complete. */}
        <aside className="space-y-3">
          {preview && (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={preview}
              alt="the uploaded scan"
              className="w-full rounded-lg border border-line"
            />
          )}
          <div className="rounded-lg border border-line bg-surface p-3.5">
            <h3 className="text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-faint">
              Not extracted ({missed})
            </h3>
            {missed === 0 ? (
              <p className="mt-1.5 text-xs text-ink-faint">
                Every line was either placed or was obvious boilerplate.
              </p>
            ) : (
              <ul className="mt-1.5 max-h-72 space-y-1 overflow-auto">
                {result.ambiguous.map((text, i) => (
                  <Missed key={`a${i}`} why="outside any section">
                    {text}
                  </Missed>
                ))}
                {result.low_confidence.map((text, i) => (
                  <Missed key={`l${i}`} why="too unclear to read">
                    {text}
                  </Missed>
                ))}
                {result.unmatched.map((text, i) => (
                  <Missed key={`u${i}`} why="no matching concept">
                    {text}
                  </Missed>
                ))}
              </ul>
            )}
          </div>
        </aside>
      </div>

      {error && <Problem>{error}</Problem>}

      <div className="mt-6 flex flex-wrap items-center gap-3 border-t border-line pt-5">
        <button
          type="button"
          onClick={save}
          disabled={approved.size === 0 || saving}
          className="inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-on-accent transition hover:brightness-110 disabled:cursor-not-allowed disabled:bg-surface-2 disabled:text-ink-faint"
        >
          {saving && (
            <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
          )}
          Add {approved.size} fact{approved.size === 1 ? "" : "s"} to the record
        </button>
        <button
          type="button"
          onClick={() => {
            setStage("choose");
            setResult(null);
            setFile(null);
          }}
          className="text-xs text-ink-muted underline-offset-2 hover:underline"
        >
          Use a different file
        </button>
      </div>
    </Shell>
  );
}

function Shell({
  title,
  onBack,
  children,
}: {
  title: string;
  onBack: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="mx-auto w-full max-w-5xl px-5 py-10 sm:px-8">
      <button
        type="button"
        onClick={onBack}
        className="text-xs text-ink-muted underline-offset-2 hover:underline"
      >
        ← Back
      </button>
      <h1 className="mt-2 mb-6 text-xl font-semibold tracking-tight text-ink">{title}</h1>
      {children}
    </div>
  );
}

function Missed({ why, children }: { why: string; children: React.ReactNode }) {
  return (
    <li className="text-[11px] leading-snug">
      <span className="block truncate font-mono text-ink-muted">{children}</span>
      <span className="text-ink-faint">{why}</span>
    </li>
  );
}

function Problem({ children }: { children: React.ReactNode }) {
  return (
    <p
      role="alert"
      className="mt-4 rounded-lg border border-danger-line bg-danger-bg px-3.5 py-3 text-xs leading-relaxed text-danger"
    >
      {children}
    </p>
  );
}
