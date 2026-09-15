"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError, postChat, type ChatResponse, type PatientSummary } from "@/lib/api";
import { Eyebrow, Panel, Skeleton, Tag } from "./ui";

type Turn = {
  role: "user" | "assistant";
  text: string;
  citedFactCount?: number;
  narrativeCount?: number;
};

const EXAMPLES = [
  "what medications is she on?",
  "any allergies I should know about?",
  "why is she on that medication?",
  "anything missing before surgery?",
];

/**
 * The conversational counterpart to QueryBox + BriefView: same NER and
 * retrieval as a single /api/query call, but threaded -- each message
 * carries the conversation forward via `conversation_id`, and a follow-up
 * like "why is she on that?" gets a fresh narrative search rather than
 * only whatever resolved on the first turn. See backend/conversation.py.
 *
 * Self-contained: owns its own thread state. The caller renders this with
 * `key={patient.patient_id}` so a new patient remounts it fresh rather than
 * needing an effect to reset state -- one patient's thread should never
 * carry onto another's screen.
 */
export function ChatPanel({ patient }: { patient: PatientSummary }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [turns, loading]);

  async function send(message: string) {
    const trimmed = message.trim();
    if (!trimmed || loading) return;

    setTurns((prev) => [...prev, { role: "user", text: trimmed }]);
    setInput("");
    setLoading(true);
    setError(null);

    try {
      const response: ChatResponse = await postChat(
        patient.patient_id,
        trimmed,
        conversationId,
      );
      setConversationId(response.conversation_id);
      setTurns((prev) => [
        ...prev,
        {
          role: "assistant",
          text: response.answer,
          citedFactCount: response.cited_fact_ids?.length ?? 0,
          narrativeCount: response.result.narrative_matches?.length ?? 0,
        },
      ]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the API.");
      // Drop the optimistic user turn that never got an answer, so a retry
      // doesn't show the question twice.
      setTurns((prev) => prev.slice(0, -1));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Panel className="flex min-h-0 flex-1 flex-col">
        <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <div className="flex items-center gap-2">
            <Eyebrow>Conversation</Eyebrow>
            <Tag tone="accent" title="Persists to Postgres; carries context turn to turn">
              multi-turn
            </Tag>
          </div>
          {turns.length > 0 && (
            <button
              type="button"
              onClick={() => {
                setTurns([]);
                setConversationId(null);
                setError(null);
              }}
              className="text-[11px] font-medium text-ink-faint hover:text-ink-muted"
            >
              New conversation
            </button>
          )}
        </div>

        <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3">
          {turns.length === 0 && !loading ? (
            <EmptyState onExample={(example) => send(example)} />
          ) : (
            turns.map((turn, i) => <Bubble key={i} turn={turn} />)
          )}
          {loading && <TypingBubble />}
        </div>

        {error && (
          <div className="border-t border-danger-line bg-danger-bg px-4 py-2">
            <p className="text-xs text-danger">{error}</p>
          </div>
        )}

        <form
          onSubmit={(event) => {
            event.preventDefault();
            send(input);
          }}
          className="flex gap-2 border-t border-line px-4 py-3"
        >
          <input
            type="text"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder={
              turns.length === 0 ? "Ask about this patient…" : "Ask a follow-up…"
            }
            disabled={loading}
            aria-label="Conversational question"
            className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-faint disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={loading || !input.trim()}
            className="shrink-0 rounded-md bg-ink px-4 py-2 text-sm font-medium text-bg transition-opacity disabled:opacity-40"
          >
            Send
          </button>
        </form>
      </Panel>
    </div>
  );
}

function Bubble({ turn }: { turn: Turn }) {
  const isUser = turn.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[85%] ${isUser ? "order-2" : ""}`}>
        <div
          className={`rounded-lg px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap ${
            isUser
              ? "bg-ink text-bg"
              : "border border-line bg-surface-2 text-ink"
          }`}
        >
          {turn.text}
        </div>
        {!isUser && (turn.citedFactCount ?? 0) > 0 && (
          <p className="mt-1 px-0.5 text-[10px] text-ink-faint">
            Grounded in {turn.citedFactCount} fact{turn.citedFactCount === 1 ? "" : "s"}
            {turn.narrativeCount ? `, ${turn.narrativeCount} clinical note${turn.narrativeCount === 1 ? "" : "s"}` : ""}
          </p>
        )}
      </div>
    </div>
  );
}

function TypingBubble() {
  return (
    <div className="flex justify-start" aria-busy aria-label="Waiting for a reply">
      <div className="rounded-lg border border-line bg-surface-2 px-3 py-2.5">
        <div className="flex gap-1">
          <Skeleton className="h-1.5 w-1.5 rounded-full" />
          <Skeleton className="h-1.5 w-1.5 rounded-full" />
          <Skeleton className="h-1.5 w-1.5 rounded-full" />
        </div>
      </div>
    </div>
  );
}

function EmptyState({ onExample }: { onExample: (example: string) => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
      <p className="max-w-xs text-xs leading-relaxed text-ink-faint">
        Ask a follow-up the way you would over a phone call. Each answer is grounded in
        this turn&apos;s retrieved facts and notes, and the thread remembers what you
        already asked.
      </p>
      <div className="flex flex-wrap justify-center gap-1.5">
        {EXAMPLES.map((example) => (
          <button
            key={example}
            type="button"
            onClick={() => onExample(example)}
            className="rounded-full border border-line bg-surface px-2.5 py-1 text-[11px] text-ink-muted transition-colors hover:border-line-strong hover:text-ink"
          >
            {example}
          </button>
        ))}
      </div>
    </div>
  );
}
