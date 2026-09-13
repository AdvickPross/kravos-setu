"""The headers and the retry policy.

Both are invisible when correct and produce unrelated-looking failures when
wrong: a malformed TIMESTAMP reads as an auth problem, and a retried 4xx
reads as a rate limit.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx
import pytest

from abha.errors import AbhaApiError, AbhaTransportError
from abha.transport import Transport, base_headers, redact, timestamp


def test_mandatory_headers_are_always_present():
    headers = base_headers()
    assert headers["REQUEST-ID"]
    assert headers["TIMESTAMP"]


def test_request_id_is_a_fresh_uuid_each_call():
    """Reusing a REQUEST-ID across calls breaks ABDM's correlation and, on
    some endpoints, is rejected as a replay."""
    ids = {base_headers()["REQUEST-ID"] for _ in range(20)}
    assert len(ids) == 20
    assert re.fullmatch(r"[0-9a-f-]{36}", ids.pop())


def test_timestamp_is_utc_not_local():
    """The bug this pins: `datetime.now().isoformat()` on an IST machine
    produces a local time with no offset, which ABDM reads as UTC and rejects
    as five and a half hours in the future."""
    parsed = datetime.strptime(timestamp(), "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )
    drift = abs((parsed - datetime.now(timezone.utc)).total_seconds())
    assert drift < 60, f"TIMESTAMP is {drift:.0f}s from UTC -- local clock leaked in"


def test_extra_headers_drop_none_values():
    """Call sites pass optional headers inline; a None must not become the
    literal string 'None' in a header ABDM then rejects."""
    headers = base_headers(extra={"X-token": None, "BENEFIT-NAME": "kravos"})
    assert "X-token" not in headers
    assert headers["BENEFIT-NAME"] == "kravos"


def test_redaction_covers_credentials_and_pii_at_every_depth():
    payload = {
        "Authorization": "Bearer abc",
        "clientSecret": "shhh",
        "authData": {"otp": {"otpValue": "123456", "txnId": "keep-me"}},
        "accounts": [{"mobile": "9999999999", "name": "keep-me"}],
    }
    masked = redact(payload)
    assert masked["Authorization"] == "<redacted>"
    assert masked["clientSecret"] == "<redacted>"
    assert masked["authData"]["otp"]["otpValue"] == "<redacted>"
    assert masked["authData"]["otp"]["txnId"] == "keep-me"
    assert masked["accounts"][0]["mobile"] == "<redacted>"
    assert masked["accounts"][0]["name"] == "keep-me"


def test_4xx_is_not_retried():
    """A rejected request replayed unchanged returns the same rejection and
    spends the sandbox's rate limit to learn nothing."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, json={"code": "ABDM-1", "message": "bad otp"})

    transport = Transport(
        httpx.Client(transport=httpx.MockTransport(handler)), backoff_seconds=0
    )
    with pytest.raises(AbhaApiError) as exc:
        transport.send_json("POST", "https://x/y", headers={}, json_body={})
    assert len(calls) == 1
    assert exc.value.code == "ABDM-1"


def test_5xx_is_retried_then_surfaces():
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, json={"message": "upstream down"})

    transport = Transport(
        httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=2,
        backoff_seconds=0,
    )
    with pytest.raises(AbhaApiError):
        transport.send_json("POST", "https://x/y", headers={}, json_body={})
    assert len(calls) == 3


def test_transport_faults_are_retried_and_typed_separately():
    """A connection reset is the one failure worth retrying blindly, and the
    one a caller may want to distinguish from a refusal."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("reset")

    transport = Transport(
        httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=1,
        backoff_seconds=0,
    )
    with pytest.raises(AbhaTransportError):
        transport.send_json("GET", "https://x/y", headers={})
    assert len(calls) == 2


def test_empty_body_on_success_is_not_a_parse_failure():
    """202 and 204 are real answers on the async parts of the surface."""
    transport = Transport(
        httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(202, content=b""))
        )
    )
    assert transport.send_json("POST", "https://x/y", headers={}) == {}


def test_three_error_body_shapes_all_yield_a_message():
    """ABDM uses all three in circulation; a caller must never see the bare
    'HTTP 400' fallback for any of them."""
    shapes = [
        {"code": "ABDM-1044", "message": "invalid otp"},
        {"error": {"code": "ABDM-1044", "message": "invalid otp"}},
        {"details": [{"code": "ABDM-1044", "message": "invalid otp"}]},
    ]
    for body in shapes:
        error = AbhaApiError.from_response(httpx.Response(400, json=body))
        assert error.code == "ABDM-1044", body
        assert "invalid otp" in error.message, body


def test_k547_gets_the_biometric_hint_appended():
    """ABDM's own message for K-547 says nothing about the lr flag, which is
    always what caused it."""
    error = AbhaApiError.from_response(
        httpx.Response(400, json={"code": "K-547", "message": "auth failed"})
    )
    assert "lr" in error.message
    assert "biometrics" in error.message


def test_non_json_error_body_keeps_the_raw_text():
    """A proxy's HTML error page must still be reportable."""
    error = AbhaApiError.from_response(httpx.Response(502, text="<html>gateway</html>"))
    assert error.raw and "gateway" in error.raw
    assert error.is_retryable


def test_flat_field_error_map_is_the_fourth_shape():
    """ABDM's request-validation layer emits a flat field -> message map with
    no code, message or details key anywhere in it.

    It is the most common error body on the whole v3 surface, and a parser
    that knows only the three enveloped shapes falls back to "ABDM returned
    HTTP 400" -- discarding the one thing the caller needed, which is *which
    field* was wrong. Reached in practice, from the UI, on a real enrolment.
    """
    error = AbhaApiError.from_response(
        httpx.Response(
            400,
            json={
                "txnId": "Invalid Transaction Id",
                "mobile": "Invalid Mobile Number",
                "timestamp": "2026-09-13 11:09:49",
            },
        )
    )
    assert "txnId: Invalid Transaction Id" in error.message
    assert "mobile: Invalid Mobile Number" in error.message
    assert error.message != "ABDM returned HTTP 400"
    assert {d["attribute"] for d in error.details} == {"txnId", "mobile"}


def test_timestamp_is_not_reported_as_a_field_error():
    """Every one of these bodies carries it, and it is metadata rather than a
    complaint. Reporting it would bury the real errors."""
    error = AbhaApiError.from_response(
        httpx.Response(400, json={"scope": "Invalid Scope", "timestamp": "2026-09-13"})
    )
    assert "timestamp" not in error.message
    assert error.message == "scope: Invalid Scope"


def test_an_enveloped_body_still_wins_over_the_flat_reading():
    """Shape 4 is recognised by exclusion, so it must not hijack a body that
    already yielded a proper message."""
    error = AbhaApiError.from_response(
        httpx.Response(400, json={"code": "ABDM-1044", "message": "invalid otp"})
    )
    assert error.message == "invalid otp"
    assert error.details == []


def test_a_body_with_no_usable_text_still_falls_back():
    error = AbhaApiError.from_response(httpx.Response(400, json={"timestamp": "x"}))
    assert error.message == "ABDM returned HTTP 400"
