"""The LLM behind the conversational endpoint (backend/conversation.py).

Groq, not Anthropic: this environment has a Groq key but not an Anthropic
one, and the conversational path is a separate feature from
``backend/llm_polish.py``'s single-shot brief rewrite (which stays on
Anthropic, off by default, untouched). Same graceful-degradation contract
as that module and ``backend/vector_store.py`` -- every failure mode
(disabled, no key, timeout, API error, malformed response) returns ``None``
rather than raising, so a caller on the request path never has to special-case
this.

``GROQ_MODEL`` defaults to ``qwen/qwen3.8-27b`` -- checked against this
account's actual `/openai/v1/models` listing, not assumed: Groq's catalog
has moved on from the older Llama 3.1 models. Picked over the also-available
``openai/gpt-oss-*`` models because those are reasoning models that spend
part of ``max_tokens`` on a hidden reasoning pass before the visible answer
(confirmed: a 20-token budget came back empty, finish_reason "length", with
the whole budget spent on reasoning) -- workable with a larger budget and
``reasoning_effort: "low"``, but qwen3.8-27b answers directly with no such
tuning needed, which fits a conversational path meant to answer follow-ups
quickly. Override via env if a different model fits better later.
"""

from __future__ import annotations

import os

import httpx

_API_KEY_ENV_VAR = "GROQ_API_KEY"
_MODEL_ENV_VAR = "GROQ_MODEL"
DEFAULT_MODEL = "qwen/qwen3.8-27b"
_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


def groq_enabled() -> bool:
    return bool(os.environ.get(_API_KEY_ENV_VAR, "").strip())


def generate_reply(
    messages: list[dict],
    *,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    timeout: float = 20.0,
) -> str | None:
    """One chat completion, or ``None`` on any failure.

    ``messages`` is the OpenAI-shaped list (``{"role": ..., "content": ...}``,
    roles ``system`` / ``user`` / ``assistant``) -- Groq's API is
    OpenAI-compatible, so no translation layer is needed here.
    """
    api_key = os.environ.get(_API_KEY_ENV_VAR, "").strip()
    if not api_key:
        return None

    model = os.environ.get(_MODEL_ENV_VAR, "").strip() or DEFAULT_MODEL

    try:
        response = httpx.post(
            _ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()
        text = body["choices"][0]["message"]["content"]
    except Exception:
        return None

    text = (text or "").strip()
    return text or None
