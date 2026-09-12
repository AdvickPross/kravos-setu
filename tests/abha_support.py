"""A scripted ABDM, so the M1 flows can be tested without a sandbox.

The premise of these tests is worth stating, because it bounds what they are
evidence of. There is no ABDM sandbox in CI and there will not be one: the
credentials are per-integrator, the sandbox is rate-limited to 100 ABHA
creations per client id, and the OTPs go to a real phone. So nothing here
proves that ABDM accepts these calls.

What it does prove is the half that is actually ours. Every failure in an
integration like this is one of: a missing mandatory header, a field
encrypted that should not have been (or the reverse), the wrong `scope` for
the chosen credential, a `txnId` not threaded from one call to the next, or a
poll loop that treats an unknown status as "keep waiting". All of those are
properties of the *request* this code builds, and all of them are pinned
here exactly.

`FakeAbdm` records every request and answers from a route table. The real
`AbhaClient`, `Transport`, `SessionManager` and crypto run unchanged -- only
the socket is replaced -- so the assertions are made against the bytes that
would have gone out.

The RSA key is generated per-session and the fake decrypts with it. That is
what makes "was this field encrypted, and with what" a checkable assertion
rather than a hope: `decrypt()` returns the plaintext ABDM would have seen.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Callable

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from abha.client import AbhaClient
from abha.config import AbhaConfig, AbhaEnvironment

_PADDING = padding.OAEP(
    mgf=padding.MGF1(algorithm=hashes.SHA1()),
    algorithm=hashes.SHA1(),
    label=None,
)


class FakeAbdm:
    """A route table plus a request log.

    Routes are keyed by the path suffix, which keeps the tests readable and
    host-agnostic -- a test asserting on enrolment should not have to repeat
    `https://abhasbx.abdm.gov.in/abha/api` on every line. A route value is
    either a dict (answered as 200 JSON), an `httpx.Response`, or a callable
    taking the request and returning either.

    A list of values is a *script*: successive calls to the same path consume
    successive entries. That is what makes the retry and re-auth paths
    testable -- "fails once, then succeeds" is otherwise not expressible.
    """

    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        self.private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        self.public_pem = (
            self.private_key.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("ascii")
        )
        self.requests: list[httpx.Request] = []
        self.routes: dict[str, Any] = {
            # Every flow needs these two before it reaches its own endpoint,
            # so they are pre-wired; a test overriding them still can.
            "/gateway/v3/sessions": {
                "accessToken": "session-token-1",
                "expiresIn": 1200,
                "refreshToken": "refresh-token-1",
                "refreshExpiresIn": 1800,
                "tokenType": "bearer",
            },
            "/v3/profile/public/certificate": lambda _r: {
                "publicKey": self.public_pem,
                "encryptionAlgorithm": "RSA/ECB/OAEPWithSHA-1AndMGF1Padding",
            },
        }
        self.routes.update(routes or {})

    # -- transport ---------------------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        for suffix, value in self.routes.items():
            if path.endswith(suffix):
                return self._respond(value, request, suffix)
        return httpx.Response(
            404, json={"code": "TEST-404", "message": f"no fake route for {path}"}
        )

    def _respond(self, value: Any, request: httpx.Request, key: str) -> httpx.Response:
        if isinstance(value, list):
            if not value:
                raise AssertionError(f"fake route {key} ran out of scripted responses")
            value = value.pop(0)
        if callable(value):
            value = value(request)
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, json=value)

    def client(self, **config_overrides: Any) -> AbhaClient:
        config = AbhaConfig(
            client_id="test-client",
            client_secret="test-secret",
            environment=AbhaEnvironment.SANDBOX,
            **config_overrides,
        )
        http = httpx.Client(transport=httpx.MockTransport(self.handler))
        return AbhaClient(config, http_client=http)

    # -- assertions --------------------------------------------------------

    def decrypt(self, ciphertext: str) -> str:
        """What ABDM would have read. The point of the whole fixture."""
        return self.private_key.decrypt(
            base64.b64decode(ciphertext), _PADDING
        ).decode("utf-8")

    def sent_to(self, suffix: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith(suffix)]

    def last(self, suffix: str) -> httpx.Request:
        matches = self.sent_to(suffix)
        if not matches:
            paths = [r.url.path for r in self.requests]
            raise AssertionError(f"nothing was sent to {suffix}; sent: {paths}")
        return matches[-1]

    def body(self, suffix: str) -> dict[str, Any]:
        return json.loads(self.last(suffix).content or b"{}")

    def headers(self, suffix: str) -> httpx.Headers:
        return self.last(suffix).headers


def error(status: int, code: str, message: str = "refused") -> httpx.Response:
    return httpx.Response(status, json={"code": code, "message": message})


def collecting_sleep() -> tuple[Callable[[float], None], list[float]]:
    """A `sleep` that records instead of sleeping.

    The face-auth poll loop would otherwise take real seconds per iteration,
    and its interval is one of the things worth asserting.
    """
    slept: list[float] = []
    return slept.append, slept
