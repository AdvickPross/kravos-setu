"""Typed failures, so callers can branch on *why* a call failed.

ABDM returns HTTP 400 for a large family of unrelated situations: a wrong
OTP, an expired transaction, an Aadhaar with no linked mobile, an ABHA that
already exists, and a malformed biometric PID block all arrive as 400. A
service that only sees "400" cannot tell the user whether to retype the OTP,
restart the flow, or stop trying -- so the status code alone is not a usable
signal and the body has to be parsed.

The bodies are not uniform either. Three shapes are in circulation across the
v3 surface:

    {"code": "ABDM-1044", "message": "..."}
    {"error": {"code": "...", "message": "...", "details": [...]}}
    {"details": [{"code": "...", "message": "...", "attribute": "..."}]}

`AbhaApiError.from_response` flattens all three. When it cannot, it keeps the
raw body on the exception rather than inventing a message -- a truncated
unparsed body is more useful in a bug report than a confident wrong summary.

One code is singled out. `K-547` means the biometric PID block was built with
`lr` set to something other than `'Y'`; it is the single most common mistake
in fingerprint enrolment and its ABDM-supplied message does not say so.
"""

from __future__ import annotations

from typing import Any

import httpx


class AbhaError(RuntimeError):
    """Base for everything this package raises."""


class AbhaTransportError(AbhaError):
    """The request never got an answer: DNS, TLS, timeout, connection reset.

    Separate from `AbhaApiError` because it is the only failure class that is
    worth retrying blindly. An API rejection retried unchanged returns the
    same rejection.
    """


class AbhaAuthError(AbhaError):
    """The session or transaction token was missing, expired or rejected."""


class AbhaApiError(AbhaError):
    """ABDM answered, and the answer was a refusal."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: str | None = None,
        details: list[dict[str, Any]] | None = None,
        raw: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.details = details or []
        self.raw = raw

    @property
    def is_retryable(self) -> bool:
        """Only server-side faults. A 4xx means the request itself is wrong."""
        return self.status_code >= 500

    @classmethod
    def from_response(cls, response: httpx.Response) -> AbhaApiError:
        raw = response.text
        try:
            body = response.json()
        except ValueError:
            body = None

        code: str | None = None
        message: str | None = None
        details: list[dict[str, Any]] = []

        if isinstance(body, dict):
            # Shape 2 nests everything under "error"; unwrap before reading so
            # the three shapes collapse into one set of lookups below.
            envelope = body.get("error") if isinstance(body.get("error"), dict) else body
            code = envelope.get("code") or envelope.get("errorCode")
            message = envelope.get("message") or envelope.get("errorMessage")
            raw_details = envelope.get("details") or body.get("details")
            if isinstance(raw_details, list):
                details = [d for d in raw_details if isinstance(d, dict)]
            # Shape 3 carries no top-level message at all -- the only human
            # text is inside details, so promote it rather than reporting
            # "HTTP 400" with an empty reason.
            if not message and details:
                message = "; ".join(
                    str(d.get("message")) for d in details if d.get("message")
                ) or None
            if not code and details:
                code = details[0].get("code")

        if not message:
            message = f"ABDM returned HTTP {response.status_code}"

        if code == "K-547":
            message += (
                " -- K-547 means the biometric PID block set `lr` to something "
                "other than 'Y'. See abha/biometrics.py: the wadh digest must "
                "be built over '2.5' + ra + rc + lr + de + pfr with lr='Y'."
            )

        return cls(
            message,
            status_code=response.status_code,
            code=code,
            details=details,
            # Bound the stored body: an HTML error page from a proxy can be
            # megabytes, and this string ends up in logs.
            raw=raw[:2000] if raw else None,
        )

    def __str__(self) -> str:
        prefix = f"[{self.code}] " if self.code else ""
        return f"{prefix}{self.message} (HTTP {self.status_code})"


class AbhaFlowError(AbhaError):
    """A local sequencing mistake, caught before a request is sent.

    Calling `verify_otp` without a `txnId`, or polling face-auth on a
    transaction that already completed. Raised by this package, never by
    ABDM -- which is the point: these are the failures worth preventing
    rather than reporting.
    """
