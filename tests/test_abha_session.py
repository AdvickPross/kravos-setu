"""Session token caching, expiry and the refresh race.

A 1200-second token makes both failure modes easy to ship unnoticed: fetching
one per request looks correct until the sandbox rate-limits, and fetching one
at startup looks correct until the process outlives twenty minutes.
"""

from __future__ import annotations

import threading

import httpx
import pytest

from abha.config import AbhaConfig, AbhaConfigError, AbhaEnvironment
from abha.errors import AbhaAuthError
from abha.session import SessionManager
from abha.transport import Transport

CONFIG = AbhaConfig(client_id="cid", client_secret="secret")


def _manager(handler, clock=None) -> SessionManager:
    transport = Transport(httpx.Client(transport=httpx.MockTransport(handler)))
    kwargs = {"clock": clock} if clock else {}
    return SessionManager(CONFIG, transport, **kwargs)


def _ok(token: str = "tok", expires_in: int = 1200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"accessToken": token, "expiresIn": expires_in}
        )

    return handler


def test_token_is_fetched_once_and_reused():
    manager = _manager(_ok())
    for _ in range(10):
        manager.access_token()
    assert manager.fetch_count == 1


def test_token_is_refetched_after_expiry():
    now = [1000.0]
    manager = _manager(_ok(expires_in=1200), clock=lambda: now[0])
    manager.access_token()
    now[0] += 1199  # inside the window only if the leeway is ignored
    manager.access_token()
    assert manager.fetch_count == 2, "the 60s leeway did not trigger an early refresh"


def test_leeway_refreshes_before_abdm_would_reject():
    """The asymmetry is deliberate: an extra session call costs one round
    trip, a request sent with a just-expired token costs a clinical failure."""
    now = [0.0]
    manager = _manager(_ok(expires_in=1200), clock=lambda: now[0])
    manager.access_token()
    now[0] += 1100  # 100s of life left, more than the 60s leeway
    manager.access_token()
    assert manager.fetch_count == 1


def test_concurrent_callers_share_one_fetch():
    """Without the lock, every in-flight request notices the expiry at the
    same moment and fires its own session call."""
    manager = _manager(_ok())
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        manager.access_token()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert manager.fetch_count == 1


def test_session_call_carries_x_cm_id_and_no_authorization():
    """X-CM-ID is required on this endpoint specifically, and an Authorization
    header cannot be sent -- this call is what produces one."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"accessToken": "t", "expiresIn": 1200})

    _manager(handler).access_token()
    assert seen[0].headers["X-CM-ID"] == "sbx"
    assert "authorization" not in seen[0].headers


def test_bearer_prefix_is_added():
    """ABDM names the field accessToken but rejects the bare value."""
    manager = _manager(_ok("abc"))
    assert manager.authorization() == "Bearer abc"


def test_missing_access_token_is_an_auth_error_not_a_key_error():
    manager = _manager(lambda r: httpx.Response(200, json={"expiresIn": 1200}))
    with pytest.raises(AbhaAuthError):
        manager.access_token()


def test_invalidate_forces_a_refetch():
    manager = _manager(_ok())
    manager.access_token()
    manager.invalidate()
    manager.access_token()
    assert manager.fetch_count == 2


def test_secret_never_appears_in_a_repr():
    """These objects reach tracebacks and FastAPI error pages.

    Asserted against a distinctive value rather than the word "secret",
    which legitimately appears as the *field name* in the redacted repr.
    """
    config = AbhaConfig(client_id="cid", client_secret="hunter2-do-not-print")
    assert "hunter2-do-not-print" not in repr(config)
    assert "<redacted>" in repr(config)
    manager = _manager(_ok("supersecret-token"))
    manager.access_token()
    assert "supersecret-token" not in repr(manager.token)


def test_production_and_sandbox_differ_on_all_three_hosts():
    """The hosts do not share a prefix -- production swaps the path segments
    and moves PHR to its own domain. A single-host assumption breaks here."""
    sbx = AbhaConfig("a", "b", AbhaEnvironment.SANDBOX)
    prod = AbhaConfig("a", "b", AbhaEnvironment.PRODUCTION)
    assert sbx.session_url != prod.session_url
    assert sbx.abha_url("/x") != prod.abha_url("/x")
    assert "phr.abdm.gov.in" in prod.phr_url("/x")
    assert sbx.cm_id == "sbx" and prod.cm_id == "abdm"


def test_missing_credentials_raise_a_fixable_error(monkeypatch):
    monkeypatch.delenv("ABDM_CLIENT_ID", raising=False)
    monkeypatch.delenv("ABDM_CLIENT_SECRET", raising=False)
    assert not AbhaConfig.is_configured()
    with pytest.raises(AbhaConfigError) as exc:
        AbhaConfig.from_env()
    assert ".env" in str(exc.value)


def test_unknown_environment_is_rejected_not_defaulted(monkeypatch):
    """Silently falling back to sandbox on a typo would point production
    traffic at the test ecosystem."""
    monkeypatch.setenv("ABDM_CLIENT_ID", "a")
    monkeypatch.setenv("ABDM_CLIENT_SECRET", "b")
    monkeypatch.setenv("ABDM_ENVIRONMENT", "prod")
    with pytest.raises(AbhaConfigError):
        AbhaConfig.from_env()
