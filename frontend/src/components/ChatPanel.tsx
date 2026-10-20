"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError, postChat, type ChatResponse, type PatientSummary } from "@/lib/api";
import { ChatIcon, LinkIcon, PlusIcon, SendIcon } from "./icons";
import { Eyebrow, Panel, Tag } from "./ui";

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
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <div className="flex items-center gap-2">
            <span className="inline-flex size-6 items-center justify-center rounded-md bg-accent-bg text-accent">
              <ChatIcon size={13} />
            </span>
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
              className="inline-flex items-center gap-1 rounded-lg border border-line px-2.5 py-1 text-[11px] font-medium text-ink-muted transition hover:border-line-strong hover:text-ink"
            >
              <PlusIcon size={12} />
              New conversation
            </button>
          )}
        </div>

        <div ref={scrollRef} className="scroll-quiet min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-5">
          {turns.length === 0 && !loading ? (
            <EmptyState onExample={(example) => send(example)} />
          ) : (
            turns.map((turn, i) => <Bubble key={i} turn={turn} />)
          )}
          {loading && <TypingBubble />}
        </div>

        {error && (
          <div className="border-t border-danger-line bg-danger-bg px-5 py-2.5">
            <p className="text-xs text-danger">{error}</p>
          </div>
        )}

        <form
          onSubmit={(event) => {
            event.preventDefault();
            send(input);
          }}
          className="flex gap-2 border-t border-line bg-surface-2/40 p-3"
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
            className="w-full rounded-xl border border-line bg-surface px-4 py-2.5 text-sm text-ink outline-none transition placeholder:text-ink-faint focus:border-accent-line focus:ring-4 focus:ring-accent-bg disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={loading || !input.trim()}
            className="btn-primary inline-flex shrink-0 items-center gap-1.5 rounded-xl px-4 text-sm font-semibold"
          >
            Send
            <SendIcon size={14} />
          </button>
        </form>
      </Panel>
    </div>
  );
}

function Bubble({ turn }: { turn: Turn }) {
  const isUser = turn.role === "user";
  return (
    <div className={`flex items-end gap-2.5 ${isUser ? "justify-end" : "justify-start"}`}>
      {!isUser && (
        <span className="brand-mark mb-5 flex size-7 shrink-0 items-center justify-center rounded-lg text-[11px] font-bold">
          K
        </span>
      )}
      <div className={`max-w-[80%] ${isUser ? "order-2" : ""}`}>
        <div
          className={`px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap ${
            isUser
              ? "rounded-2xl rounded-br-md bg-accent text-on-accent shadow-card"
              : "rounded-2xl rounded-bl-md border border-line bg-surface-2 text-ink"
          }`}
        >
          {turn.text}
        </div>
        {!isUser && (turn.citedFactCount ?? 0) > 0 && (
          <p className="mt-1.5 inline-flex items-center gap-1 px-1 text-[10px] font-medium text-ink-faint">
            <LinkIcon size={10} />
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
    <div className="flex items-end gap-2.5" aria-busy aria-label="Waiting for a reply">
      <span className="brand-mark flex size-7 shrink-0 items-center justify-center rounded-lg text-[11px] font-bold">
        K
      </span>
      <div className="rounded-2xl rounded-bl-md border border-line bg-surface-2 px-4 py-3">
        <div className="flex gap-1">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="typing-dot size-1.5 rounded-full bg-ink-faint"
              style={{ animationDelay: `${i * 160}ms` }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function EmptyState({ onExample }: { onExample: (example: string) => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
      <span className="inline-flex size-12 items-center justify-center rounded-2xl bg-accent-bg text-accent">
        <ChatIcon size={22} />
      </span>
      <div>
        <p className="text-base font-semibold text-ink">Start a conversation</p>
        <p className="mx-auto mt-1 max-w-sm text-xs leading-relaxed text-ink-muted">
          Ask a follow-up the way you would over a phone call. Each answer is grounded in
          this turn&apos;s retrieved facts and notes, and the thread remembers what you
          already asked.
        </p>
      </div>
      <div className="flex max-w-xl flex-wrap justify-center gap-1.5">
        {EXAMPLES.map((example) => (
          <button
            key={example}
            type="button"
            onClick={() => onExample(example)}
            className="rounded-full border border-line bg-surface px-3 py-1 text-xs text-ink-muted transition hover:border-accent-line hover:bg-accent-bg hover:text-accent"
          >
            {example}
          </button>
        ))}
      </div>
    </div>
  );
}
