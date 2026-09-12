"""The HTTP floor: mandatory headers, one retry policy, one error boundary.

Nearly every v3 call requires `REQUEST-ID` and `TIMESTAMP` alongside the
usual `Authorization`, and ABDM rejects the call outright if either is
missing or malformed. They are generated here, once, rather than at each of
the ~25 call sites -- a header this easy to forget should not be the caller's
responsibility.

`TIMESTAMP` has a trap in it. ABDM wants UTC ISO-8601 and rejects a value
that drifts too far from its own clock. `datetime.now().isoformat()` on a
machine in IST produces a local time with no offset, which reads to ABDM as
UTC and lands five and a half hours in the future. `_timestamp()` uses an
aware UTC clock and formats the `Z` suffix explicitly.

Everything network-shaped is funnelled through `send_json`, so there is
exactly one place that decides what a failure means, and exactly one place
that retries. Retries are confined to transport faults and 5xx: a 4xx is the
request being wrong, and sending it again unchanged just spends the sandbox's
rate limit to receive the same refusal.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from abha.errors import AbhaApiError, AbhaTransportError

log = logging.getLogger(__name__)

# Redacted before anything reaches a log line. These are the header and body
# keys that carry either a credential or unencrypted PII.
_SENSITIVE = {
    "authorization",
    "x-token",
    "t-token",
    "clientsecret",
    "client_secret",
    "accesstoken",
    "refreshtoken",
    "otpvalue",
    "loginid",
    "password",
    "aadhaar",
    "mobile",
}


def new_request_id() -> str:
    return str(uuid.uuid4())


def timestamp() -> str:
    """UTC, ISO-8601, explicit `Z`. See the module docstring on why not `now()`."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def redact(payload: Any) -> Any:
    """Recursively mask credentials and PII so a payload can be logged.

    Applied to headers and bodies before any debug logging. An integration
    that logs its own request bodies verbatim leaks Aadhaar numbers into
    whatever ships the logs, and that is not recoverable after the fact.
    """
    if isinstance(payload, dict):
        return {
            k: ("<redacted>" if k.lower().replace("-", "") in _SENSITIVE else redact(v))
            for k, v in payload.items()
        }
    if isinstance(payload, list):
        return [redact(item) for item in payload]
    return payload


def base_headers(
    *,
    request_id: str | None = None,
    when: str | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    headers = {
        "REQUEST-ID": request_id or new_request_id(),
        "TIMESTAMP": when or timestamp(),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if extra:
        # Drop Nones so callers can pass optional headers inline
        # (`{"X-token": token}`) without guarding each one.
        headers.update({k: v for k, v in extra.items() if v is not None})
    return headers


class Transport:
    """Thin wrapper over one pooled `httpx.Client`.

    Takes an `httpx.Client` rather than building one so tests can hand in a
    `MockTransport`-backed client. That is the whole reason this class is
    separable from `AbhaClient`: the request *shape* -- headers, encryption,
    sequencing -- is what has to be right, and it can be pinned exactly
    without a live sandbox.
    """

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        timeout: float = 30.0,
        max_retries: int = 2,
        backoff_seconds: float = 0.5,
    ) -> None:
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Transport:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def send(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            log.debug(
                "%s %s headers=%s body=%s", method, url, redact(headers), redact(json_body)
            )
            try:
                response = self._client.request(
                    method, url, headers=headers, json=json_body
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == self.max_retries:
                    raise AbhaTransportError(f"{method} {url} failed: {exc}") from exc
                time.sleep(self.backoff_seconds * (2**attempt))
                continue

            if response.status_code < 400:
                return response

            error = AbhaApiError.from_response(response)
            # Only 5xx is retried. A 4xx replayed unchanged returns the same
            # 4xx and burns sandbox quota; see the module docstring.
            if not error.is_retryable or attempt == self.max_retries:
                raise error
            last_error = error
            time.sleep(self.backoff_seconds * (2**attempt))

        # Unreachable: every path above either returns or raises.
        raise AbhaTransportError(f"{method} {url} exhausted retries: {last_error}")

    def send_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self.send(method, url, headers=headers, json_body=json_body)
        if not response.content:
            # 202/204 are real answers in the async parts of the surface --
            # an empty body is success, not a parse failure.
            return {}
        try:
            parsed = response.json()
        except ValueError as exc:
            raise AbhaApiError(
                f"ABDM returned non-JSON on a successful {method} {url}",
                status_code=response.status_code,
                raw=response.text[:2000],
            ) from exc
        return parsed if isinstance(parsed, dict) else {"data": parsed}
