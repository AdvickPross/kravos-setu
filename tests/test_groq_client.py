"""backend.groq_client -- every failure mode (no key, timeout, API error,
malformed response) must return None, never raise. The real Groq API is
never called here.
"""

from __future__ import annotations

import httpx
import pytest

from backend.groq_client import DEFAULT_MODEL, generate_reply, groq_enabled


@pytest.fixture(autouse=True)
def _no_groq_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)


# --------------------------------------------------------------------- flag


def test_enabled_false_when_key_unset():
    assert groq_enabled() is False


def test_enabled_true_when_key_set(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    assert groq_enabled() is True


def test_enabled_false_for_blank_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "   ")
    assert groq_enabled() is False


# ------------------------------------------------------------- generate_reply


def test_returns_none_when_key_unset():
    assert generate_reply([{"role": "user", "content": "hi"}]) is None


def test_returns_text_on_success(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")

    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "pong"}}]}

    def _fake_post(url, headers, json, timeout):
        assert headers["Authorization"] == "Bearer gsk_test"
        assert json["model"] == DEFAULT_MODEL
        return _Response()

    monkeypatch.setattr(httpx, "post", _fake_post)
    assert generate_reply([{"role": "user", "content": "say pong"}]) == "pong"


def test_uses_model_override_from_env(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("GROQ_MODEL", "some/other-model")

    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    captured = {}

    def _fake_post(url, headers, json, timeout):
        captured["model"] = json["model"]
        return _Response()

    monkeypatch.setattr(httpx, "post", _fake_post)
    generate_reply([{"role": "user", "content": "hi"}])
    assert captured["model"] == "some/other-model"


def test_returns_none_on_http_error(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")

    def _fake_post(url, headers, json, timeout):
        raise httpx.ConnectTimeout("boom")

    monkeypatch.setattr(httpx, "post", _fake_post)
    assert generate_reply([{"role": "user", "content": "hi"}]) is None


def test_returns_none_on_non_2xx_status(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")

    class _Response:
        def raise_for_status(self):
            raise httpx.HTTPStatusError("bad", request=None, response=None)

        def json(self):
            return {}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Response())
    assert generate_reply([{"role": "user", "content": "hi"}]) is None


def test_returns_none_on_malformed_response(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")

    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"unexpected": "shape"}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Response())
    assert generate_reply([{"role": "user", "content": "hi"}]) is None


def test_returns_none_for_empty_content(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")

    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "   "}}]}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Response())
    assert generate_reply([{"role": "user", "content": "hi"}]) is None
